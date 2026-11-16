from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db, iso, utcnow
from ..models import User
from ..security import (authenticate, check_password_policy, clear_session_cookie, client_ip,
                        get_current_user, hash_password, login_throttle, set_session_cookie,
                        verify_password)

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
