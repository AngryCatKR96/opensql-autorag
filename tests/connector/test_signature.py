import hashlib
import hmac
import time

import pytest
from opensql_autorag_connector.signature import (
    SignatureError,
    parse_signature_header,
    verify_signature,
)

SECRET = "outline-signing-secret"


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},s={digest}"


def millis(offset_seconds: float = 0) -> str:
    """A signature timestamp the way Outline stamps it: Date.now(), milliseconds."""
    return str(int((time.time() + offset_seconds) * 1000))


def test_parse_signature_header_reads_both_fields():
    parsed = parse_signature_header("t=1754500000000,s=abc123")
    assert parsed.timestamp == "1754500000000"
    assert parsed.signature == "abc123"


@pytest.mark.parametrize("header", [None, "", "abc123", "t=1754500000000", "s=abc123"])
def test_malformed_headers_are_rejected(header):
    with pytest.raises(SignatureError):
        parse_signature_header(header)


def test_valid_signature_passes():
    body = b'{"event":"documents.update"}'
    verify_signature(body=body, header=sign(body, millis()), secret=SECRET)


def test_signature_of_a_different_body_is_rejected():
    header = sign(b'{"event":"documents.update"}', "1754500000")
    with pytest.raises(SignatureError):
        verify_signature(body=b'{"event":"documents.delete"}', header=header, secret=SECRET)


def test_signature_from_a_different_secret_is_rejected():
    body = b"{}"
    header = sign(body, "1754500000", secret="not-our-secret")
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=header, secret=SECRET)


def test_missing_secret_is_rejected():
    body = b"{}"
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=sign(body, "1"), secret="")


def test_stale_timestamp_is_rejected():
    body = b"{}"
    stale = millis(-3600)
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=sign(body, stale), secret=SECRET)


def test_timestamp_from_the_future_is_rejected():
    body = b"{}"
    ahead = millis(3600)
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=sign(body, ahead), secret=SECRET)


def test_fresh_timestamp_passes():
    body = b"{}"
    verify_signature(body=body, header=sign(body, millis()), secret=SECRET)


def test_a_timestamp_in_seconds_is_not_accepted_as_milliseconds():
    """Outline stamps `t` with Date.now(); a seconds value is not a valid signature."""
    body = b"{}"
    seconds = str(int(time.time()))
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=sign(body, seconds), secret=SECRET)


def test_tolerance_of_zero_accepts_any_age():
    body = b"{}"
    verify_signature(
        body=body, header=sign(body, millis(-99999)), secret=SECRET, tolerance_seconds=0
    )


def test_unparseable_timestamp_is_rejected():
    body = b"{}"
    with pytest.raises(SignatureError):
        verify_signature(body=body, header=sign(body, "not-a-number"), secret=SECRET)
