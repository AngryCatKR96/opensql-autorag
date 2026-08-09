import os

import psycopg
import pytest
from psycopg.rows import dict_row

# The compose file publishes the demo database on 5433; AUTORAG_DATABASE_URL wins
# when it is set, so the same tests run against another instance unchanged.
DEMO_DATABASE_URL = "postgresql://autorag:autorag@127.0.0.1:5433/autorag"

REQUIRED_TABLES = ("documents", "document_sources", "document_chunks", "chunk_embeddings")


def _database_url() -> str:
    return os.environ.get("AUTORAG_DATABASE_URL") or DEMO_DATABASE_URL


@pytest.fixture
def db_connection():
    """A connection to the demo database, rolled back afterwards.

    Skips rather than fails when the database is not up, so the suite still runs
    without the container. The schema is checked before anything is written, so a
    URL pointing at some other database can never be modified here.
    """
    url = _database_url()
    try:
        connection = psycopg.connect(url, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable at {url}: {exc}")

    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS present", ("document_chunks",)
            )
            row = cursor.fetchone()
            missing = not (row and row["present"])
            if not missing:
                cursor.execute(
                    """
                    SELECT count(*) AS found
                    FROM information_schema.columns
                    WHERE table_name = 'documents' AND column_name = 'retired_at'
                    """
                )
                missing = cursor.fetchone()["found"] == 0
        if missing:
            connection.close()
            pytest.skip(
                f"{url} does not carry the AutoRAG schema with documents.retired_at; "
                "apply infra/db/init.sql"
            )
        try:
            yield connection
        finally:
            # Nothing a test writes is kept.
            connection.rollback()
