"""Preflight against an Outline instance that is broken in a specific way.

Each case asserts the failure is attributed to the right endpoint, because the
point of these checks is telling someone what to fix on a wiki this code has
never seen.
"""

import httpx
import pytest
from opensql_autorag_connector.client import OutlineDocument
from opensql_autorag_connector.preflight import run_preflight


def response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "/api/x"))


def refuse(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(str(status), request=httpx.Request("POST", "/api/x"),
                                 response=response(status))


class FakeClient:
    """An Outline that answers, or fails, per endpoint."""

    def __init__(
        self,
        collections: list[dict] | None = None,
        documents: list[OutlineDocument] | None = None,
        body: str | None = "# Doc\n\ntext",
        fails: dict[str, Exception] | None = None,
    ) -> None:
        self.collections = collections if collections is not None else [
            {"id": "col-platform", "name": "Platform"}
        ]
        self.documents = documents if documents is not None else [
            OutlineDocument(
                id="doc-1",
                title="Runbook",
                updated_at="2026-08-08T00:00:00.000Z",
                collection_id="col-platform",
                url="/doc/runbook",
            )
        ]
        self.body = body
        self.fails = fails or {}

    def _maybe_fail(self, endpoint: str) -> None:
        if endpoint in self.fails:
            raise self.fails[endpoint]

    def whoami(self) -> dict:
        self._maybe_fail("auth.info")
        return {
            "user": {"id": "user-1", "name": "Dana", "email": "dana@example.com"},
            "team": {"name": "Acme"},
        }

    def list_collections(self) -> list[dict]:
        self._maybe_fail("collections.list")
        return self.collections

    def iter_documents(self, collection_id: str | None = None):
        self._maybe_fail("documents.list")
        for document in self.documents:
            if collection_id is None or document.collection_id == collection_id:
                yield document

    def get_document(self, document_id: str) -> OutlineDocument:
        self._maybe_fail("documents.info")
        return OutlineDocument(
            id=document_id,
            title="Runbook",
            updated_at="2026-08-08T00:00:00.000Z",
            collection_id="col-platform",
            url="/doc/runbook",
            text=self.body,
        )


def failed(result, name: str) -> str:
    return next(check.detail for check in result.checks if check.name == name and not check.ok)


def test_a_working_instance_passes_every_check():
    result = run_preflight(FakeClient(), ("col-platform",))

    assert result.ok
    assert [check.name for check in result.checks] == [
        "auth.info",
        "collections.list",
        "sync scope",
        "documents.list",
        "documents.info",
    ]


def test_the_authenticated_user_and_workspace_are_reported():
    """So someone can tell at a glance which key and wiki they just pointed at."""
    result = run_preflight(FakeClient())

    detail = next(check.detail for check in result.checks if check.name == "auth.info")
    assert "Dana" in detail
    assert "dana@example.com" in detail
    assert "Acme" in detail


def test_a_rejected_key_stops_at_auth_info():
    result = run_preflight(FakeClient(fails={"auth.info": refuse(401)}))

    assert not result.ok
    assert [check.name for check in result.checks] == ["auth.info"]
    assert "rejected" in failed(result, "auth.info")


def test_a_scoped_key_is_explained_as_a_scope_problem():
    """403 on an endpoint means the key exists but is not allowed to call it."""
    result = run_preflight(FakeClient(fails={"collections.list": refuse(403)}))

    detail = failed(result, "collections.list")
    assert "scopes" in detail
    assert "collections.list" in detail


def test_a_base_url_that_is_not_outline_is_called_out():
    result = run_preflight(FakeClient(fails={"auth.info": refuse(404)}))

    assert "AUTORAG_OUTLINE_BASE_URL" in failed(result, "auth.info")


def test_an_unreachable_host_is_not_reported_as_a_key_problem():
    unreachable = httpx.ConnectError("connection refused")
    result = run_preflight(FakeClient(fails={"auth.info": unreachable}))

    detail = failed(result, "auth.info")
    assert "could not reach" in detail


def test_a_collection_the_key_cannot_read_is_named():
    result = run_preflight(FakeClient(), ("col-platform", "col-missing"))

    detail = failed(result, "sync scope")
    assert "col-missing" in detail
    assert "col-platform" not in detail


def test_a_wrong_collection_id_is_not_reported_as_an_empty_wiki():
    """The misleading reading this check exists to avoid."""
    result = run_preflight(FakeClient(), ("col-missing",))

    assert not result.ok
    assert "documents.list" not in [check.name for check in result.checks]


def test_no_collection_filter_warns_that_everything_is_in_scope():
    result = run_preflight(FakeClient(), ())

    detail = next(check.detail for check in result.checks if check.name == "sync scope")
    assert "AUTORAG_OUTLINE_COLLECTIONS" in detail


def test_an_empty_scope_is_not_a_failure():
    """A wiki with nothing in the synced collections yet is fine, just empty."""
    result = run_preflight(FakeClient(documents=[]), ("col-platform",))

    assert result.ok


def test_a_document_with_no_body_fails_because_nothing_would_be_indexed():
    result = run_preflight(FakeClient(body=""), ("col-platform",))

    assert not result.ok
    assert "nothing would be indexed" in failed(result, "documents.info")


def test_bodies_being_unreadable_is_attributed_to_documents_info():
    result = run_preflight(FakeClient(fails={"documents.info": refuse(403)}), ("col-platform",))

    assert "documents.info" in failed(result, "documents.info")


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_every_failure_says_what_it_costs(status):
    result = run_preflight(FakeClient(fails={"documents.list": refuse(status)}))

    assert not result.ok
    assert failed(result, "documents.list")
