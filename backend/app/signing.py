"""Ed25519 signing of tasks sent to agents.

The private key lives in a file (VCENTRAL_TASK_SIGNING_KEY_FILE), not in the database, so someone who can
only write to the database cannot make agents run commands. Agents learn the public key on their first
report after enrollment and refuse tasks signed by any other key afterwards.

Create a key:  openssl genpkey -algorithm ed25519 -out task-signing.key
          or:  python -m app.signing generate task-signing.key
"""
from __future__ import annotations

import base64
import json
import sys
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import get_settings


class SigningUnavailable(Exception):
    pass


@lru_cache(maxsize=2)
def _load(path: str, mtime: float) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningUnavailable(f"{path} is not an Ed25519 private key")
    return key


def private_key() -> Ed25519PrivateKey:
    path = get_settings().task_signing_key_file
    if not path:
        raise SigningUnavailable("Tasks are disabled: VCENTRAL_TASK_SIGNING_KEY_FILE is not set")
    p = Path(path)
    try:
        return _load(str(p), p.stat().st_mtime)
    except FileNotFoundError:
        raise SigningUnavailable(f"Task signing key {path} does not exist")
    except PermissionError:
        raise SigningUnavailable(f"Task signing key {path} is not readable by the vCentral service")
    except ValueError as exc:
        raise SigningUnavailable(f"Task signing key {path} is invalid: {exc}")


def available() -> bool:
    try:
        private_key()
        return True
    except SigningUnavailable:
        return False


def public_key_b64() -> str | None:
    try:
        raw = private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except SigningUnavailable:
        return None
    return base64.b64encode(raw).decode()


def sign(payload: dict) -> dict:
    """Return {"payload": <exact JSON text>, "signature": <base64>}; the agent verifies the text as sent."""
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = private_key().sign(text.encode())
    return {"payload": text, "signature": base64.b64encode(sig).decode()}


def generate(path: str) -> None:
    p = Path(path)
    if p.exists():
        raise SystemExit(f"{path} already exists; not overwriting (agents trust the existing key)")
    pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption())
    p.write_bytes(pem)
    p.chmod(0o600)
    print(f"Wrote {path}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "generate":
        generate(sys.argv[2])
    else:
        raise SystemExit("usage: python -m app.signing generate PATH")
