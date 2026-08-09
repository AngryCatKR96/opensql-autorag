from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from opensql_autorag_api.settings import settings

logger = logging.getLogger(__name__)

# Whether this process has already reported that the server cannot take the HNSW
# settings. It is a property of the server, so saying it once is enough.
_warned_about_hnsw = False


def _apply_hnsw_settings(connection: psycopg.Connection) -> None:
    """Ask pgvector to keep searching when the permission filter rejects results.

    An HNSW scan visits a bounded candidate pool, and this platform filters those
    candidates by what the caller may read. On a wiki where one person can read
    little of what is indexed, enough of the pool can be filtered away that a
    query returns fewer than top_k while matching documents were never visited --
    silently, since a short result list looks exactly like a narrow query.
    Iterative scan resumes the search instead of stopping there.

    pgvector registers these settings when its library loads, which a session has
    not necessarily done yet, so the load is explicit. Everything here is optional:
    a server without them still answers queries, it just cannot resume a scan.
    """
    global _warned_about_hnsw
    if not settings.hnsw_iterative_scan:
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("LOAD 'vector'")
            cursor.execute(
                "SELECT set_config('hnsw.iterative_scan', %s, false)",
                (settings.hnsw_iterative_scan,),
            )
            if settings.hnsw_max_scan_tuples:
                cursor.execute(
                    "SELECT set_config('hnsw.max_scan_tuples', %s, false)",
                    (str(settings.hnsw_max_scan_tuples),),
                )
    except psycopg.Error as exc:
        connection.rollback()
        if not _warned_about_hnsw:
            _warned_about_hnsw = True
            logger.warning(
                "could not enable hnsw.iterative_scan (%s); a filtered search may "
                "return fewer than top_k results on a large index. It needs "
                "pgvector 0.8 or newer",
                exc,
            )


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        _apply_hnsw_settings(connection)
        yield connection
