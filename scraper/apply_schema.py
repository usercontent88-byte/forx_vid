"""Apply the versioned catalog schema before writing scrape data to Neon."""
from __future__ import annotations

import os
from pathlib import Path

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "001_video_catalog.sql"


def load_migration_sql() -> str:
    """Read the initial idempotent schema migration from the repository."""
    if not MIGRATION_PATH.is_file():
        raise FileNotFoundError(f"Catalog schema migration not found: {MIGRATION_PATH}")
    sql = MIGRATION_PATH.read_text(encoding="utf-8").strip()
    if not sql:
        raise ValueError(f"Catalog schema migration is empty: {MIGRATION_PATH}")
    return sql


def apply_schema(database_url: str | None = None) -> None:
    """Apply the idempotent catalog migration in one PostgreSQL transaction.

    This creates missing catalog tables/indexes; the migration contains no DROP or
    TRUNCATE statements. Call this only for the database intended for catalog data.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL to the intended Neon database before applying the schema.")

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install dependencies with: python -m pip install -r requirements.txt") from exc

    sql = load_migration_sql()
    # Psycopg uses PostgreSQL's simple-query protocol here because no query
    # parameters are passed; this migration intentionally contains many DDL statements.
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, prepare=False)


def main() -> int:
    apply_schema()
    print(f"Catalog schema migration applied successfully from {MIGRATION_PATH.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
