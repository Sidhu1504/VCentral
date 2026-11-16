import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="vcentral-test-")
_agent_dir = os.path.join(_tmp, "agent-bin")
os.makedirs(_agent_dir)
with open(os.path.join(_agent_dir, "vcentral-agent-linux-amd64"), "wb") as f:
    f.write(b"\x7fELF fake agent")
with open(os.path.join(_agent_dir, "manifest.json"), "w") as f:
    f.write('{"version": "0.2.0", "files": {"linux-amd64": {"file": "vcentral-agent-linux-amd64", "sha256": "x"}}}')
os.environ["VCENTRAL_AGENT_DIR"] = _agent_dir

# Task signing key (Ed25519), as the install script creates it.
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

_key_path = os.path.join(_tmp, "task-signing.key")
with open(_key_path, "wb") as f:
    f.write(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
os.environ["VCENTRAL_TASK_SIGNING_KEY_FILE"] = _key_path
# SQLite by default; run against PostgreSQL with TEST_DATABASE_URL=postgresql+psycopg2://...
os.environ.update({
    "VCENTRAL_DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"),
    "VCENTRAL_SECRET_KEY": "x" * 40,
    "VCENTRAL_ADMIN_USERNAME": "admin",
    "VCENTRAL_ADMIN_PASSWORD": "InitialPass123",
    "VCENTRAL_COOKIE_SECURE": "false",
    "VCENTRAL_SUBNETS": "10.0.0.0/24",
    "VCENTRAL_STATIC_DIR": "",
    "VCENTRAL_ADMIN_EMAILS": "ops@example.com",
    "VCENTRAL_PUBLIC_URL": "https://vcentral.test",
})

from app.db import Base, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    init_db()
    import app.runtime
    app.runtime._cache = None
    from app.api.agent_api import enroll_throttle
    from app.security import login_throttle
    login_throttle._failures.clear()
    enroll_throttle._failures.clear()
    yield
