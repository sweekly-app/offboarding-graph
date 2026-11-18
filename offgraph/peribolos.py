"""`offgraph import-peribolos`: read a Peribolos configuration and write the CSV
files `offgraph load` reads, for one GitHub organisation.

Peribolos (part of Kubernetes' Prow) keeps an organisation's GitHub access as
code: its admins and members, and every team with its maintainers, members,
child teams and repository permissions. Open-source organisations publish it,
for example kubernetes/org (config/kubernetes/).

Input, either:
  - a folder laid out like kubernetes/org's config/ORG/: org.yaml plus any
    number of */teams.yaml files, each with a top-level `teams:` mapping; or
  - one YAML file holding the organisation's settings (admins, members, teams),
    or the full `orgs: {ORG: {...}}` form, with --org naming the organisation.

Output: people.csv, groups.csv, memberships.csv, accounts.csv, resources.csv,
grants.csv and prices.csv. People are identified by their GitHub login (the
config has no emails) and are all written as active: mark leavers in
people.csv yourself. Team maintainers become group admins, child teams become
CHILD_OF links, and org admins become app admins. Peribolos says nothing about
who owns a repository or what a seat costs, so resources have no owner and
accounts are unpaid. Nothing is fetched: the files are read from disk.
Needs PyYAML: pip install "offgraph[peribolos]"."""
from __future__ import annotations

import csv
from pathlib import Path

from .data import COLUMNS, ExportError, norm_email
from .github import _access

APP = "GitHub"
OUT_FILES = ("people.csv", "groups.csv", "memberships.csv", "accounts.csv", "resources.csv",
             "grants.csv", "prices.csv")
LEVELS = {"read", "triage", "write", "maintain", "admin"}  # Peribolos' RepoPermissionLevel


def _yaml(path: Path):
    try:
        import yaml
    except ImportError:
        raise ExportError('import-peribolos needs PyYAML: pip install "offgraph[peribolos]"') from None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise ExportError(f"{path.name}: not YAML{where}") from None


def _org_config(src: Path, org: str | None) -> tuple[str, dict, list[tuple[str, dict]]]:
    """(org name, org settings, [(file label, teams mapping)])."""
    if src.is_dir():
        main = src / "org.yaml"
        if not main.exists():
            raise ExportError(f"{src} has no org.yaml (point at the folder that holds the org's org.yaml, "
                              "for example kubernetes/org's config/kubernetes)")
        settings = _yaml(main) or {}
        name = org or src.resolve().name
        files = [("org.yaml", settings.get("teams") or {})]
        for f in sorted(src.glob("*/teams.yaml")):
            files.append((f"{f.parent.name}/teams.yaml", (_yaml(f) or {}).get("teams") or {}))
    elif src.is_file():
        settings = _yaml(src) or {}
        if isinstance(settings, dict) and "orgs" in settings:
            orgs = settings["orgs"] or {}
            if not org:
                if len(orgs) != 1:
                    raise ExportError(f"{src.name} holds {len(orgs)} organisations ({', '.join(sorted(orgs))}); "
                                      "pick one with --org")
                org = next(iter(orgs))
            if org not in orgs:
                raise ExportError(f"{src.name} has no organisation {org!r} (it has {', '.join(sorted(orgs))})")
            settings = orgs[org] or {}
        name = org or src.stem
        files = [(src.name, settings.get("teams") or {})]
    else:
        raise ExportError(f"{src} not found")
    if not isinstance(settings, dict):
        raise ExportError(f"{src}: expected the organisation's settings (admins, members, teams)")
    return name, settings, files


def convert(src: Path, org: str | None = None) -> dict[str, list[dict]]:
    """The seven tables, or ExportError listing every problem."""
    name, settings, files = _org_config(src, org)
    problems: list[str] = []
    admins = {norm_email(str(u)) for u in settings.get("admins") or []}
    members = {norm_email(str(u)) for u in settings.get("members") or []}
    groups: list[dict] = []
    memberships: list[dict] = []
    grants: list[dict] = []
    repos: set[str] = set()
    seen: dict[str, str] = {}
    people = admins | members

    def team(slug: str, body, parent: str, where: str) -> None:
        if slug in seen:
            problems.append(f"{where}: team {slug!r} is also defined in {seen[slug]}")
            return
        seen[slug] = where
        if not isinstance(body, dict):
            problems.append(f"{where}: team {slug!r} is not a mapping")
            return
        groups.append({"group": slug, "parent": parent})
        maint = [norm_email(str(u)) for u in body.get("maintainers") or []]
        for login in sorted(set(maint)):
            memberships.append({"email": login, "group": slug, "role": "admin"})
        for login in sorted({norm_email(str(u)) for u in body.get("members") or []} - set(maint)):
            memberships.append({"email": login, "group": slug, "role": "member"})
        people.update(maint, (norm_email(str(u)) for u in body.get("members") or []))
        for repo, level in sorted((body.get("repos") or {}).items()):
            level = str(level).lower()
            if level not in LEVELS:
                problems.append(f"{where}: team {slug!r}, repo {repo!r}: unknown permission {level!r} "
                                f"(Peribolos uses {', '.join(sorted(LEVELS))})")
                continue
            repos.add(repo)
            grants.append({"resource": f"{APP}:{name}/{repo}", "grantee": slug, "grantee_type": "group",
                           "access": _access(None, level if level != "read" else None)})
        for child, child_body in sorted((body.get("teams") or {}).items()):
            team(child, child_body, slug, where)

    for where, teams in files:
        if not isinstance(teams, dict):
            problems.append(f"{where}: `teams` is not a mapping")
            continue
        for slug, body in sorted(teams.items()):
            team(slug, body, "", where)
    if not people:
        problems.append(f"{src}: no admins, members or team members found")
    if problems:
        raise ExportError("\n".join(problems))
    people_rows = [{"email": p, "name": p, "team": "", "status": "active"} for p in sorted(people)]
    accounts = [{"email": p, "app": APP, "paid": "no", "admin": "yes" if p in admins else "no",
                 "last_active": ""} for p in sorted(admins | members)]
    resources = [{"resource": f"{APP}:{name}/{r}", "app": APP, "kind": "repository", "title": r, "owner": ""}
                 for r in sorted(repos)]
    return {"people.csv": people_rows, "groups.csv": groups, "memberships.csv": memberships,
            "accounts.csv": accounts, "resources.csv": resources, "grants.csv": grants,
            "prices.csv": [{"app": APP, "monthly_usd": "0"}]}


def write(tables: dict[str, list[dict]], out: Path) -> dict[str, int]:
    """Write the tables into an empty folder; never over files that are already there."""
    taken = [n for n in OUT_FILES if (out / n).exists()]
    if taken:
        raise ExportError(f"{out} already has {', '.join(taken)}; write to an empty folder")
    out.mkdir(parents=True, exist_ok=True)
    for name in OUT_FILES:
        cols = ("app", "monthly_usd") if name == "prices.csv" else COLUMNS[name]
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols))
            w.writeheader()
            w.writerows(tables[name])
    return {n: len(tables[n]) for n in OUT_FILES}
