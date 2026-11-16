"""Background worker: vCenter sync, "Sync now" requests, VM health checks, metric roll-ups.

Run with:  python -m app.worker
"""
import logging
import threading
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from datetime import datetime, timedelta

from .config import get_settings
from .db import SessionLocal, init_db, iso, utcnow
from .owners import suggest_owners
from .health import evaluate_health
from .onboarding import run_digest, update_unmanaged
from .tasks import expire_runs, run_due_schedules
from .runtime import runtime
from .state import get_state, set_state
from .sync import prune, record_sync_failure, rollup_hourly, run_sync
from .vcenter import VCenterClient, describe_error

log = logging.getLogger("vcentral.worker")

settings = get_settings()
client = VCenterClient(settings)
# Sync and health checks both update VM rows; never run them at the same time.
_vm_lock = threading.Lock()
_last_sync_started = 0.0


def sync_job(reason: str = "scheduled") -> None:
    global _last_sync_started
    if not _vm_lock.acquire(blocking=False):
        return
    try:
        _last_sync_started = time.monotonic()
        with SessionLocal() as db:
            try:
                summary = run_sync(db, client, settings)
                log.info("Sync (%s) ok: %s", reason, summary)
            except Exception as exc:
                log.exception("Sync (%s) failed", reason)
                client.disconnect()  # force a fresh session next time
                record_sync_failure(db, describe_error(exc, settings))
    finally:
        _vm_lock.release()


def owners_job(reason: str = "scheduled") -> None:
    """Suggest owners for VMs whose creator is unknown. Reads vCenter events, so it runs hourly or on request."""
    try:
        activity = client.user_activity()  # outside the lock: reading events can take a while
    except Exception as exc:
        log.warning("Owner suggestions skipped: %s", describe_error(exc, settings))
        client.disconnect()
        return
    with _vm_lock:
        with SessionLocal() as db:
            result = suggest_owners(db, activity, settings)
            set_state(db, "owner_scan", {**result, "finished_at": iso(utcnow()), "reason": reason})
            db.commit()
            log.info("Owner suggestions (%s): %s", reason, result)


def tick_job() -> None:
    """Every few seconds: run a sync when one is requested from the UI or the interval has passed."""
    with SessionLocal() as db:
        scan = get_state(db, "owner_scan_request")
        if scan.get("pending"):
            set_state(db, "owner_scan_request", {**scan, "pending": False})
            db.commit()
    if scan.get("pending"):
        owners_job(reason=f"requested by {scan.get('by', 'unknown')}")
    with SessionLocal() as db:
        req = get_state(db, "sync_request")
        if req.get("pending"):
            set_state(db, "sync_request", {**req, "pending": False})
            db.commit()
            sync_job(reason=f"requested by {req.get('by', 'unknown')}")
            return
    if time.monotonic() - _last_sync_started >= runtime()["sync_interval_seconds"] or _last_sync_started == 0:
        sync_job()


def health_job() -> None:
    if not _vm_lock.acquire(blocking=False):
        return  # a sync is running; check again in a few seconds
    try:
        with SessionLocal() as db:
            changes = evaluate_health(db)
            if changes:
                log.info("Health: %d problem change(s)", changes)
            update_unmanaged(db)
    except Exception:
        log.exception("Health check failed")
    finally:
        _vm_lock.release()


def digest_job() -> None:
    """Hourly check; the digest itself goes out only as often as the policy says."""
    try:
        with SessionLocal() as db:
            result = run_digest(db)
            if result["sent"] or result["failed"]:
                log.info("Onboarding digest: %s", result)
    except Exception:
        log.exception("Sending the onboarding digest failed")


def tasks_job() -> None:
    """Start scheduled tasks that are due, and expire task runs no agent picked up."""
    try:
        with SessionLocal() as db:
            started = run_due_schedules(db)
            expired = expire_runs(db)
            if started or expired:
                log.info("Tasks: %d scheduled task(s) started, %d run(s) expired or lost", started, expired)
    except Exception:
        log.exception("Task housekeeping failed")


def rollup_job() -> None:
    with SessionLocal() as db:
        hours = rollup_hourly(db)
        if hours:
            log.info("Rolled up %d hour(s) of metrics", hours)


def prune_job() -> None:
    with SessionLocal() as db:
        prune(db, settings)
        log.info("Pruned old metrics and history")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    init_db()
    scheduler = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    scheduler.add_job(tick_job, "interval", seconds=5, id="tick")
    scheduler.add_job(health_job, "interval", seconds=10, id="health")
    scheduler.add_job(digest_job, "cron", minute=15, id="digest")
    scheduler.add_job(tasks_job, "cron", second=5, id="tasks")  # every minute
    scheduler.add_job(owners_job, "interval", hours=1, id="owners",
                      next_run_time=datetime.now().astimezone() + timedelta(seconds=90))
    scheduler.add_job(rollup_job, "cron", minute=5, id="rollup")
    scheduler.add_job(prune_job, "cron", hour=3, minute=30, id="prune")
    log.info("Worker started; vCenter sync every %ss (changeable on the Settings page)", runtime()["sync_interval_seconds"])
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
