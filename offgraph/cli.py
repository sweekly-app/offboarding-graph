"""offgraph: load SaaS exports into Relate and ask what a leaver still has."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import demo, engine, report
from .data import ExportError, norm_email, read_prices
from .data import load as load_exports

NO_OWNER = "a new owner (nobody active can edit it)"


class NoSuchPerson(LookupError):
    pass


def _parser() -> argparse.ArgumentParser:
    # --store and --json are accepted before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=argparse.SUPPRESS,
                        help="Relate store: a single .rl file (default ./offgraph.rl) or a directory")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print JSON")
    ap = argparse.ArgumentParser(prog="offgraph", description=__doc__, parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", parents=[common], help="write the seeded demo exports")
    d.add_argument("folder", type=Path)
    d.add_argument("--seed", type=int, default=7)
    l = sub.add_parser("load", parents=[common],
                       help="load a folder of exports into the store (prices from prices.csv there, else the demo prices)")
    l.add_argument("folder", type=Path)
    for name, text in (("reach", "every resource the person can reach, and through which group"),
                       ("orphans", "resources only they own, with a suggested new owner"),
                       ("sole-admin", "groups whose only active admin they are"),
                       ("checklist", "the offboarding steps in a safe order")):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("email")
    sub.add_parser("seats", parents=[common], help="paid seats still held by people marked as left")
    cy = sub.add_parser("cypher", parents=[common], help="run your own read-only Cypher on the store")
    cy.add_argument("statement")
    rp = sub.add_parser("report", parents=[common], help="write one HTML page for a person: graph, risks, checklist")
    rp.add_argument("email")
    rp.add_argument("-o", "--out", type=Path, default=None, help="default: report-<name>.html")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    store = Path(getattr(a, "store", "./offgraph.rl"))
    as_json = getattr(a, "json", False)
    try:
        return _run(a, store, as_json)
    except NoSuchPerson as e:
        print(f"offgraph: no person with email {e.args[0]} in {store}", file=sys.stderr)
    except ExportError as e:
        print(f"offgraph: the exports cannot be loaded as they are:\n{e}", file=sys.stderr)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"offgraph: {e}", file=sys.stderr)
    except Exception as e:  # engine errors (relate.Error) and anything unexpected, in one line
        print(f"offgraph: {type(e).__name__}: {e}", file=sys.stderr)
    return 2


def _person(store: Path, email: str) -> str:
    email = norm_email(email)
    if not engine.rows(store, "person", email):
        raise NoSuchPerson(email)
    return email


def _run(a, store: Path, as_json: bool) -> int:
    if a.cmd == "demo":
        print(json.dumps(demo.generate(a.folder, seed=a.seed)))
        return 0
    if a.cmd == "load":
        ex = load_exports(a.folder, read_prices(a.folder, demo.prices()))
        engine.load(store, ex)
        extra = f" ({ex.duplicates_removed} duplicate rows removed)" if ex.duplicates_removed else ""
        print(f"loaded {a.folder} into {store}{extra}")
        return 0
    if a.cmd == "cypher":
        out = engine.run(store, a.statement)
        print(json.dumps(out, indent=2, default=str) if as_json else "\n".join(
            "  ".join(f"{k}={v}" for k, v in r.items()) for r in out))
        return 0
    if a.cmd == "report":
        email = _person(store, a.email)
        out_file = a.out or Path(f"report-{email.split('@')[0]}.html")
        out_file.write_text(report.html(
            email,
            reach=engine.rows(store, "reach", email),
            orphans=engine.rows(store, "orphans", email),
            sole_admin=[r["group_name"] for r in engine.rows(store, "sole_admin", email)],
            seats=engine.rows(store, "my_seats", email),
            checklist=_checklist(store, email)), encoding="utf-8")
        print(f"wrote {out_file}")
        return 0
    if a.cmd == "checklist":
        steps = _checklist(store, _person(store, a.email))
        print(json.dumps(steps, indent=2) if as_json else "\n".join(
            f"{i}. {s}" for i, s in enumerate(steps, 1)))
        return 0
    qname = a.cmd.replace("-", "_")
    email = _person(store, a.email) if hasattr(a, "email") else None
    out = engine.rows(store, qname, email)
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        for r in out:
            print("  ".join(f"{k}={v}" for k, v in r.items()))
        if qname == "seats":
            total = sum(float(r["monthly_usd"]) for r in out)
            print(f"total: {total:.2f} USD a month")
    return 0


def _checklist(store: Path, email: str) -> list[str]:
    """Same order as oracle.checklist, answered by the engine."""
    steps = []
    for r in sorted(engine.rows(store, "orphans", email), key=lambda r: r["resource"]):
        steps.append(f"Transfer ownership of {r['resource']} to {r['suggested_owner'] or NO_OWNER}")
    for r in sorted(engine.rows(store, "sole_admin", email), key=lambda r: r["group_name"]):
        steps.append(f"Name a second admin for group {r['group_name']}, then remove {email} as admin")
    for r in sorted(engine.rows(store, "app_admin", email), key=lambda r: r["app"]):
        steps.append(f"Remove admin rights in {r['app']}")
    for r in sorted(engine.rows(store, "my_seats", email), key=lambda r: r["app"]):
        steps.append(f"Remove the paid {r['app']} seat ({float(r['monthly_usd']):.2f} USD a month)")
    for r in sorted({r["resource"] for r in engine.rows(store, "reach", email)
                     if r["via"] == "direct"}):
        steps.append(f"Revoke the direct grant on {r}")
    steps.append("Disable the account everywhere")
    return steps


if __name__ == "__main__":
    sys.exit(main())
