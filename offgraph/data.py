"""Read the six CSV exports into plain Python rows, and check them.

A load never drops a row silently: rows that point at an unknown person, group,
resource or app are refused with a list of what is wrong, because a dropped row
would turn into a false "all clear". Exact duplicate rows (as when two exports
are merged) are removed and counted.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .demo import FILES

MAX_DEPTH = 6  # group nesting levels followed by reach (graph.QUESTIONS["reach"])

COLUMNS = {
    "people.csv": ("email", "name", "team", "status"),
    "groups.csv": ("group", "parent"),
    "memberships.csv": ("email", "group", "role"),
    "accounts.csv": ("email", "app", "paid", "admin", "last_active"),
    "resources.csv": ("resource", "app", "kind", "title", "owner"),
    "grants.csv": ("resource", "grantee", "grantee_type", "access"),
}
EMAIL_FIELDS = {"people.csv": ("email",), "memberships.csv": ("email",),
                "accounts.csv": ("email",), "resources.csv": ("owner",)}
ALLOWED = {
    ("people.csv", "status"): {"active", "left"},
    ("memberships.csv", "role"): {"member", "admin"},
    ("accounts.csv", "paid"): {"yes", "no"},
    ("accounts.csv", "admin"): {"yes", "no"},
    ("grants.csv", "grantee_type"): {"group", "person"},
    ("grants.csv", "access"): {"view", "comment", "edit", "admin"},
}


class ExportError(ValueError):
    """The exports cannot be loaded as they are; the message lists every problem."""


@dataclass(frozen=True)
class Exports:
    people: list[dict]
    groups: list[dict]
    memberships: list[dict]
    accounts: list[dict]
    resources: list[dict]
    grants: list[dict]
    prices: dict[str, float]
    duplicates_removed: int = field(default=0, compare=False)

    def group_names(self) -> list[str]:
        """Every group named anywhere in groups.csv, once."""
        names = {g["group"] for g in self.groups} | {g["parent"] for g in self.groups if g["parent"]}
        return sorted(names)


def norm_email(value: str) -> str:
    return value.strip().lower()


def _read(path: Path, name: str, problems: list[str]) -> list[dict]:
    # utf-8-sig: Spreadsheet programs' "CSV UTF-8" export starts with a byte-order mark.
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in COLUMNS[name] if c not in header]
        if missing:
            problems.append(f"{name}: missing column(s) {', '.join(missing)} (found: {', '.join(header) or 'none'})")
            return []
        rows = []
        for n, raw in enumerate(reader, start=2):
            if None in raw:
                problems.append(f"{name} line {n}: more fields than columns (an unquoted comma?)")
                continue
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            if not any(row.values()):
                continue
            for col in EMAIL_FIELDS.get(name, ()):
                row[col] = norm_email(row[col])
            for (fname, col) in ALLOWED:
                if fname == name:
                    row[col] = row[col].lower()
            if name == "grants.csv" and row["grantee_type"] == "person":
                row["grantee"] = norm_email(row["grantee"])
            for (fname, col), ok in ALLOWED.items():
                if fname == name and row[col] not in ok:
                    problems.append(f"{name} line {n}: {col} must be one of {sorted(ok)}, got {row[col]!r}")
            rows.append({c: row[c] for c in COLUMNS[name]})
        return rows


def _dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    seen, out = set(), []
    for r in rows:
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out, len(rows) - len(out)


def read_prices(folder: Path, default: dict[str, float]) -> dict[str, float]:
    """`prices.csv` (app, monthly_usd) in the exports folder, else the defaults."""
    path = folder / "prices.csv"
    if not path.exists():
        return dict(default)
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"app", "monthly_usd"} <= {h.strip() for h in (reader.fieldnames or [])}:
            raise ExportError("prices.csv: needs the columns app,monthly_usd")
        for n, raw in enumerate(reader, start=2):
            if None in raw:
                raise ExportError(f"prices.csv line {n}: more fields than columns (an unquoted comma? "
                                  "write 13.33, not 13,33)")
            row = {(k or "").strip(): (val or "").strip() for k, val in raw.items()}
            if not row.get("app"):
                continue
            try:
                price = float(row["monthly_usd"])
            except ValueError:
                raise ExportError(f"prices.csv line {n}: monthly_usd must be a number, got {row['monthly_usd']!r}") from None
            if not (price >= 0 and price < float("inf")):
                raise ExportError(f"prices.csv line {n}: monthly_usd must be zero or more, got {row['monthly_usd']!r}")
            out[row["app"]] = price
    return out


def load(folder: Path, prices: dict[str, float]) -> Exports:
    folder = Path(folder)
    missing = [f for f in FILES if not (folder / f).exists()]
    if missing:
        raise FileNotFoundError(f"missing exports in {folder}: {', '.join(missing)}")
    problems: list[str] = []
    tables, removed = {}, 0
    for name in FILES:
        rows, n = _dedupe(_read(folder / name, name, problems))
        tables[name] = rows
        removed += n
    if problems:
        raise ExportError("\n".join(problems))
    ex = Exports(tables["people.csv"], tables["groups.csv"], tables["memberships.csv"],
                 tables["accounts.csv"], tables["resources.csv"], tables["grants.csv"],
                 dict(prices), removed)
    check(ex)
    return ex


def check(ex: Exports) -> None:
    """Refuse exports whose rows point at something that is not there."""
    problems: list[str] = []
    emails = [p["email"] for p in ex.people]
    dup = sorted({e for e in emails if emails.count(e) > 1})
    if dup:
        problems.append(f"people.csv: the same email on rows that differ: {', '.join(dup)}")
    people, groups = set(emails), set(ex.group_names())
    ids = [r["resource"] for r in ex.resources]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    if dup_ids:
        problems.append(f"resources.csv: the same resource id on rows that differ: {', '.join(dup_ids)}")
    resources = set(ids)
    acc = [(a["email"], a["app"]) for a in ex.accounts]
    dup_acc = sorted({f"{e} in {a}" for e, a in acc if acc.count((e, a)) > 1})
    if dup_acc:
        problems.append(f"accounts.csv: two different rows for the same account: {', '.join(dup_acc[:10])}")

    def unknown(what: str, values) -> None:
        values = sorted(set(values))
        if values:
            shown = ", ".join(values[:10]) + (f" and {len(values) - 10} more" if len(values) > 10 else "")
            problems.append(f"{what}: {shown}")

    unknown("memberships.csv: email not in people.csv", (m["email"] for m in ex.memberships if m["email"] not in people))
    unknown("memberships.csv: group not in groups.csv", (m["group"] for m in ex.memberships if m["group"] not in groups))
    unknown("accounts.csv: email not in people.csv", (a["email"] for a in ex.accounts if a["email"] not in people))
    unknown("accounts.csv: app with no price (add it to prices.csv: app,monthly_usd)",
            (a["app"] for a in ex.accounts if a["app"] not in ex.prices))
    unknown("resources.csv: owner not in people.csv (add them, with status left if they are gone)",
            (r["owner"] for r in ex.resources if r["owner"] not in people))
    unknown("grants.csv: resource not in resources.csv", (g["resource"] for g in ex.grants if g["resource"] not in resources))
    unknown("grants.csv: group not in groups.csv",
            (g["grantee"] for g in ex.grants if g["grantee_type"] == "group" and g["grantee"] not in groups))
    unknown("grants.csv: person not in people.csv",
            (g["grantee"] for g in ex.grants if g["grantee_type"] == "person" and g["grantee"] not in people))
    depth, cycle = nesting(ex)
    if cycle:
        problems.append(f"groups.csv: a group is its own ancestor: {' -> '.join(cycle)}")
    elif depth > MAX_DEPTH:
        problems.append(f"groups.csv: groups nest {depth} levels deep; reach follows at most {MAX_DEPTH}")
    if problems:
        raise ExportError("\n".join(problems))


def parents(ex: Exports) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for g in ex.groups:
        out.setdefault(g["group"], set())
        if g["parent"]:
            out[g["group"]].add(g["parent"])
    return out


def nesting(ex: Exports) -> tuple[int, list[str] | None]:
    """(deepest CHILD_OF chain in edges, a cycle if there is one)."""
    par = parents(ex)
    memo: dict[str, int] = {}

    def depth(g: str, path: list[str]) -> int:
        if g in path:
            raise _Cycle(path[path.index(g):] + [g])
        if g not in memo:
            memo[g] = max((1 + depth(p, path + [g]) for p in par.get(g, ())), default=0)
        return memo[g]

    try:
        return max((depth(g, []) for g in par), default=0), None
    except _Cycle as c:
        return 0, c.path


class _Cycle(Exception):
    def __init__(self, path: list[str]):
        self.path = path
