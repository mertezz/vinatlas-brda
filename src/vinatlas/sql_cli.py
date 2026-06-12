"""Read-only SQL runner over data/vinatlas.duckdb — the agent/CLI access path
(no separate DuckDB install; uses the venv's duckdb package).

Run from the project root, one statement per call:
    uv run vinatlas-sql "SELECT * FROM v_wineries LIMIT 5"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

DB_PATH = Path("data/vinatlas.duckdb")


def main() -> None:
    parser = argparse.ArgumentParser(prog="vinatlas-sql", description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--max-rows", type=int, default=100)
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.sql(args.query).show(max_rows=args.max_rows)


if __name__ == "__main__":
    main()
