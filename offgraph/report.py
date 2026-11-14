"""One self-contained HTML page for one leaver: the reach graph, the risks and
the checklist. No scripts, no external files; open it in any browser."""
from __future__ import annotations

import math
from html import escape


def _xy(cx: float, cy: float, r: float, i: int, n: int, start: float = -math.pi / 2):
    a = start + 2 * math.pi * i / max(n, 1)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _short(t: str, n: int = 20) -> str:
    return t if len(t) <= n else t[: n - 1] + "…"


def svg(email: str, reach: list[dict], orphans: list[dict]) -> str:
    """Leaver in the centre, the groups that give access on the middle ring,
    resources on the outer ring next to their group. Orphan-risk resources
    (and direct grants) get their own sector so their edges cross nothing."""
    W, H, cx, cy = 760, 560, 380, 280
    orphan_ids = {o["resource"] for o in orphans}
    titles = {o["resource"]: o.get("title") or o["resource"] for o in orphans}
    titles.update({r["resource"]: r.get("title") or r["resource"] for r in reach})
    first_via: dict[str, str] = {}
    for r in sorted(reach, key=lambda r: (r["via"] == "direct", r["via"], r["resource"])):
        first_via.setdefault(r["resource"], r["via"])
    def sector(res: str) -> tuple:
        if res in orphan_ids:
            return (2, "", res)
        v = first_via[res]
        return (1, "", res) if v == "direct" else (0, v, res)
    resources = sorted(set(first_via) | orphan_ids, key=sector)
    n = len(resources)
    rpos = {res: _xy(cx, cy, 235, i, n) for i, res in enumerate(resources)}
    groups = sorted({r["via"] for r in reach if r["via"] != "direct"})
    gpos = {}
    for g in groups:  # a group sits at the circular mean angle of its resources
        idx = [i for i, res in enumerate(resources) if first_via.get(res) == g and res not in orphan_ids]
        if not idx:
            idx = [i for i, res in enumerate(resources) if any(r["via"] == g and r["resource"] == res for r in reach)]
        angs = [-math.pi / 2 + 2 * math.pi * i / max(n, 1) for i in idx]
        a = math.atan2(sum(map(math.sin, angs)), sum(map(math.cos, angs)))
        gpos[g] = (cx + 120 * math.cos(a), cy + 120 * math.sin(a))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Access graph for {escape(email)}">']
    for g, (x, y) in gpos.items():
        out.append(f'<line class="e" x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}"/>')
    for r in reach:
        x2, y2 = rpos[r["resource"]]
        if r["via"] == "direct":
            out.append(f'<line class="e direct" x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
        else:
            x1, y1 = gpos[r["via"]]
            out.append(f'<line class="e" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    for res in orphan_ids:
        x2, y2 = rpos[res]
        out.append(f'<line class="e owns" x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    for g, (x, y) in gpos.items():
        out.append(f'<g class="n grp"><circle cx="{x:.1f}" cy="{y:.1f}" r="30"/>'
                   f'<text x="{x:.1f}" y="{y + 4:.1f}">{escape(g)}</text></g>')
    for res, (x, y) in rpos.items():
        cls = "n res risk" if res in orphan_ids else "n res"
        out.append(f'<g class="{cls}"><rect x="{x - 56:.1f}" y="{y - 17:.1f}" width="112" height="34" rx="8"/>'
                   f'<text x="{x:.1f}" y="{y - 1:.1f}">{escape(res)}</text>'
                   f'<text class="sub" x="{x:.1f}" y="{y + 11:.1f}">{escape(_short(titles.get(res, res)))}</text></g>')
    out.append(f'<g class="n me"><circle cx="{cx}" cy="{cy}" r="44"/>'
               f'<text x="{cx}" y="{cy + 4}">{escape(email.split("@")[0])}</text></g>')
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
svg{width:100%;min-width:640px;height:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
.e{stroke:var(--line);stroke-width:1.5}.e.direct{stroke:var(--direct);stroke-dasharray:5 4}
.e.owns{stroke:var(--risk);stroke-width:2}.n text{text-anchor:middle;font-size:12px;fill:var(--fg)}
.n .sub{font-size:9.5px;fill:var(--mut)}.me circle{fill:var(--me)}.me text{fill:#fff;font-weight:600}
.grp circle{fill:var(--grp);stroke:var(--line)}.res rect{fill:var(--res);stroke:var(--line)}
.risk rect{fill:var(--riskbg);stroke:var(--risk);stroke-width:2}
ol li{margin:4px 0}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:14px}.tw{overflow-x:auto}td{overflow-wrap:anywhere}.legend span{margin-right:14px}
"""


def html(email: str, reach: list[dict], orphans: list[dict], sole_admin: list[str],
         seats: list[dict], checklist: list[str]) -> str:
    orphan_ids = {o["resource"] for o in orphans}
    cost = sum(float(s["monthly_usd"]) for s in seats)
    rows = "".join(
        f"<tr><td>{escape(o['resource'])}</td><td>{escape(o.get('title') or '')}</td>"
        f"<td>{escape(o['suggested_owner'] or '—')}</td><td class='mut'>{escape(o.get('reason') or '')}</td></tr>"
        for o in sorted(orphans, key=lambda o: o["resource"]))
    steps = "".join(f"<li>{escape(s)}</li>" for s in checklist)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offboarding: {escape(email)}</title><style>{CSS}</style></head><body><main>
<h1>Offboarding {escape(email)}</h1>
<p class="mut">Answered by Cypher queries on a local Relate graph. Nothing left this machine.</p>
<div class="kpis">
<div class="kpi"><b>{len({r['resource'] for r in reach})}</b>resources reachable</div>
<div class="kpi"><b>{len(orphans)}</b>resources only they own</div>
<div class="kpi"><b>{len(sole_admin)}</b>groups left without an admin</div>
<div class="kpi"><b>{cost:.2f} USD</b>a month in paid seats</div>
</div>
<div class='tw'>{svg(email, reach, orphans)}</div>
<p class="legend mut"><span>● groups that give access</span><span style="color:var(--risk)">■ only they own it</span>
<span style="color:var(--direct)">- - direct grant</span></p>
<h2>Resources that would be orphaned</h2>
{"<div class='tw'><table><tr><th>Resource</th><th>Title</th><th>Suggested new owner</th><th>Why</th></tr>" + rows + "</table></div>" if orphans else "<p class='mut'>None.</p>"}
<h2>Checklist, in a safe order</h2><ol>{steps}</ol>
</main></body></html>
"""
