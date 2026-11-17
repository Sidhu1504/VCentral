"""Reports from the command line, e.g. for cron jobs or e-mail.

    python -m app.cli report                 # list the reports
    python -m app.cli report tools           # CSV to stdout
    python -m app.cli report usage --days 7 --format table
    python -m app.cli report tools -o /tmp/tools.csv

On a native install use scripts/report.sh, which loads /etc/vcentral/vcentral.env first.
"""
import argparse
import sys

from .db import SessionLocal
from .reports import PERIODS, REPORTS, run_report, to_csv


def _table(report: dict) -> str:
    cols = report["columns"]

    def text(v):
        if v is None:
            return ""
        return "; ".join(map(str, v)) if isinstance(v, list) else str(v)
    rows = [[text(r.get(c["key"])) for c in cols] for r in report["rows"]]
    widths = [min(max([len(c["title"])] + [len(r[i]) for r in rows]), 40) for i, c in enumerate(cols)]
    line = lambda cells: "  ".join(s[:w].ljust(w) for s, w in zip(cells, widths)).rstrip()  # noqa: E731
    out = [report["title"] + (f" (last {report['days']} days)" if report["days"] else ""),
           "  ".join(f"{s['label']}: {s['value']}" for s in report["summary"]), "",
           line([c["title"] for c in cols]), line(["-" * w for w in widths])]
    out += [line(r) for r in rows] or ["(nothing to report)"]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vcentral report", description="vCentral reports")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="print a report (CSV by default)")
    rp.add_argument("name", nargs="?", choices=list(REPORTS), help="report to run; leave out to list them")
    rp.add_argument("--days", type=int, default=30, help=f"period for reports that cover a period ({'/'.join(map(str, PERIODS))} on the web page)")
    rp.add_argument("--format", choices=["csv", "table"], default="csv")
    rp.add_argument("-o", "--output", help="write to this file instead of the screen")
    args = parser.parse_args(argv)

    if not args.name:
        for key, r in REPORTS.items():
            print(f"  {key:12} {r['title']}: {r['description']}")
        return 0
    with SessionLocal() as db:
        report = run_report(db, args.name, args.days)
    text = to_csv(report) if args.format == "csv" else _table(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8-sig" if args.format == "csv" else "utf-8", newline="") as f:
            f.write(text)
        print(f"{report['title']}: {len(report['rows'])} rows written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
