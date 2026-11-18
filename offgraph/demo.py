"""Seeded demo company: plain CSV exports from five generic SaaS tools (Docs, Chat,
Tracker, Wiki, CodeHost). Every name is invented; every address uses example.com;
the tools and their prices are invented demo values, not real products."""
from __future__ import annotations

import csv
import random
from pathlib import Path

FIRST = ["Ada", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun",
         "Kai", "Lea", "Max", "Nia", "Oli", "Pia", "Quin", "Rae", "Sam", "Tia"]
LAST = ["Park", "Novak", "Silva", "Okafor", "Berg", "Ruiz", "Lind", "Moreau",
        "Tanaka", "Weber"]
TEAMS = ["Engineering", "Sales", "Support", "Finance", "Design", "Ops"]
APPS = {  # app -> monthly price per paid seat, USD (invented demo values)
    "Docs": 16.0,
    "Chat": 7.0,
    "Tracker": 10.0,
    "Wiki": 5.0,
    "CodeHost": 5.0,
}
RESOURCE_KINDS = {
    "Docs": "shared drive",
    "Chat": "channel",
    "Tracker": "project",
    "Wiki": "space",
    "CodeHost": "repository",
}

# Five leavers in four teams (index % 6 gives the team).
LEAVER_INDEXES = {3, 13, 21, 34, 50}

FILES = ("people.csv", "groups.csv", "memberships.csv", "accounts.csv",
         "resources.csv", "grants.csv")


def generate(out: Path, seed: int = 7, people: int = 60) -> dict[str, int]:
    """Write the six CSV files to `out` and return row counts."""
    rnd = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    persons = []
    used = set()
    for i in range(people):
        while True:
            name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
            if name not in used:
                used.add(name)
                break
        email = name.lower().replace(" ", ".") + "@example.com"
        team = TEAMS[i % len(TEAMS)]
        status = "left" if i in LEAVER_INDEXES else "active"
        persons.append({"email": email, "name": f"[Sample] {name}", "team": team,
                        "status": status})

    # Groups: one per team, a parent "All staff", and two nested cross-team groups
    # (a three-level chain is added at the end).
    groups = [{"group": "all-staff", "parent": ""}]
    groups += [{"group": t.lower(), "parent": "all-staff"} for t in TEAMS]
    groups += [{"group": "eng-oncall", "parent": "engineering"},
               {"group": "finance-approvers", "parent": "finance"}]
    memberships = []
    for p in persons:
        memberships.append({"email": p["email"], "group": p["team"].lower(),
                            "role": "member"})
    for g in ("eng-oncall", "finance-approvers"):
        team = "Engineering" if g == "eng-oncall" else "Finance"
        pool = [p for p in persons if p["team"] == team]
        for p in rnd.sample(pool, 3):
            memberships.append({"email": p["email"], "group": g, "role": "member"})
    # Each team group gets one or two admins; some leavers are the only admin.
    for t in TEAMS:
        pool = [p for p in persons if p["team"] == t]
        admins = rnd.sample(pool, 1 if t in ("Finance", "Design") else 2)
        if t == "Finance":  # the demo's sole-admin case: Finance's only admin is leaving
            admins = [next(p for p in pool if p["status"] == "left")]
        for a in admins:
            memberships.append({"email": a["email"], "group": t.lower(), "role": "admin"})

    accounts = []
    for p in persons:
        for app, _price in APPS.items():
            if app == "CodeHost" and p["team"] not in ("Engineering", "Ops"):
                continue
            if app in ("Tracker", "Wiki") and p["team"] == "Sales" and rnd.random() < 0.5:
                continue
            accounts.append({"email": p["email"], "app": app,
                             "paid": "yes", "admin": "no",
                             "last_active": "2026-10-%02d" % rnd.randint(1, 28)
                             if p["status"] == "active" else "2026-08-%02d" % rnd.randint(1, 28)})
    for acc in rnd.sample(accounts, 6):
        acc["admin"] = "yes"
    # One leaver is still a Chat admin.
    lv_chat = next(a for a in accounts if a["app"] == "Chat"
                    and any(p["email"] == a["email"] and p["status"] == "left" for p in persons))
    lv_chat["admin"] = "yes"

    resources = []
    grants = []
    rid = 0
    for t in TEAMS:
        pool = [p for p in persons if p["team"] == t]
        for app, kind in RESOURCE_KINDS.items():
            if app == "CodeHost" and t not in ("Engineering", "Ops"):
                continue
            rid += 1
            owner = rnd.choice(pool)
            res = {"resource": f"R{rid:03d}", "app": app, "kind": kind,
                   "title": f"{t} {kind}", "owner": owner["email"]}
            resources.append(res)
            grants.append({"resource": res["resource"], "grantee": t.lower(),
                           "grantee_type": "group", "access": "edit"})
            # A few resources are shared with all staff through the parent group.
            if rnd.random() < 0.3:
                grants.append({"resource": res["resource"], "grantee": "all-staff",
                               "grantee_type": "group", "access": "view"})
    # Make sure at least two leavers own resources (the orphan story of the demo).
    leavers = [p for p in persons if p["status"] == "left"]
    for res, lv in zip(resources[::7], leavers):
        res["owner"] = lv["email"]
    # A direct personal grant to a leaver, outside any group.
    grants.append({"resource": resources[1]["resource"], "grantee": leavers[0]["email"],
                   "grantee_type": "person", "access": "admin"})

    # A deeper chain (no random draws, so every row above stays as it was): the
    # company budget is shared with "leadership"; the Finance leaver reaches it only
    # through budget-owners -> spend-approvers -> leadership, which no single export shows.
    fin_leaver = next(m["email"] for m in memberships
                      if m["group"] == "finance" and m["role"] == "admin")
    # Their admin is an active person who already administers Ops, so no team's
    # owner suggestion changes.
    ops_admin = next(m["email"] for m in memberships if m["group"] == "ops" and m["role"] == "admin"
                     and any(p["email"] == m["email"] and p["status"] == "active" for p in persons))
    fin_active = sorted(p["email"] for p in persons
                        if p["team"] == "Finance" and p["status"] == "active")
    groups += [{"group": "leadership", "parent": "all-staff"},
               {"group": "spend-approvers", "parent": "leadership"},
               {"group": "budget-owners", "parent": "spend-approvers"}]
    memberships += [{"email": ops_admin, "group": g, "role": "admin"}
                    for g in ("leadership", "spend-approvers", "budget-owners")]
    memberships += [{"email": fin_active[0], "group": "budget-owners", "role": "member"},
                    {"email": fin_leaver, "group": "budget-owners", "role": "member"}]
    rid += 1
    budget = {"resource": f"R{rid:03d}", "app": "Docs", "kind": "shared drive",
              "title": "Company budget", "owner": ops_admin}
    resources.append(budget)
    grants.append({"resource": budget["resource"], "grantee": "leadership",
                   "grantee_type": "group", "access": "edit"})

    tables = {"people.csv": persons, "groups.csv": groups,
              "memberships.csv": memberships, "accounts.csv": accounts,
              "resources.csv": resources, "grants.csv": grants}
    for fname, rows in tables.items():
        with (out / fname).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return {k: len(v) for k, v in tables.items()}


def prices() -> dict[str, float]:
    return dict(APPS)
