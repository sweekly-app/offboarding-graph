"""One self-contained HTML page for one leaver: the reach graph, the risks and
the checklist. No scripts, no external files; open it in any browser."""
from __future__ import annotations

from html import escape


def _short(t: str, n: int = 20) -> str:
    return t if len(t) <= n else t[: n - 1] + "…"


def svg(email: str, reach: list[dict], orphans: list[dict], sole_admin: list[str] = (),
        memberships: list[str] = ()) -> str:
    """Left to right, the way access flows: the leaver, the groups they are a member
    of, the groups those nest under (one column per CHILD_OF hop), then the
    resources. Edges carry their relationship type; the leaver's only-admin groups
    and only-owner resources are marked."""
    orphan_ids = {o["resource"] for o in orphans}
    titles = {o["resource"]: o.get("title") or o["resource"] for o in orphans}
    titles.update({r["resource"]: r.get("title") or r["resource"] for r in reach})
    chains = [r.get("path") or [r["via"]] for r in reach if r["via"] != "direct"]
    depth: dict[str, int] = {}
    for ch in chains:
        for i, g in enumerate(ch, 1):
            depth[g] = min(depth.get(g, i), i)
    for g in sole_admin:
        depth.setdefault(g, 1)
    member_of = set(memberships) | {ch[0] for ch in chains} | set(sole_admin)
    D = max(depth.values(), default=0)
    COL, RW, ROW = 175, 160, 46
    GAP = 60  # extra room between the leaver and the first groups
    rx = 70 + GAP + COL * (D + 1) + RW / 2
    W = int(rx + RW / 2 + 20)
    # resource rows: orphans first, then by the depth and name of the granting group
    via_of: dict[str, tuple] = {}
    for r in reach:
        k = (0, "") if r["via"] == "direct" else (depth.get(r["via"], 1), r["via"])
        via_of[r["resource"]] = min(via_of.get(r["resource"], k), k)
    resources = sorted(set(via_of) | orphan_ids,
                       key=lambda x: (x not in orphan_ids, via_of.get(x, (0, "")), x))
    H = max(len(resources) * ROW + 40, 200)
    ry = {res: 20 + ROW / 2 + i * ROW for i, res in enumerate(resources)}
    # a group sits level with what it leads to (resources, then the groups above it)
    targets: dict[str, list[float]] = {g: [] for g in depth}
    for r in reach:
        if r["via"] != "direct":
            targets.setdefault(r["via"], []).append(ry[r["resource"]])
    gy: dict[str, float] = {}
    up: dict[str, set[str]] = {}
    for ch in chains:
        for a, b in zip(ch, ch[1:]):
            up.setdefault(a, set()).add(b)
    for d in range(D, 0, -1):
        col = sorted(g for g in depth if depth[g] == d)
        for g in col:
            ys = targets.get(g, []) + [gy[b] for b in up.get(g, ()) if b in gy]
            gy[g] = sum(ys) / len(ys) if ys else H - 30
        y = -1e9  # keep 52 px between groups in a column
        for g in sorted(col, key=lambda g: (gy[g], g)):
            gy[g] = y = max(gy[g], y + 52)
        H = max(H, int(y + 40))
    gx = {g: 70 + GAP + COL * d for g, d in depth.items()}
    mx, my = 70.0, H / 2
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Access graph for {escape(email)}">',
           '<defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
           'orient="auto"><path d="M0 0L8 4L0 8z" class="arh"/></marker></defs>']

    def edge(x1, y1, x2, y2, cls, label, at=0.5, bend=False):
        lx, ly = x1 + (x2 - x1) * at, y1 + (y2 - y1) * at - 4
        if bend:  # rise from the leaver first, then run level with the resource, over the groups
            out.append(f'<path class="e {cls}" fill="none" d="M{x1:.1f} {y1:.1f} Q{x1:.1f} {y2:.1f} '
                       f'{x2:.1f} {y2:.1f}" marker-end="url(#ar)"/>')
            lx, ly = x2 - 90, y2 - 5
        else:
            out.append(f'<line class="e {cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                       f'marker-end="url(#ar)"/>')
        if label:
            out.append(f'<text class="el {cls}" x="{lx:.1f}" y="{ly:.1f}">{escape(label)}</text>')
    seen = set()
    for g in sorted(member_of & set(depth)):
        edge(mx + 40, my, gx[g] - 64, gy[g], "mem", "MEMBER_OF", 0.5)
    for ch in chains:
        for a, b in zip(ch, ch[1:]):
            if (a, b) not in seen:
                seen.add((a, b))
                edge(gx[a] + 64, gy[a], gx[b] - 64, gy[b], "child", "CHILD_OF")
    for r in reach:
        x2, y2 = rx - RW / 2, ry[r["resource"]]
        if r["via"] == "direct":
            edge(mx, my - 40, x2, y2, "direct", f"CAN {r['access']} (direct)", bend=True)
        else:
            edge(gx[r["via"]] + 64, gy[r["via"]], x2, y2, "can", f"CAN {r['access']}", 0.78)
    for res in sorted(orphan_ids):
        edge(mx, my - 40, rx - RW / 2, ry[res], "owns", "OWNS", bend=True)
    for g in sorted(depth):
        x, y = gx[g], gy[g]
        cls = "n grp sole" if g in sole_admin else "n grp"
        out.append(f'<g class="{cls}"><rect x="{x - 64:.1f}" y="{y - 17:.1f}" width="128" height="34" rx="17"/>'
                   f'<text x="{x:.1f}" y="{y + 5:.1f}">{escape(_short(g, 18))}</text>'
                   + (f'<text class="tag" x="{x:.1f}" y="{y + 30:.1f}">no other active admin</text>'
                      if g in sole_admin else "") + "</g>")
    for res, y in ry.items():
        cls = "n res risk" if res in orphan_ids else "n res"
        out.append(f'<g class="{cls}"><rect x="{rx - RW / 2:.1f}" y="{y - 18:.1f}" width="{RW}" height="36" rx="8"/>'
                   f'<text x="{rx:.1f}" y="{y - 2:.1f}">{escape(res)}</text>'
                   f'<text class="sub" x="{rx:.1f}" y="{y + 12:.1f}">{escape(_short(titles.get(res, res), 24))}</text></g>')
    out.append(f'<g class="n me"><circle cx="{mx}" cy="{my}" r="40"/>'
               f'<text x="{mx}" y="{my + 5}">{escape(_short(email.split("@")[0], 12))}</text></g>')
    out.append("</svg>")
    return "\n".join(out)


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1f23;--mut:#666b73;--line:#c9ccd2;--card:#fff;--me:#2f5d8a;--grp:#e7eef6;
--res:#f3f3f1;--risk:#b4372f;--riskbg:#fbe9e7;--direct:#b8741a}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8e9ec;--mut:#9aa0a8;--line:#3a3f47;
--card:#1e2126;--me:#6fa3d6;--grp:#22303f;--res:#262a30;--risk:#ef7a70;--riskbg:#3a2220;--direct:#e0a14a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:900px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}.mut{color:var(--mut)}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.kpi{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:10px 14px;min-width:140px}.kpi b{display:block;font-size:22px}
svg{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
.e{stroke:var(--line);stroke-width:1.5}.arh{fill:var(--mut)}.e.mem,.e.child{stroke:var(--me);stroke-width:2}.e.direct{stroke:var(--direct);stroke-dasharray:5 4}
.e.owns{stroke:var(--risk);stroke-width:2;stroke-dasharray:2 3}.el{font-size:10px;fill:var(--mut);text-anchor:middle;paint-order:stroke;stroke:var(--card);stroke-width:3px}.el.owns{fill:var(--risk)}.el.direct{fill:var(--direct)}.n text{text-anchor:middle;font-size:13px;fill:var(--fg)}
.n .sub{font-size:10.5px;fill:var(--mut)}.me circle{fill:var(--me)}.me text{fill:#fff;font-weight:600}
.grp rect{fill:var(--grp);stroke:var(--line)}.sole rect{stroke:var(--risk);stroke-width:2.5}.n .tag{font-size:10px;fill:var(--risk)}.res rect{fill:var(--res);stroke:var(--line)}
.risk rect{fill:var(--riskbg);stroke:var(--risk);stroke-width:2}
.legend span{display:block}.hint{display:none}@media (max-width:600px){svg{min-width:720px}.hint{display:block}}a{color:var(--me)}ol li{margin:4px 0}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:14px}.tw{overflow-x:auto}td{overflow-wrap:break-word}
"""


def _n(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def html(email: str, reach: list[dict], orphans: list[dict], sole_admin: list[str],
         seats: list[dict], checklist: list[str], memberships: list[str] = ()) -> str:
    orphan_ids = {o["resource"] for o in orphans}
    cost = sum(float(s["monthly_usd"]) for s in seats)
    rows = "".join(
        f"<tr><td>{escape(o['resource'])}</td><td>{escape(o.get('title') or '')}</td>"
        f"<td>{escape(o['suggested_owner'] or '—')}</td><td class='mut'>{escape(o.get('reason') or '')}</td></tr>"
        for o in sorted(orphans, key=lambda o: o["resource"]))
    steps = "".join(f"<li>{escape(s)}</li>" for s in checklist)
    n_reach = len({r["resource"] for r in reach} | orphan_ids)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offboarding: {escape(email)}</title><style>{CSS}</style></head><body><main>
<h1>Offboarding {escape(email)}</h1>
<p class="mut">Answered by Cypher queries on a local Relate graph. Nothing left this machine.</p>
<div class="kpis">
<div class="kpi"><b>{n_reach}</b>{_n(n_reach, "resource", "resources")} they can reach or own</div>
<div class="kpi"><b>{len(orphans)}</b>{_n(len(orphans), "resource", "resources")} only they own</div>
<div class="kpi"><b>{len(sole_admin)}</b>{_n(len(sole_admin), "group", "groups")} left without an admin</div>
<div class="kpi"><b>{cost:.2f} USD</b>a month in paid seats</div>
</div>
<p class="hint mut">Swipe sideways to see the whole graph.</p>
<div class='tw'>{svg(email, reach, orphans, sole_admin, memberships)}</div>
<p class="legend mut"><span>● Read left to right: person, the groups they are in, the groups those nest under, what the grant opens.</span>
<span style="color:var(--risk)">■ only they own it · red outline: no other active admin remains in that group</span>
<span style="color:var(--direct)">- - direct grant</span></p>
<h2>Resources that would be orphaned</h2>
{"<div class='tw'><table><tr><th>Resource</th><th>Title</th><th>Suggested new owner</th><th>Why</th></tr>" + rows + "</table></div>" if orphans else "<p class='mut'>None.</p>"}
<h2>Checklist, in a safe order</h2><ol>{steps}</ol>
</main></body></html>
"""
