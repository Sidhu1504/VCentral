"""Browser SSH / RDP through guacd (Apache Guacamole's proxy daemon).

Browser (guacamole-common-js) <-WebSocket-> vCentral API <-TCP-> guacd (127.0.0.1:4822) <-SSH/RDP-> VM

The admin types the VM login for each session. It is kept in memory only until the connection is
made (at most TICKET_TTL seconds) and is never written to the database or logs.
"""
import asyncio
import codecs
import secrets
import threading
import time
from dataclasses import dataclass, field

TICKET_TTL = 60  # seconds between "Connect" and the browser opening the tunnel
DEFAULT_PORTS = {"ssh": 22, "rdp": 3389}


class GuacError(Exception):
    """Connection problem; `status` is a Guacamole status code the browser understands."""

    def __init__(self, message: str, status: int = 519):  # 519 = upstream not found
        super().__init__(message)
        self.status = status


# -- protocol ----------------------------------------------------------------------------------------
# An instruction is "LEN.VALUE,LEN.VALUE,...;" where LEN counts Unicode characters (code points).

def encode(*elements) -> str:
    return ",".join(f"{len(s)}.{s}" for s in (str(e) for e in elements)) + ";"


class Parser:
    """Splits a text stream into complete instructions (guacd may split them across TCP reads)."""

    def __init__(self):
        self.buf = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> str:
        """Add bytes; return the longest prefix of complete instructions (possibly empty)."""
        self.buf += self.decoder.decode(data)
        end, pos, n = 0, 0, len(self.buf)
        while pos < n:
            dot = self.buf.find(".", pos)
            if dot == -1:
                break
            try:
                length = int(self.buf[pos:dot])
            except ValueError:
                raise GuacError("Malformed data from guacd", 521)
            term = dot + 1 + length
            if term >= n:
                break
            if self.buf[term] == ";":
                end = term + 1
            elif self.buf[term] != ",":
                raise GuacError("Malformed data from guacd", 521)
            pos = term + 1
        out, self.buf = self.buf[:end], self.buf[end:]
        return out

    @staticmethod
    def split(text: str) -> list[list[str]]:
        """Parse complete instructions into lists of elements (opcode first)."""
        result, cur, pos = [], [], 0
        while pos < len(text):
            dot = text.index(".", pos)
            length = int(text[pos:dot])
            cur.append(text[dot + 1:dot + 1 + length])
            term = dot + 1 + length
            if text[term] == ";":
                result.append(cur)
                cur = []
            pos = term + 1
        return result


# -- tickets -----------------------------------------------------------------------------------------

@dataclass
class Ticket:
    user_id: int
    username: str
    vm_id: int
    vm_name: str
    protocol: str
    params: dict  # guacd connection parameters, including the password
    width: int
    height: int
    dpi: int
    timezone: str | None
    source_ip: str | None
    created: float = field(default_factory=time.monotonic)


class TicketStore:
    """Single-use, short-lived tickets (one API process; the native install runs a single uvicorn)."""

    def __init__(self):
        self._items: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    def add(self, t: Ticket) -> str:
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._purge()
            self._items[key] = t
        return key

    def take(self, key: str) -> Ticket | None:
        with self._lock:
            self._purge()
            return self._items.pop(key, None)

    def _purge(self):
        now = time.monotonic()
        for k in [k for k, t in self._items.items() if now - t.created > TICKET_TTL]:
            del self._items[k]


tickets = TicketStore()


@dataclass
class LiveSession:
    id: str
    username: str
    vm_id: int
    vm_name: str
    protocol: str
    host: str
    login: str
    started: float = field(default_factory=time.time)


live_sessions: dict[str, LiveSession] = {}


def connection_params(protocol: str, host: str, port: int, username: str, password: str,
                      domain: str | None = None, security: str = "any") -> dict:
    p = {"hostname": host, "port": str(port), "username": username, "password": password}
    if protocol == "ssh":
        p.update({"font-size": "12", "color-scheme": "gray-black", "terminal-type": "xterm-256color",
                  "scrollback": "2000"})
    else:
        p.update({"domain": domain or "", "security": security, "ignore-cert": "true",
                  "resize-method": "display-update", "enable-font-smoothing": "true",
                  "enable-wallpaper": "false", "disable-audio": "true"})
    return p


# -- guacd -------------------------------------------------------------------------------------------

async def probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def guacd_handshake(host: str, port: int, t: Ticket):
    """Open a guacd connection for the ticket.

    Returns (reader, writer, connection_id, parser, leftover) where leftover holds complete instructions
    that arrived right after "ready" and must be sent to the browser first."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
    except (OSError, asyncio.TimeoutError):
        raise GuacError("The remote access service (guacd) is not running on the vCentral server")
    parser = Parser()
    pending = ""  # complete instructions received but not yet consumed

    async def read_instruction(timeout=15) -> list[str]:
        nonlocal pending
        while not pending:
            data = await asyncio.wait_for(reader.read(65536), timeout)
            if not data:
                raise GuacError("guacd closed the connection")
            pending += parser.feed(data)
        end = _first_end(pending)
        first, pending = Parser.split(pending[:end])[0], pending[end:]
        return first

    try:
        writer.write(encode("select", t.protocol).encode())
        await writer.drain()
        args = await read_instruction()
        if args[0] == "error":
            raise GuacError(f"guacd: {args[1] if len(args) > 1 else 'error'}")
        if args[0] != "args":
            raise GuacError("Unexpected reply from guacd", 521)
        names = args[1:]
        writer.write((encode("size", t.width, t.height, t.dpi) + encode("audio") + encode("video")
                      + encode("image", "image/png", "image/jpeg", "image/webp")
                      + (encode("timezone", t.timezone) if t.timezone else "")).encode())
        values = [n if n.startswith("VERSION_") else t.params.get(n, "") for n in names]
        writer.write(encode("connect", *values).encode())
        await writer.drain()
        ready = await read_instruction(30)
        if ready[0] != "ready":
            raise GuacError(f"guacd: {ready[1] if len(ready) > 1 else ready[0]}")
        return reader, writer, ready[1] if len(ready) > 1 else "", parser, pending
    except asyncio.TimeoutError:
        writer.close()
        raise GuacError("guacd did not answer in time", 520)
    except Exception:
        writer.close()
        raise


def _first_end(text: str) -> int:
    pos = 0
    while True:
        dot = text.index(".", pos)
        term = dot + 1 + int(text[pos:dot])
        if text[term] == ";":
            return term + 1
        pos = term + 1
