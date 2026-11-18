"""`offgraph audit`: every leaver at once, answered by the engine. The same
numbers as the per-person commands, gathered for the whole company."""
from __future__ import annotations

from html import escape
from pathlib import Path

from . import engine, graph, report


def summary(store: Path) -> dict:
    from .cli import _checklist, _reach
    leavers = []
    for p in sorted(engine.rows(store, "leavers"), key=lambda r: r["email"]):
        e = p["email"]
        reach = _reach(store, e)
        orphans = sorted(engine.rows(store, "orphans", e), key=lambda r: r["resource"])
        my_seats = engine.rows(store, "my_seats", e)
        leavers.append({
            "email": e, "name": p["name"], "team": p["team"],
            "orphans": {r["resource"]: r["suggested_owner"] for r in orphans},
            "orphan_rows": orphans,
            "sole_admin": sorted(r["group_name"] for r in engine.rows(store, "sole_admin", e)),
            "can_edit": sorted({r["resource"] for r in reach if r["access"] in ("edit", "admin")}),
            "seats_usd": round(sum(float(r["monthly_usd"]) for r in my_seats), 2),
            "steps": _checklist(store, e),
            "reach": reach,
            "memberships": [r["group_name"] for r in engine.rows(store, "memberships", e)],
            "seat_rows": my_seats,
        })
    seats = engine.rows(store, "seats")
    return {"leavers": leavers,
            "no_admin_groups": sorted(r["group_name"] for r in engine.rows(store, "no_admin_groups")),
            "seats": len(seats), "seats_usd": round(sum(float(r["monthly_usd"]) for r in seats), 2)}


def plain(s: dict) -> dict:
    """The JSON shape (also what oracle.audit computes)."""
    keep = ("email", "orphans", "sole_admin", "can_edit", "seats_usd", "steps")
    return {"leavers": [{k: l[k] for k in keep} for l in s["leavers"]],
            "no_admin_groups": s["no_admin_groups"], "seats": s["seats"], "seats_usd": s["seats_usd"]}


def text(s: dict) -> str:
    n, k, g = len(s["leavers"]), s["seats"], len(s["no_admin_groups"])
    out = [f"{n} {report._n(n, 'person', 'people')} marked as left; {k} paid {report._n(k, 'seat', 'seats')} "
           f"still held, {s['seats_usd']:.2f} USD a month; {g} {report._n(g, 'group', 'groups')} with no active admin", ""]
    for l in s["leavers"]:
        out.append(f"{l['email']}  ({l['team']})")
        owned = ", ".join(f"{r} -> {w or 'nobody active'}" for r, w in l["orphans"].items())
        out.append(f"  owns alone: {owned or 'nothing'}")
        out.append(f"  admin with no other active admin, of: {', '.join(l['sole_admin']) or 'no group'}")
        out.append(f"  can still edit through grants: {len(l['can_edit'])} resources; seats {l['seats_usd']:.2f} USD a month")
        out.append(f"  first step: {l['steps'][0]}")
    out += ["", f"groups with no active admin: {', '.join(s['no_admin_groups']) or 'none'}"]
    return "\n".join(out)


def html(s: dict) -> str:
    rows = "".join(
        f"<tr><td><a href='#{escape(l['email'])}'>{escape(l['email'])}</a></td><td>{escape(l['team'])}</td>"
        f"<td>{len(l['orphans'])}</td><td>{escape(', '.join(l['sole_admin']) or '—')}</td>"
        f"<td>{len(l['can_edit'])}</td><td>{l['seats_usd']:.2f}</td></tr>" for l in s["leavers"])
    sections = "".join(
        f"<h2 id='{escape(l['email'])}'>{escape(l['email'])}</h2>"
        f"<div class='tw'>{report.svg(l['email'], l['reach'], l['orphan_rows'], l['sole_admin'], l['memberships'])}</div>"
        f"<ol>{''.join(f'<li>{escape(x)}</li>' for x in l['steps'])}</ol>" for l in s["leavers"])
    n_orph = sum(len(l["orphans"]) for l in s["leavers"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offboarding audit</title><style>{report.CSS}</style></head><body><main>
<h1>Offboarding audit: everyone marked as left</h1>
<p class="mut">Answered by Cypher queries on a local Relate graph. Nothing left this machine.</p>
<div class="kpis">
<div class="kpi"><b>{len(s['leavers'])}</b>people marked as left</div>
<div class="kpi"><b>{n_orph}</b>{report._n(n_orph, "resource", "resources")} only a leaver owns</div>
<div class="kpi"><b>{len(s['no_admin_groups'])}</b>{report._n(len(s['no_admin_groups']), "group", "groups")} with no active admin</div>
<div class="kpi"><b>{s['seats_usd']:.2f} USD</b>a month in {s['seats']} paid seats</div>
</div>
<p class="hint mut">Swipe the table and the graphs sideways to see all of them.</p>
<div class='tw'><table><tr><th>Leaver</th><th>Team</th><th>Owns alone</th><th>Admin, no other active admin left</th><th>Can still edit through grants</th><th>Seats, USD a month</th></tr>{rows}</table></div>
<p class="mut">Groups with no active admin: {escape(', '.join(s['no_admin_groups']) or 'none')}.</p>
<p class="legend mut"><span>● Each graph reads left to right: person, the groups they are in, the groups those nest under, what the grant opens.</span>
<span style="color:var(--risk)">■ only they own it · red outline: no other active admin remains in that group</span></p>
{sections}
</main></body></html>
"""
