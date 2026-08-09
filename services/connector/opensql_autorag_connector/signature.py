from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

# How far the signature timestamp may be from now. Outline retries a failed
# delivery with a fresh signature, so a window this wide never rejects a
# legitimate retry, and it bounds how long a captured request can be replayed.
DEFAULT_TOLERANCE_SECONDS = 300


class SignatureError(Exception):
    """The webhook signature is missing, malformed, or does not match."""


@dataclass(frozen=True)
class ParsedSignature:
    timestamp: str
    signature: str


def parse_signature_header(header: str | None) -> ParsedSignature:
    """Parse an `Outline-Signature: t=<timestamp>,s=<hex digest>` header."""
    if not header:
        raise SignatureError("missing Outline-Signature header")

    fields: dict[str, str] = {}
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key and value:
            fields[key] = value

    timestamp = fields.get("t")
    signature = fields.get("s")
    if not timestamp or not signature:
        raise SignatureError(f"malformed Outline-Signature header: {header!r}")
    return ParsedSignature(timestamp=timestamp, signature=signature)


def _timestamp_seconds(timestamp: str) -> float:
    """Convert the `t` field of Outline-Signature to epoch seconds.

    Outline stamps it with `Date.now()`, so `t` is milliseconds
    (`server/models/WebhookSubscription.ts`, `signature()`).
    """
    return float(timestamp) / 1000


def verify_signature(
    body: bytes,
    header: str | None,
    secret: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Raise SignatureError unless `body` was signed by Outline with `secret`.

    The signed string is `{timestamp}.{raw request body}`, hashed with HMAC-SHA256.
    `tolerance_seconds` of 0 disables the replay window.
    """
    if not secret:
        raise SignatureError("AUTORAG_OUTLINE_WEBHOOK_SECRET is not set")

    parsed = parse_signature_header(header)
    signed = parsed.timestamp.encode("utf-8") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parsed.signature):
        raise SignatureError("Outline-Signature does not match the request body")

    if tolerance_seconds > 0:
        try:
            age = time.time() - _timestamp_seconds(parsed.timestamp)
        except ValueError as exc:
            raise SignatureError(f"unparseable timestamp: {parsed.timestamp!r}") from exc
        if abs(age) > tolerance_seconds:
            raise SignatureError(f"signature timestamp is {age:.0f}s away from now")
