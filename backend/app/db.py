from datetime import datetime, timezone
import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

log = logging.getLogger(__name__)

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp; every datetime in the database is naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
    _add_missing_columns()
    if engine.dialect.name == "postgresql":
        # Use TimescaleDB for metrics when the extension is available; plain tables otherwise.
        if _try_sql("CREATE EXTENSION IF NOT EXISTS timescaledb"):
            for table in ("vm_metrics", "vm_metrics_hourly", "agent_metrics", "agent_metrics_hourly"):
                _try_sql(f"SELECT create_hypertable('{table}', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")


# Columns added to existing tables after the first release. create_all() only creates missing tables,
# so databases from an earlier version get these with ALTER TABLE on startup.
ADDED_COLUMNS = [
    ("schedules", "variables", "JSON"),
    ("users", "pending_approval", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("vms", "critical", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("vms", "health", "VARCHAR(16) NOT NULL DEFAULT 'ok'"),
    ("vms", "problems", "JSON"),
    ("vms", "unmanaged_since", "TIMESTAMP"),
    ("agents", "platform", "VARCHAR(32)"),
    ("vms", "owner_suggestion", "VARCHAR(255)"),
    ("vms", "owner_suggestion_reason", "VARCHAR(512)"),
    ("vms", "owner_suggestion_confidence", "VARCHAR(8)"),
]


def _add_missing_columns() -> None:
    insp = inspect(engine)
    for table, column, ddl in ADDED_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            log.info("Upgraded schema: added %s.%s", table, column)


def _try_sql(sql: str) -> bool:
    """Run one statement in its own transaction so a failure cannot poison the others."""
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
        return True
    except Exception as exc:
        log.info("Optional database step skipped (%s): %s", sql.split("(")[0], exc)
        return False
