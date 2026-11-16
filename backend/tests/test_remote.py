"""Browser SSH/RDP: tickets, the guacd handshake and the relay, against a fake guacd."""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.db import SessionLocal
from app.main import app, bootstrap_admin
from app.models import VM, AuditLog
from app.remote import Parser, encode
from app.sync import run_sync

from .test_api import H, ready_admin
from .test_sync import FakeClient, make_vm


class FakeGuacd:
    """Speaks just enough of the guacd protocol; records what it was asked to connect to."""

    def __init__(self):
        self.connect_values, self.received, self.select = None, "", None
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.port = self.server.sockets[0].getsockname()[1]
        self.ready.set()
        self.loop.run_forever()

    async def _handle(self, reader, writer):
        parser, pending = Parser(), []

        async def nxt():
            while not pending:
                data = await reader.read(4096)
                if not data:
                    raise ConnectionError("closed")
                pending.extend(Parser.split(parser.feed(data)))
            return pending.pop(0)

        try:
            self.select = (await nxt())[1]
        except ConnectionError:
            return  # availability probe
        writer.write(encode("args", "VERSION_1_3_0", "hostname", "port", "username", "password", "font-size").encode())
        while (ins := await nxt())[0] != "connect":
            pass
        self.connect_values = ins[1:]
        # "ready", then an instruction split across two writes, with non-ASCII text (lengths count characters)
        whole = encode("ready", "$abc") + encode("name", "Grüße ✓ server") + encode("sync", "1")
        cut = len(whole.encode()) - 5
        writer.write(whole.encode()[:cut])
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(whole.encode()[cut:])
        await writer.drain()
        while data := await reader.read(4096):
            self.received += data.decode()

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)


@pytest.fixture
def setup(monkeypatch):
    bootstrap_admin()
    fake = FakeClient()
    fake.vms = [make_vm("a", "web-01", ip="10.0.0.5")]
    with SessionLocal() as db:
        run_sync(db, fake, get_settings())
        vm_id = db.query(VM).one().id
    guacd = FakeGuacd()
    monkeypatch.setattr(get_settings(), "guacd_port", guacd.port)
    with TestClient(app) as c:
        ready_admin(c)
        yield c, vm_id, guacd
    guacd.close()


def ticket(c, vm_id, **kw):
    body = {"protocol": "ssh", "host": "10.0.0.5", "username": "root", "password": "s3cret!", **kw}
    return c.post(f"/api/vms/{vm_id}/remote", headers=H, json=body)


def test_parser_handles_split_and_unicode():
    p = Parser()
    data = (encode("name", "Grüße ✓") + encode("sync", "5")).encode()
    assert p.feed(data[:7]) == ""
    out = p.feed(data[7:])
    assert Parser.split(out) == [["name", "Grüße ✓"], ["sync", "5"]]


def test_session(setup):
    c, vm_id, guacd = setup
    assert c.get(f"/api/vms/{vm_id}/remote").json()["addresses"] == ["10.0.0.5"]
    assert c.get("/api/remote/status").json()["available"] is True
    # Only the VM's own addresses: vCentral is not a general-purpose proxy.
    assert ticket(c, vm_id, host="10.0.0.99").status_code == 400

    t = ticket(c, vm_id).json()["ticket"]
    url = f"/api/remote/tunnel?ticket={t}"
    with c.websocket_connect(url, subprotocols=["guacamole"], headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_text() == encode("", "$abc")  # tunnel open
        got = ""
        while "sync" not in got:
            got += ws.receive_text()
        assert Parser.split(got) == [["name", "Grüße ✓ server"], ["sync", "1"]]
        ping = encode("", "ping", "1700000000000")
        ws.send_text(ping)
        assert ws.receive_text() == ping  # answered by vCentral, not forwarded
        ws.send_text(encode("key", "65", "1"))
        live = c.get("/api/remote/status").json()["sessions"]
        assert [(s["vm_name"], s["protocol"], s["login"]) for s in live] == [("web-01", "ssh", "root")]
    assert guacd.select == "ssh"
    assert guacd.connect_values == ["VERSION_1_3_0", "10.0.0.5", "22", "root", "s3cret!", "12"]

    # The ticket was single-use.
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(url, subprotocols=["guacamole"]) as ws:
            ws.receive_text()

    with SessionLocal() as db:
        actions = [(a.action, a.success) for a in db.query(AuditLog).filter(AuditLog.action.like("remote.%"))]
        assert actions == [("remote.connect", True), ("remote.disconnect", True)]
        assert "s3cret" not in str([a.detail for a in db.query(AuditLog)])  # never logged


def test_session_rules(setup):
    c, vm_id, _ = setup
    t = ticket(c, vm_id).json()["ticket"]
    # Another site cannot use the admin's browser to open the tunnel.
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/api/remote/tunnel?ticket={t}", subprotocols=["guacamole"],
                                 headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    # Logged-out browsers cannot open a session even with a valid ticket.
    t = ticket(c, vm_id).json()["ticket"]
    c.post("/api/auth/logout", headers=H)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/api/remote/tunnel?ticket={t}", subprotocols=["guacamole"]) as ws:
            ws.receive_text()
    assert ticket(c, vm_id).status_code == 401


def test_guacd_down(setup, monkeypatch):
    c, vm_id, _ = setup
    monkeypatch.setattr(get_settings(), "guacd_port", 1)
    r = ticket(c, vm_id)
    assert r.status_code == 503 and "guacd" in r.json()["detail"]
