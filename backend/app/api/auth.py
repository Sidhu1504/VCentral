from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db, iso, utcnow
from ..models import User
from ..security import (LoginThrottle, authenticate, check_password_policy, clear_session_cookie, client_ip,
                        get_current_user, hash_password, login_throttle, set_session_cookie,
                        verify_password)
from ..signup import get_signup_policy, notify_admins

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_out(user: User) -> dict:
    return {
        "id": user.id, "username": user.username, "full_name": user.full_name, "email": user.email,
        "role": user.role, "is_active": user.is_active, "must_change_password": user.must_change_password,
        "created_at": iso(user.created_at), "last_login_at": iso(user.last_login_at),
        "pending_approval": bool(user.pending_approval),
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    username = body.username.strip()
    wait = login_throttle.retry_after(username, ip)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).")
    user = authenticate(db, username, body.password)
    if user is None:
        pending = db.scalar(select(User).where(User.username == username, User.pending_approval.is_(True)))
        if pending is not None and verify_password(body.password, pending.password_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Your account is waiting for an admin to approve it. Try again once you hear back.")
        login_throttle.failed(username, ip)
        audit(db, request, username, "login", success=False)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    login_throttle.succeeded(username, ip)
    user.last_login_at = utcnow()
    audit(db, request, user.username, "login")
    db.commit()
    set_session_cookie(response, user)
    return user_out(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        user = get_current_user(request, db)
        audit(db, request, user.username, "logout")
        db.commit()
    except HTTPException:
        pass
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, response: Response,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        audit(db, request, user.username, "change_password", success=False)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be different")
    check_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # signs out other sessions
    audit(db, request, user.username, "change_password")
    db.commit()
    set_session_cookie(response, user)
    return user_out(user)


# -- self sign-up --------------------------------------------------------------------------------------

signup_throttle = LoginThrottle(max_failures=5, window=3600, lockout=3600)  # 5 sign-ups per IP per hour


class SignupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    full_name: str | None = Field(None, max_length=128)
    email: EmailStr | None = None
    password: str = Field(max_length=256)


@router.get("/signup")
def signup_options(db: Session = Depends(get_db)):
    """Shown on the sign-in page before login."""
    return get_signup_policy(db)


@router.post("/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response, background: BackgroundTasks,
           db: Session = Depends(get_db)):
    policy = get_signup_policy(db)
    if not policy["enabled"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is turned off. Ask an admin for an account.")
    ip = client_ip(request)
    if signup_throttle.retry_after("signup", ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-ups from this address. Try again later.")
    username = body.username.strip()
    if db.scalar(select(func.count()).select_from(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is taken")
    check_password_policy(body.password)
    signup_throttle.failed("signup", ip)
    approval = policy["requires_approval"]
    user = User(username=username, full_name=(body.full_name or "").strip() or None, email=body.email,
                password_hash=hash_password(body.password), role="viewer",  # never anything else
                is_active=not approval, pending_approval=approval)
    db.add(user)
    audit(db, request, username, "signup", target=username, role="viewer", needs_approval=approval)
    db.commit()
    if approval:
        background.add_task(notify_admins, user.username, user.full_name)
        return {"pending_approval": True}
    user.last_login_at = utcnow()
    db.commit()
    set_session_cookie(response, user)
    return {"pending_approval": False, "user": user_out(user)}
