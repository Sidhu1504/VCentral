"""Remote access: browser SSH/RDP sessions (through guacd) and VMware Remote Console links."""
import asyncio
import logging
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import VM, Agent, User
from ..remote import (DEFAULT_PORTS, GuacError, LiveSession, Ticket, connection_params, encode, guacd_handshake,
                      live_sessions, probe, tickets)
from ..security import client_ip, get_current_user, require_operator

log = logging.getLogger("vcentral.remote")
router = APIRouter(tags=["remote"])


class SessionIn(BaseModel):
    protocol: str = Field(pattern="^(ssh|rdp)$")
    host: str = Field(max_length=45)
    port: int | None = Field(None, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field("", max_length=1024)
    domain: str | None = Field(None, max_length=256)  # RDP only
    security: str = Field("any", pattern="^(any|nla|tls|rdp)$")  # RDP only
    width: int = Field(1280, ge=320, le=7680)
    height: int = Field(720, ge=200, le=4320)
    dpi: int = Field(96, ge=48, le=300)
    timezone: str | None = Field(None, max_length=64)


def vm_addresses(db: Session, vm: VM) -> list[str]:
    """Addresses vCentral knows for this VM; sessions may only go to these (vCentral is not a general proxy)."""
    ips = [vm.primary_ip] + list(vm.ip_addresses or []) + list(vm.last_known_ips or [])
    for a in db.scalars(select(Agent).where(Agent.vm_id == vm.id, Agent.status == "active")):
        ips += list(a.ips or [])
    seen = []
    for ip in ips:
        if ip and ip not in seen:
            seen.append(ip)
    return seen


@router.get("/api/remote/status")
async def status(_: User = Depends(require_operator)):
    s = get_settings()
    available = s.remote_access_enabled and await probe(s.guacd_host, s.guacd_port)
    vc = s.vcenter_host + (f":{s.vcenter_port}" if s.vcenter_port != 443 else "") if s.vcenter_host else None
    return {"enabled": s.remote_access_enabled, "available": available, "vcenter": vc,
            "sessions": [{"id": x.id, "username": x.username, "vm_id": x.vm_id, "vm_name": x.vm_name,
                          "protocol": x.protocol, "host": x.host, "login": x.login,
                          "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x.started))}
                         for x in live_sessions.values()]}


@router.get("/api/vms/{vm_id}/remote")
def remote_targets(vm_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(404, "VM not found")
    return {"addresses": vm_addresses(db, vm), "suggested": "rdp" if vm.os_family == "windows" else "ssh",
            "moid": vm.moid}


@router.post("/api/vms/{vm_id}/remote")
async def start_session(vm_id: int, body: SessionIn, request: Request, user: User = Depends(require_operator)):
    s = get_settings()
    if not s.remote_access_enabled:
        raise HTTPException(403, "Remote access is turned off on this server (VCENTRAL_REMOTE_ACCESS_ENABLED)")
    with SessionLocal() as db:
        vm = db.get(VM, vm_id)
        if vm is None:
            raise HTTPException(404, "VM not found")
        if body.host not in vm_addresses(db, vm):
            raise HTTPException(400, "That address does not belong to this VM")
        vm_name = vm.name
    if not await probe(s.guacd_host, s.guacd_port):
        raise HTTPException(503, "The remote access service (guacd) is not running on the vCentral server. "
                                 "Re-run deploy/native/install.sh, or: sudo systemctl start guacd")
    port = body.port or DEFAULT_PORTS[body.protocol]
    params = connection_params(body.protocol, body.host, port, body.username, body.password, body.domain,
                               body.security)
    key = tickets.add(Ticket(user_id=user.id, username=user.username, vm_id=vm_id, vm_name=vm_name,
                             protocol=body.protocol, params=params, width=body.width, height=body.height,
                             dpi=body.dpi, timezone=body.timezone, source_ip=client_ip(request)))
    return {"ticket": key}


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # not a browser
    return urlsplit(origin).netloc == ws.headers.get("host")


def _audit(ws: WebSocket, username: str, action: str, target: str, success: bool = True, **detail):
    with SessionLocal() as db:
        audit(db, ws, username, action, target, success, **detail)
        db.commit()


@router.websocket("/api/remote/tunnel")
async def tunnel(ws: WebSocket, ticket: str = ""):
    s = get_settings()
    with SessionLocal() as db:
        try:
            user = get_current_user(ws, db)
        except HTTPException:
            user = None
        allowed = user is not None and user.role in ("admin", "operator") and not user.must_change_password
        user_id = user.id if user else None
    t = tickets.take(ticket) if ticket else None
    if not allowed or t is None or t.user_id != user_id or not _same_origin(ws):
        await ws.close(code=1008)  # policy violation: not logged in, wrong user, or ticket used/expired
        return
    await ws.accept(subprotocol="guacamole")
    host, login = t.params["hostname"], t.params.get("username", "")
    target = f"{t.vm_name} ({t.protocol.upper()} {host})"
    try:
        reader, writer, conn_id, parser, leftover = await guacd_handshake(s.guacd_host, s.guacd_port, t)
    except GuacError as e:
        _audit(ws, t.username, "remote.connect", target, False, protocol=t.protocol, login=login, error=str(e))
        await _fail(ws, str(e), e.status)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("remote session failed to start")
        _audit(ws, t.username, "remote.connect", target, False, protocol=t.protocol, login=login, error=str(e))
        await _fail(ws, "Could not start the session", 519)
        return
    t.params.clear()  # drop the password as soon as guacd has it

    sid = conn_id or str(uuid.uuid4())
    live_sessions[sid] = LiveSession(id=sid, username=t.username, vm_id=t.vm_id, vm_name=t.vm_name,
                                     protocol=t.protocol, host=host, login=login)
    _audit(ws, t.username, "remote.connect", target, protocol=t.protocol, login=login)
    started = time.monotonic()

    async def guacd_to_browser():
        await ws.send_text(encode("", sid))  # tells guacamole-common-js the tunnel is open
        if leftover:
            await ws.send_text(leftover)
        while True:
            data = await reader.read(65536)
            if not data:
                return
            text = parser.feed(data)
            if text:
                await ws.send_text(text)

    async def browser_to_guacd():
        while True:
            msg = await ws.receive_text()
            if msg.startswith("0.,"):  # tunnel-internal (keep-alive ping): answer it, guacd doesn't need it
                await ws.send_text(msg)
                continue
            writer.write(msg.encode())
            await writer.drain()

    tasks = [asyncio.create_task(guacd_to_browser()), asyncio.create_task(browser_to_guacd())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Bookkeeping first: nothing below an await is guaranteed to run if this handler is cancelled.
        for task in tasks:
            task.cancel()
        writer.close()
        live_sessions.pop(sid, None)
        _audit(ws, t.username, "remote.disconnect", target, protocol=t.protocol, login=login,
               duration_seconds=int(time.monotonic() - started))
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 - already closed by the browser
            pass


async def _fail(ws: WebSocket, message: str, status: int):
    try:
        await ws.send_text(encode("error", message, status))
        await ws.close(code=1000, reason=str(status))
    except Exception:  # noqa: BLE001
        pass
