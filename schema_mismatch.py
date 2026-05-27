#!/usr/bin/env python3
"""Professional Streamlit UI for comparing MySQL schema drift."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd
import pymysql
import streamlit as st


PAGE_TITLE = "Schema Drift Analyzer"
SEVERITIES = ["High", "Medium", "Low"]
SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}
TEXT_TYPES = {
    "char",
    "varchar",
    "tinytext",
    "text",
    "mediumtext",
    "longtext",
    "enum",
    "set",
}
NUMERIC_TYPES = {
    "bit",
    "bool",
    "boolean",
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "decimal",
    "numeric",
    "float",
    "double",
    "real",
}
EXPRESSION_DEFAULTS = {
    "current_timestamp",
    "current_timestamp()",
    "localtime",
    "localtime()",
    "localtimestamp",
    "localtimestamp()",
    "now()",
    "uuid()",
}


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title=PAGE_TITLE,
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# CUSTOM CSS
# =========================================================

st.markdown(
    """
<style>
    :root {
        --app-bg: #f5f7fb;
        --surface: #ffffff;
        --surface-muted: #f8fafc;
        --text: #172033;
        --muted: #667085;
        --line: #d9e2ec;
        --brand: #0f766e;
        --brand-dark: #115e59;
        --navy: #111827;
        --red: #b42318;
        --amber: #b54708;
        --green: #047857;
    }

    .stApp {
        background: var(--app-bg);
        color: var(--text);
    }

    .block-container {
        padding: 1.75rem 2rem 2.5rem;
        max-width: 1440px;
    }

    [data-testid="stSidebar"] {
        background: #111827;
        border-right: 1px solid rgba(255,255,255,0.08);
    }

    [data-testid="stSidebar"] * {
        color: #f9fafb;
    }

    [data-testid="stSidebar"] input {
        color: #111827;
        background: #ffffff;
        border-radius: 8px;
    }

    [data-testid="stSidebar"] label p {
        color: #d1d5db;
        font-weight: 600;
    }

    h1, h2, h3 {
        color: var(--text);
        letter-spacing: 0;
    }

    .hero {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 8px;
        padding: 22px 24px;
        margin-bottom: 18px;
        color: white;
        display: flex;
        justify-content: space-between;
        gap: 18px;
        align-items: center;
    }

    .hero h1 {
        color: white !important;
        margin: 0;
        font-size: 2rem;
        line-height: 1.1;
    }

    .hero-subtitle {
        margin-top: 8px;
        color: #cbd5e1;
        font-size: 0.98rem;
    }

    .hero-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        justify-content: flex-end;
    }

    .pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 7px 10px;
        border-radius: 999px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.16);
        color: #e5e7eb;
        font-size: 0.82rem;
        font-weight: 700;
        white-space: nowrap;
    }

    .sidebar-title {
        padding: 14px 12px;
        border-radius: 8px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        margin-bottom: 14px;
    }

    .sidebar-title h2 {
        color: white !important;
        margin: 0;
        font-size: 1.05rem;
    }

    .sidebar-title p {
        margin: 4px 0 0;
        color: #cbd5e1;
        font-size: 0.82rem;
    }

    .empty-state {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 28px;
        margin-top: 18px;
    }

    .empty-state h3 {
        margin-top: 0;
        font-size: 1.25rem;
    }

    .empty-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-top: 16px;
    }

    .mini-card {
        background: var(--surface-muted);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
    }

    .mini-card strong {
        display: block;
        margin-bottom: 4px;
    }

    .mini-card span {
        color: var(--muted);
        font-size: 0.9rem;
    }

    .section-label {
        color: var(--muted);
        font-size: 0.78rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin: 16px 0 8px;
    }

    div[data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 15px 16px;
        min-height: 106px;
    }

    div[data-testid="stMetric"] label p {
        color: var(--muted);
        font-weight: 700;
    }

    div[data-testid="stMetricValue"] {
        color: var(--text);
        font-weight: 800;
    }

    .stButton > button,
    .stFormSubmitButton > button {
        background: var(--brand);
        color: white;
        border: 1px solid var(--brand);
        border-radius: 8px;
        min-height: 44px;
        font-weight: 800;
    }

    .stButton > button:hover,
    .stFormSubmitButton > button:hover {
        background: var(--brand-dark);
        border-color: var(--brand-dark);
        color: white;
    }

    .stDownloadButton > button {
        background: #111827;
        color: white;
        border: 1px solid #111827;
        border-radius: 8px;
        font-weight: 800;
    }

    .stDownloadButton > button:hover {
        background: #1f2937;
        border-color: #1f2937;
        color: white;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 8px;
        overflow: hidden;
        background: white;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 10px 14px;
        background: #eef2f7;
        color: #334155;
        font-weight: 700;
    }

    .stTabs [aria-selected="true"] {
        background: white;
        color: var(--brand);
    }

    .footer-note {
        color: var(--muted);
        font-size: 0.88rem;
        margin-top: 20px;
    }

    @media (max-width: 900px) {
        .block-container {
            padding: 1rem;
        }

        .hero {
            align-items: flex-start;
            flex-direction: column;
        }

        .hero-actions {
            justify-content: flex-start;
        }

        .empty-grid {
            grid-template-columns: 1fr;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)


# =========================================================
# DB CONNECTION
# =========================================================


def conn(host: str, port: str | int, user: str, password: str, db: str):
    return pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=db,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=120,
        write_timeout=30,
    )


def q(c: Any, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with c.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
    return [{str(k).lower(): v for k, v in row.items()} for row in rows]


def quote_name(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def safe_mysql_token(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_$]+", text):
        return text
    return quote_name(text)


# =========================================================
# TABLE FUNCTIONS
# =========================================================


def get_create_table(c: Any, table: str) -> str | None:
    rows = q(c, f"SHOW CREATE TABLE {quote_name(table)}")
    if not rows:
        return None
    row = rows[0]
    for key, value in row.items():
        if "create table" in key.lower():
            return str(value)
    return None


def get_tables(c: Any, db: str) -> dict[str, dict[str, Any]]:
    rows = q(
        c,
        """
        SELECT
            T.TABLE_NAME AS table_name,
            T.ENGINE AS engine,
            T.TABLE_COLLATION AS table_collation,
            CCSA.CHARACTER_SET_NAME AS table_charset
        FROM information_schema.TABLES T
        LEFT JOIN information_schema.COLLATION_CHARACTER_SET_APPLICABILITY CCSA
          ON CCSA.COLLATION_NAME = T.TABLE_COLLATION
        WHERE T.TABLE_SCHEMA = %s
          AND T.TABLE_TYPE = 'BASE TABLE'
        ORDER BY T.TABLE_NAME
        """,
        (db,),
    )
    return {r["table_name"]: r for r in rows}


def get_columns(c: Any, db: str) -> dict[str, dict[str, dict[str, Any]]]:
    rows = q(
        c,
        """
        SELECT
            TABLE_NAME AS table_name,
            COLUMN_NAME AS column_name,
            ORDINAL_POSITION AS ordinal_position,
            COLUMN_TYPE AS column_type,
            DATA_TYPE AS data_type,
            IS_NULLABLE AS is_nullable,
            COLUMN_DEFAULT AS column_default,
            EXTRA AS extra,
            CHARACTER_SET_NAME AS character_set_name,
            COLLATION_NAME AS collation_name,
            COLUMN_KEY AS column_key,
            COLUMN_COMMENT AS column_comment
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        (db,),
    )
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["table_name"], {})[row["column_name"]] = row
    return result


def get_indexes(c: Any, db: str) -> dict[str, dict[str, dict[str, Any]]]:
    rows = q(
        c,
        """
        SELECT
            TABLE_NAME AS table_name,
            INDEX_NAME AS index_name,
            NON_UNIQUE AS non_unique,
            INDEX_TYPE AS index_type,
            GROUP_CONCAT(
                CONCAT(
                    COLUMN_NAME,
                    IF(SUB_PART IS NOT NULL, CONCAT('(', SUB_PART, ')'), '')
                )
                ORDER BY SEQ_IN_INDEX
            ) AS columns_in_index
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s
        GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE, INDEX_TYPE
        ORDER BY TABLE_NAME, INDEX_NAME
        """,
        (db,),
    )
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["table_name"], {})[row["index_name"]] = row
    return result


def get_foreign_keys(c: Any, db: str) -> dict[str, dict[str, dict[str, Any]]]:
    rows = q(
        c,
        """
        SELECT
            KCU.TABLE_NAME AS table_name,
            KCU.CONSTRAINT_NAME AS constraint_name,
            KCU.COLUMN_NAME AS column_name,
            KCU.REFERENCED_TABLE_NAME AS referenced_table_name,
            KCU.REFERENCED_COLUMN_NAME AS referenced_column_name,
            KCU.ORDINAL_POSITION AS ordinal_position,
            RC.UPDATE_RULE AS update_rule,
            RC.DELETE_RULE AS delete_rule
        FROM information_schema.KEY_COLUMN_USAGE KCU
        LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS RC
          ON RC.CONSTRAINT_SCHEMA = KCU.CONSTRAINT_SCHEMA
         AND RC.CONSTRAINT_NAME = KCU.CONSTRAINT_NAME
         AND RC.TABLE_NAME = KCU.TABLE_NAME
        WHERE KCU.TABLE_SCHEMA = %s
          AND KCU.REFERENCED_TABLE_NAME IS NOT NULL
        ORDER BY KCU.TABLE_NAME, KCU.CONSTRAINT_NAME, KCU.ORDINAL_POSITION
        """,
        (db,),
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["table_name"], row["constraint_name"])
        grouped.setdefault(
            key,
            {
                "table_name": row["table_name"],
                "constraint_name": row["constraint_name"],
                "columns": [],
                "referenced_table_name": row["referenced_table_name"],
                "referenced_columns": [],
                "update_rule": row.get("update_rule"),
                "delete_rule": row.get("delete_rule"),
            },
        )
        grouped[key]["columns"].append(row["column_name"])
        grouped[key]["referenced_columns"].append(row["referenced_column_name"])

    by_table: dict[str, dict[str, dict[str, Any]]] = {}
    for fk in grouped.values():
        by_table.setdefault(fk["table_name"], {})[fk["constraint_name"]] = {
            **fk,
            "columns": ",".join(fk["columns"]),
            "referenced_columns": ",".join(fk["referenced_columns"]),
        }
    return by_table


# =========================================================
# DIFF FUNCTIONS
# =========================================================


def add_diff(
    diffs: list[dict[str, Any]],
    object_type: str,
    object_name: str,
    diff_type: str,
    source_value: Any,
    target_value: Any,
    severity: str,
    fix_sql: str | None = None,
) -> None:
    diffs.append(
        {
            "object_type": object_type,
            "object_name": object_name,
            "diff_type": diff_type,
            "source_value": source_value,
            "target_value": target_value,
            "severity": severity,
            "fix_sql": fix_sql or "",
        }
    )


def normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    return text.lower()


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def index_signature(index: dict[str, Any]) -> tuple[str, str, str]:
    return (
        normalize_text(index.get("non_unique")),
        normalize_text(index.get("index_type")).upper(),
        normalize_text(index.get("columns_in_index")).lower(),
    )


def fk_signature(fk: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        normalize_text(fk.get("columns")).lower(),
        normalize_text(fk.get("referenced_table_name")).lower(),
        normalize_text(fk.get("referenced_columns")).lower(),
        normalize_text(fk.get("delete_rule")).upper(),
        normalize_text(fk.get("update_rule")).upper(),
    )


# =========================================================
# SQL BUILDERS
# =========================================================


def render_default_sql(col: dict[str, Any]) -> str:
    default = col.get("column_default")
    if default is None:
        return ""

    text = str(default).strip()
    lower = text.lower()
    data_type = str(col.get("data_type") or "").lower()

    if lower == "null":
        return " DEFAULT NULL"
    if lower in EXPRESSION_DEFAULTS or lower.startswith("current_timestamp("):
        return f" DEFAULT {text}"
    if data_type in NUMERIC_TYPES and re.fullmatch(r"-?\d+(\.\d+)?", text):
        return f" DEFAULT {text}"
    if lower in {"true", "false"} and data_type in {"bool", "boolean"}:
        return f" DEFAULT {lower.upper()}"

    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f" DEFAULT '{escaped}'"


def build_column_definition(col: dict[str, Any]) -> str:
    sql = f"{quote_name(col['column_name'])} {col['column_type']}"
    data_type = str(col.get("data_type") or "").lower()

    if data_type in TEXT_TYPES:
        if col.get("character_set_name"):
            sql += f" CHARACTER SET {safe_mysql_token(col['character_set_name'])}"
        if col.get("collation_name"):
            sql += f" COLLATE {safe_mysql_token(col['collation_name'])}"

    if col.get("is_nullable") == "NO":
        sql += " NOT NULL"
    else:
        sql += " NULL"

    sql += render_default_sql(col)

    if col.get("extra"):
        sql += f" {col['extra']}"

    if col.get("column_comment"):
        comment = str(col["column_comment"]).replace("\\", "\\\\").replace("'", "\\'")
        sql += f" COMMENT '{comment}'"

    return sql


def build_add_column_sql(table: str, col: dict[str, Any]) -> str:
    return f"ALTER TABLE {quote_name(table)}\nADD COLUMN {build_column_definition(col)};".strip()


def build_modify_column_sql(table: str, col: dict[str, Any]) -> str:
    return f"ALTER TABLE {quote_name(table)}\nMODIFY COLUMN {build_column_definition(col)};".strip()


def render_index_columns(columns_in_index: Any) -> str:
    columns = []
    for raw_column in str(columns_in_index or "").split(","):
        raw_column = raw_column.strip()
        match = re.fullmatch(r"(.+?)\((\d+)\)", raw_column)
        if match:
            columns.append(f"{quote_name(match.group(1))}({match.group(2)})")
        elif raw_column:
            columns.append(quote_name(raw_column))
    return ", ".join(columns)


def build_add_index_sql(table: str, index: dict[str, Any]) -> str:
    name = str(index["index_name"])
    columns = render_index_columns(index.get("columns_in_index"))
    index_type = str(index.get("index_type") or "").upper()

    if name == "PRIMARY":
        clause = f"ADD PRIMARY KEY ({columns})"
    elif index_type == "FULLTEXT":
        clause = f"ADD FULLTEXT INDEX {quote_name(name)} ({columns})"
    elif index_type == "SPATIAL":
        clause = f"ADD SPATIAL INDEX {quote_name(name)} ({columns})"
    elif str(index.get("non_unique")) == "0":
        clause = f"ADD UNIQUE INDEX {quote_name(name)} ({columns})"
    else:
        clause = f"ADD INDEX {quote_name(name)} ({columns})"

    return f"ALTER TABLE {quote_name(table)}\n{clause};"


def build_add_foreign_key_sql(table: str, fk: dict[str, Any]) -> str:
    columns = ", ".join(quote_name(col) for col in str(fk.get("columns") or "").split(",") if col)
    ref_columns = ", ".join(quote_name(col) for col in str(fk.get("referenced_columns") or "").split(",") if col)
    sql = (
        f"ALTER TABLE {quote_name(table)}\n"
        f"ADD CONSTRAINT {quote_name(fk['constraint_name'])}\n"
        f"FOREIGN KEY ({columns})\n"
        f"REFERENCES {quote_name(fk['referenced_table_name'])} ({ref_columns})"
    )
    delete_rule = normalize_text(fk.get("delete_rule")).upper()
    update_rule = normalize_text(fk.get("update_rule")).upper()
    if delete_rule and delete_rule not in {"RESTRICT", "NO ACTION"}:
        sql += f"\nON DELETE {delete_rule}"
    if update_rule and update_rule not in {"RESTRICT", "NO ACTION"}:
        sql += f"\nON UPDATE {update_rule}"
    return sql + ";"


# =========================================================
# COMPARE
# =========================================================


def compare(source: dict[str, Any], target: dict[str, Any], source_conn: Any) -> tuple[list[dict[str, Any]], list[str]]:
    diffs: list[dict[str, Any]] = []
    generated_sql: list[str] = []
    generated_seen: set[str] = set()

    def remember_sql(sql: str | None) -> None:
        if sql and sql not in generated_seen:
            generated_sql.append(sql)
            generated_seen.add(sql)

    s_tables = source["tables"]
    t_tables = target["tables"]
    s_table_names = set(s_tables)
    t_table_names = set(t_tables)

    for table in sorted(s_table_names - t_table_names):
        create_sql = get_create_table(source_conn, table)
        remember_sql(create_sql)
        add_diff(
            diffs,
            "table",
            table,
            "Missing table in target",
            "exists",
            "missing",
            "High",
            create_sql,
        )

    for table in sorted(t_table_names - s_table_names):
        add_diff(
            diffs,
            "table",
            table,
            "Extra table in target",
            "missing",
            "exists",
            "Medium",
        )

    for table in sorted(s_table_names & t_table_names):
        s_table = s_tables[table]
        t_table = t_tables[table]

        for attr, label in (
            ("engine", "Storage engine mismatch"),
            ("table_collation", "Table collation mismatch"),
        ):
            if normalize_text(s_table.get(attr)).lower() != normalize_text(t_table.get(attr)).lower():
                add_diff(
                    diffs,
                    "table",
                    table,
                    label,
                    s_table.get(attr),
                    t_table.get(attr),
                    "Low" if attr == "table_collation" else "Medium",
                )

        s_cols = source["columns"].get(table, {})
        t_cols = target["columns"].get(table, {})

        for col in sorted(set(s_cols) - set(t_cols)):
            fix_sql = build_add_column_sql(table, s_cols[col])
            remember_sql(fix_sql)
            add_diff(
                diffs,
                "column",
                f"{table}.{col}",
                "Missing column in target",
                "exists",
                "missing",
                "High",
                fix_sql,
            )

        for col in sorted(set(t_cols) - set(s_cols)):
            add_diff(
                diffs,
                "column",
                f"{table}.{col}",
                "Extra column in target",
                "missing",
                "exists",
                "Medium",
            )

        for col in sorted(set(s_cols) & set(t_cols)):
            sc = s_cols[col]
            tc = t_cols[col]
            fix_sql = build_modify_column_sql(table, sc)

            checks = (
                ("column_type", "Column type mismatch", "Medium"),
                ("is_nullable", "Nullability mismatch", "Medium"),
                ("extra", "Column extra mismatch", "Medium"),
                ("character_set_name", "Character set mismatch", "Low"),
                ("collation_name", "Column collation mismatch", "Low"),
                ("column_comment", "Column comment mismatch", "Low"),
            )
            needs_modify = False
            for attr, label, severity in checks:
                if normalize_text(sc.get(attr)).lower() != normalize_text(tc.get(attr)).lower():
                    needs_modify = True
                    add_diff(
                        diffs,
                        "column",
                        f"{table}.{col}",
                        label,
                        sc.get(attr),
                        tc.get(attr),
                        severity,
                        fix_sql,
                    )

            if normalize_default(sc.get("column_default")) != normalize_default(tc.get("column_default")):
                needs_modify = True
                add_diff(
                    diffs,
                    "column",
                    f"{table}.{col}",
                    "Default value mismatch",
                    sc.get("column_default"),
                    tc.get("column_default"),
                    "Medium",
                    fix_sql,
                )

            if needs_modify:
                remember_sql(fix_sql)

        s_indexes = source["indexes"].get(table, {})
        t_indexes = target["indexes"].get(table, {})

        for index_name in sorted(set(s_indexes) - set(t_indexes)):
            fix_sql = build_add_index_sql(table, s_indexes[index_name])
            remember_sql(fix_sql)
            add_diff(
                diffs,
                "index",
                f"{table}.{index_name}",
                "Missing index in target",
                s_indexes[index_name].get("columns_in_index"),
                "missing",
                "High" if index_name == "PRIMARY" else "Medium",
                fix_sql,
            )

        for index_name in sorted(set(t_indexes) - set(s_indexes)):
            add_diff(
                diffs,
                "index",
                f"{table}.{index_name}",
                "Extra index in target",
                "missing",
                t_indexes[index_name].get("columns_in_index"),
                "Low",
            )

        for index_name in sorted(set(s_indexes) & set(t_indexes)):
            if index_signature(s_indexes[index_name]) != index_signature(t_indexes[index_name]):
                fix_sql = build_add_index_sql(table, s_indexes[index_name])
                add_diff(
                    diffs,
                    "index",
                    f"{table}.{index_name}",
                    "Index definition mismatch",
                    s_indexes[index_name].get("columns_in_index"),
                    t_indexes[index_name].get("columns_in_index"),
                    "Medium",
                    f"-- Review existing target index before applying\n{fix_sql}",
                )

        s_fks = source["foreign_keys"].get(table, {})
        t_fks = target["foreign_keys"].get(table, {})

        for fk_name in sorted(set(s_fks) - set(t_fks)):
            fix_sql = build_add_foreign_key_sql(table, s_fks[fk_name])
            remember_sql(fix_sql)
            add_diff(
                diffs,
                "foreign_key",
                f"{table}.{fk_name}",
                "Missing foreign key in target",
                f"{s_fks[fk_name].get('columns')} -> {s_fks[fk_name].get('referenced_table_name')}.{s_fks[fk_name].get('referenced_columns')}",
                "missing",
                "High",
                fix_sql,
            )

        for fk_name in sorted(set(t_fks) - set(s_fks)):
            add_diff(
                diffs,
                "foreign_key",
                f"{table}.{fk_name}",
                "Extra foreign key in target",
                "missing",
                f"{t_fks[fk_name].get('columns')} -> {t_fks[fk_name].get('referenced_table_name')}.{t_fks[fk_name].get('referenced_columns')}",
                "Low",
            )

        for fk_name in sorted(set(s_fks) & set(t_fks)):
            if fk_signature(s_fks[fk_name]) != fk_signature(t_fks[fk_name]):
                fix_sql = build_add_foreign_key_sql(table, s_fks[fk_name])
                add_diff(
                    diffs,
                    "foreign_key",
                    f"{table}.{fk_name}",
                    "Foreign key definition mismatch",
                    fk_signature(s_fks[fk_name]),
                    fk_signature(t_fks[fk_name]),
                    "High",
                    f"-- Review existing target foreign key before applying\n{fix_sql}",
                )

    diffs.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["severity"], 99),
            item["object_type"],
            item["object_name"],
            item["diff_type"],
        )
    )
    return diffs, generated_sql


# =========================================================
# NAMING ISSUES
# =========================================================


def naming_issues(schema: dict[str, Any], label: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")

    for table, cols in schema["columns"].items():
        if not pattern.match(table):
            add_diff(
                issues,
                "naming",
                f"{label}.{table}",
                "Table naming issue",
                table,
                "snake_case expected",
                "Low",
            )

        for col in cols:
            if not pattern.match(col):
                add_diff(
                    issues,
                    "naming",
                    f"{label}.{table}.{col}",
                    "Column naming issue",
                    col,
                    "snake_case expected",
                    "Low",
                )

    return issues


# =========================================================
# LOAD SCHEMA
# =========================================================


def load_schema(host: str, port: str, user: str, password: str, db: str) -> dict[str, Any]:
    c = conn(host, port, user, password, db)
    try:
        return {
            "tables": get_tables(c, db),
            "columns": get_columns(c, db),
            "indexes": get_indexes(c, db),
            "foreign_keys": get_foreign_keys(c, db),
        }
    finally:
        c.close()


# =========================================================
# UI HELPERS
# =========================================================


def render_header() -> None:
    st.markdown(
        """
<div class="hero">
    <div>
        <h1>Schema Drift Analyzer</h1>
        <div class="hero-subtitle">MySQL source-to-target structure review</div>
    </div>
    <div class="hero-actions">
        <span class="pill">Read-only</span>
        <span class="pill">Tables</span>
        <span class="pill">Columns</span>
        <span class="pill">Indexes</span>
        <span class="pill">Foreign keys</span>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_empty_state() -> None:
    st.markdown(
        """
<div class="empty-state">
    <h3>Ready for comparison</h3>
    <div class="empty-grid">
        <div class="mini-card">
            <strong>Source schema</strong>
            <span>Baseline database structure</span>
        </div>
        <div class="mini-card">
            <strong>Target schema</strong>
            <span>Database checked for drift</span>
        </div>
        <div class="mini-card">
            <strong>Migration output</strong>
            <span>Non-destructive SQL suggestions</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )


def style_diff_table(df: pd.DataFrame) -> Any:
    def row_style(row: pd.Series) -> list[str]:
        severity = row.get("severity")
        if severity == "High":
            return ["background-color: #fff1f0"] * len(row)
        if severity == "Medium":
            return ["background-color: #fff7e6"] * len(row)
        if severity == "Low":
            return ["background-color: #f0fdf4"] * len(row)
        return [""] * len(row)

    return df.style.apply(row_style, axis=1)


def display_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(
        columns={
            "object_type": "Object type",
            "object_name": "Object name",
            "diff_type": "Difference",
            "source_value": "Source value",
            "target_value": "Target value",
            "severity": "Severity",
            "fix_sql": "Suggested SQL",
        }
    )


def filtered_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    filter_cols = st.columns([1.1, 1.1, 1.2, 2.2])
    severities = filter_cols[0].multiselect("Severity", SEVERITIES, default=SEVERITIES)
    object_types = sorted(df["object_type"].dropna().unique().tolist())
    selected_types = filter_cols[1].multiselect("Object type", object_types, default=object_types)
    diff_types = sorted(df["diff_type"].dropna().unique().tolist())
    selected_diffs = filter_cols[2].multiselect("Difference", diff_types, default=diff_types)
    search = filter_cols[3].text_input("Search", placeholder="table, column, index, constraint")

    output = df.copy()
    if severities:
        output = output[output["severity"].isin(severities)]
    if selected_types:
        output = output[output["object_type"].isin(selected_types)]
    if selected_diffs:
        output = output[output["diff_type"].isin(selected_diffs)]
    if search:
        needle = search.strip().lower()
        output = output[
            output.apply(
                lambda row: needle
                in " ".join(str(value).lower() for value in row[["object_name", "diff_type", "source_value", "target_value"]]),
                axis=1,
            )
        ]
    return output


def build_report(df: pd.DataFrame, source_db: str, target_db: str, generated_sql: list[str]) -> str:
    report = "# Database Schema Drift Report\n\n"
    report += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    report += f"Source DB: {source_db}\n\n"
    report += f"Target DB: {target_db}\n\n"
    report += f"Total differences: {len(df)}\n\n"
    report += "## Differences\n\n"
    report += df.to_markdown(index=False) if not df.empty else "No schema drift found."
    if generated_sql:
        report += "\n\n## Generated Migration SQL\n\n```sql\n"
        report += "\n\n".join(generated_sql)
        report += "\n```\n"
    return report


def render_results(diffs: list[dict[str, Any]], generated_sql: list[str], source_db: str, target_db: str) -> None:
    st.markdown('<div class="section-label">Result summary</div>', unsafe_allow_html=True)

    if not diffs:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total differences", 0)
        c2.metric("High", 0)
        c3.metric("Medium", 0)
        c4.metric("Low", 0)
        st.success("No schema drift found.")
        return

    df = pd.DataFrame(diffs)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total differences", len(df))
    c2.metric("High", int((df["severity"] == "High").sum()))
    c3.metric("Medium", int((df["severity"] == "Medium").sum()))
    c4.metric("Low", int((df["severity"] == "Low").sum()))
    c5.metric("SQL statements", len(generated_sql))

    detail_tab, sql_tab, export_tab = st.tabs(["Drift details", "Migration SQL", "Exports"])

    with detail_tab:
        st.markdown('<div class="section-label">Filters</div>', unsafe_allow_html=True)
        filtered = filtered_dataframe(df)
        display_df = filtered.copy()
        display_df["fix_sql"] = display_df["fix_sql"].fillna("").str.replace("\n", " ", regex=False)
        st.dataframe(
            style_diff_table(display_columns(display_df)),
            use_container_width=True,
            hide_index=True,
        )

    with sql_tab:
        migration_sql = "\n\n".join(generated_sql)
        if migration_sql:
            st.code(migration_sql, language="sql")
            st.download_button(
                "Download migration SQL",
                migration_sql,
                file_name=f"{target_db}_schema_migration.sql",
                mime="text/sql",
                use_container_width=True,
            )
        else:
            st.info("No non-destructive migration SQL was generated.")

    with export_tab:
        report = build_report(df, source_db, target_db, generated_sql)
        csv_data = df.to_csv(index=False)
        left, middle, right = st.columns(3)
        left.download_button(
            "Download markdown report",
            report,
            file_name="schema_drift_report.md",
            mime="text/markdown",
            use_container_width=True,
        )
        middle.download_button(
            "Download CSV report",
            csv_data,
            file_name="schema_drift_report.csv",
            mime="text/csv",
            use_container_width=True,
        )
        right.download_button(
            "Download SQL",
            "\n\n".join(generated_sql),
            file_name=f"{target_db}_schema_migration.sql",
            mime="text/sql",
            use_container_width=True,
            disabled=not generated_sql,
        )


def valid_inputs(host: str, port: str, user: str, source_db: str, target_db: str) -> tuple[bool, str]:
    if not host.strip():
        return False, "Host is required."
    if not port.strip().isdigit():
        return False, "Port must be a number."
    if not user.strip():
        return False, "Username is required."
    if not source_db.strip():
        return False, "Source database name is required."
    if not target_db.strip():
        return False, "Target database name is required."
    return True, ""


# =========================================================
# SIDEBAR UI
# =========================================================


render_header()

with st.sidebar:
    st.markdown(
        """
<div class="sidebar-title">
    <h2>Database Configuration</h2>
    <p>MySQL connection and comparison scope</p>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.form("schema_compare_form"):
        st.markdown("#### Connection")
        host = st.text_input("Host", placeholder="127.0.0.1")
        port = st.text_input("Port", "3306")
        user = st.text_input("Username")
        password = st.text_input("Password", type="password")

        st.markdown("#### Databases")
        s_db = st.text_input("Source database")
        t_db = st.text_input("Target database")

        st.markdown("#### Checks")
        include_naming = st.checkbox("Include naming checks", value=True)
        compare_btn = st.form_submit_button("Compare schema", use_container_width=True)


# =========================================================
# MAIN ACTION
# =========================================================


if not compare_btn:
    render_empty_state()
else:
    is_valid, validation_error = valid_inputs(host, port, user, s_db, t_db)
    if not is_valid:
        st.error(validation_error)
    else:
        source_conn = None
        try:
            with st.spinner("Loading source schema..."):
                source_schema = load_schema(host, port, user, password, s_db)

            with st.spinner("Loading target schema..."):
                target_schema = load_schema(host, port, user, password, t_db)

            source_conn = conn(host, port, user, password, s_db)
            with st.spinner("Comparing structure..."):
                diffs, generated_sql = compare(source_schema, target_schema, source_conn)

            if include_naming:
                diffs += naming_issues(source_schema, "source")
                diffs += naming_issues(target_schema, "target")
                diffs.sort(
                    key=lambda item: (
                        SEVERITY_ORDER.get(item["severity"], 99),
                        item["object_type"],
                        item["object_name"],
                        item["diff_type"],
                    )
                )

            render_results(diffs, generated_sql, s_db, t_db)

        except Exception as exc:
            st.error(f"Error: {exc}")
        finally:
            if source_conn is not None:
                source_conn.close()


# =========================================================
# FOOTER
# =========================================================


st.markdown("---")
st.markdown(
    '<div class="footer-note">Use a read-only database user. This tool reads MySQL metadata and does not modify databases.</div>',
    unsafe_allow_html=True,
)

