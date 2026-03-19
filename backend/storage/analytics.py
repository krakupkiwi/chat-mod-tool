"""
DuckDB analytics layer.

DuckDB attaches the SQLite file directly — no data replication needed.
All queries run against the live SQLite tables, so results are always
up to date.

Usage (sync, called from asyncio via run_in_executor):
    results = run_analytics(settings.db_path, SOME_QUERY, params)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_analytics(db_path: str, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    """
    Execute a DuckDB query against the SQLite database and return rows as dicts.
    Runs synchronously — call via asyncio.to_thread() from async code.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        # Attach the SQLite file — DuckDB reads SQLite natively since v0.9
        con.execute(f"ATTACH '{db_path}' AS ids (TYPE sqlite, READ_ONLY TRUE)")
        rel = con.execute(query, list(params))
        columns = [desc[0] for desc in rel.description]
        return [dict(zip(columns, row)) for row in rel.fetchall()]
    finally:
        con.close()
