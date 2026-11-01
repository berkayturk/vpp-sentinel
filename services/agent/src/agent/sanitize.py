"""Masking applied before anything reaches the model: device ids, IPs, tokens.

Device ids keep their site and type (the blast radius needs them) and lose
their number behind a keyed pseudonym that is stable for the lifetime of the
salt, so the model can still say "the same three devices" without ever
seeing a real identifier.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from hashlib import sha256

DEVICE_ID = re.compile(r"\b([a-z]+(?:-[a-z]+)*)-(charger|battery|inverter)-(\d{3})\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{6,}|sk-ant-[A-Za-z0-9_-]{6,}"
    r"|Bearer\s+[A-Za-z0-9._-]{6,})"
)
PSEUDONYM_HEX = 6

_PROCESS_SALT = secrets.token_bytes(16)


def _salt(explicit: bytes | None) -> bytes:
    if explicit is not None:
        return explicit
    from_env = os.getenv("AGENT_PSEUDONYM_SALT")
    return from_env.encode() if from_env else _PROCESS_SALT


def pseudonymise_device_id(device_id: str, salt: bytes | None = None) -> str:
    """``lugano-south-charger-001`` → ``lugano-south-charger-#a1b2c3`` (keyed, stable)."""
    match = DEVICE_ID.fullmatch(device_id)
    if match is None:
        return device_id
    site, device_type, _ = match.groups()
    digest = hmac.new(_salt(salt), device_id.encode(), sha256).hexdigest()[:PSEUDONYM_HEX]
    return f"{site}-{device_type}-#{digest}"


def sanitize_text(text: str, salt: bytes | None = None) -> str:
    """Mask every device id, IPv4 address and credential-shaped token in ``text``."""
    key = _salt(salt)
    masked = DEVICE_ID.sub(lambda m: pseudonymise_device_id(m.group(0), key), text)
    masked = TOKEN.sub("<token>", masked)
    return IPV4.sub("<ip>", masked)
