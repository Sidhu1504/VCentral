"""Infrastructure page: the vCenter REST client (against a fake appliance) and the API's permissions."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra import ApplianceClient, ApplianceError
from app.main import app, bootstrap_admin

from .test_api import H, ready_admin

SERVICES = {"vpxd": {"state": "STARTED", "health": "HEALTHY", "startup_type": "AUTOMATIC"},
            "vsphere-ui": {"state": "STOPPED", "startup_type": "AUTOMATIC"},
            "foo-svc": {"state": "STARTED", "health": "DEGRADED", "startup_type": "MANUAL"}}


def fake_appliance(legacy=False, allow=True):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            calls.append(("POST", self.path))
            if self.path == "/api/session":
                return self.reply(404) if legacy else self.reply(201, "tok")
            if self.path == "/rest/com/vmware/cis/session":
                return self.reply(200, {"value": "tok"})
            if self.headers.get("vmware-api-session-id") != "tok":
                return self.reply(401)
            if not allow:
                return self.reply(403, {"messages": [{"default_message": "Unable to authorize user"}]})
            if self.path in ("/api/vcenter/services/vsphere-ui?action=restart", "/rest/vcenter/services/vsphere-ui/restart"):
                return self.reply(204)
            self.reply(404)

        def do_DELETE(self):
            calls.append(("DELETE", self.path))
            self.reply(200)

        def do_GET(self):
            calls.append(("GET", self.path))
            if self.headers.get("vmware-api-session-id") != "tok":
                return self.reply(401)
            if legacy and self.path == "/rest/vcenter/services":
                return self.reply(200, {"value": [{"key": k, "value": v} for k, v in SERVICES.items()]})
            if not legacy and self.path == "/api/vcenter/services":
                return self.reply(200, SERVICES)
            self.reply(404)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, calls


def client_for(srv):
    c = ApplianceClient(Settings(vcenter_host="x", vcenter_user="u", vcenter_password="p"))
    c.base = f"http://127.0.0.1:{srv.server_port}"
    return c


@pytest.mark.parametrize("legacy", [False, True])
def test_services_and_restart(legacy):
    srv, calls = fake_appliance(legacy=legacy)
    try:
        c = client_for(srv)
        svcs = c.services()
        by_id = {s["id"]: s for s in svcs}
        assert by_id["vpxd"] == {"id": "vpxd", "name": "vCenter Server", "state": "started", "health": "healthy",
                                 "startup": "automatic", "core": True}
        assert by_id["vsphere-ui"]["state"] == "stopped" and by_id["vsphere-ui"]["name"] == "vSphere Client"
        assert svcs[0]["id"] == "vpxd"  # core services first
        c.service_action("vsphere-ui", "restart")
        c.logout()
        assert c.legacy is legacy and calls[-1][0] == "DELETE"
    finally:
        srv.shutdown()


def test_not_allowed_gives_clear_message():
    srv, _ = fake_appliance(allow=False)
    try:
        with pytest.raises(ApplianceError) as e:
            client_for(srv).service_action("vsphere-ui", "stop")
        assert e.value.status == 403 and "SystemConfiguration.Administrators" in str(e.value)
        assert "Unable to authorize user" in str(e.value)
    finally:
        srv.shutdown()


def test_service_actions_admin_only():
    bootstrap_admin()
    with TestClient(app) as c:
        ready_admin(c)
        c.post("/api/users", headers=H, json={"username": "op2", "password": "OperatorPass1", "role": "operator"})
        with TestClient(app) as o:
            o.post("/api/auth/login", headers=H, json={"username": "op2", "password": "OperatorPass1"})
            o.post("/api/auth/change-password", headers=H,
                   json={"current_password": "OperatorPass1", "new_password": "OperatorPass2"})
            assert o.post("/api/infra/vcenter/services/vpxd/restart", headers=H).status_code == 403
        assert c.post("/api/infra/vcenter/services/vpxd/explode", headers=H).status_code == 422
