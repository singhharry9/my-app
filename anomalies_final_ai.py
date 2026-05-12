# app.py
import json
import os
import re
from datetime import datetime
from typing import Any, Iterable, Mapping

import pandas as pd
import pymysql
import streamlit as st

st.set_page_config(page_title="AI-Assisted Schema Anomaly Detector", layout="wide")

st.title("AI-Assisted Schema Anomaly Advisor")
st.caption("Read-only MySQL scanner with AI DBA summary and risk guidance")

RISKY_NUMERIC_NAMES = re.compile(
    r"(stock|qty|quantity|amount|balance|rate|price|cost|total|debit|credit)",
    re.I,
)

DATE_LIKE_NAMES = re.compile(r"(date|time|dob|created|updated|expiry)", re.I)
ID_LIKE_NAME = re.compile(r"(^.+_id$|^[a-z][a-z0-9_]+id$)", re.I)
EXPECTED_FUTURE_DATE_NAMES = re.compile(
    r"(expiry|expire|due|valid|eta|expected|schedule|scheduled|promise|delivery|followup|next)",
    re.I,
)
UNIQUE_CANDIDATE_NAMES = re.compile(
    r"(^|_)(email|mobile|phone|gst|gstin|pan|aadhaar|code|invoice|voucher|bill|challan|order)"
    r"(_?(no|num|number|code|id))?($|_)",
    re.I,
)
LOW_VOLUME_ROW_THRESHOLD = 100000
INTEGER_TYPE_ORDER = ["tinyint", "smallint", "mediumint", "int", "bigint"]
LARGE_TABLE_BYTES = 1024 * 1024 * 1024
VERY_LARGE_TABLE_BYTES = 5 * LARGE_TABLE_BYTES
LARGE_INDEX_BYTES = 512 * 1024 * 1024
MIN_INDEX_RATIO_BYTES = 10 * 1024 * 1024
EMPTY_TABLE_STORAGE_BYTES = 10 * 1024 * 1024
LOW_VOLUME_LARGE_STORAGE_BYTES = 128 * 1024 * 1024

INTEGER_LIMITS = {
    ("tinyint", False): 127,
    ("tinyint", True): 255,
    ("smallint", False): 32767,
    ("smallint", True): 65535,
    ("mediumint", False): 8388607,
    ("mediumint", True): 16777215,
    ("int", False): 2147483647,
    ("int", True): 4294967295,
    ("bigint", False): 9223372036854775807,
    ("bigint", True): 18446744073709551615,
}

SENSITIVE_FIELD_NAMES = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|email|mobile|phone|ssn|aadhaar|pan)",
    re.I,
)
EMAIL_VALUE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
LONG_NUMBER_VALUE = re.compile(r"\b\d{10,}\b")
WHITESPACE = re.compile(r"\s+")

AI_ADVISOR_INSTRUCTIONS = """
You are a senior MySQL DBA reviewing a read-only anomaly scan.

Return concise markdown with these sections:
1. Executive summary
2. Highest-risk findings
3. Recommended DBA actions
4. Validation SQL to run manually
5. Questions for the application owner

Rules:
- Do not invent tables, columns, or counts that are not present in the JSON.
- Do not recommend destructive SQL as an automatic fix.
- Any SQL must be read-only validation SQL unless clearly labeled as manual
  remediation for a DBA change window.
- Explain business impact in plain language.
- Treat samples as possibly redacted and incomplete.
""".strip()


def get_conn(host, port, user, password, database):
    return pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()

    return [
        {str(k).lower(): v for k, v in row.items()}
        for row in rows
    ]


def quote_name(name):
    return "`" + str(name).replace("`", "``") + "`"


def get_tables(conn, database):
    rows = q(
        conn,
        """
        SELECT TABLE_NAME AS table_name
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """,
        (database,),
    )
    return [r["table_name"] for r in rows]


def get_columns(conn, database, table):
    return q(
        conn,
        """
        SELECT
            COLUMN_NAME AS column_name,
            DATA_TYPE AS data_type,
            COLUMN_TYPE AS column_type,
            IS_NULLABLE AS is_nullable,
            COLUMN_KEY AS column_key,
            EXTRA AS extra,
            CHARACTER_MAXIMUM_LENGTH AS character_maximum_length,
            NUMERIC_PRECISION AS numeric_precision,
            NUMERIC_SCALE AS numeric_scale,
            CHARACTER_SET_NAME AS character_set_name,
            COLLATION_NAME AS collation_name
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """,
        (database, table),
    )


def get_table_status(conn, database, table):
    rows = q(
        conn,
        """
        SELECT
            ENGINE AS engine,
            TABLE_ROWS AS table_rows,
            AUTO_INCREMENT AS auto_increment,
            TABLE_COLLATION AS table_collation,
            DATA_LENGTH AS data_length,
            INDEX_LENGTH AS index_length
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND TABLE_TYPE = 'BASE TABLE'
        """,
        (database, table),
    )
    return rows[0] if rows else {}


def get_table_storage_summary(conn, database, tables):
    if not tables:
        return []

    placeholders = ", ".join(["%s"] * len(tables))
    rows = q(
        conn,
        f"""
        SELECT
            TABLE_NAME AS table_name,
            ENGINE AS engine,
            TABLE_ROWS AS estimated_rows,
            DATA_LENGTH AS data_bytes,
            INDEX_LENGTH AS index_bytes,
            DATA_FREE AS free_bytes,
            DATA_LENGTH + INDEX_LENGTH AS total_bytes
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
          AND TABLE_TYPE = 'BASE TABLE'
          AND TABLE_NAME IN ({placeholders})
        ORDER BY total_bytes DESC, TABLE_NAME
        """,
        tuple([database] + list(tables)),
    )

    summary = []
    for row in rows:
        data_bytes = int(row.get("data_bytes") or 0)
        index_bytes = int(row.get("index_bytes") or 0)
        total_bytes = int(row.get("total_bytes") or 0)
        free_bytes = int(row.get("free_bytes") or 0)
        ratio = round(index_bytes / data_bytes, 2) if data_bytes else ""

        summary.append(
            {
                "table": row.get("table_name", ""),
                "engine": row.get("engine", ""),
                "estimated_rows": row.get("estimated_rows") or "",
                "data_size": format_bytes(data_bytes),
                "index_size": format_bytes(index_bytes),
                "total_size": format_bytes(total_bytes),
                "free_size": format_bytes(free_bytes),
                "index_data_ratio": ratio,
            }
        )

    return summary


def get_indexes(conn, database, table):
    return q(
        conn,
        """
        SELECT
            INDEX_NAME AS index_name,
            NON_UNIQUE AS non_unique,
            GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_in_index
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
        GROUP BY INDEX_NAME, NON_UNIQUE
        ORDER BY INDEX_NAME
        """,
        (database, table),
    )


def get_index_columns(conn, database, table):
    return q(
        conn,
        """
        SELECT
            INDEX_NAME AS index_name,
            NON_UNIQUE AS non_unique,
            SEQ_IN_INDEX AS seq_in_index,
            COLUMN_NAME AS column_name,
            SUB_PART AS sub_part,
            INDEX_TYPE AS index_type
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
        ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """,
        (database, table),
    )


def get_foreign_keys(conn, database, table):
    return q(
        conn,
        """
        SELECT
            COLUMN_NAME AS column_name,
            REFERENCED_TABLE_NAME AS referenced_table_name,
            REFERENCED_COLUMN_NAME AS referenced_column_name,
            CONSTRAINT_NAME AS constraint_name
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND REFERENCED_TABLE_NAME IS NOT NULL
        """,
        (database, table),
    )


def get_primary_key_columns(conn, database, table):
    return q(
        conn,
        """
        SELECT kcu.COLUMN_NAME AS column_name
        FROM information_schema.TABLE_CONSTRAINTS tc
        JOIN information_schema.KEY_COLUMN_USAGE kcu
          ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
         AND kcu.TABLE_NAME = tc.TABLE_NAME
         AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
        WHERE tc.TABLE_SCHEMA = %s
          AND tc.TABLE_NAME = %s
          AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ORDER BY kcu.ORDINAL_POSITION
        """,
        (database, table),
    )


def safe_count(conn, database, table):
    db = quote_name(database)
    tbl = quote_name(table)
    rows = q(conn, f"SELECT COUNT(*) AS cnt FROM {db}.{tbl}")
    return rows[0]["cnt"] if rows else 0


def add_issue(issues, table, check, severity, detail, count=None, column=None, sample=None):
    issues.append(
        {
            "table": table,
            "column": column or "",
            "check": check,
            "severity": severity,
            "issue_count": count if count is not None else "",
            "detail": detail,
            "sample": sample or "",
        }
    )


def format_bytes(value):
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


def normalize_identifier(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def singular_aliases(name):
    norm = normalize_identifier(name)
    aliases = {norm}

    if norm.endswith("ies") and len(norm) > 3:
        aliases.add(norm[:-3] + "y")
    if norm.endswith("es") and len(norm) > 2:
        aliases.add(norm[:-2])
    if norm.endswith("s") and len(norm) > 1:
        aliases.add(norm[:-1])

    return aliases


def relation_base_from_column(column):
    col = str(column).lower()
    if col == "id" or col.endswith("uuid"):
        return ""

    if col.endswith("_id"):
        return col[:-3]
    if ID_LIKE_NAME.search(col) and col.endswith("id"):
        return col[:-2]

    return ""


def infer_parent_tables(column, all_tables):
    base = relation_base_from_column(column)
    if not base:
        return []

    base_norm = normalize_identifier(base)
    base_tail_norm = normalize_identifier(base.rsplit("_", 1)[-1])
    candidate_norms = {base_norm, base_tail_norm}

    for norm in list(candidate_norms):
        if not norm:
            continue
        candidate_norms.update(
            {
                norm + "s",
                norm + "es",
                norm + "master",
                norm + "masters",
                norm + "detail",
                norm + "details",
            }
        )
        if norm.endswith("y"):
            candidate_norms.add(norm[:-1] + "ies")

    matches = []
    for candidate_table in all_tables or []:
        table_aliases = singular_aliases(candidate_table)
        if candidate_norms.intersection(table_aliases):
            matches.append(candidate_table)

    return sorted(set(matches))


def build_index_definitions(index_columns):
    index_defs = {}
    for row in index_columns:
        index_name = row["index_name"]
        index_defs.setdefault(
            index_name,
            {
                "name": index_name,
                "non_unique": row["non_unique"],
                "columns": [],
                "sub_parts": [],
            },
        )
        index_defs[index_name]["columns"].append(row["column_name"])
        index_defs[index_name]["sub_parts"].append(row.get("sub_part"))

    return list(index_defs.values())


def index_signature(index_def):
    return (
        tuple(index_def["columns"]),
        tuple(index_def["sub_parts"]),
        int(index_def["non_unique"]),
    )


def unique_index_contains_column(index_defs, column_name):
    col_key = str(column_name).lower()
    for idx in index_defs:
        if int(idx["non_unique"]) != 0:
            continue
        if any(str(col).lower() == col_key for col in idx["columns"]):
            return True
    return False


def scan_table_storage_health(issues, table, row_count, table_status):
    data_length = int(table_status.get("data_length") or 0)
    index_length = int(table_status.get("index_length") or 0)
    total_size = data_length + index_length

    if total_size >= LARGE_TABLE_BYTES:
        severity = "High" if total_size >= VERY_LARGE_TABLE_BYTES else "Medium"
        add_issue(
            issues,
            table,
            "Large table storage",
            severity,
            (
                f"Table uses {format_bytes(total_size)} total storage "
                f"({format_bytes(data_length)} data, {format_bytes(index_length)} indexes)."
            ),
            count=row_count,
        )

    if index_length >= LARGE_INDEX_BYTES:
        add_issue(
            issues,
            table,
            "Large index storage",
            "Medium",
            f"Indexes use {format_bytes(index_length)}. Review duplicate/redundant indexes and workload.",
            count=row_count,
        )

    if data_length > 0 and index_length > data_length and index_length >= MIN_INDEX_RATIO_BYTES:
        ratio = index_length / data_length
        add_issue(
            issues,
            table,
            "High index/data size ratio",
            "Medium",
            (
                f"Index size is {ratio:.2f}x data size "
                f"({format_bytes(index_length)} index vs {format_bytes(data_length)} data)."
            ),
            count=row_count,
        )

    if row_count == 0 and total_size >= EMPTY_TABLE_STORAGE_BYTES:
        add_issue(
            issues,
            table,
            "Empty table using storage",
            "Medium",
            f"Table has zero rows but still uses {format_bytes(total_size)} storage.",
            count=row_count,
        )

    if 0 < row_count <= LOW_VOLUME_ROW_THRESHOLD and total_size >= LOW_VOLUME_LARGE_STORAGE_BYTES:
        severity = "Medium" if total_size >= LARGE_TABLE_BYTES else "Info"
        add_issue(
            issues,
            table,
            "Low-volume table with high storage",
            severity,
            (
                f"Table has {row_count} rows but uses {format_bytes(total_size)}. "
                "Check large text/blob columns, fragmentation, and index size."
            ),
            count=row_count,
        )


def scan_duplicate_and_redundant_indexes(issues, table, index_defs):
    comparable_indexes = [idx for idx in index_defs if idx["name"] != "PRIMARY"]
    by_signature = {}

    for idx in comparable_indexes:
        by_signature.setdefault(index_signature(idx), []).append(idx["name"])

    for names in by_signature.values():
        if len(names) > 1:
            add_issue(
                issues,
                table,
                "Duplicate index",
                "Medium",
                f"Indexes have the same columns and uniqueness: {', '.join(names)}.",
                count=len(names),
                sample=", ".join(names),
            )

    reported = set()
    for left in comparable_indexes:
        left_cols = tuple(left["columns"])
        left_parts = tuple(left["sub_parts"])

        if not left_cols or int(left["non_unique"]) == 0:
            continue

        for right in comparable_indexes:
            if left["name"] == right["name"]:
                continue

            right_cols = tuple(right["columns"])
            right_parts = tuple(right["sub_parts"])
            if len(left_cols) >= len(right_cols):
                continue

            if right_cols[: len(left_cols)] != left_cols:
                continue
            if right_parts[: len(left_parts)] != left_parts:
                continue

            key = (left["name"], right["name"])
            if key in reported:
                continue

            reported.add(key)
            add_issue(
                issues,
                table,
                "Possible redundant index",
                "Medium",
                (
                    f"{left['name']} ({', '.join(left_cols)}) is a left-prefix of "
                    f"{right['name']} ({', '.join(right_cols)}). Verify workload and "
                    "foreign-key needs before dropping anything."
                ),
                sample=f"{left['name']} -> {right['name']}",
            )
            break


def scan_missing_unique_constraints(
    conn,
    database,
    table,
    issues,
    columns,
    index_defs,
    scan_sql_suffix,
):
    db = quote_name(database)
    tbl = quote_name(table)

    for column in columns:
        col = column["column_name"]
        if not UNIQUE_CANDIDATE_NAMES.search(col):
            continue
        if unique_index_contains_column(index_defs, col):
            continue

        colq = quote_name(col)
        rows = q(
            conn,
            f"""
            SELECT
                COUNT(*) AS non_null_count,
                COUNT(DISTINCT {colq}) AS distinct_count
            FROM (
                SELECT {colq}
                FROM {db}.{tbl}
                WHERE {colq} IS NOT NULL
                  AND TRIM(CAST({colq} AS CHAR)) <> ''
                {scan_sql_suffix}
            ) x
            """,
        )

        non_null_count = rows[0]["non_null_count"] if rows else 0
        distinct_count = rows[0]["distinct_count"] if rows else 0
        if not non_null_count:
            continue

        if distinct_count < non_null_count:
            add_issue(
                issues,
                table,
                "Duplicate business value without unique constraint",
                "High",
                (
                    f"{col} looks like a business key and has duplicate sampled values, "
                    "but no unique index/constraint protects it."
                ),
                count=non_null_count - distinct_count,
                column=col,
            )
        else:
            add_issue(
                issues,
                table,
                "Missing unique constraint",
                "Medium",
                (
                    f"{col} looks like a business key and sampled values are unique, "
                    "but no unique index/constraint is defined. Confirm whether uniqueness "
                    "is required globally or within a company/year scope."
                ),
                count=non_null_count,
                column=col,
            )


def integer_limit_for_column(column):
    dtype = str(column.get("data_type") or "").lower()
    ctype = str(column.get("column_type") or "").lower()
    return INTEGER_LIMITS.get((dtype, "unsigned" in ctype))


def integer_range(dtype, unsigned):
    max_value = INTEGER_LIMITS[(dtype, unsigned)]
    min_value = 0 if unsigned else -max_value - 1
    return min_value, max_value


def recommended_integer_type(min_value, max_value, unsigned):
    for dtype in INTEGER_TYPE_ORDER:
        type_min, type_max = integer_range(dtype, unsigned)
        if min_value >= type_min and max_value <= type_max:
            return dtype
    return "bigint"


def integer_rank(dtype):
    try:
        return INTEGER_TYPE_ORDER.index(dtype)
    except ValueError:
        return -1


def future_date_severity(column_name):
    return "Info" if EXPECTED_FUTURE_DATE_NAMES.search(str(column_name)) else "Medium"


def scan_oversized_integer_types(
    conn,
    database,
    table,
    issues,
    columns,
    row_count,
    scan_sql_suffix,
):
    if row_count > LOW_VOLUME_ROW_THRESHOLD:
        return

    db = quote_name(database)
    tbl = quote_name(table)
    integer_columns = [
        column
        for column in columns
        if str(column.get("data_type") or "").lower()
        in ["bigint", "int", "mediumint", "smallint"]
    ]

    for column in integer_columns:
        dtype = str(column.get("data_type") or "").lower()
        ctype = str(column.get("column_type") or "").lower()
        col = column["column_name"]
        colq = quote_name(col)
        unsigned = "unsigned" in ctype

        rows = q(
            conn,
            f"""
            SELECT MIN({colq}) AS min_value, MAX({colq}) AS max_value
            FROM (
                SELECT {colq}
                FROM {db}.{tbl}
                WHERE {colq} IS NOT NULL
                {scan_sql_suffix}
            ) x
            """,
        )

        min_value = rows[0]["min_value"] if rows else None
        max_value = rows[0]["max_value"] if rows else None

        if min_value is None or max_value is None:
            if dtype in ["bigint", "int"]:
                add_issue(
                    issues,
                    table,
                    "Oversized integer datatype in low-volume table",
                    "Info",
                    (
                        f"{col} uses {ctype}, but this table has {row_count} rows and "
                        "no sampled non-null values. Verify whether this size is needed."
                    ),
                    count=row_count,
                    column=col,
                )
            continue

        suggested_type = recommended_integer_type(int(min_value), int(max_value), unsigned)
        if integer_rank(dtype) <= integer_rank(suggested_type):
            continue

        add_issue(
            issues,
            table,
            "Oversized integer datatype in low-volume table",
            "Info",
            (
                f"{col} uses {ctype}, table has {row_count} rows, and sampled values "
                f"range from {min_value} to {max_value}. Values fit in "
                f"{suggested_type}{' unsigned' if unsigned else ''}; keep current type "
                "if future growth, foreign-key compatibility, or application contracts require it."
            ),
            count=row_count,
            column=col,
        )


def scan_auto_increment_limit(issues, table, columns, table_status):
    next_value = table_status.get("auto_increment")
    if not next_value:
        return

    for column in columns:
        if "auto_increment" not in str(column.get("extra") or "").lower():
            continue

        max_value = integer_limit_for_column(column)
        if not max_value:
            continue

        usage_pct = (int(next_value) / max_value) * 100
        if usage_pct < 80:
            continue

        severity = "High" if usage_pct >= 90 else "Medium"
        add_issue(
            issues,
            table,
            "Auto increment near limit",
            severity,
            (
                f"{column['column_name']} next AUTO_INCREMENT value is {next_value}, "
                f"about {usage_pct:.2f}% of {column.get('column_type')} max {max_value}."
            ),
            column=column["column_name"],
        )


def scan_collation_mismatches(issues, table, columns, table_status):
    table_collation = table_status.get("table_collation")
    text_columns = [
        column
        for column in columns
        if column.get("collation_name") or column.get("character_set_name")
    ]

    if not text_columns:
        return

    charset_groups = {}
    collation_groups = {}
    for column in text_columns:
        charset = column.get("character_set_name")
        collation = column.get("collation_name")
        if charset:
            charset_groups.setdefault(charset, []).append(column["column_name"])
        if collation:
            collation_groups.setdefault(collation, []).append(column["column_name"])

        if table_collation and collation and collation != table_collation:
            add_issue(
                issues,
                table,
                "Column collation mismatch",
                "Medium",
                (
                    f"{column['column_name']} uses {collation}, while table default "
                    f"collation is {table_collation}. This can slow joins/comparisons."
                ),
                column=column["column_name"],
            )

    if len(charset_groups) > 1:
        sample = "; ".join(
            f"{charset}: {', '.join(cols[:5])}" for charset, cols in charset_groups.items()
        )
        add_issue(
            issues,
            table,
            "Mixed column charset",
            "Medium",
            "Text columns use multiple character sets.",
            count=len(charset_groups),
            sample=sample,
        )

    if len(collation_groups) > 1:
        sample = "; ".join(
            f"{collation}: {', '.join(cols[:5])}"
            for collation, cols in collation_groups.items()
        )
        add_issue(
            issues,
            table,
            "Mixed column collation",
            "Medium",
            "Text columns use multiple collations.",
            count=len(collation_groups),
            sample=sample,
        )


def scan_table(conn, database, table, full_scan=False, sample_limit=5000, all_tables=None):
    issues = []

    db = quote_name(database)
    tbl = quote_name(table)

    columns = get_columns(conn, database, table)
    indexes = get_indexes(conn, database, table)
    index_columns = get_index_columns(conn, database, table)
    fks = get_foreign_keys(conn, database, table)
    pk_columns = get_primary_key_columns(conn, database, table)
    table_status = get_table_status(conn, database, table)
    all_tables = all_tables or get_tables(conn, database)

    row_count = safe_count(conn, database, table)
    scan_sql_suffix = "" if full_scan else f" LIMIT {int(sample_limit)}"
    index_defs = build_index_definitions(index_columns)

    scan_table_storage_health(issues, table, row_count, table_status)
    scan_duplicate_and_redundant_indexes(issues, table, index_defs)
    scan_missing_unique_constraints(
        conn,
        database,
        table,
        issues,
        columns,
        index_defs,
        scan_sql_suffix,
    )
    scan_oversized_integer_types(
        conn,
        database,
        table,
        issues,
        columns,
        row_count,
        scan_sql_suffix,
    )
    scan_auto_increment_limit(issues, table, columns, table_status)
    scan_collation_mismatches(issues, table, columns, table_status)

    pk_cols = [c["column_name"] for c in pk_columns]
    if not pk_cols:
        add_issue(
            issues,
            table,
            "Missing primary key",
            "High",
            "Table has no primary key. This can cause duplicate rows and slow joins.",
        )

    fk_cols = {str(fk["column_name"]).lower() for fk in fks}
    pk_col_names = {str(col).lower() for col in pk_cols}

    for c in columns:
        col = c["column_name"]
        col_key = str(col).lower()

        if col_key in pk_col_names or col_key in fk_cols:
            continue

        parent_tables = [parent for parent in infer_parent_tables(col, all_tables) if parent != table]
        if parent_tables:
            add_issue(
                issues,
                table,
                "Missing foreign key relation",
                "Medium",
                (
                    f"{col} looks like a relation column, but no foreign key is defined. "
                    f"Possible parent table: {', '.join(parent_tables[:3])}."
                ),
                column=col,
            )

            if len(parent_tables) == 1:
                parent_table = parent_tables[0]
                parent_pk_columns = get_primary_key_columns(conn, database, parent_table)
                if len(parent_pk_columns) == 1:
                    colq = quote_name(col)
                    parent_tblq = quote_name(parent_table)
                    parent_pk = parent_pk_columns[0]["column_name"]
                    parent_pkq = quote_name(parent_pk)

                    rows = q(
                        conn,
                        f"""
                        SELECT COUNT(*) AS cnt
                        FROM (
                            SELECT {colq} AS child_value
                            FROM {db}.{tbl}
                            WHERE {colq} IS NOT NULL
                            {scan_sql_suffix}
                        ) child
                        LEFT JOIN {db}.{parent_tblq} parent
                          ON child.child_value = parent.{parent_pkq}
                        WHERE parent.{parent_pkq} IS NULL
                        """,
                    )
                    cnt = rows[0]["cnt"]
                    if cnt:
                        add_issue(
                            issues,
                            table,
                            "Inferred relation orphan records",
                            "High",
                            (
                                f"{col} looks related to {parent_table}.{parent_pk}, "
                                "but sampled child values do not exist in parent table."
                            ),
                            count=cnt,
                            column=col,
                        )

    if row_count == 0:
        add_issue(
            issues,
            table,
            "Empty table",
            "Info",
            "Table has zero rows.",
            count=0,
        )

    for c in columns:
        col = c["column_name"]
        dtype = str(c.get("data_type") or "").lower()
        ctype = str(c.get("column_type") or "").lower()
        max_len = c.get("character_maximum_length")

        if dtype in ["text", "mediumtext", "longtext", "blob", "mediumblob", "longblob"]:
            add_issue(
                issues,
                table,
                "Big datatype",
                "Medium",
                f"Column uses {ctype}. Confirm this is required.",
                column=col,
            )

        if dtype == "varchar" and max_len and int(max_len) >= 1000:
            add_issue(
                issues,
                table,
                "Large varchar",
                "Medium",
                f"VARCHAR({max_len}) may be oversized.",
                column=col,
            )

        if RISKY_NUMERIC_NAMES.search(col) and dtype in ["varchar", "char", "text"]:
            add_issue(
                issues,
                table,
                "Possible wrong datatype",
                "High",
                f"Numeric-looking column is stored as {ctype}.",
                column=col,
            )

        if DATE_LIKE_NAMES.search(col) and dtype in ["varchar", "char", "text"]:
            add_issue(
                issues,
                table,
                "Possible wrong datatype",
                "Medium",
                f"Date-looking column is stored as {ctype}.",
                column=col,
            )

    numeric_cols = [
        c["column_name"]
        for c in columns
        if str(c.get("data_type") or "").lower()
        in ["int", "bigint", "smallint", "mediumint", "tinyint", "decimal", "double", "float"]
    ]

    date_cols = [
        c["column_name"]
        for c in columns
        if str(c.get("data_type") or "").lower() in ["date", "datetime", "timestamp"]
    ]
    date_col_types = {
        c["column_name"]: str(c.get("data_type") or "").lower()
        for c in columns
        if str(c.get("data_type") or "").lower() in ["date", "datetime", "timestamp"]
    }

    string_cols = [
        c["column_name"]
        for c in columns
        if str(c.get("data_type") or "").lower()
        in ["varchar", "char", "text", "mediumtext", "longtext"]
    ]

    # Null checks for key/index columns
    for c in columns:
        col = c["column_name"]
        colq = quote_name(col)

        if c.get("column_key") in ["PRI", "MUL", "UNI"]:
            rows = q(
                conn,
                f"""
                SELECT COUNT(*) AS cnt
                FROM (
                    SELECT {colq}
                    FROM {db}.{tbl}
                    {scan_sql_suffix}
                ) x
                WHERE {colq} IS NULL
                """,
            )
            cnt = rows[0]["cnt"]
            if cnt:
                add_issue(
                    issues,
                    table,
                    "Null in key column",
                    "High",
                    "Key/index column contains NULL values.",
                    count=cnt,
                    column=col,
                )

    # Negative stock/amount/quantity checks
    for col in numeric_cols:
        if RISKY_NUMERIC_NAMES.search(col):
            colq = quote_name(col)
            rows = q(
                conn,
                f"""
                SELECT COUNT(*) AS cnt, MIN({colq}) AS min_value
                FROM (
                    SELECT {colq}
                    FROM {db}.{tbl}
                    {scan_sql_suffix}
                ) x
                WHERE {colq} < 0
                """,
            )
            cnt = rows[0]["cnt"]
            if cnt:
                add_issue(
                    issues,
                    table,
                    "Negative value",
                    "High",
                    f"Negative values found. Minimum value: {rows[0]['min_value']}",
                    count=cnt,
                    column=col,
                )

    # Zero / invalid date checks
    for col in date_cols:
        colq = quote_name(col)
        rows = q(
            conn,
            f"""
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT {colq}
                FROM {db}.{tbl}
                {scan_sql_suffix}
            ) x
            WHERE CAST({colq} AS CHAR) IN ('0000-00-00', '0000-00-00 00:00:00')
            """,
        )
        cnt = rows[0]["cnt"]
        if cnt:
            add_issue(
                issues,
                table,
                "Invalid zero date",
                "High",
                "Zero date found.",
                count=cnt,
                column=col,
            )

        compare_to = "CURDATE()" if date_col_types.get(col) == "date" else "CURRENT_TIMESTAMP()"
        rows = q(
            conn,
            f"""
            SELECT COUNT(*) AS cnt, MIN({colq}) AS first_future, MAX({colq}) AS max_future
            FROM (
                SELECT {colq}
                FROM {db}.{tbl}
                {scan_sql_suffix}
            ) x
            WHERE {colq} IS NOT NULL
              AND {colq} > {compare_to}
            """,
        )
        cnt = rows[0]["cnt"]
        if cnt:
            add_issue(
                issues,
                table,
                "Future date value",
                future_date_severity(col),
                (
                    "Future date values found. Verify whether this is valid business data. "
                    f"First future value: {rows[0]['first_future']}; "
                    f"maximum value: {rows[0]['max_future']}."
                ),
                count=cnt,
                column=col,
            )

    # Wrong date format stored in varchar/date-like columns
    for col in string_cols:
        if DATE_LIKE_NAMES.search(col):
            colq = quote_name(col)
            rows = q(
                conn,
                f"""
                SELECT {colq} AS bad_value
                FROM {db}.{tbl}
                WHERE {colq} IS NOT NULL
                  AND TRIM(CAST({colq} AS CHAR)) <> ''
                  AND STR_TO_DATE({colq}, '%%Y-%%m-%%d') IS NULL
                  AND STR_TO_DATE({colq}, '%%d-%%m-%%Y') IS NULL
                  AND STR_TO_DATE({colq}, '%%Y-%%m-%%d %%H:%%i:%%s') IS NULL
                LIMIT 5
                """,
            )
            if rows:
                add_issue(
                    issues,
                    table,
                    "Wrong date format",
                    "Medium",
                    "Date-like text column has values not matching common date formats.",
                    count=len(rows),
                    column=col,
                    sample=", ".join(str(r["bad_value"]) for r in rows),
                )

            rows = q(
                conn,
                f"""
                SELECT parsed_value AS future_value
                FROM (
                    SELECT COALESCE(
                        STR_TO_DATE({colq}, '%%Y-%%m-%%d %%H:%%i:%%s'),
                        STR_TO_DATE({colq}, '%%Y-%%m-%%d'),
                        STR_TO_DATE({colq}, '%%d-%%m-%%Y')
                    ) AS parsed_value
                    FROM {db}.{tbl}
                    WHERE {colq} IS NOT NULL
                      AND TRIM(CAST({colq} AS CHAR)) <> ''
                    {scan_sql_suffix}
                ) x
                WHERE parsed_value IS NOT NULL
                  AND parsed_value > CURRENT_TIMESTAMP()
                LIMIT 5
                """,
            )
            if rows:
                add_issue(
                    issues,
                    table,
                    "Future date value",
                    future_date_severity(col),
                    "Date-like text column has future date values. Verify whether this is valid business data.",
                    count=len(rows),
                    column=col,
                    sample=", ".join(str(r["future_value"]) for r in rows),
                )

    # Possible duplicate business values
    candidate_cols = [
        c["column_name"]
        for c in columns
        if re.search(r"(code|number|mobile|email|name)", c["column_name"], re.I)
    ]

    for col in candidate_cols[:5]:
        colq = quote_name(col)
        rows = q(
            conn,
            f"""
            SELECT {colq} AS value, COUNT(*) AS cnt
            FROM {db}.{tbl}
            WHERE {colq} IS NOT NULL
              AND TRIM(CAST({colq} AS CHAR)) <> ''
            GROUP BY {colq}
            HAVING COUNT(*) > 1
            LIMIT 5
            """,
        )
        if rows:
            add_issue(
                issues,
                table,
                "Possible duplicate business value",
                "Medium",
                "Same business value appears multiple times.",
                count=len(rows),
                column=col,
                sample=str(rows[:3]),
            )

    # Foreign key orphan checks
    for fk in fks:
        child_col = fk["column_name"]
        parent_table = fk["referenced_table_name"]
        parent_col = fk["referenced_column_name"]

        child_colq = quote_name(child_col)
        parent_tblq = quote_name(parent_table)
        parent_colq = quote_name(parent_col)

        rows = q(
            conn,
            f"""
            SELECT COUNT(*) AS cnt
            FROM {db}.{tbl} child
            LEFT JOIN {db}.{parent_tblq} parent
              ON child.{child_colq} = parent.{parent_colq}
            WHERE child.{child_colq} IS NOT NULL
              AND parent.{parent_colq} IS NULL
            """,
        )
        cnt = rows[0]["cnt"]
        if cnt:
            add_issue(
                issues,
                table,
                "Wrong relation / orphan records",
                "High",
                f"{child_col} references missing {parent_table}.{parent_col}",
                count=cnt,
                column=child_col,
            )

    if len(indexes) > 8:
        add_issue(
            issues,
            table,
            "Many indexes",
            "Medium",
            f"Table has {len(indexes)} indexes. Check write overhead and duplicate indexes.",
        )

    return issues


def scan_unused_indexes(conn, database):
    try:
        return q(
            conn,
            """
            SELECT OBJECT_NAME AS table_name, INDEX_NAME AS index_name
            FROM performance_schema.table_io_waits_summary_by_index_usage
            WHERE OBJECT_SCHEMA = %s
              AND INDEX_NAME IS NOT NULL
              AND COUNT_STAR = 0
            ORDER BY OBJECT_NAME, INDEX_NAME
            """,
            (database,),
        )
    except Exception:
        return []


def records(data):
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        return data.to_dict(orient="records")
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        return [item if isinstance(item, Mapping) else {"value": item} for item in data]
    return [{"value": data}]


def redact_text(value):
    if value is None:
        return ""

    text = str(value)
    text = EMAIL_VALUE.sub("<email>", text)
    text = LONG_NUMBER_VALUE.sub("<number>", text)
    text = WHITESPACE.sub(" ", text).strip()
    return text[:1000]


def redact_row(row):
    redacted = {}
    for key, value in row.items():
        key_text = str(key)
        if SENSITIVE_FIELD_NAMES.search(key_text):
            redacted[key_text] = "<redacted>"
        else:
            redacted[key_text] = redact_text(value)
    return redacted


def prepare_ai_payload(
    database,
    tables_scanned,
    full_scan,
    sample_limit,
    issues,
    unused_indexes,
    max_issues,
    max_unused_indexes,
):
    issue_rows = [redact_row(row) for row in records(issues)]
    unused_index_rows = [redact_row(row) for row in records(unused_indexes)]

    return {
        "database": redact_text(database),
        "tables_scanned": int(tables_scanned),
        "full_scan": bool(full_scan),
        "sample_limit": int(sample_limit),
        "issue_count": len(issue_rows),
        "issues_sent_to_ai": min(len(issue_rows), int(max_issues)),
        "issues": issue_rows[: int(max_issues)],
        "unused_index_count": len(unused_index_rows),
        "unused_indexes_sent_to_ai": min(
            len(unused_index_rows),
            int(max_unused_indexes),
        ),
        "unused_indexes": unused_index_rows[: int(max_unused_indexes)],
    }


def generate_ai_advice(
    database,
    tables_scanned,
    full_scan,
    sample_limit,
    issues,
    unused_indexes,
    api_key,
    model,
    max_issues,
    max_unused_indexes,
    max_output_tokens,
):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the OpenAI SDK first: pip install openai") from exc

    payload = prepare_ai_payload(
        database=database,
        tables_scanned=tables_scanned,
        full_scan=full_scan,
        sample_limit=sample_limit,
        issues=issues,
        unused_indexes=unused_indexes,
        max_issues=max_issues,
        max_unused_indexes=max_unused_indexes,
    )

    input_text = (
        "Analyze this database anomaly scan JSON and give DBA advice.\n\n"
        + json.dumps(payload, indent=2, sort_keys=True, default=str)
    )

    client = OpenAI(api_key=api_key or None)
    response = client.responses.create(
        model=model,
        instructions=AI_ADVISOR_INSTRUCTIONS,
        input=input_text,
        max_output_tokens=int(max_output_tokens),
        store=False,
    )

    return getattr(response, "output_text", "") or str(response)


def build_markdown_report(database, selected_tables, full_scan, result_df, storage_summary):
    report = "# Database Anomaly Scan Report\n\n"
    report += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    report += f"Database: {database}\n\n"
    report += f"Tables scanned: {len(selected_tables)}\n\n"
    report += f"Full scan: {full_scan}\n\n"

    if result_df.empty:
        report += "No anomalies found from selected checks.\n"
    else:
        report += result_df.to_markdown(index=False)

    if storage_summary:
        storage_df = pd.DataFrame(storage_summary)
        report += "\n\n## Table Storage Summary\n\n"
        report += storage_df.to_markdown(index=False)

    return report


def app_secret(name, default=""):
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def configured_openai_api_key(sidebar_value):
    return sidebar_value.strip() or app_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")


def configured_ai_model(sidebar_value):
    return (
        sidebar_value.strip()
        or app_secret("ANOMALY_AI_MODEL")
        or os.getenv("ANOMALY_AI_MODEL", "")
        or "gpt-5"
    )


st.sidebar.header("Database Connection")

host = st.sidebar.text_input("Host")
port = st.sidebar.text_input("Port", "3306")
user = st.sidebar.text_input("User")
password = st.sidebar.text_input("Password", type="password")
database = st.sidebar.text_input("Database")

st.sidebar.header("AI Advisor")
ai_api_key = st.sidebar.text_input(
    "OpenAI API Key",
    type="password",
    help="Optional. Leave blank to use Streamlit secrets or OPENAI_API_KEY.",
)
ai_model = st.sidebar.text_input(
    "AI Model",
    configured_ai_model(""),
)
ai_max_issues = st.sidebar.number_input(
    "Max issues sent to AI",
    min_value=10,
    max_value=500,
    value=80,
    step=10,
)
ai_max_unused_indexes = st.sidebar.number_input(
    "Max unused indexes sent to AI",
    min_value=10,
    max_value=500,
    value=50,
    step=10,
)
ai_max_output_tokens = st.sidebar.number_input(
    "AI response token limit",
    min_value=500,
    max_value=8000,
    value=1600,
    step=100,
)

scan_mode = st.radio(
    "Scan Mode",
    ["Whole Database", "Single Table", "Selected Tables"],
    horizontal=True,
)

full_scan = st.checkbox(
    "Full scan all rows",
    value=False,
    help="For production DB, keep this off first. Sample scan is safer.",
)

sample_limit = st.number_input(
    "Sample rows per table when full scan is off",
    min_value=100,
    max_value=100000,
    value=5000,
    step=1000,
)

if "tables" not in st.session_state:
    st.session_state.tables = []

if "result_df" not in st.session_state:
    st.session_state.result_df = None

if "unused_indexes" not in st.session_state:
    st.session_state.unused_indexes = []

if "storage_summary" not in st.session_state:
    st.session_state.storage_summary = []

if "scan_meta" not in st.session_state:
    st.session_state.scan_meta = {}

if "ai_advice" not in st.session_state:
    st.session_state.ai_advice = ""

if st.button("Connect and Load Tables"):
    try:
        conn = get_conn(host, port, user, password, database)
        st.session_state.tables = get_tables(conn, database)
        conn.close()
        st.success(f"Loaded {len(st.session_state.tables)} tables.")
    except Exception as e:
        st.error(e)

tables = st.session_state.tables
selected_tables = []

if tables:
    if scan_mode == "Whole Database":
        selected_tables = tables
        st.info(f"Whole database scan selected: {len(selected_tables)} tables")

    elif scan_mode == "Single Table":
        table = st.selectbox("Select table", tables)
        selected_tables = [table]

    else:
        selected_tables = st.multiselect("Select tables", tables)
        st.info(f"Selected {len(selected_tables)} tables")

if st.button("Run Anomaly Scan"):
    if not selected_tables:
        st.error("Please load/select tables first.")
    else:
        all_issues = []
        progress = st.progress(0)

        try:
            conn = get_conn(host, port, user, password, database)
            all_database_tables = st.session_state.tables or get_tables(conn, database)

            for i, table in enumerate(selected_tables, start=1):
                st.write(f"Scanning `{table}` ...")
                try:
                    all_issues.extend(
                        scan_table(
                            conn,
                            database,
                            table,
                            full_scan=full_scan,
                            sample_limit=sample_limit,
                            all_tables=all_database_tables,
                        )
                    )
                except Exception as e:
                    add_issue(
                        all_issues,
                        table,
                        "Scan error",
                        "High",
                        str(e),
                    )

                progress.progress(i / len(selected_tables))

            unused_indexes = scan_unused_indexes(conn, database)
            storage_summary = get_table_storage_summary(conn, database, selected_tables)
            conn.close()

            result_df = pd.DataFrame(all_issues)
            st.session_state.result_df = result_df
            st.session_state.unused_indexes = unused_indexes
            st.session_state.storage_summary = storage_summary
            st.session_state.scan_meta = {
                "database": database,
                "selected_tables": selected_tables,
                "tables_scanned": len(selected_tables),
                "full_scan": full_scan,
                "sample_limit": sample_limit,
            }
            st.session_state.ai_advice = ""
            st.success("Scan completed. Review results below, then ask the AI advisor.")

        except Exception as e:
            st.error(e)

if st.session_state.result_df is not None:
    result_df = st.session_state.result_df
    unused_indexes = st.session_state.unused_indexes
    storage_summary = st.session_state.storage_summary
    scan_meta = st.session_state.scan_meta

    st.subheader("Anomaly Results")

    if result_df.empty:
        st.success("No anomalies found from selected checks.")
    else:
        st.dataframe(result_df, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Issues", len(result_df))
        c2.metric("High Severity", len(result_df[result_df["severity"] == "High"]))
        c3.metric("Medium Severity", len(result_df[result_df["severity"] == "Medium"]))

    report = build_markdown_report(
        scan_meta.get("database", ""),
        scan_meta.get("selected_tables", []),
        scan_meta.get("full_scan", False),
        result_df,
        storage_summary,
    )

    st.download_button(
        "Download Report",
        report,
        file_name="database_anomaly_report.md",
        mime="text/markdown",
    )

    st.subheader("Table Storage Summary")
    if storage_summary:
        storage_df = pd.DataFrame(storage_summary)
        st.dataframe(storage_df, use_container_width=True)
        st.caption(
            "Storage values come from information_schema.TABLES and can be approximate for InnoDB."
        )
    else:
        st.info("No table storage metadata found.")

    st.subheader("Possible Unused Indexes")

    if unused_indexes:
        unused_df = pd.DataFrame(unused_indexes)
        st.dataframe(unused_df, use_container_width=True)
        st.warning("Do not drop indexes directly. Verify usage across full business cycle first.")
    else:
        st.info("No unused index data found or permission not available.")

    st.subheader("AI DBA Advisor")
    st.caption(
        "AI receives only the anomaly report and unused-index metadata, not DB credentials."
    )

    final_ai_api_key = configured_openai_api_key(ai_api_key)
    final_ai_model = configured_ai_model(ai_model)

    if not final_ai_api_key:
        st.info("Add an OpenAI API key in the sidebar, Streamlit secrets, or OPENAI_API_KEY.")

    if st.button("Summarize Anomalies with AI"):
        if not final_ai_api_key:
            st.error("OpenAI API key is required before using AI.")
        elif not final_ai_model:
            st.error("Please enter an AI model in the sidebar.")
        else:
            try:
                with st.spinner("AI is reviewing anomaly results..."):
                    st.session_state.ai_advice = generate_ai_advice(
                        database=scan_meta.get("database", ""),
                        tables_scanned=scan_meta.get("tables_scanned", 0),
                        full_scan=scan_meta.get("full_scan", False),
                        sample_limit=scan_meta.get("sample_limit", sample_limit),
                        issues=result_df,
                        unused_indexes=unused_indexes,
                        api_key=final_ai_api_key,
                        model=final_ai_model,
                        max_issues=ai_max_issues,
                        max_unused_indexes=ai_max_unused_indexes,
                        max_output_tokens=ai_max_output_tokens,
                    )
            except Exception as e:
                st.error(e)

    if st.session_state.ai_advice:
        st.markdown(st.session_state.ai_advice)
        st.download_button(
            "Download AI Advice",
            st.session_state.ai_advice,
            file_name="database_anomaly_ai_advice.md",
            mime="text/markdown",
        )

st.warning(
    "Use read-only DB credentials only. Start with sample scan before full database scan on production."
)
