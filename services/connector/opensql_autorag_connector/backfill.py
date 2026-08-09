from __future__ import annotations

import argparse
import logging
from collections import Counter
from collections.abc import Iterable
from uuid import UUID

import httpx

from opensql_autorag_connector.client import OutlineClient, OutlineDocument
from opensql_autorag_connector.ingest import (
    IngestOutcome,
    fetch_synced_document_ids,
    ingest_document,
    is_unchanged,
    retire_document,
)
from opensql_autorag_connector.preflight import run_preflight
from opensql_autorag_connector.settings import OutlineSettings, settings

logger = logging.getLogger(__name__)


def _iter_scope(
    client: OutlineClient,
    collection_ids: Iterable[str],
) -> Iterable[OutlineDocument]:
    ids = tuple(collection_ids)
    if not ids:
        yield from client.iter_documents()
        return
    for collection_id in ids:
        yield from client.iter_documents(collection_id=collection_id)


def run_backfill(
    client: OutlineClient,
    collection_ids: Iterable[str] = (),
    force: bool = False,
    prune: bool = True,
) -> Counter[str]:
    """Sync every document in scope, fetching bodies only for changed documents.

    With `prune`, documents that AutoRAG has from this scope but Outline no longer
    lists in it are retired -- how a sync recovers from a webhook it never
    received.
    """
    scope = tuple(collection_ids)
    counts: Counter[str] = Counter()
    seen: set[UUID] = set()
    for listed in _iter_scope(client, scope):
        counts["scanned"] += 1
        seen.add(UUID(listed.id))
        if not force and is_unchanged(listed):
            counts[IngestOutcome.SKIPPED] += 1
            continue
        try:
            # documents.list omits the body, so the markdown is fetched here.
            document = client.get_document(listed.id)
            result = ingest_document(document, force=force)
        except httpx.HTTPError as exc:
            # A document can be archived, deleted, or outside the key's access
            # between listing and fetching; one failure must not stop the sync.
            logger.warning("skipping %s (%s): %s", listed.id, listed.title, exc)
            counts["failed"] += 1
            continue
        counts[result.outcome] += 1

    if prune:
        counts["retired"] = _prune(scope, seen, failed=counts["failed"])
    return counts


def _prune(collection_ids: tuple[str, ...], seen: set[UUID], failed: int) -> int:
    """Retire synced documents that this run's listing did not contain.

    Skipped when any document failed, because a listing cut short by an error is
    not evidence that the missing documents are gone -- retiring on a partial
    listing would take a healthy wiki out of search.
    """
    if failed:
        logger.warning("not pruning: %s document(s) failed, so the listing is incomplete", failed)
        return 0

    known = fetch_synced_document_ids(collection_ids)
    disappeared = known - seen
    for document_id in disappeared:
        retire_document(str(document_id))
        logger.info("retired %s: no longer listed in Outline", document_id)
    return len(disappeared)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Outline documents into AutoRAG")
    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        dest="collections",
        help="Outline collection id to sync; repeatable. Defaults to AUTORAG_OUTLINE_COLLECTIONS.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest documents even when their body is unchanged.",
    )
    parser.add_argument(
        "--list-collections",
        action="store_true",
        help="Print the collections the API key can read, then exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify this Outline instance and API key without syncing anything, then exit.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_false",
        dest="prune",
        help="Keep serving synced documents that Outline no longer lists in this scope.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    active: OutlineSettings = settings

    with OutlineClient(active) as client:
        if args.list_collections:
            for collection in client.list_collections():
                print(f"{collection['id']}  {collection.get('name')}")
            return

        collection_ids = tuple(args.collections) or active.collection_ids

        if args.check:
            result = run_preflight(client, collection_ids)
            for check in result.checks:
                print(check.line())
            raise SystemExit(0 if result.ok else 1)

        if not collection_ids:
            logger.warning(
                "no collection filter set: every document the API key can read will be "
                "indexed, and retrieval has no permission filter yet"
            )
        counts = run_backfill(client, collection_ids, force=args.force, prune=args.prune)

    logger.info(
        "backfill done: scanned=%s ingested=%s skipped=%s empty=%s failed=%s retired=%s",
        counts["scanned"],
        counts[IngestOutcome.INGESTED],
        counts[IngestOutcome.SKIPPED],
        counts[IngestOutcome.EMPTY],
        counts["failed"],
        counts["retired"],
    )


if __name__ == "__main__":
    main()
