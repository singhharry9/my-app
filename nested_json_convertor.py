from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


APP_TITLE = "Nested JSON Table Agent"

IDENTIFIER_RE = re.compile(r"[^0-9a-zA-Z_]+")
INTEGER_RE = re.compile(r"^[+-]?\d+$")
DECIMAL_RE = re.compile(r"^[+-]?(?:\d+\.\d+|\d+\.|\.\d+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-][0-2]\d:?[0-5]\d)?$"
)

SYSTEM_COLUMNS = ["_row_id", "_parent_id", "_parent_table", "_array_index"]


@dataclass
class Table:
    name: str
    path: str
    parent: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class NormalizeResult:
    root_table: str
    tables: dict[str, Table]
    relations: list[dict[str, str]]
    warnings: list[str]
    schema: dict[str, list[dict[str, Any]]]
    generated_at: str


@dataclass
class ExportBundle:
    schema_sql: bytes
    data_sql: bytes
    excel: bytes
    report_json: bytes
    csv_files: dict[str, bytes]


def clean_name(value: str, default: str = "field") -> str:
    name = IDENTIFIER_RE.sub("_", str(value).strip()).strip("_").lower()
    if not name:
        name = default
    if name[0].isdigit():
        name = f"c_{name}"
    return name[:80]


def compact_name(name: str) -> str:
    if len(name) <= 58:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:49].rstrip('_')}_{digest}"


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def parse_json_text(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("JSON data is required.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


class JsonTableNormalizer:
    def __init__(self, root_table: str):
        self.root_table = clean_name(root_table or "root", "root")
        self.tables: dict[str, Table] = {}
        self.path_to_table: dict[str, str] = {}
        self.next_ids: dict[str, int] = {}
        self.relations: list[dict[str, str]] = []
        self.relation_keys: set[tuple[str, str, str]] = set()
        self.warnings: list[str] = []

    def normalize(self, payload: Any) -> NormalizeResult:
        root_name = self.table_for_path([self.root_table], "")
        if isinstance(payload, list):
            self.ensure_table(root_name, self.root_table, "")
            if not payload:
                self.tables[root_name].notes.append("Root JSON array is empty.")
            for index, item in enumerate(payload):
                self.add_value_row(root_name, [self.root_table], item, "", None, index)
        elif isinstance(payload, dict):
            self.add_object_row(root_name, [self.root_table], payload, "", None, None)
        else:
            self.add_scalar_row(root_name, payload, "", None, None)

        schema = {name: infer_schema(table) for name, table in self.tables.items()}
        return NormalizeResult(
            root_table=self.root_table,
            tables=self.tables,
            relations=self.relations,
            warnings=self.warnings,
            schema=schema,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )

    def table_for_path(self, parts: list[str], parent: str) -> str:
        cleaned = [clean_name(part) for part in parts if str(part).strip()]
        path = ".".join(cleaned) or self.root_table
        if path in self.path_to_table:
            return self.path_to_table[path]

        base = compact_name("_".join(cleaned) or self.root_table)
        table_name = base
        suffix = 2
        while table_name in self.tables and self.tables[table_name].path != path:
            table_name = compact_name(f"{base}_{suffix}")
            suffix += 1
        self.path_to_table[path] = table_name
        self.ensure_table(table_name, path, parent)
        return table_name

    def ensure_table(self, table_name: str, path: str, parent: str) -> Table:
        if table_name not in self.tables:
            self.tables[table_name] = Table(name=table_name, path=path, parent=parent)
        return self.tables[table_name]

    def next_row_id(self, table_name: str) -> int:
        value = self.next_ids.get(table_name, 0) + 1
        self.next_ids[table_name] = value
        return value

    def add_relation(self, parent_table: str, child_table: str, key: str) -> None:
        relation_key = (parent_table, child_table, key)
        if relation_key in self.relation_keys:
            return
        self.relation_keys.add(relation_key)
        self.relations.append(
            {
                "parent_table": parent_table,
                "child_table": child_table,
                "array_field": key,
                "join": f"{child_table}._parent_id = {parent_table}._row_id",
            }
        )

    def add_value_row(
        self,
        table_name: str,
        path_parts: list[str],
        value: Any,
        parent_table: str,
        parent_id: int | None,
        array_index: int | None,
    ) -> int:
        if isinstance(value, dict):
            return self.add_object_row(table_name, path_parts, value, parent_table, parent_id, array_index)
        if isinstance(value, list):
            row_id = self.add_scalar_row(
                table_name,
                json.dumps(value, ensure_ascii=False),
                parent_table,
                parent_id,
                array_index,
                value_type="array_json",
            )
            self.warnings.append(
                f"Array item in table '{table_name}' was another array, so it was stored as JSON text."
            )
            return row_id
        return self.add_scalar_row(table_name, value, parent_table, parent_id, array_index)

    def add_object_row(
        self,
        table_name: str,
        path_parts: list[str],
        obj: dict[str, Any],
        parent_table: str,
        parent_id: int | None,
        array_index: int | None,
    ) -> int:
        row_id = self.next_row_id(table_name)
        row: dict[str, Any] = {"_row_id": row_id}
        if parent_id is not None:
            row["_parent_id"] = parent_id
            row["_parent_table"] = parent_table
        if array_index is not None:
            row["_array_index"] = array_index

        for raw_key, value in obj.items():
            key = clean_name(raw_key)
            if is_scalar(value):
                row[key] = value
            elif isinstance(value, dict):
                self.flatten_object(value, row, key, table_name, row_id, path_parts + [key])
            elif isinstance(value, list):
                self.add_array(table_name, row_id, path_parts + [key], key, value)
            else:
                row[key] = str(value)

        self.append_row(table_name, row)
        return row_id

    def flatten_object(
        self,
        obj: dict[str, Any],
        row: dict[str, Any],
        prefix: str,
        current_table: str,
        current_row_id: int,
        path_parts: list[str],
    ) -> None:
        for raw_key, value in obj.items():
            key = f"{prefix}_{clean_name(raw_key)}"
            if is_scalar(value):
                row[key] = value
            elif isinstance(value, dict):
                self.flatten_object(value, row, key, current_table, current_row_id, path_parts + [key])
            elif isinstance(value, list):
                self.add_array(current_table, current_row_id, path_parts + [key], key, value)
            else:
                row[key] = str(value)

    def add_array(
        self,
        parent_table: str,
        parent_id: int,
        path_parts: list[str],
        key: str,
        values: list[Any],
    ) -> None:
        child_table = self.table_for_path(path_parts, parent_table)
        self.add_relation(parent_table, child_table, key)
        if not values:
            table = self.tables[child_table]
            for column in SYSTEM_COLUMNS:
                if column not in table.columns:
                    table.columns.append(column)
            table.notes.append(f"Array field '{key}' was empty for at least one parent row.")
            return

        for index, item in enumerate(values):
            self.add_value_row(child_table, path_parts, item, parent_table, parent_id, index)

    def add_scalar_row(
        self,
        table_name: str,
        value: Any,
        parent_table: str,
        parent_id: int | None,
        array_index: int | None,
        value_type: str = "value",
    ) -> int:
        row_id = self.next_row_id(table_name)
        row: dict[str, Any] = {"_row_id": row_id}
        if parent_id is not None:
            row["_parent_id"] = parent_id
            row["_parent_table"] = parent_table
        if array_index is not None:
            row["_array_index"] = array_index
        row[value_type] = value
        self.append_row(table_name, row)
        return row_id

    def append_row(self, table_name: str, row: dict[str, Any]) -> None:
        table = self.tables[table_name]
        table.rows.append(row)
        ordered = [column for column in SYSTEM_COLUMNS if column in row]
        ordered += [column for column in row if column not in SYSTEM_COLUMNS]
        for column in ordered:
            if column not in table.columns:
                table.columns.append(column)


def normalize_json(text: str, root_table: str) -> NormalizeResult:
    return JsonTableNormalizer(root_table).normalize(parse_json_text(text))


def classify_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "decimal"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return "blank"
        if DATETIME_RE.match(stripped):
            return "datetime_text"
        if DATE_RE.match(stripped):
            return "date_text"
        if INTEGER_RE.match(stripped):
            return "integer_text"
        if DECIMAL_RE.match(stripped):
            return "decimal_text"
        if stripped.lower() in {"true", "false", "yes", "no"}:
            return "boolean_text"
        return "text"
    return "text"


def base_type(kind: str) -> str:
    if kind in {"integer", "integer_text"}:
        return "integer"
    if kind in {"decimal", "decimal_text"}:
        return "decimal"
    if kind == "date_text":
        return "date"
    if kind == "datetime_text":
        return "datetime"
    if kind in {"boolean", "boolean_text"}:
        return "boolean"
    if kind in {"null", "blank"}:
        return "empty"
    return "text"


def infer_schema(table: Table) -> list[dict[str, Any]]:
    schema = []
    row_count = len(table.rows)
    for column in table.columns:
        values = [row.get(column) for row in table.rows]
        type_counts: dict[str, int] = {}
        max_len = 0
        null_count = 0
        blank_count = 0
        for value in values:
            kind = classify_value(value)
            type_counts[kind] = type_counts.get(kind, 0) + 1
            if value is None:
                null_count += 1
            if isinstance(value, str) and value.strip() == "":
                blank_count += 1
            if value is not None:
                max_len = max(max_len, len(str(value)))

        base_types = {
            base_type(kind)
            for kind, count in type_counts.items()
            if count and base_type(kind) != "empty"
        }
        schema.append(
            {
                "column": column,
                "suggested_type": suggested_sql_type(base_types, max_len, column),
                "observed_types": ", ".join(sorted(type_counts)) or "empty",
                "nullable": null_count > 0 or row_count == 0,
                "nulls": null_count,
                "blanks": blank_count,
                "max_length": max_len,
                "mixed_types": len(base_types) > 1,
            }
        )
    return schema


def suggested_sql_type(base_types: set[str], max_len: int, column: str) -> str:
    if column in {"_row_id", "_parent_id", "_array_index"}:
        return "BIGINT"
    if column == "_parent_table":
        return "VARCHAR(80)"
    if not base_types:
        return "TEXT"
    if base_types == {"integer"}:
        return "BIGINT"
    if base_types <= {"integer", "decimal"}:
        return "DECIMAL(18,6)"
    if base_types == {"date"}:
        return "DATE"
    if base_types <= {"date", "datetime"}:
        return "DATETIME"
    if base_types == {"boolean"}:
        return "BOOLEAN"
    return f"VARCHAR({max(1, min(max_len, 255))})" if max_len <= 255 else "TEXT"


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def display_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def result_to_dict(result: NormalizeResult) -> dict[str, Any]:
    return {
        "generated_at": result.generated_at,
        "root_table": result.root_table,
        "relations": result.relations,
        "warnings": result.warnings,
        "tables": [
            {
                "name": table.name,
                "path": table.path,
                "parent": table.parent,
                "row_count": len(table.rows),
                "columns": table.columns,
                "schema": result.schema.get(table.name, []),
                "rows": table.rows,
                "notes": table.notes,
            }
            for table in result.tables.values()
        ],
    }


def quote_sql_name(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = csv_value(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def render_sql_schema(result: NormalizeResult) -> str:
    lines = [
        f"-- Generated by {APP_TITLE}",
        f"-- Generated at: {result.generated_at}",
        "-- Review column types before using in production.",
        "",
    ]
    for relation in result.relations:
        lines.append(
            f"-- Relation: {relation['child_table']}._parent_id -> {relation['parent_table']}._row_id "
            f"from array '{relation['array_field']}'"
        )
    if result.relations:
        lines.append("")

    for table in result.tables.values():
        schema = result.schema.get(table.name, [])
        schema_by_column = {row["column"]: row for row in schema}
        lines.append(f"CREATE TABLE {quote_sql_name(table.name)} (")
        definitions = []
        for column in table.columns:
            info = schema_by_column.get(column, {})
            sql_type = info.get("suggested_type", "TEXT")
            suffix = " NOT NULL" if column == "_row_id" else " NULL"
            definitions.append(f"  {quote_sql_name(column)} {sql_type}{suffix}")
        if "_row_id" in table.columns:
            definitions.append("  PRIMARY KEY (`_row_id`)")
        if "_parent_id" in table.columns:
            index_name = compact_name(f"idx_{table.name}_parent_id")
            definitions.append(f"  KEY {quote_sql_name(index_name)} (`_parent_id`)")
        lines.append(",\n".join(definitions))
        lines.append(");")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_sql_data(result: NormalizeResult) -> str:
    lines = [
        f"-- Generated by {APP_TITLE}",
        f"-- Generated at: {result.generated_at}",
        "-- Includes table schema and INSERT statements.",
        "",
        render_sql_schema(result).rstrip(),
        "",
    ]
    for table in result.tables.values():
        if not table.rows:
            lines.append(f"-- No rows for {table.name}")
            lines.append("")
            continue
        columns_sql = ", ".join(quote_sql_name(column) for column in table.columns)
        lines.append(f"-- Data for {table.name}")
        for row in table.rows:
            values_sql = ", ".join(sql_literal(row.get(column)) for column in table.columns)
            lines.append(f"INSERT INTO {quote_sql_name(table.name)} ({columns_sql}) VALUES ({values_sql});")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def table_csv_bytes(table: Table) -> bytes:
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=table.columns, extrasaction="ignore")
    writer.writeheader()
    for row in table.rows:
        writer.writerow({column: csv_value(row.get(column)) for column in table.columns})
    return handle.getvalue().encode("utf-8")


def excel_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name or "A"


def xml_text(value: Any) -> str:
    text = csv_value(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return html.escape(text, quote=True)


def excel_sheet_name(name: str, used: set[str]) -> str:
    sheet = re.sub(r"[\[\]:*?/\\]", "_", name).strip() or "Sheet"
    sheet = sheet[:31]
    base = sheet
    suffix = 2
    while sheet in used:
        tail = f"_{suffix}"
        sheet = (base[: 31 - len(tail)] + tail)[:31]
        suffix += 1
    used.add(sheet)
    return sheet


def render_sheet_xml(table: Table) -> str:
    rows_xml = []
    all_rows = [dict.fromkeys(table.columns, "")] + table.rows
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for col_index, column in enumerate(table.columns, start=1):
            ref = f"{excel_column_name(col_index)}{row_index}"
            value = column if row_index == 1 else row.get(column)
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{xml_text(value)}</t></is></c>')
        rows_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    dimension_end = f"{excel_column_name(max(1, len(table.columns)))}{max(1, len(all_rows))}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{dimension_end}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        '</worksheet>'
    )


def render_excel_bytes(result: NormalizeResult) -> bytes:
    output = io.BytesIO()
    used_names: set[str] = set()
    sheet_meta = [
        (index, table, excel_sheet_name(table.name, used_names))
        for index, table in enumerate(result.tables.values(), start=1)
    ]
    workbook_sheets = "".join(
        f'<sheet name="{xml_text(sheet_name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, _table, sheet_name in sheet_meta
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index, _table, _sheet_name in sheet_meta
    )
    workbook_rels += (
        f'<Relationship Id="rId{len(sheet_meta) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index, _table, _sheet_name in sheet_meta
    )

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{sheet_overrides}"
            "</Types>",
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets>"
            "</workbook>",
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{workbook_rels}"
            "</Relationships>",
        )
        workbook.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
            "</styleSheet>",
        )
        for index, table, _sheet_name in sheet_meta:
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", render_sheet_xml(table))
    return output.getvalue()


def make_exports(result: NormalizeResult) -> ExportBundle:
    return ExportBundle(
        schema_sql=render_sql_schema(result).encode("utf-8"),
        data_sql=render_sql_data(result).encode("utf-8"),
        excel=render_excel_bytes(result),
        report_json=json.dumps(result_to_dict(result), indent=2, default=str).encode("utf-8"),
        csv_files={table.name: table_csv_bytes(table) for table in result.tables.values()},
    )


SAMPLE_JSON = """{
  "id": 198424,
  "trans": [
    {
      "status": "I",
      "item_id": "37355",
      "req_qty": "9.91",
      "approved_qty": "9.91",
      "approved_date": "2026-04-20",
      "approved_by_emp_id": 5140,
      "approved_by_user_id": 99
    }
  ],
  "number": "26/BR18918",
  "req_date": "2026-04-20",
  "req_type": "T",
  "narration": "STOCK TRANSFER",
  "company_id": "1",
  "created_at": "2026-04-20T03:35:31.000000Z",
  "created_by": 99,
  "project_id": null,
  "updated_at": "2026-04-20T03:35:31.000000Z",
  "updated_by": 99,
  "inventory_id": "48",
  "req_inventory_id": "52"
}"""


def setup_streamlit_page() -> Any:
    try:
        import streamlit as st
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Streamlit is not installed. Install it with: pip install streamlit"
        ) from exc

    st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded")
    st.markdown(
        """
        <style>
        .stApp {
          background: linear-gradient(180deg, #e4f4fa 0, #f7f9fc 330px, #eef4f8 100%);
        }
        [data-testid="stHeader"] { background: rgba(255, 255, 255, 0); }
        .hero {
          background: linear-gradient(135deg, #17324d 0%, #0f7c80 52%, #f59e0b 100%);
          border-radius: 8px;
          color: white;
          padding: 24px 26px;
          margin-bottom: 18px;
          box-shadow: 0 16px 34px rgba(17, 49, 78, 0.18);
        }
        .hero h1 {
          color: white;
          font-size: 2rem;
          line-height: 1.15;
          margin: 0;
        }
        .eyebrow {
          color: rgba(255, 255, 255, 0.82);
          font-size: 0.78rem;
          font-weight: 800;
          margin: 0 0 6px;
          text-transform: uppercase;
        }
        .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
        .chip {
          border: 1px solid rgba(255, 255, 255, 0.42);
          background: rgba(255, 255, 255, 0.16);
          border-radius: 999px;
          color: white;
          font-size: 0.78rem;
          font-weight: 760;
          padding: 7px 10px;
        }
        div[data-testid="stMetric"] {
          background: #f7fbff;
          border: 1px solid #d4e0eb;
          border-radius: 8px;
          border-top: 4px solid #0f7c80;
          padding: 12px;
        }
        div[data-testid="stDownloadButton"] > button,
        div[data-testid="stButton"] > button {
          border-radius: 6px;
          font-weight: 760;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    return st


def preview_rows(table: Table, limit: int = 200) -> list[dict[str, str]]:
    return [
        {column: display_value(row.get(column)) for column in table.columns}
        for row in table.rows[:limit]
    ]


def streamlit_main() -> None:
    st = setup_streamlit_page()

    if "json_text" not in st.session_state:
        st.session_state.json_text = SAMPLE_JSON

    st.markdown(
        f"""
        <div class="hero">
          <p class="eyebrow">JSON to relational tables</p>
          <h1>{APP_TITLE}</h1>
          <div class="chip-row">
            <span class="chip">CSV</span>
            <span class="chip">SQL</span>
            <span class="chip">Excel</span>
            <span class="chip">Report</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Controls")
        root_table = st.text_input("Root table name", value="stock_request")
        uploaded = st.file_uploader("JSON file", type=["json"])
        if uploaded is not None:
            upload_id = f"{uploaded.name}:{uploaded.size}"
            if st.session_state.get("upload_id") != upload_id:
                st.session_state.upload_id = upload_id
                st.session_state.json_text = uploaded.getvalue().decode("utf-8")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Sample", use_container_width=True):
                st.session_state.json_text = SAMPLE_JSON
        with col_b:
            if st.button("Format", use_container_width=True):
                try:
                    st.session_state.json_text = json.dumps(
                        json.loads(st.session_state.json_text), indent=2, ensure_ascii=False
                    )
                except json.JSONDecodeError as exc:
                    st.error(f"Line {exc.lineno}, column {exc.colno}: {exc.msg}")

    st.subheader("Input JSON")
    st.text_area("JSON", key="json_text", height=360, label_visibility="collapsed")
    analyze = st.button("Analyze JSON", type="primary", use_container_width=True)

    if not analyze:
        return

    try:
        result = normalize_json(st.session_state.json_text, root_table)
        exports = make_exports(result)
    except Exception as exc:
        st.error(str(exc))
        return

    total_rows = sum(len(table.rows) for table in result.tables.values())
    total_columns = sum(len(table.columns) for table in result.tables.values())
    mixed_columns = sum(
        1
        for schema in result.schema.values()
        for column in schema
        if column.get("mixed_types")
    )

    st.subheader("Generated Table Model")
    metric_cols = st.columns(5)
    metric_cols[0].metric("Tables", len(result.tables))
    metric_cols[1].metric("Rows", total_rows)
    metric_cols[2].metric("Columns", total_columns)
    metric_cols[3].metric("Relations", len(result.relations))
    metric_cols[4].metric("Mixed Type Columns", mixed_columns)

    download_cols = st.columns(4)
    download_cols[0].download_button("schema.sql", exports.schema_sql, "schema.sql", "application/sql")
    download_cols[1].download_button("data.sql", exports.data_sql, "data.sql", "application/sql")
    download_cols[2].download_button(
        "tables.xlsx",
        exports.excel,
        "tables.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    download_cols[3].download_button("report.json", exports.report_json, "report.json", "application/json")

    if result.relations:
        st.markdown("#### Parent Child Relations")
        st.dataframe(result.relations, use_container_width=True, hide_index=True)

    notes = list(result.warnings)
    for table in result.tables.values():
        notes.extend(f"{table.name}: {note}" for note in table.notes)
    if notes:
        with st.expander("Review Notes", expanded=True):
            for note in notes:
                st.warning(note)

    for table in result.tables.values():
        st.divider()
        st.subheader(table.name)
        st.caption(f"{len(table.rows)} row(s), {len(table.columns)} column(s), path: {table.path}")
        st.download_button(
            f"Download {table.name}.csv",
            exports.csv_files[table.name],
            f"{table.name}.csv",
            "text/csv",
            key=f"csv_{table.name}",
        )

        schema_tab, preview_tab = st.tabs(["Schema", "Preview"])
        with schema_tab:
            st.dataframe(result.schema.get(table.name, []), use_container_width=True, hide_index=True)
        with preview_tab:
            st.dataframe(preview_rows(table), use_container_width=True, hide_index=True)
            if len(table.rows) > 200:
                st.caption("Showing first 200 rows. CSV download contains all rows.")


if __name__ == "__main__":
    streamlit_main()
