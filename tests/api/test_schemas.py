from uuid import uuid4

from opensql_autorag_api.main import app
from opensql_autorag_api.schemas import DocumentUploadResponse, SearchRequest


def test_upload_response_serializes_ids():
    response = DocumentUploadResponse(document_id=uuid4(), version_id=uuid4(), job_id=uuid4())
    payload = response.model_dump(mode="json")
    assert isinstance(payload["document_id"], str)
    assert isinstance(payload["version_id"], str)
    assert isinstance(payload["job_id"], str)


def test_search_request_defaults_top_k_to_five():
    request = SearchRequest(query="OpenSQL pgvector")
    assert request.top_k == 5


def test_version_upload_route_is_registered():
    paths = {route.path for route in app.routes}
    assert "/documents/{document_id}/versions" in paths
