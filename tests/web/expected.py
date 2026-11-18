"""Write the CLI's answers for every demo person as JSON (the reference for check.mjs)."""
import json
import sys
from pathlib import Path

from offgraph import engine
from offgraph.cli import _checklist, _reach

store = Path(sys.argv[1])
people = engine.run(store, "MATCH (p:Person) RETURN p.email AS email ORDER BY email")
out = {}
for p in people:
    e = p["email"]
    w = engine.whatif(store, [e])
    from offgraph import report
    import re
    svg = report.svg(e, _reach(store, e), engine.rows(store, "orphans", e),
                     [r["group_name"] for r in engine.rows(store, "sole_admin", e)],
                     [r["group_name"] for r in engine.rows(store, "memberships", e)])
    import html
    out[e] = {"svg_text": sorted(html.unescape(t) for t in re.findall(r">([^<>]+)</text>", svg)),
              "reach": [[r["resource"], r["access"], r["via"], r["path"]] for r in _reach(store, e)],
              "checklist": _checklist(store, e),
              "whatif": {"already_left": w["already_left"], "unowned": [r["resource"] for r in w["unowned"]],
                         "no_admin_groups": w["no_admin_groups"], "no_editor": [r["resource"] for r in w["no_editor"]],
                         "no_admin": [r["resource"] for r in w["no_admin"]],
                         "seats_usd": w["seats_usd"]}}
json.dump(out, sys.stdout, indent=1)
