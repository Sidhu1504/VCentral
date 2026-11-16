"""Time-series helpers for the metric graphs."""
from datetime import timedelta

from ..db import iso

MAX_POINTS = 720  # plenty for a chart; keeps 24h of 10-second samples light for the browser


def raw_points(rows, fields) -> list[dict]:
    return [{"_ts": r.ts, **{f: getattr(r, f) for f in fields}} for r in rows]


def downsample(points: list[dict], start, end, max_points: int = MAX_POINTS) -> list[dict]:
    """Average samples into at most max_points equal time buckets; returns points with an ISO "ts"."""
    if len(points) <= max_points:
        return [{"ts": iso(p["_ts"]), **{k: v for k, v in p.items() if k != "_ts"}} for p in points]
    bucket = (end - start).total_seconds() / max_points
    groups: dict[int, list[dict]] = {}
    for p in points:
        groups.setdefault(int((p["_ts"] - start).total_seconds() // bucket), []).append(p)
    out = []
    for idx in sorted(groups):
        rows = groups[idx]
        merged = {"ts": iso(start + timedelta(seconds=idx * bucket))}
        for key in rows[0]:
            if key == "_ts":
                continue
            vals = [r[key] for r in rows if r[key] is not None]
            merged[key] = round(sum(vals) / len(vals), 1) if vals else None
        out.append(merged)
    return out
