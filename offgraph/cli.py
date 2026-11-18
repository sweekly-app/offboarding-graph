"""offgraph: load SaaS exports into Relate and ask what a leaver still has."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import demo, engine, graph, report
from .data import ExportError, norm_email, read_prices
from .data import load as load_exports

NO_OWNER = "a new owner (nobody active can edit it)"
MAX_PROBLEMS = 20  # load problems shown before "... and N more"
EMPTY = {  # one line instead of silence when a question has no answer
    "reach": "nothing: no group or direct grant gives {email} access to any resource",
    "orphans": "none: {email} is not the only owner of any resource",
    "sole_admin": "none: every group {email} administers has another active admin",
    "seats": "none: nobody marked as left still holds a paid seat",
    "single_admins": "none: every resource with an admin has at least two (app admins not counted)",
}


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
    d.add_argument("--force", action="store_true", help="overwrite CSV files already in the folder")
    gh = sub.add_parser("import-github", parents=[common],
                        help="turn JSON saved from GitHub's REST API (one organisation) into the CSV files")
    gh.add_argument("folder", type=Path, help="the saved JSON (see offgraph/github.py for the endpoints)")
    gh.add_argument("--logins", type=Path, required=True, help="CSV with login,email")
    gh.add_argument("--repo-owners", type=Path, default=None, help="optional CSV with repo,owner")
    gh.add_argument("--people", type=Path, default=None, help="optional people.csv to copy into the output")
    gh.add_argument("--prices", type=Path, default=None, help="optional prices.csv to copy into the output")
    gh.add_argument("-o", "--out", type=Path, required=True, help="an empty folder for the CSV files")
    pb = sub.add_parser("import-peribolos", parents=[common],
                        help="turn a Peribolos config (GitHub access kept as code, e.g. kubernetes/org) into the CSV files")
    pb.add_argument("source", type=Path, help="the org's folder (org.yaml plus */teams.yaml) or one YAML file")
    pb.add_argument("--org", default=None, help="the organisation, when the file holds several (orgs: form)")
    pb.add_argument("-o", "--out", type=Path, required=True, help="an empty folder for the CSV files")
    l = sub.add_parser("load", parents=[common],
                       help="load a folder of exports into the store (prices from prices.csv there, else the demo prices)")
    l.add_argument("folder", type=Path)
    for name, text in (("reach", "every resource the person can reach, and through which group"),
                       ("orphans", "resources only they own, with a suggested new owner"),
                       ("sole-admin", "groups they administer where no other active admin remains"),
                       ("checklist", "the offboarding steps in a safe order")):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("email")
    sub.add_parser("seats", parents=[common], help="paid seats still held by people marked as left")
    sub.add_parser("single-admins", parents=[common], help="resources whose admin access rests on one active "
                   "person, through groups or directly (app admins not counted)")
    wi = sub.add_parser("whatif", parents=[common], help="what breaks if these people are removed: simulated "
                        "in a Relate transaction on a copy of the store, then rolled back")
    wi.add_argument("emails", nargs="+")
    au = sub.add_parser("audit", parents=[common], help="everyone marked as left at once: what they own alone, "
                        "the groups left with no other active admin, what they can still edit, seats")
    au.add_argument("-o", "--out", type=Path, default=None, help="also write one HTML page (graph per leaver)")
    cy = sub.add_parser("cypher", parents=[common], help="run your own read-only Cypher on the store")
    cy.add_argument("statement")
    cy.add_argument("--limit", type=int, default=50, help="rows to print (0: all; default 50)")
    sub.add_parser("mcp", parents=[common],
                   help="serve the answers as read-only tools to an AI assistant (MCP over stdin/stdout)")
    wb = sub.add_parser("web", parents=[common], help="write one HTML page that runs the questions in the "
                        "browser on Relate's WebAssembly build, with the exports embedded")
    wb.add_argument("folder", type=Path, help="a folder of exports (as for load)")
    wb.add_argument("-o", "--out", type=Path, default=Path("offgraph.html"))
    wb.add_argument("--title", default="your company", help="who the exports belong to, in the intro")
    wb.add_argument("--name", default="your company", help="the company's short name, next to its figures")
    wb.add_argument("--start", default=None, help="the person shown first")
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
        print(f"offgraph: no person with email or login {e.args[0]} in {store}", file=sys.stderr)
    except ExportError as e:
        lines = str(e).splitlines()
        if len(lines) > MAX_PROBLEMS:
            lines = lines[:MAX_PROBLEMS] + [f"... and {len(lines) - MAX_PROBLEMS} more problems"]
        print("offgraph: the exports cannot be loaded as they are:\n" + "\n".join(lines), file=sys.stderr)
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
        taken = [n for n in demo.FILES if (a.folder / n).exists()]
        if taken and not a.force:
            raise ValueError(f"{a.folder} already has {', '.join(taken)}; pick another folder "
                             "or add --force to overwrite them with the demo company")
        print(json.dumps(demo.generate(a.folder, seed=a.seed)))
        return 0
    if a.cmd == "import-github":
        from . import github
        counts = github.write(github.convert(a.folder, a.logins, a.repo_owners), a.out,
                              {"people.csv": a.people, "prices.csv": a.prices})
        print(json.dumps(counts))
        return 0
    if a.cmd == "import-peribolos":
        from . import peribolos
        print(json.dumps(peribolos.write(peribolos.convert(a.source, a.org), a.out)))
        return 0
    if a.cmd == "load":
        ex = load_exports(a.folder, read_prices(a.folder, demo.prices()))
        engine.load(store, ex)
        extra = f" ({ex.duplicates_removed} duplicate rows removed)" if ex.duplicates_removed else ""
        print(f"loaded {a.folder} into {store}{extra}")
        return 0
    if a.cmd == "whatif":
        emails = [_person(store, e) for e in a.emails]
        w = engine.whatif(store, emails)
        if as_json:
            print(json.dumps(w, indent=2))
            return 0
        def ids(rows):
            return ", ".join(f"{r['resource']} ({r['title']})" for r in rows) or "none"
        print(f"If {', '.join(emails)} {'is' if len(emails) == 1 else 'are'} removed "
              "(simulated in a Relate transaction on a copy of the store, then rolled back):")
        print(f"  resources left with no active owner: {ids(w['unowned'])}")
        print(f"  groups left with no active admin: {', '.join(w['no_admin_groups']) or 'none'}")
        print(f"  resources nobody active can edit any more: {ids(w['no_editor'])}")
        print(f"  resources left with no admin (app admins not counted): {ids(w['no_admin'])}")
        print(f"  paid seats to remove: {len(w['seats'])}, {w['seats_usd']:.2f} USD a month")
        for e in w["already_left"]:
            print(f"  {e} is already marked as left, so removing them changes nothing here; "
                  f"what they still leave behind: offgraph report {e} (and offgraph seats)")
        return 0
    if a.cmd == "audit":
        from . import audit
        s = audit.summary(store)
        if a.out:  # write first, so a bad path fails before anything is printed
            try:
                a.out.write_text(audit.html(s), encoding="utf-8")
            except OSError as e:
                raise ValueError(f"cannot write {a.out}: {e.strerror}") from None
        print(json.dumps(audit.plain(s), indent=2) if as_json else audit.text(s))
        if a.out:
            print(f"wrote {a.out}", file=sys.stderr if as_json else sys.stdout)
        return 0
    if a.cmd == "web":
        from . import web
        ex = load_exports(a.folder, read_prices(a.folder, demo.prices()))
        start = norm_email(a.start) if a.start else None
        if start and start not in {p["email"] for p in ex.people}:
            raise NoSuchPerson(start)
        a.out.write_text(web.page(ex, a.title, start, Path(__file__).parent.parent / "examples.cypher", a.name),
                         encoding="utf-8")
        print(f"wrote {a.out}")
        return 0
    if a.cmd == "mcp":
        from . import mcp
        return mcp.serve(store)
    if a.cmd == "cypher":
        try:
            out = engine.run(store, a.statement)
        except Exception as e:
            if "read-only" in str(e).lower() or "read only" in str(e).lower():
                raise ValueError("cypher is read-only: this statement would change the store "
                                 "(rebuild it with `offgraph load` instead)") from None
            raise
        if as_json:
            print(json.dumps([{k: plain(v) for k, v in r.items()} for r in out], indent=2, default=str))
            return 0
        shown = out if a.limit <= 0 else out[: a.limit]
        for r in shown:
            print("  ".join(f"{k}={show(v)}" for k, v in r.items()))
        if len(out) > len(shown):
            print(f"... {len(out) - len(shown)} more rows (--limit 0 shows all)")
        if not out:
            print("(no rows)")
        return 0
    if a.cmd == "report":
        email = _person(store, a.email)
        out_file = a.out or Path(f"report-{email.split('@')[0]}.html")
        out_file.write_text(report.html(
            email,
            reach=_reach(store, email),
            orphans=engine.rows(store, "orphans", email),
            sole_admin=[r["group_name"] for r in engine.rows(store, "sole_admin", email)],
            seats=engine.rows(store, "my_seats", email),
            memberships=[r["group_name"] for r in engine.rows(store, "memberships", email)],
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
    out = _reach(store, email) if qname == "reach" else engine.rows(store, qname, email)
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        for r in out:
            print("  ".join(f"{k}={' > '.join(v) if k == 'path' else v}" for k, v in r.items()))
        if not out:
            print(EMPTY[qname].format(email=email))
        if qname == "single_admins" and out:
            print(f"{len(out)} resources rest on one admin")
        if qname == "seats":
            total = sum(float(r["monthly_usd"]) for r in out)
            print(f"total: {total:.2f} USD a month")
    return 0


def _reach(store: Path, email: str) -> list[dict]:
    """The reach rows, each with `path`: the shortest chain of groups from one the
    person is a member of up to the group holding the grant (["direct"] for a
    direct grant). Sorted by resource, then via."""
    chains = graph.best_chains(engine.rows(store, "paths", email))
    out = []
    for r in engine.rows(store, "reach", email):
        path = ["direct"] if r["via"] == "direct" else chains.get((r["resource"], r["via"]), [r["via"]])
        out.append({**r, "path": path})
    return sorted(out, key=lambda r: (r["resource"], r["via"]))


def plain(v):
    """Relate values as JSON-friendly data: nodes, relationships and paths as dicts."""
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if hasattr(v, "nodes") and hasattr(v, "relationships"):
        return {"nodes": [plain(n) for n in v.nodes], "relationships": [plain(r) for r in v.relationships]}
    if hasattr(v, "labels") and hasattr(v, "properties"):
        return {"labels": list(v.labels), **dict(v.properties)}
    if hasattr(v, "type") and hasattr(v, "properties"):
        return {"type": v.type, **dict(v.properties)}
    return v


def show(v) -> str:
    """Relate values in Cypher-like text: (:Person {email: ...}), [:MEMBER_OF {role: ...}]."""
    def props(d) -> str:
        return " {" + ", ".join(f"{k}: {x!r}" for k, x in dict(d).items()) + "}" if dict(d) else ""
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(show(x) for x in v) + "]"
    if hasattr(v, "nodes") and hasattr(v, "relationships"):
        parts = [show(v.nodes[0])] if v.nodes else []
        for prev, rel, node in zip(v.nodes, v.relationships, v.nodes[1:]):
            fwd = getattr(rel, "start_node", prev.id) == prev.id
            parts += [f"-{show(rel)}->" if fwd else f"<-{show(rel)}-", show(node)]
        return "".join(parts)
    if hasattr(v, "labels") and hasattr(v, "properties"):
        return "(" + "".join(f":{l}" for l in v.labels) + props(v.properties) + ")"
    if hasattr(v, "type") and hasattr(v, "properties"):
        return f"[:{v.type}{props(v.properties)}]"
    return str(v)


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
