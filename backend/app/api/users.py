from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..models import ROLES, User
from ..security import check_password_policy, hash_password, require_admin
from .auth import user_out

router = APIRouter(prefix="/api/users", tags=["users"])

ROLE_PATTERN = "^(" + "|".join(ROLES) + ")$"


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    full_name: str | None = Field(None, max_length=128)
    email: EmailStr | None = None
    role: str = Field("viewer", pattern=ROLE_PATTERN)
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=128)
    email: EmailStr | None = None
    role: str | None = Field(None, pattern=ROLE_PATTERN)
    is_active: bool | None = None
    password: str | None = None  # admin reset; user must change it at next login


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True)))


@router.get("")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    if db.scalar(select(User).where(func.lower(User.username) == body.username.lower())):
        raise HTTPException(409, "Username already exists")
    check_password_policy(body.password)
    user = User(username=body.username, full_name=body.full_name, email=body.email, role=body.role,
                password_hash=hash_password(body.password), must_change_password=True)
    db.add(user)
    audit(db, request, admin.username, "create_user", target=body.username, role=body.role)
    db.commit()
    return user_out(user)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_unset=True)
    demoting = (changes.get("role") not in (None, "admin") or changes.get("is_active") is False)
    if user.role == "admin" and user.is_active and demoting and _active_admins(db) <= 1:
        raise HTTPException(400, "Cannot remove the last active admin")
    if changes.get("is_active") is True:
        user.pending_approval = False  # activating a sign-up approves it
    for field in ("full_name", "email", "role", "is_active"):
        if field in changes:
            setattr(user, field, changes[field])
    if changes.get("password"):
        check_password_policy(changes["password"])
        user.password_hash = hash_password(changes["password"])
        user.must_change_password = True
    if {"role", "is_active", "password"} & changes.keys():
        user.token_version += 1  # sign the user out everywhere
    audit(db, request, admin.username, "update_user", target=user.username,
          fields=sorted(k for k in changes if k != "password") + (["password"] if "password" in changes else []))
    db.commit()
    return user_out(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot delete your own account")
    if user.role == "admin" and user.is_active and _active_admins(db) <= 1:
        raise HTTPException(400, "Cannot delete the last active admin")
    db.delete(user)
    audit(db, request, admin.username, "delete_user", target=user.username)
    db.commit()


@router.post("/{user_id}/approve")
def approve_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Let a self-signed-up account in (it stays a Viewer unless an admin changes the role)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if not user.pending_approval:
        raise HTTPException(400, "This account is not waiting for approval")
    user.pending_approval, user.is_active = False, True
    audit(db, request, admin.username, "approve_signup", target=user.username, role=user.role)
    db.commit()
    return user_out(user)
