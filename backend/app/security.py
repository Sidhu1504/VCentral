"""Password hashing, session cookies (signed JWT), role checks and login throttling."""
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db, utcnow
from .models import User

COOKIE_NAME = "vcentral_session"
MIN_PASSWORD_LENGTH = 10
_settings = get_settings()


# -- passwords -------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# Used to spend the same time on unknown usernames as on wrong passwords.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode()) > 72:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password must be at most 72 bytes")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    return user


# -- sessions --------------------------------------------------------------------------------

def set_session_cookie(response: Response, user: User) -> None:
    now = utcnow()
    token = jwt.encode(
        {"sub": str(user.id), "ver": user.token_version, "iat": now,
         "exp": now + timedelta(hours=_settings.session_hours)},
        _settings.secret_key, algorithm="HS256",
    )
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=_settings.cookie_secure,
                        samesite="strict", max_age=_settings.session_hours * 3600, path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    try:
        payload = jwt.decode(token, _settings.secret_key, algorithms=["HS256"])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return user


def get_active_user(user: User = Depends(get_current_user)) -> User:
    """Any logged-in user who has completed a required password change."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
    return user


def require_role(*roles: str):
    def checker(user: User = Depends(get_active_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        return user
    return checker


require_viewer = require_role("admin", "operator", "viewer")
require_operator = require_role("admin", "operator")
require_admin = require_role("admin")


# -- login throttling ------------------------------------------------------------------------

class LoginThrottle:
    """Lock a username+IP pair for a while after repeated failures (per API process)."""

    def __init__(self, max_failures: int = 5, window: int = 900, lockout: int = 900):
        self.max_failures, self.window, self.lockout = max_failures, window, lockout
        self._failures: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _key(self, username: str, ip: str) -> tuple[str, str]:
        return (username.lower(), ip)

    def retry_after(self, username: str, ip: str) -> int:
        with self._lock:
            q = self._failures.get(self._key(username, ip))
            if not q:
                return 0
            now = time.monotonic()
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_failures:
                return max(0, int(self.lockout - (now - q[-1])))
            return 0

    def failed(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures[self._key(username, ip)].append(time.monotonic())

    def succeeded(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username, ip), None)


login_throttle = LoginThrottle()


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP; uvicorn runs with --proxy-headers only trusting the proxy.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
