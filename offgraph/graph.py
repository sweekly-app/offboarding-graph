"""The graph model in Relate, and the five offboarding questions in Cypher.

Model
  (:Person {email, name, team, status})
  (:Group {name})        (:Group)-[:CHILD_OF]->(:Group)
  (:App {name, price})
  (:Account {key, paid, admin, last_active})
  (:Resource {id, app, kind, title})
  (:Person)-[:MEMBER_OF {role}]->(:Group)
  (:Person)-[:HAS_ACCOUNT]->(:Account)-[:IN_APP]->(:App)
  (:Person)-[:OWNS]->(:Resource)      (an owner is optional)
  (:Group|Person)-[:CAN {access}]->(:Resource)
A member of a group also reaches everything granted to its parent groups.
"""
from __future__ import annotations

from .data import Exports


def lit(value) -> str:
    """A Cypher literal. Strings are single-quoted with backslash escapes."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def load_script(ex: Exports) -> str:
    """One statement per line, ready to pipe into `relate -d <store>`."""
    out: list[str] = []
    add = out.append
    for p in ex.people:
        add(f"CREATE (:Person {{email: {lit(p['email'])}, name: {lit(p['name'])}, "
            f"team: {lit(p['team'])}, status: {lit(p['status'])}}});")
    for name in ex.group_names():
        add(f"CREATE (:Group {{name: {lit(name)}}});")
    for g in ex.groups:
        if g["parent"]:
            add(f"MATCH (c:Group {{name: {lit(g['group'])}}}), (p:Group {{name: {lit(g['parent'])}}}) "
                f"CREATE (c)-[:CHILD_OF]->(p);")
    for app, price in ex.prices.items():
        add(f"CREATE (:App {{name: {lit(app)}, price: {lit(float(price))}}});")
    for m in ex.memberships:
        add(f"MATCH (p:Person {{email: {lit(m['email'])}}}), (g:Group {{name: {lit(m['group'])}}}) "
            f"CREATE (p)-[:MEMBER_OF {{role: {lit(m['role'])}}}]->(g);")
    for a in ex.accounts:
        key = f"{a['app']}|{a['email']}"
        add(f"MATCH (p:Person {{email: {lit(a['email'])}}}), (x:App {{name: {lit(a['app'])}}}) "
            f"CREATE (p)-[:HAS_ACCOUNT]->(:Account {{key: {lit(key)}, paid: {lit(a['paid'] == 'yes')}, "
            f"admin: {lit(a['admin'] == 'yes')}, last_active: {lit(a['last_active'])}}})-[:IN_APP]->(x);")
    for r in ex.resources:
        node = (f"(:Resource {{id: {lit(r['resource'])}, app: {lit(r['app'])}, "
                f"kind: {lit(r['kind'])}, title: {lit(r['title'])}}})")
        if r["owner"]:
            add(f"MATCH (o:Person {{email: {lit(r['owner'])}}}) CREATE (o)-[:OWNS]->{node};")
        else:
            add(f"CREATE {node};")
    for g in ex.grants:
        label, key = ("Group", "name") if g["grantee_type"] == "group" else ("Person", "email")
        add(f"MATCH (s:{label} {{{key}: {lit(g['grantee'])}}}), (r:Resource {{id: {lit(g['resource'])}}}) "
            f"CREATE (s)-[:CAN {{access: {lit(g['access'])}}}]->(r);")
    return "\n".join(out) + "\n"


CLEAR = "MATCH (n) DETACH DELETE n"


def load_statements(ex: Exports) -> list[tuple[str, dict]]:
    """Parameterised batch load: one UNWIND per kind of row (library backend)."""
    acc = [{"key": f"{a['app']}|{a['email']}", "email": a["email"], "app": a["app"],
            "paid": a["paid"] == "yes", "admin": a["admin"] == "yes",
            "last_active": a["last_active"]} for a in ex.accounts]
    by_type = {t: [g for g in ex.grants if g["grantee_type"] == t] for t in ("group", "person")}
    return [
        ("UNWIND $rows AS r CREATE (:Person {email: r.email, name: r.name, team: r.team, "
         "status: r.status})", {"rows": ex.people}),
        ("UNWIND $rows AS r CREATE (:Group {name: r.name})",
         {"rows": [{"name": n} for n in ex.group_names()]}),
        ("UNWIND $rows AS r MATCH (c:Group {name: r.group}), (p:Group {name: r.parent}) "
         "CREATE (c)-[:CHILD_OF]->(p)", {"rows": [g for g in ex.groups if g["parent"]]}),
        ("UNWIND $rows AS r CREATE (:App {name: r.name, price: r.price})",
         {"rows": [{"name": a, "price": float(p)} for a, p in ex.prices.items()]}),
        ("UNWIND $rows AS r MATCH (p:Person {email: r.email}), (g:Group {name: r.group}) "
         "CREATE (p)-[:MEMBER_OF {role: r.role}]->(g)", {"rows": ex.memberships}),
        ("UNWIND $rows AS r MATCH (p:Person {email: r.email}), (x:App {name: r.app}) "
         "CREATE (p)-[:HAS_ACCOUNT]->(:Account {key: r.key, paid: r.paid, admin: r.admin, "
         "last_active: r.last_active})-[:IN_APP]->(x)", {"rows": acc}),
        ("UNWIND $rows AS r CREATE (:Resource {id: r.resource, app: r.app, kind: r.kind, title: r.title})",
         {"rows": ex.resources}),
        ("UNWIND $rows AS r MATCH (o:Person {email: r.owner}), (x:Resource {id: r.resource}) "
         "CREATE (o)-[:OWNS]->(x)", {"rows": [r for r in ex.resources if r["owner"]]}),
        ("UNWIND $rows AS r MATCH (s:Group {name: r.grantee}), (x:Resource {id: r.resource}) "
         "CREATE (s)-[:CAN {access: r.access}]->(x)", {"rows": by_type["group"]}),
        ("UNWIND $rows AS r MATCH (s:Person {email: r.grantee}), (x:Resource {id: r.resource}) "
         "CREATE (s)-[:CAN {access: r.access}]->(x)", {"rows": by_type["person"]}),
    ]


# The questions. `$email` is substituted with a literal by `query()`.
QUESTIONS = {
    "reach": """
MATCH (p:Person {email: $email})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(g:Group)-[c:CAN]->(r:Resource)
RETURN DISTINCT r.id AS resource, r.title AS title, r.app AS app, c.access AS access, g.name AS via
UNION
MATCH (p:Person {email: $email})-[c:CAN]->(r:Resource)
RETURN DISTINCT r.id AS resource, r.title AS title, r.app AS app, c.access AS access, 'direct' AS via
""",
    "paths": """
MATCH (p:Person {email: $email})-[:MEMBER_OF]->(g0:Group)
MATCH path = (g0)-[:CHILD_OF*0..6]->(g:Group)
MATCH (g)-[:CAN]->(r:Resource)
RETURN DISTINCT r.id AS resource, g.name AS via, [n IN nodes(path) | n.name] AS chain
""",
    "orphans": """
MATCH (p:Person {email: $email})-[:OWNS]->(r:Resource)
OPTIONAL MATCH (a:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(:Group)-[c:CAN]->(r)
WHERE a.email <> p.email AND c.access IN ['edit', 'admin']
WITH p, r, min(a.email) AS by_access
OPTIONAL MATCH (n:Person {team: p.team, status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(:Group)
WHERE n.email <> p.email
WITH p, r, by_access, min(n.email) AS by_team
OPTIONAL MATCH (m:Person {status: 'active'})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(:Group)-[e:CAN]->(r)
WHERE m.email <> p.email AND e.access IN ['edit', 'admin']
WITH p, r, by_access, by_team, collect(DISTINCT m.email) AS via_groups
OPTIONAL MATCH (d:Person {status: 'active'})-[e:CAN]->(r)
WHERE d.email <> p.email AND e.access IN ['edit', 'admin']
WITH r, by_access, by_team, via_groups, collect(DISTINCT d.email) AS direct
WITH r, by_access, by_team, via_groups + direct AS editors
UNWIND (CASE WHEN size(editors) = 0 THEN [null] ELSE editors END) AS ed
WITH r, by_access, by_team, min(ed) AS by_editor
RETURN r.id AS resource, r.title AS title, r.app AS app,
       coalesce(by_access, by_team, by_editor) AS suggested_owner,
       CASE WHEN by_access IS NOT NULL THEN "admin of a group that can edit it"
            WHEN by_team IS NOT NULL THEN "admin in the leaver's team"
            WHEN by_editor IS NOT NULL THEN "active editor (no admin left)"
            ELSE "none found" END AS reason
""",
    "seats": """
MATCH (p:Person {status: 'left'})-[:HAS_ACCOUNT]->(a:Account {paid: true})-[:IN_APP]->(x:App)
RETURN p.email AS email, x.name AS app, x.price AS monthly_usd, a.last_active AS last_active
""",
    "sole_admin": """
MATCH (p:Person {email: $email})-[:MEMBER_OF {role: 'admin'}]->(g:Group)
OPTIONAL MATCH (o:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(g)
WHERE o.email <> p.email
WITH g, count(o) AS other_admins
WHERE other_admins = 0
RETURN g.name AS group_name
""",
    "memberships": """
MATCH (p:Person {email: $email})-[:MEMBER_OF]->(g:Group)
RETURN DISTINCT g.name AS group_name
""",
    "leavers": """
MATCH (p:Person {status: 'left'})
RETURN p.email AS email, p.name AS name, p.team AS team
""",
    "no_admin_groups": """
MATCH (g:Group)
OPTIONAL MATCH (a:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(g)
WITH g, count(a) AS admins
WHERE admins = 0
RETURN g.name AS group_name
""",
    "unowned": """
MATCH (o:Person)-[:OWNS]->(r:Resource)
WHERE o.status <> 'active'
RETURN r.id AS resource, r.title AS title
""",
    "no_editor": """
MATCH (r:Resource)
OPTIONAL MATCH (m:Person {status: 'active'})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(:Group)-[e:CAN]->(r)
WHERE e.access IN ['edit', 'admin']
WITH r, count(m) AS via_groups
OPTIONAL MATCH (d:Person {status: 'active'})-[f:CAN]->(r)
WHERE f.access IN ['edit', 'admin']
WITH r, via_groups, count(d) AS direct
WHERE via_groups + direct = 0
RETURN r.id AS resource, r.title AS title
""",
    "single_admins": """
MATCH (r:Resource)
OPTIONAL MATCH (o:Person)-[:HAS_ACCOUNT]->(:Account {admin: true})-[:IN_APP]->(x:App)
WHERE x.name = r.app
WITH r, collect(DISTINCT o.email) AS app_admins
OPTIONAL MATCH (m:Person {status: 'active'})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(:Group)-[e:CAN]->(r)
WHERE e.access = 'admin' AND NOT m.email IN app_admins
WITH r, app_admins, collect(DISTINCT m.email) AS via_groups
OPTIONAL MATCH (d:Person {status: 'active'})-[f:CAN]->(r)
WHERE f.access = 'admin' AND NOT d.email IN app_admins
WITH r, via_groups, collect(DISTINCT d.email) AS direct
WITH r, via_groups + direct AS admins
UNWIND admins AS a
WITH r, collect(DISTINCT a) AS people
WHERE size(people) = 1
RETURN r.id AS resource, r.title AS title, people[0] AS admin
""",
    "no_admin": """
MATCH (r:Resource)
OPTIONAL MATCH (o:Person)-[:HAS_ACCOUNT]->(:Account {admin: true})-[:IN_APP]->(x:App)
WHERE x.name = r.app
WITH r, collect(DISTINCT o.email) AS app_admins
OPTIONAL MATCH (m:Person {status: 'active'})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(:Group)-[e:CAN]->(r)
WHERE e.access = 'admin' AND NOT m.email IN app_admins
WITH r, app_admins, count(m) AS via_groups
OPTIONAL MATCH (d:Person {status: 'active'})-[f:CAN]->(r)
WHERE f.access = 'admin' AND NOT d.email IN app_admins
WITH r, via_groups, count(d) AS direct
WHERE via_groups + direct = 0
RETURN r.id AS resource, r.title AS title
""",
    "paid_seats_of": """
MATCH (p:Person)-[:HAS_ACCOUNT]->(a:Account {paid: true})-[:IN_APP]->(x:App)
WHERE p.email IN $emails AND p.status = 'active'
RETURN p.email AS email, x.name AS app, x.price AS monthly_usd
""",
    "person": """
MATCH (p:Person {email: $email})
RETURN p.email AS email, p.name AS name, p.team AS team, p.status AS status
""",
    "my_seats": """
MATCH (p:Person {email: $email})-[:HAS_ACCOUNT]->(a:Account {paid: true})-[:IN_APP]->(x:App)
RETURN x.name AS app, x.price AS monthly_usd
""",
    "app_admin": """
MATCH (p:Person {email: $email})-[:HAS_ACCOUNT]->(a:Account {admin: true})-[:IN_APP]->(x:App)
RETURN x.name AS app
""",
}


def best_chains(rows: list[dict]) -> dict[tuple[str, str], list[str]]:
    """From the `paths` rows: (resource, via) -> the shortest group chain
    (ties: the alphabetically first), the same choice as oracle.reach_paths."""
    best: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        key, chain = (r["resource"], r["via"]), list(r["chain"])
        if key not in best or (len(chain), chain) < (len(best[key]), best[key]):
            best[key] = chain
    return best


REMOVE = "MATCH (p:Person) WHERE p.email IN $emails SET p.status = 'left'"


def params(name: str, email: str | None = None) -> dict:
    """Cypher parameters for a question (library backend)."""
    if "$email" in QUESTIONS[name]:
        if email is None:
            raise ValueError(f"question {name!r} needs an email")
        return {"email": email}
    return {}


def query(name: str, email: str | None = None) -> str:
    """The question with `$email` inlined as a literal (CLI backend)."""
    q = QUESTIONS[name].strip()
    if "$email" in q:
        if email is None:
            raise ValueError(f"question {name!r} needs an email")
        q = q.replace("$email", lit(email))
    return q
