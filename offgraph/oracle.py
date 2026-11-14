"""The same five answers computed in plain Python from the CSV rows.

Used by the tests to check the generator, and by `pytest -m engine` to check
that Relate's Cypher answers match these, row for row."""
from __future__ import annotations

from .data import Exports, parents


def _ancestors(ex: Exports, group: str) -> list[str]:
    """The group and every group above it (any number of parents; cycles and
    depth are refused at load, data.check)."""
    par = parents(ex)
    out, seen, todo = [], set(), [group]
    while todo:
        g = todo.pop()
        if g in seen:
            continue
        seen.add(g)
        out.append(g)
        todo.extend(sorted(par.get(g, ())))
    return out


def person(ex: Exports, email: str) -> dict:
    for p in ex.people:
        if p["email"] == email:
            return p
    raise KeyError(email)


def can_edit(ex: Exports, email: str, resource: str) -> bool:
    return any(r == resource and a in ("edit", "admin") for r, a, _v in reach(ex, email))


def reach(ex: Exports, email: str) -> set[tuple[str, str, str]]:
    """(resource, access, via) for every resource the person can reach."""
    groups = set()
    for m in ex.memberships:
        if m["email"] == email:
            groups.update(_ancestors(ex, m["group"]))
    out = set()
    for g in ex.grants:
        if g["grantee_type"] == "group" and g["grantee"] in groups:
            out.add((g["resource"], g["access"], g["grantee"]))
        if g["grantee_type"] == "person" and g["grantee"] == email:
            out.add((g["resource"], g["access"], "direct"))
    return out


def orphans(ex: Exports, email: str) -> dict[str, str | None]:
    """resource -> suggested new owner: the lowest-email active admin of a group
    granted edit (or admin) on the resource itself (not of groups nested under it,
    which would let a company-wide grant pick an unrelated team's admin); else the lowest-email active admin in the
    leaver's team; else the lowest-email active person who can edit it (a direct
    grant, or through nested groups); else None. Never the leaver."""
    return {res: who for res, (who, _why) in orphans_with_reason(ex, email).items()}


def orphans_with_reason(ex: Exports, email: str) -> dict[str, tuple[str | None, str]]:
    status = {p["email"]: p["status"] for p in ex.people}
    team = person(ex, email)["team"]

    def active_admins(groups: set[str], same_team: bool = False) -> list[str]:
        return sorted({m["email"] for m in ex.memberships
                       if m["role"] == "admin" and m["group"] in groups
                       and status.get(m["email"]) == "active" and m["email"] != email
                       and (not same_team or person(ex, m["email"])["team"] == team)})

    all_groups = set(ex.group_names())
    team_admins = active_admins(all_groups, same_team=True)
    out = {}
    for r in ex.resources:
        if r["owner"] != email:
            continue
        editors = {g["grantee"] for g in ex.grants if g["resource"] == r["resource"]
                   and g["grantee_type"] == "group" and g["access"] in ("edit", "admin")}
        by_access = active_admins(editors)  # admins of the groups holding the grant itself
        if by_access:
            out[r["resource"]] = (by_access[0], "admin of a group that can edit it")
        elif team_admins:
            out[r["resource"]] = (team_admins[0], "admin in the leaver's team")
        elif editors_any := sorted(q for q, s in status.items() if s == "active" and q != email
                                   and can_edit(ex, q, r["resource"])):
            out[r["resource"]] = (editors_any[0], "active editor (no admin left)")
        else:
            out[r["resource"]] = (None, "none found")
    return out


def seats(ex: Exports) -> list[tuple[str, str, float]]:
    """Paid seats still held by people marked as left."""
    left = {p["email"] for p in ex.people if p["status"] == "left"}
    return sorted({(a["email"], a["app"], ex.prices[a["app"]]) for a in ex.accounts
                   if a["email"] in left and a["paid"] == "yes"})


def person_seats(ex: Exports, email: str) -> list[tuple[str, float]]:
    """The person's own paid seats, whatever their status (offboarding often
    starts before HR marks them as left)."""
    return sorted({(a["app"], ex.prices[a["app"]]) for a in ex.accounts
                   if a["email"] == email and a["paid"] == "yes"})


def sole_admin(ex: Exports, email: str) -> set[str]:
    active = {p["email"] for p in ex.people if p["status"] == "active"}
    mine = {m["group"] for m in ex.memberships if m["email"] == email and m["role"] == "admin"}
    out = set()
    for g in mine:
        others = {m["email"] for m in ex.memberships
                  if m["group"] == g and m["role"] == "admin" and m["email"] in active
                  and m["email"] != email}
        if not others:
            out.add(g)
    return out


def app_admin(ex: Exports, email: str) -> set[str]:
    return {a["app"] for a in ex.accounts if a["email"] == email and a["admin"] == "yes"}


def checklist(ex: Exports, email: str) -> list[str]:
    """The ordered offboarding steps for one leaver."""
    steps = []
    for res, new_owner in sorted(orphans(ex, email).items()):
        who = new_owner or "a new owner (nobody active can edit it)"
        steps.append(f"Transfer ownership of {res} to {who}")
    for g in sorted(sole_admin(ex, email)):
        steps.append(f"Name a second admin for group {g}, then remove {email} as admin")
    for app in sorted(app_admin(ex, email)):
        steps.append(f"Remove admin rights in {app}")
    for app, price in person_seats(ex, email):
        steps.append(f"Remove the paid {app} seat ({price:.2f} USD a month)")
    direct = sorted({r for r, _a, via in reach(ex, email) if via == "direct"})
    for res in direct:
        steps.append(f"Revoke the direct grant on {res}")
    steps.append("Disable the account everywhere")
    return steps
