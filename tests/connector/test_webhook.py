import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from opensql_autorag_connector import app as app_module

SECRET = "outline-signing-secret"


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},s={digest}"


def millis(offset_seconds: float = 0) -> str:
    """A signature timestamp the way Outline stamps it: Date.now(), milliseconds."""
    return str(int((time.time() + offset_seconds) * 1000))


@pytest.fixture
def client(monkeypatch):
    """The webhook app with the real replay window, only the secret stubbed."""
    synced: list[str] = []
    deactivated: list[str] = []
    monkeypatch.setattr(app_module.settings, "webhook_secret", SECRET, raising=False)
    monkeypatch.setattr(app_module, "sync_document", synced.append)
    monkeypatch.setattr(app_module, "deactivate_document", deactivated.append)
    test_client = TestClient(app_module.app)
    test_client.synced = synced
    test_client.deactivated = deactivated
    return test_client


def post(client: TestClient, event: dict, header: str | None = None):
    body = json.dumps(event).encode("utf-8")
    headers = {"content-type": "application/json"}
    headers["outline-signature"] = header if header is not None else sign(body, millis())
    return client.post("/outline/webhook", content=body, headers=headers)


def test_document_update_is_queued(client):
    document_id = "0f1a4c8e-3c9d-4a1e-9b6f-2f0d5c7a1234"
    response = post(client, {"event": "documents.update", "payload": {"id": document_id}})

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert client.synced == [document_id]


def test_document_id_is_read_from_a_nested_model(client):
    document_id = "0f1a4c8e-3c9d-4a1e-9b6f-2f0d5c7a1234"
    response = post(
        client,
        {"event": "documents.publish", "payload": {"model": {"id": document_id}}},
    )

    assert response.status_code == 200
    assert client.synced == [document_id]


def test_unsigned_request_is_rejected(client):
    response = post(client, {"event": "documents.update", "payload": {"id": "x"}}, header="")

    assert response.status_code == 401
    assert client.synced == []


def test_tampered_body_is_rejected(client):
    header = sign(json.dumps({"event": "documents.update"}).encode("utf-8"), "1754500000")
    response = post(client, {"event": "documents.update", "payload": {"id": "x"}}, header=header)

    assert response.status_code == 401
    assert client.synced == []


def test_unrelated_events_are_ignored(client):
    response = post(client, {"event": "users.create", "payload": {"id": "u1"}})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert client.synced == []


DOCUMENT_ID = "0f1a4c8e-3c9d-4a1e-9b6f-2f0d5c7a1234"


@pytest.mark.parametrize(
    "event",
    [
        "documents.delete",
        "documents.permanent_delete",
        "documents.archive",
        "documents.unpublish",
    ],
)
def test_removal_events_retire_the_document(client, event):
    response = post(client, {"event": event, "payload": {"id": DOCUMENT_ID}})

    assert response.status_code == 200
    assert response.json()["status"] == "retiring"
    assert client.deactivated == [DOCUMENT_ID]
    # Retiring must not go back to Outline: the document may already be gone.
    assert client.synced == []


@pytest.mark.parametrize("event", ["documents.restore", "documents.unarchive"])
def test_restore_events_re_index_the_document(client, event):
    response = post(client, {"event": event, "payload": {"id": DOCUMENT_ID}})

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert client.synced == [DOCUMENT_ID]
    assert client.deactivated == []


def test_a_move_re_fetches_so_the_new_collection_is_recorded(client):
    response = post(
        client,
        {"event": "documents.move", "payload": {"id": DOCUMENT_ID}},
    )

    assert response.status_code == 200
    assert client.synced == [DOCUMENT_ID]


def test_emptying_the_trash_is_ignored(client):
    """It carries no document id; each document already raised documents.delete."""
    response = post(client, {"event": "documents.empty_trash", "payload": {}})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert client.deactivated == []
    assert client.synced == []


def test_a_stale_signature_is_rejected(client):
    body = json.dumps({"event": "documents.update", "payload": {"id": DOCUMENT_ID}}).encode()
    header = sign(body, millis(-3600))

    response = client.post(
        "/outline/webhook",
        content=body,
        headers={"content-type": "application/json", "outline-signature": header},
    )

    assert response.status_code == 401
    assert client.synced == []


def test_payload_without_a_document_id_is_a_bad_request(client):
    response = post(client, {"event": "documents.update", "payload": {}})

    assert response.status_code == 400
    assert client.synced == []
