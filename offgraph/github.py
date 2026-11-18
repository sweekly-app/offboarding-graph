"""`offgraph import-github`: turn JSON saved from GitHub's REST API into the CSV
files `offgraph load` reads, for one organisation.

Input folder (each file is the JSON body of one documented endpoint; pages saved
one after another in the same file are fine):
  teams.json                    GET /orgs/ORG/teams
  members.json                  GET /orgs/ORG/members
  admins.json                   GET /orgs/ORG/members?role=admin
  repos.json                    GET /orgs/ORG/repos
  team-members/SLUG.json        GET /orgs/ORG/teams/SLUG/members
  team-maintainers/SLUG.json    GET /orgs/ORG/teams/SLUG/members?role=maintainer
  team-repos/SLUG.json          GET /orgs/ORG/teams/SLUG/repos
  collaborators/REPO.json       GET /repos/ORG/REPO/collaborators?affiliation=direct
plus `logins.csv` (login,email), because the API does not give members' emails,
and optionally `repo-owners.csv` (repo,owner) naming who owns each repository.

Output: groups.csv, memberships.csv, accounts.csv, resources.csv and grants.csv.
people.csv (with status active or left) and prices.csv come from you.
Nothing is fetched: the files are read from disk."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .data import ExportError, norm_email

APP = "GitHub"
OUT_FILES = ("groups.csv", "memberships.csv", "accounts.csv", "resources.csv", "grants.csv")
MAX_LISTED = 10


def _pages(path: Path) -> list:
    """One JSON array, or several written back to back (one per page)."""
    text, out, i = path.read_text(encoding="utf-8-sig"), [], 0
    if not text.strip():
        raise ExportError(f"{_rel(path)} is empty (a failed save?); an endpoint with no rows returns []")
    dec = json.JSONDecoder()
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        try:
            value, i = dec.raw_decode(text, i)
        except json.JSONDecodeError as e:
            raise ExportError(f"{_rel(path)}: not JSON from the API ({e.msg}, line {e.lineno})") from None
        if not isinstance(value, list):
            raise ExportError(f"{_rel(path)}: expected a JSON array (a list response), got {type(value).__name__}")
        out.extend(value)


def _rel(path: Path) -> str:
    """team-members/x.json rather than x.json, so the message names the file to fix."""
    return f"{path.parent.name}/{path.name}" if path.parent.name in SUBFOLDERS else path.name


SUBFOLDERS = ("team-members", "team-maintainers", "team-repos", "collaborators")


def _access(perms: dict | None, role_name: str | None) -> str:
    """GitHub's repository role as offgraph's access level."""
    p = perms or {}
    if p.get("admin") or role_name == "admin":
        return "admin"
    if p.get("maintain") or p.get("push") or role_name in ("maintain", "write"):
        return "edit"
    if p.get("triage") or role_name == "triage":
        return "comment"
    return "view"


def _csv_map(path: Path, key: str, value: str) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and (key not in rows[0] or value not in rows[0]):
        raise ExportError(f"{path.name}: needs the columns {key},{value}")
    return {r[key].strip().lower(): r[value].strip() for r in rows if r.get(key, "").strip()}


def convert(folder: Path, logins_csv: Path, owners_csv: Path | None = None) -> dict[str, list[dict]]:
    """The five tables, or ExportError listing every problem."""
    problems: list[str] = []
    need = [folder / n for n in ("teams.json", "members.json", "admins.json", "repos.json")]
    missing = [p.name for p in need if not p.exists()]
    if missing:
        raise ExportError(f"missing in {folder}: {', '.join(missing)}")
    email = {k: norm_email(v) for k, v in _csv_map(logins_csv, "login", "email").items()}
    owners = _csv_map(owners_csv, "repo", "owner") if owners_csv else {}
    unknown: set[str] = set()

    def who(login: str) -> str:
        e = email.get(login.lower())
        if not e:
            unknown.add(login)
        return e or ""

    teams = _pages(folder / "teams.json")
    slugs = {t["slug"] for t in teams}
    groups = [{"group": t["slug"], "parent": (t.get("parent") or {}).get("slug", "")} for t in teams]
    memberships = []
    def required(sub: str, name: str, endpoint: str) -> list | None:
        path = folder / sub / f"{name}.json"
        if not path.exists():
            problems.append(f"{sub}/{name}.json is missing ({endpoint}; save [] if it has no rows)")
            return None
        try:
            return _pages(path)
        except ExportError as e:
            problems.append(str(e))
            return None

    team_repos: dict[str, list] = {}
    for slug in sorted(slugs):
        maint = required("team-maintainers", slug, f"GET /orgs/ORG/teams/{slug}/members?role=maintainer")
        members = required("team-members", slug, f"GET /orgs/ORG/teams/{slug}/members")
        team_repos[slug] = required("team-repos", slug, f"GET /orgs/ORG/teams/{slug}/repos") or []
        if maint is None or members is None:
            continue
        # The API lists members of child teams too, marked inherited; they belong to the child team,
        # and reach finds the parent through CHILD_OF.
        admins = {u["login"].lower() for u in maint if not u.get("inherited")}
        for u in members:
            if u.get("inherited"):
                continue
            memberships.append({"email": who(u["login"]), "group": slug,
                                "role": "admin" if u["login"].lower() in admins else "member"})
    org_admins = {u["login"].lower() for u in _pages(folder / "admins.json")}
    accounts = [{"email": who(u["login"]), "app": APP, "paid": "yes",
                 "admin": "yes" if u["login"].lower() in org_admins else "no", "last_active": ""}
                for u in _pages(folder / "members.json")]
    resources, grants, no_owner = [], [], []
    for r in _pages(folder / "repos.json"):
        name, rid = r["name"], f"{APP}:{r['full_name']}"
        people = required("collaborators", name, f"GET /repos/ORG/{name}/collaborators?affiliation=direct") or []
        for u in people:
            grants.append({"resource": rid, "grantee": who(u["login"]), "grantee_type": "person",
                           "access": _access(u.get("permissions"), u.get("role_name"))})
        owner = owners.get(name.lower()) or owners.get(r["full_name"].lower())
        if owner:
            owner = norm_email(owner) if "@" in owner else who(owner)
        else:
            direct_admins = sorted(u["login"] for u in people
                                   if _access(u.get("permissions"), u.get("role_name")) == "admin")
            owner = who(direct_admins[0]) if direct_admins else ""
            if not direct_admins:
                no_owner.append(name)
        resources.append({"resource": rid, "app": APP, "kind": "repository", "title": name, "owner": owner})
    for slug in sorted(slugs):
        for r in team_repos[slug]:
            grants.append({"resource": f"{APP}:{r['full_name']}", "grantee": slug, "grantee_type": "group",
                           "access": _access(r.get("permissions"), r.get("role_name"))})

    def listed(values) -> str:
        values = sorted(values)
        more = f" and {len(values) - MAX_LISTED} more" if len(values) > MAX_LISTED else ""
        return ", ".join(values[:MAX_LISTED]) + more
    if unknown:
        problems.append(f"logins.csv: no email for {listed(unknown)}")
    if no_owner:
        problems.append(f"no owner for {listed(no_owner)}: name one in repo-owners.csv (repo,owner), "
                        "or give the repository a direct admin collaborator")
    if problems:
        raise ExportError("\n".join(problems))
    return {"groups.csv": groups, "memberships.csv": memberships, "accounts.csv": accounts,
            "resources.csv": resources, "grants.csv": grants}


def write(tables: dict[str, list[dict]], out: Path, copies: dict[str, Path | None] | None = None) -> dict[str, int]:
    """Write the tables (and copy people.csv / prices.csv when given); never over
    files that are already there."""
    from .data import COLUMNS
    copies = {n: p for n, p in (copies or {}).items() if p}
    taken = [n for n in (*OUT_FILES, *copies) if (out / n).exists()]
    if taken:
        raise ExportError(f"{out} already has {', '.join(taken)}; write to an empty folder "
                          "(then add your people.csv and prices.csv there)")
    out.mkdir(parents=True, exist_ok=True)
    for name in OUT_FILES:
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(COLUMNS[name]))
            w.writeheader()
            w.writerows(tables[name])
    for name, src in copies.items():
        (out / name).write_text(src.read_text(encoding="utf-8-sig"), encoding="utf-8")
    return {n: len(tables[n]) for n in OUT_FILES}
