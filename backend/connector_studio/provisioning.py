"""Explicit local provisioning; no committed credentials and no secret logging."""
import base64
import os
from pathlib import Path
import secrets
import sys

PATHS = {"OWNER_ACCESS_TOKEN": Path("/run/owner/credential"),
         "SECRET_ENCRYPTION_KEY": Path("/run/vault/key"),
         "MCP_GATEWAY_TOKEN": Path("/run/gateway/token")}


def provision():
    for name, path in PATHS.items():
        # Secure workspace configuration takes precedence; generated files persist in named volumes.
        value = os.environ.get(name) or (base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                if name == "SECRET_ENCRYPTION_KEY" else secrets.token_urlsafe(32))
        if len(value) < 32:
            raise RuntimeError("Secure configuration must contain at least 32 characters")
        if name == "SECRET_ENCRYPTION_KEY" and len(base64.urlsafe_b64decode(value)) != 32:
            raise RuntimeError("Encryption configuration must encode exactly 32 bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(value)
            os.chown(path, 10001, 10001)
        os.chown(path.parent, 10001, 10001)
    print("Local owner, encryption, and gateway configuration provisioned; existing keys preserved. No values displayed.")


def configured_secret(name):
    value = os.environ.get(name)
    if not value:
        path = Path(os.environ.get(name + "_FILE", str(PATHS[name])))
        try:
            value = path.read_text().strip()
        except OSError:
            raise RuntimeError("Required secure configuration is unavailable") from None
    if len(value) < 32:
        raise RuntimeError("Required secure configuration is invalid")
    from .redaction import remember
    remember(value)
    return value


if __name__ == "__main__":
    if sys.argv[1:] == ["init"]:
        provision()
    elif sys.argv[1:] == ["show-owner"]:
        # Only the operator explicitly invokes this in their own terminal; agents must never run it.
        print(configured_secret("OWNER_ACCESS_TOKEN"))
    elif sys.argv[1:] == ["rotate-owner"]:
        path = PATHS["OWNER_ACCESS_TOKEN"]
        temporary = path.with_suffix(".new")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(32))
        os.chown(temporary, 10001, 10001)
        os.replace(temporary, path)
        print("Owner credential rotated; prior sessions expire on their next request. No value displayed.")
    else:
        raise SystemExit("Usage: provisioning init|show-owner|rotate-owner")
