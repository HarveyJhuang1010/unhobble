"""Keep credential values out of every file the bench writes. Stdlib only.

The model's Bash tool inherits the run's environment, so `printenv` would put
the token into the transcript. The bench knows the values it passed in, and
replaces them before anything is written: raw, JSON-escaped (once and twice),
and base64 (standard and URL-safe, with and without padding). Anything that
looks like an Anthropic credential (`sk-ant-` and 8+ more characters) is
replaced too, which catches a copy that was cut off or wrapped. Other fragments
of a split or re-encoded token cannot all be caught; credential_exposed flags
the runs where that could have happened.
"""
from __future__ import annotations

import base64
import json
import re

MARK = "[REDACTED]"
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
MIN_LENGTH = 20  # real credentials are far longer; shorter values would mangle ordinary output
ANTHROPIC_PREFIXED = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")


def credentials(env: dict) -> list[str]:
    return [env[name] for name in CREDENTIAL_VARS if env.get(name)]


def forms(secret: str) -> list[str]:
    raw = secret.encode("utf-8")
    encoded = [base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode()]
    found = {secret, json.dumps(secret)[1:-1], json.dumps(json.dumps(secret))[3:-3]}
    found.update(encoded + [e.rstrip("=") for e in encoded])
    return sorted(found, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= MIN_LENGTH:
            for form in forms(secret):
                text = text.replace(form, MARK)
    return ANTHROPIC_PREFIXED.sub(MARK, text)


def redact_bytes(data: bytes, secrets: list[str]) -> bytes:
    return redact(data.decode("utf-8", "surrogateescape"), secrets).encode("utf-8", "surrogateescape")
