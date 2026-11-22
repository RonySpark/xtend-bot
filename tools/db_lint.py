#!/usr/bin/env python3
"""Check the shared SQLite store against schema.sql and data invariants."""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ingest_followers import SCHEMA_PATH

CONSTRAINT_STARTS = {"unique", "primary", "constraint", "check", "foreign"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def split_commas(body):
    parts = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return parts


def read_paren_body(sql, start):
    depth = 1
    index = start
    while index < len(sql) and depth:
        if sql[index] == "(":
            depth += 1
        elif sql[index] == ")":
            depth -= 1
        index += 1
    return sql[start : index - 1]


def parse_schema(sql):
    tables = []
    for match in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        sql,
        flags=re.IGNORECASE,
    ):
        body = read_paren_body(sql, match.end())
        columns = []
        uniques = []
        for part in split_commas(body):
            text = part.strip()
            if not text:
                continue
            tokens = text.split()
            if tokens[0].lower() in CONSTRAINT_STARTS:
                unique_match = re.search(
                    r"\bUNIQUE\s*\(([^)]*)\)", text, flags=re.IGNORECASE
                )
                if unique_match:
                    cols = [
                        name.strip().strip('"')
                        for name in unique_match.group(1).split(",")
                        if name.strip()
                    ]
                    if cols:
                        uniques.append({"columns": cols, "where": None})
                continue
            name = tokens[0].strip('"')
            if re.search(r"\bUNIQUE\b", text, flags=re.IGNORECASE):
                uniques.append({"columns": [name], "where": None})
            ref = re.search(
                r"\bREFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)",
                text,
                flags=re.IGNORECASE,
            )
            columns.append(
                {
                    "name": name,
                    "not_null": re.search(r"\bNOT\s+NULL\b", text, flags=re.IGNORECASE)
                    is not None,
                    "references": (ref.group(1), ref.group(2)) if ref else None,
                }
            )
        tables.append(
            {"name": match.group(1), "columns": columns, "uniques": uniques}
        )
    by_name = {table["name"]: table for table in tables}
    index_re = re.compile(
        r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"\w+\s+ON\s+(\w+)\s*\(([^)]*)\)\s*(?:WHERE\s+([^;]+))?",
        flags=re.IGNORECASE,
    )
    for match in index_re.finditer(sql):
        table = by_name.get(match.group(1))
        if table is None:
            continue
        columns = [
            name.strip().strip('"')
            for name in match.group(2).split(",")
            if name.strip()
        ]
        if not columns:
            continue
        where = match.group(3).strip() if match.group(3) else None
        table["uniques"].append({"columns": columns, "where": where})
    return tables


def is_timestamp(name):
    lowered = name.lower()
    return lowered == "date" or lowered.endswith("_at") or lowered.endswith("_seen")


def timestamp_is_bad(value):
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return True
        return False
    if not isinstance(value, str):
        return True
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return True
    return parsed.utcoffset() != timedelta(0)


def open_readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def column_names(conn, table):
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})")
    return {row["name"] for row in rows}


def check_schema(conn, tables):
    problems = []
    for table in tables:
        if not table_exists(conn, table["name"]):
            problems.append(f"missing table: {table['name']}")
            continue
        present = column_names(conn, table["name"])
        for column in table["columns"]:
            if column["name"] not in present:
                problems.append(f"missing column: {table['name']}.{column['name']}")
    return problems


def check_integrity(conn):
    rows = list(conn.execute("PRAGMA integrity_check"))
    if not rows:
        return ["integrity_check: no result"]
    return [f"integrity_check: {row[0]}" for row in rows if row[0] != "ok"]


def check_foreign_keys(conn):
    return [
        f"foreign_key_check: {row[0]} row {row[1]} parent {row[2]} fkid {row[3]}"
        for row in conn.execute("PRAGMA foreign_key_check")
    ]


def check_references(conn, tables):
    problems = []
    for table in tables:
        if not table_exists(conn, table["name"]):
            continue
        present = column_names(conn, table["name"])
        label = "id" if "id" in present else "rowid"
        label_sql = "child.rowid" if label == "rowid" else f"child.{quote_ident(label)}"
        child_name = quote_ident(table["name"])
        for column in table["columns"]:
            if column["references"] is None or column["name"] not in present:
                continue
            parent, parent_col = column["references"]
            if not table_exists(conn, parent) or parent_col not in column_names(conn, parent):
                continue
            child_col = quote_ident(column["name"])
            parent_column = quote_ident(parent_col)
            rows = conn.execute(
                f"""
                SELECT {label_sql} AS row_id, child.{child_col} AS value
                FROM {child_name} AS child
                LEFT JOIN {quote_ident(parent)} AS parent
                  ON parent.{parent_column} = child.{child_col}
                WHERE child.{child_col} IS NOT NULL
                  AND parent.{parent_column} IS NULL
                ORDER BY row_id
                """
            )
            for row in rows:
                problems.append(
                    f"{table['name']} row {row['row_id']} points at missing {parent} {row['value']}"
                )
    return problems


def check_uniques(conn, tables):
    problems = []
    for table in tables:
        if not table_exists(conn, table["name"]):
            continue
        present = column_names(conn, table["name"])
        for group in table["uniques"]:
            columns = group["columns"]
            where_sql = group["where"]
            if any(column not in present for column in columns):
                continue
            if where_sql and any(
                name not in present
                and name.lower() not in {"is", "null", "not", "and", "or"}
                for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", where_sql)
            ):
                continue
            quoted = ", ".join(quote_ident(column) for column in columns)
            if where_sql:
                where = where_sql
            else:
                where = " AND ".join(
                    f"{quote_ident(column)} IS NOT NULL" for column in columns
                )
            rows = conn.execute(
                f"""
                SELECT {quoted}, COUNT(*) AS n
                FROM {quote_ident(table["name"])}
                WHERE {where}
                GROUP BY {quoted}
                HAVING n > 1
                ORDER BY {quoted}
                """
            )
            for row in rows:
                pairs = ", ".join(f"{column}={row[column]!r}" for column in columns)
                problems.append(f"duplicate rows in {table['name']}: {pairs}")
    return problems


def check_timestamps(conn, tables):
    problems = []
    for table in tables:
        if not table_exists(conn, table["name"]):
            continue
        present = column_names(conn, table["name"])
        label = "id" if "id" in present else "rowid"
        for column in table["columns"]:
            if column["name"] not in present or not is_timestamp(column["name"]):
                continue
            rows = conn.execute(
                f"""
                SELECT {quote_ident(label)} AS row_id, {quote_ident(column["name"])} AS value
                FROM {quote_ident(table["name"])}
                ORDER BY row_id
                """
            )
            for row in rows:
                value = row["value"]
                where = f"{table['name']}.{column['name']} row {row['row_id']}"
                if value is None:
                    if column["not_null"]:
                        problems.append(f"bad timestamp: {where} is null")
                    continue
                if timestamp_is_bad(value):
                    problems.append(f"bad timestamp: {where} value {value!r}")
    return problems


def lint(db_path):
    if not SCHEMA_PATH.is_file():
        return [f"schema not found: {SCHEMA_PATH}"]
    tables = parse_schema(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not tables:
        return ["schema.sql has no tables"]
    path = Path(db_path)
    if not path.exists():
        return [f"database not found: {path}"]
    if not path.is_file():
        return [f"database is not a file: {path}"]
    try:
        conn = open_readonly(path)
    except sqlite3.Error as exc:
        return [f"database is not readable: {exc}"]
    try:
        problems = []
        problems.extend(check_schema(conn, tables))
        problems.extend(check_integrity(conn))
        problems.extend(check_foreign_keys(conn))
        problems.extend(check_references(conn, tables))
        problems.extend(check_uniques(conn, tables))
        problems.extend(check_timestamps(conn, tables))
        return problems
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the shared SQLite store against schema.sql and data invariants."
    )
    parser.add_argument("--db", required=True, help="SQLite database file to check")
    args = parser.parse_args(argv)
    problems = lint(args.db)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
