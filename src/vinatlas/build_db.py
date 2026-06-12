"""Build data/vinatlas.duckdb from the raw JSONL captures.

Idempotent: raw tables are CREATE OR REPLACE'd from data/raw/vivino/, views are
recreated on top. Fail-loud: a missing raw entity directory aborts the build.
Run from the project root:
    uv run vinatlas-build
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

RAW_ENTITIES = ["regions", "explore", "winery_wines", "winery_vintages", "reviews"]
SQL_FILES = [Path("sql/raw_vivino.sql"), Path("sql/views_vivino.sql")]
DB_PATH = Path("data/vinatlas.duckdb")
TABLES = [f"raw_vivino_{e}" for e in RAW_ENTITIES]
VIEWS = ["v_wineries", "v_wines", "v_vintages", "v_reviews"]


def main() -> None:
    missing = [e for e in RAW_ENTITIES if not list(Path("data/raw/vivino", e).glob("*.jsonl"))]
    if missing:
        sys.exit(f"no raw JSONL for: {', '.join(missing)} — run vinatlas-fetch first")

    con = duckdb.connect(str(DB_PATH))
    for sql_file in SQL_FILES:
        con.execute(sql_file.read_text(encoding="utf-8"))
        print(f"applied {sql_file}")

    for rel in TABLES + VIEWS:
        (n,) = con.execute(f"SELECT count(*) FROM {rel}").fetchone()
        print(f"{rel}: {n} rows")
    con.close()
    print(f"built {DB_PATH}")


if __name__ == "__main__":
    main()