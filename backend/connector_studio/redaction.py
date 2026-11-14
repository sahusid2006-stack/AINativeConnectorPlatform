"""Central redaction for credential-bearing trusted components."""
import os
import json
from threading import RLock
from urllib.parse import quote

_lock = RLock()
_values = set()
_FIELDS = {"password", "token", "authorization", "secret", "credential", "credential_value", "api_key", "csrf_token", "cookie", "set_cookie"}


def remember(value):
    if isinstance(value, str) and value:
        with _lock:
            _values.add(value)
            _values.add(quote(value, safe=""))
            _values.add(json.dumps(value, ensure_ascii=False)[1:-1])


def redacted(value):
    for name in ("POSTGRES_PASSWORD", "OWNER_ACCESS_TOKEN", "SECRET_ENCRYPTION_KEY", "MCP_GATEWAY_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        remember(os.environ.get(name))
    if isinstance(value, dict):
        def sensitive(key):
            normalized = str(key).lower().replace("-", "_")
            return normalized in _FIELDS or normalized.endswith(("_token", "_password", "_secret", "_api_key", "_encryption_key"))
        return {key: "[REDACTED]" if sensitive(key) else redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    if isinstance(value, str):
        with _lock:
            for secret in sorted(_values, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
    return value
