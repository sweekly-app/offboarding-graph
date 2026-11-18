import json
import os
import shutil
from pathlib import Path

import pytest

from offgraph import demo, graph, oracle
from offgraph.data import load


@pytest.fixture(scope="module")
def ex(tmp_path_factory):
    folder = tmp_path_factory.mktemp("exports")
    demo.generate(folder, seed=7)
    return load(folder, demo.prices())


def test_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    demo.generate(a, seed=7)
    demo.generate(b, seed=7)
    for f in demo.FILES:
        assert (a / f).read_text(encoding="utf-8") == (b / f).read_text(encoding="utf-8")


def test_demo_data_is_invented(ex):
    for p in ex.people:
        assert p["email"].endswith("@example.com")
        assert p["name"].startswith("[Sample] ")


def test_demo_has_the_story(ex):
    leavers = [p["email"] for p in ex.people if p["status"] == "left"]
    assert len(leavers) == 5
    assert sum(1 for l in leavers if oracle.orphans(ex, l)) >= 2
    assert any(via == "direct" for l in leavers for _r, _a, via in oracle.reach(ex, l))
    assert oracle.seats(ex), "leavers must still hold paid seats"


def test_reach_includes_parent_group_grants(ex):
    p = ex.people[0]
    via = {v for _r, _a, v in oracle.reach(ex, p["email"])}
    assert p["team"].lower() in via
    parent_grants = {g["resource"] for g in ex.grants if g["grantee"] == "all-staff"}
    if parent_grants:
        assert "all-staff" in via


def test_sole_admin_ignores_leavers_as_cover(ex):
    for p in ex.people:
        for g in oracle.sole_admin(ex, p["email"]):
            others = [m for m in ex.memberships if m["group"] == g and m["role"] == "admin"
                      and m["email"] != p["email"]
                      and oracle.person(ex, m["email"])["status"] == "active"]
            assert others == []


def test_checklist_order(ex):
    lv = next(l["email"] for l in ex.people if l["status"] == "left" and oracle.orphans(ex, l["email"]))
    steps = oracle.checklist(ex, lv)
    assert steps[0].startswith("Transfer ownership")
    assert steps[-1] == "Disable the account everywhere"
    seat_steps = [i for i, s in enumerate(steps) if s.startswith("Remove the paid")]
    transfer = [i for i, s in enumerate(steps) if s.startswith("Transfer")]
    assert max(transfer) < min(seat_steps)


def test_literals_escape_quotes():
    assert graph.lit("O'Brien") == "'O\\'Brien'"
    assert graph.lit("a\\b") == "'a\\\\b'"
    assert graph.lit(True) == "true"
    assert graph.lit(3.5) == "3.5"


def test_load_script_has_one_statement_per_row(ex):
    script = graph.load_script(ex)
    lines = [l for l in script.splitlines() if l]
    expected = (len(ex.people) + len(ex.groups) + sum(1 for g in ex.groups if g["parent"])
                + len(ex.prices) + len(ex.memberships) + len(ex.accounts)
                + len(ex.resources) + len(ex.grants))
    assert len(lines) == expected
    assert all(l.endswith(";") for l in lines)


def test_query_substitutes_email():
    q = graph.query("reach", "a'b@example.com")
    assert "$email" not in q and "'a\\'b@example.com'" in q
    with pytest.raises(ValueError):
        graph.query("orphans")


def test_load_statements_cover_every_row(ex):
    stmts = graph.load_statements(ex)
    n = sum(len(params["rows"]) for _c, params in stmts)
    expected = (len(ex.people) + len(ex.groups) + sum(1 for g in ex.groups if g["parent"])
                + len(ex.prices) + len(ex.memberships) + len(ex.accounts)
                + len(ex.resources) + sum(1 for r in ex.resources if r["owner"]) + len(ex.grants))
    assert n == expected
    for cypher, params in stmts:
        assert cypher.startswith("UNWIND $rows AS r ") and set(params) == {"rows"}


def test_nobody_is_their_own_cover(ex):
    for p in ex.people:
        assert p["email"] not in oracle.orphans(ex, p["email"]).values()
    assert "o.email <> p.email" in graph.QUESTIONS["sole_admin"]
    assert "n.email <> p.email" in graph.QUESTIONS["orphans"]


def test_owner_suggestion_prefers_editors_of_the_resource(ex):
    found = False
    for p in ex.people:
        for res, (who, why) in oracle.orphans_with_reason(ex, p["email"]).items():
            assert who != p["email"]
            if why == "admin of a group that can edit it":
                editors = {g["grantee"] for g in ex.grants if g["resource"] == res
                           and g["access"] in ("edit", "admin")}
                assert any(m["email"] == who and m["group"] in editors and m["role"] == "admin"
                           for m in ex.memberships)
                found = True
    assert found


def _report_for(ex, email):
    from offgraph import report
    titles = {r["resource"]: r["title"] for r in ex.resources}
    reach = [{"resource": r, "access": a, "via": v, "title": titles[r]}
             for r, a, v in oracle.reach(ex, email)]
    orphans = [{"resource": r, "title": titles[r], "suggested_owner": w, "reason": why}
               for r, (w, why) in oracle.orphans_with_reason(ex, email).items()]
    seats = [{"email": e, "app": a, "monthly_usd": p} for e, a, p in oracle.seats(ex) if e == email]
    return report.html(email, reach, orphans, sorted(oracle.sole_admin(ex, email)), seats,
                       oracle.checklist(ex, email))


def test_report_shows_every_reachable_and_orphaned_resource(ex):
    for p in ex.people:
        if p["status"] != "left":
            continue
        page = _report_for(ex, p["email"])
        ids = {r for r, _a, _v in oracle.reach(ex, p["email"])} | set(oracle.orphans(ex, p["email"]))
        for r in ids:
            assert f">{r}</text>" in page
        assert page.count('class="n res risk"') == len(oracle.orphans(ex, p["email"]))
        assert f"<b>{len(ids)}</b>" in page and page.count('<g class="n res') == len(ids)
        assert "<script" not in page and "http" not in page.split("<main>")[1]


def test_report_escapes_html():
    from offgraph import report
    page = report.html("<zx>@example.com", [{"resource": "R<z1>", "access": "edit", "via": "g&h",
                                              "title": "<zt>"}], [], [], [], ["<zs>"])
    for raw in ("<zx>", "<z1>", "<zt>", "<zs>", "g&h"):
        assert raw not in page


def test_params_match_questions():
    for name, q in graph.QUESTIONS.items():
        if "$email" in q:
            assert graph.params(name, "a@example.com") == {"email": "a@example.com"}
            with pytest.raises(ValueError):
                graph.params(name)
        else:
            assert graph.params(name) == {}


# Real engine: runs only where Relate is installed (`pip install relate-db`, or RELATE_BIN).
from offgraph import engine  # noqa: E402


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_engine_matches_oracle(ex, tmp_path):
    from offgraph.cli import _checklist

    store = tmp_path / "store"
    engine.load(store, ex)
    engine.load(store, ex)  # a reload replaces, never duplicates
    for p in ex.people:
        e = p["email"]
        got = {(r["resource"], r["access"], r["via"]) for r in engine.rows(store, "reach", e)}
        assert got == oracle.reach(ex, e), e
        got_o = {r["resource"]: (r["suggested_owner"], r["reason"])
                 for r in engine.rows(store, "orphans", e)}
        assert got_o == oracle.orphans_with_reason(ex, e), e
        got_s = {r["group_name"] for r in engine.rows(store, "sole_admin", e)}
        assert got_s == oracle.sole_admin(ex, e), e
        assert _checklist(store, e) == oracle.checklist(ex, e), e
    got_seats = sorted((r["email"], r["app"], float(r["monthly_usd"]))
                       for r in engine.rows(store, "seats"))
    assert got_seats == oracle.seats(ex)


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_engine_handles_quotes_in_values(tmp_path):
    from offgraph.data import Exports
    ex = Exports(
        people=[{"email": "o'brien@example.com", "name": "[Sample] O'Brien", "team": "Ops", "status": "left"}],
        groups=[{"group": "ops", "parent": ""}],
        memberships=[{"email": "o'brien@example.com", "group": "ops", "role": "admin"}],
        accounts=[], resources=[{"resource": "r1", "app": "Chat", "kind": "channel",
                                 "title": "Bob's \\ notes", "owner": "o'brien@example.com"}],
        grants=[], prices={"Chat": 7.0})
    store = tmp_path / "q"
    engine.load(store, ex)
    assert [r["title"] for r in engine.rows(store, "orphans", "o'brien@example.com")] == ["Bob's \\ notes"]
    assert [r["group_name"] for r in engine.rows(store, "sole_admin", "o'brien@example.com")] == ["ops"]


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_cli_end_to_end(tmp_path, capsys, monkeypatch):
    from offgraph.cli import main
    monkeypatch.chdir(tmp_path)
    store = str(tmp_path / "t.rl")
    assert main(["demo", "exports"]) == 0
    assert main(["--store", store, "load", "exports"]) == 0
    capsys.readouterr()
    assert main(["--store", store, "seats"]) == 0
    assert "total: 180.00 USD a month" in capsys.readouterr().out
    assert main(["--store", store, "--json", "cypher", "MATCH (p:Person) RETURN count(p) AS n"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"n": 60}]
    assert main(["--store", store, "report", "dev.ruiz@example.com", "-o", "r.html"]) == 0
    assert "R001" in (tmp_path / "r.html").read_text(encoding="utf-8")


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_example_queries_run(ex, tmp_path):
    store = tmp_path / "x.rl"
    engine.load(store, ex)
    lines = (Path(__file__).parent.parent / "examples.cypher").read_text(encoding="utf-8").splitlines()
    code = "\n".join(l for l in lines if not l.strip().startswith("//"))
    stmts = [s.strip() for s in code.split(";") if s.strip()]
    assert len(stmts) >= 4
    for s in stmts:
        engine.run(store, s)


def test_every_owner_fallback_is_exercised_by_the_demo(ex):
    reasons = {why for p in ex.people for _w, why in oracle.orphans_with_reason(ex, p["email"]).values()}
    assert {"admin of a group that can edit it", "active editor (no admin left)"} <= reasons


def test_committed_exports_equal_the_seeded_demo(tmp_path):
    demo.generate(tmp_path, seed=7)
    committed = Path(__file__).parent.parent / "demo" / "exports"
    for f in demo.FILES:
        assert (committed / f).read_text(encoding="utf-8") == (tmp_path / f).read_text(encoding="utf-8"), f


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_committed_store_matches_its_exports():
    from offgraph.cli import _checklist
    root = Path(__file__).parent.parent / "demo"
    ex = load(root / "exports", demo.prices())
    store = root / "offboarding.rl"
    assert engine.run(store, "MATCH (p:Person) RETURN count(p) AS n") == [{"n": len(ex.people)}]
    for p in ex.people:
        e = p["email"]
        got = {r["resource"]: (r["suggested_owner"], r["reason"]) for r in engine.rows(store, "orphans", e)}
        assert got == oracle.orphans_with_reason(ex, e), e
        assert {(r["resource"], r["access"], r["via"]) for r in engine.rows(store, "reach", e)} == oracle.reach(ex, e)
        assert {r["group_name"] for r in engine.rows(store, "sole_admin", e)} == oracle.sole_admin(ex, e)
        assert _checklist(store, e) == oracle.checklist(ex, e), e
    assert sorted((r["email"], r["app"], float(r["monthly_usd"]))
                  for r in engine.rows(store, "seats")) == oracle.seats(ex)



# --- Audit cases (2026-11-13): own data that must not load silently wrong -----

HEAD = {
    "people.csv": "email,name,team,status",
    "groups.csv": "group,parent",
    "memberships.csv": "email,group,role",
    "accounts.csv": "email,app,paid,admin,last_active",
    "resources.csv": "resource,app,kind,title,owner",
    "grants.csv": "resource,grantee,grantee_type,access",
}


def write_exports(folder, prefix="", **tables):
    folder.mkdir(parents=True, exist_ok=True)
    for name, head in HEAD.items():
        rows = tables.get(name.replace(".csv", ""), [])
        (folder / name).write_text(prefix + "\n".join([head] + rows) + "\n", encoding="utf-8")
    return folder


BASE = dict(
    people=["lea@example.com,[Sample] Lea,Ops,left", "max@example.com,[Sample] Max,Ops,active",
            "nia@example.com,[Sample] Nia,Sales,active", "oli@example.com,[Sample] Oli,Sales,active"],
    groups=["ops,all", "all,"],
    memberships=["lea@example.com,ops,admin", "max@example.com,ops,member"],
    accounts=["lea@example.com,Chat,yes,no,2026-08-01"],
    resources=["r1,Chat,channel,Ops channel,lea@example.com"],
    grants=["r1,all,group,view"],
)


def test_bom_header_loads(tmp_path):
    ex = load(write_exports(tmp_path / "x", prefix="\ufeff", **BASE), demo.prices())
    assert [p["email"] for p in ex.people][0] == "lea@example.com"


@pytest.mark.parametrize("change, message", [
    ({"resources": ["r1,Chat,channel,Ops channel,gone@example.com"]}, "owner not in people.csv"),
    ({"accounts": ["lea@example.com,Video,yes,no,2026-08-01"]}, "app with no price"),
    ({"memberships": ["lea@example.com,nowhere,admin"]}, "group not in groups.csv"),
    ({"grants": ["r1,ghost@example.com,person,edit"]}, "person not in people.csv"),
    ({"grants": ["r9,all,group,view"]}, "resource not in resources.csv"),
    ({"groups": ["a,b", "b,a"]}, "its own ancestor"),
    ({"groups": [f"g{i},g{i + 1}" for i in range(7)] + ["g7,"]}, "nest 7 levels"),
    ({"people": BASE["people"] + ["lea@example.com,[Sample] Lea2,Ops,active"]}, "same email"),
    ({"memberships": ["lea@example.com,ops,boss"]}, "role must be one of"),
    ({"grants": ["r1,all,group,writer"]}, "access must be one of"),
    ({"accounts": ["lea@example.com,Chat,yes,no,2026-08-01", "lea@example.com,Chat,no,no,2026-08-01"]},
     "same account"),
])
def test_bad_exports_are_refused(tmp_path, change, message):
    from offgraph.data import ExportError
    with pytest.raises(ExportError, match=message):
        load(write_exports(tmp_path / "x", **{**BASE, **change}), demo.prices())


def test_missing_column_is_refused(tmp_path):
    from offgraph.data import ExportError
    folder = write_exports(tmp_path / "x", **BASE)
    (folder / "people.csv").write_text("mail,name,team,status\n", encoding="utf-8")
    with pytest.raises(ExportError, match="missing column"):
        load(folder, demo.prices())


def test_prices_csv_adds_apps(tmp_path):
    folder = write_exports(tmp_path / "x", **{**BASE, "accounts": ["lea@example.com,Video,yes,no,2026-08-01"]})
    (folder / "prices.csv").write_text("app,monthly_usd\nVideo,13.33\n", encoding="utf-8")
    from offgraph.data import read_prices
    ex = load(folder, read_prices(folder, demo.prices()))
    assert oracle.seats(ex) == [("lea@example.com", "Video", 13.33)]


def test_duplicate_rows_and_email_case(tmp_path):
    ex = load(write_exports(tmp_path / "x", **{**BASE,
              "people": BASE["people"] + ["LEA@Example.com,[Sample] Lea,Ops,left"],
              "accounts": BASE["accounts"] * 2}), demo.prices())
    assert ex.duplicates_removed == 2
    assert oracle.seats(ex) == [("lea@example.com", "Chat", 7.0)]
    assert oracle.checklist(ex, "lea@example.com").count("Name a second admin for group ops, "
                                                         "then remove lea@example.com as admin") == 1


TWO_PARENTS = {**BASE, "groups": ["ops,all", "ops,oncall", "all,", "oncall,"],
               "resources": BASE["resources"] + ["r2,Tracker,project,Pager,max@example.com"],
               "grants": ["r1,all,group,view", "r2,oncall,group,edit"]}
EDITORS = {**BASE, "groups": ["ops,all", "all,", "sales,all"],
           "memberships": ["lea@example.com,ops,admin", "oli@example.com,sales,member"],
           "people": BASE["people"][:1] + BASE["people"][2:],  # no other Ops person: no team admin
           "grants": ["r1,all,group,edit", "r1,nia@example.com,person,edit"]}


def test_two_parents_reach_both(tmp_path):
    ex = load(write_exports(tmp_path / "x", **TWO_PARENTS), demo.prices())
    assert {(r, v) for r, _a, v in oracle.reach(ex, "max@example.com")} == {("r1", "all"), ("r2", "oncall")}


def test_owner_fallback_counts_direct_and_nested_editors(tmp_path):
    ex = load(write_exports(tmp_path / "x", **EDITORS), demo.prices())
    # nia has a direct edit grant; oli edits through sales -> all. The lower email wins.
    assert oracle.orphans_with_reason(ex, "lea@example.com") == {
        "r1": ("nia@example.com", "active editor (no admin left)")}


def test_checklist_covers_seats_before_status_changes(ex):
    active = next(p["email"] for p in ex.people if p["status"] == "active"
                  and any(a["email"] == p["email"] and a["paid"] == "yes" for a in ex.accounts))
    assert any(s.startswith("Remove the paid") for s in oracle.checklist(ex, active))


def test_cli_errors_are_one_line(tmp_path, capsys):
    from offgraph.cli import main
    assert main(["--store", str(tmp_path / "none.rl"), "seats"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("offgraph: ") and "Traceback" not in err
    bad = write_exports(tmp_path / "bad", **{**BASE, "accounts": ["lea@example.com,Video,yes,no,x"]})
    assert main(["--store", str(tmp_path / "b.rl"), "load", str(bad)]) == 2
    assert "app with no price" in capsys.readouterr().err


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
@pytest.mark.parametrize("tables", [
    TWO_PARENTS, EDITORS,
    {**EDITORS, "memberships": EDITORS["memberships"] + ["oli@example.com,sales,admin"]},
    {**BASE, "grants": ["r1,max@example.com,person,view", "r1,max@example.com,person,edit"]},
], ids=["two-parents", "editors", "child-group-admin", "two-direct-grants"])
def test_engine_matches_oracle_on_audit_cases(tmp_path, tables):
    from offgraph.cli import _checklist
    ex = load(write_exports(tmp_path / "x", **tables), demo.prices())
    store = tmp_path / "s.rl"
    engine.load(store, ex)
    for p in ex.people:
        e = p["email"]
        assert {(r["resource"], r["access"], r["via"]) for r in engine.rows(store, "reach", e)} == oracle.reach(ex, e)
        assert {r["resource"]: (r["suggested_owner"], r["reason"])
                for r in engine.rows(store, "orphans", e)} == oracle.orphans_with_reason(ex, e)
        assert {r["group_name"] for r in engine.rows(store, "sole_admin", e)} == oracle.sole_admin(ex, e)
        assert _checklist(store, e) == oracle.checklist(ex, e)


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_cli_person_checks_and_json_after_subcommand(tmp_path, capsys):
    from offgraph.cli import main
    store = str(tmp_path / "t.rl")
    assert main(["--store", store, "load", str(write_exports(tmp_path / "x", **BASE))]) == 0
    capsys.readouterr()
    assert main(["--store", store, "checklist", "nobody@example.com"]) == 2
    assert "no person with email or login nobody@example.com" in capsys.readouterr().err
    assert main(["checklist", "LEA@example.com", "--store", store, "--json"]) == 0
    steps = json.loads(capsys.readouterr().out)
    assert steps[-1] == "Disable the account everywhere" and any("Chat seat" in s for s in steps)
    out = tmp_path / "r.html"
    assert main(["--store", store, "report", "lea@example.com", "-o", str(out)]) == 0
    assert "●" in out.read_bytes().decode("utf-8")


def test_access_case_and_prices_errors(tmp_path):
    from offgraph.data import ExportError, read_prices
    ex = load(write_exports(tmp_path / "x", **{**BASE, "grants": ["r1,all,group,Edit"]}), demo.prices())
    assert ex.grants[0]["access"] == "edit"
    folder = write_exports(tmp_path / "y", **BASE)
    (folder / "prices.csv").write_text("app,monthly_usd\nVideo,thirteen\n", encoding="utf-8")
    with pytest.raises(ExportError, match="monthly_usd must be a number"):
        read_prices(folder, demo.prices())
    for bad, msg in (("Video,13,33", "unquoted comma"), ("Video,-1", "zero or more"), ("Video,nan", "zero or more")):
        (folder / "prices.csv").write_text(f"app,monthly_usd\n{bad}\n", encoding="utf-8")
        with pytest.raises(ExportError, match=msg):
            read_prices(folder, demo.prices())


def test_direct_grant_step_once(tmp_path):
    ex = load(write_exports(tmp_path / "x", **{**BASE, "grants": ["r1,max@example.com,person,view",
                                                                  "r1,max@example.com,person,edit"]}), demo.prices())
    assert oracle.checklist(ex, "max@example.com").count("Revoke the direct grant on r1") == 1


def test_admin_of_a_nested_group_is_not_preferred(tmp_path):
    # all-staff holds the grant; oli only administers sales, nested under it. A company-wide
    # grant must not make an unrelated team's admin the first choice (re-audit RO-26).
    tables = {**EDITORS, "memberships": EDITORS["memberships"] + ["oli@example.com,sales,admin"]}
    ex = load(write_exports(tmp_path / "x", **tables), demo.prices())
    assert oracle.orphans_with_reason(ex, "lea@example.com") == {
        "r1": ("nia@example.com", "active editor (no admin left)")}
    tables["memberships"] = tables["memberships"] + ["oli@example.com,all,admin"]
    ex = load(write_exports(tmp_path / "y", **tables), demo.prices())
    assert oracle.orphans_with_reason(ex, "lea@example.com") == {
        "r1": ("oli@example.com", "admin of a group that can edit it")}


def test_no_text_file_relies_on_the_locale_encoding():
    root = Path(__file__).parent.parent
    import re
    for f in list((root / "offgraph").glob("*.py")) + list((root / "tests").glob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(read_text|write_text)\(|\.open\(", line) and "encoding" not in line \
                    and "test_no_text_file" not in line and "re.search" not in line:
                nxt = f.read_text(encoding="utf-8").splitlines()[n:n + 8]
                assert any("encoding=" in l for l in nxt), f"{f.name}:{n}"


# --- Review round 1 (2026-11-15): the hops made visible -------------------------

DEEP = {**BASE, "groups": ["ops,all", "all,", "a,b", "b,c", "c,all", "x,c"],
        "memberships": BASE["memberships"] + ["max@example.com,a,member", "max@example.com,x,member"],
        "resources": BASE["resources"] + ["r2,Docs,shared drive,Budget,nia@example.com"],
        "grants": ["r1,all,group,view", "r2,c,group,edit"]}


def test_reach_paths_are_shortest_and_valid(tmp_path):
    ex = load(write_exports(tmp_path / "x", **DEEP), demo.prices())
    got = oracle.reach_paths(ex, "max@example.com")
    assert got[("r2", "c")] == ["x", "c"]           # x -> c beats a -> b -> c
    assert got[("r1", "all")] == ["ops", "all"]     # ops -> all beats x -> c -> all
    par = {(g["group"], g["parent"]) for g in ex.groups}
    member = {m["group"] for m in ex.memberships if m["email"] == "max@example.com"}
    for (res, via), chain in got.items():
        assert chain[0] in member and chain[-1] == via
        assert all((a, b) in par for a, b in zip(chain, chain[1:]))
    assert set(got) == {(r, v) for r, _a, v in oracle.reach(ex, "max@example.com") if v != "direct"}


def test_demo_has_a_three_group_chain(ex):
    fin = next(m["email"] for m in ex.memberships if m["group"] == "finance" and m["role"] == "admin")
    chains = oracle.reach_paths(ex, fin).values()
    assert ["budget-owners", "spend-approvers", "leadership"] in chains


def test_report_draws_the_chain_and_marks_sole_admin(ex):
    from offgraph import report
    fin = next(m["email"] for m in ex.memberships if m["group"] == "finance" and m["role"] == "admin")
    titles = {r["resource"]: r["title"] for r in ex.resources}
    paths = oracle.reach_paths(ex, fin)
    reach = [{"resource": r, "access": a, "via": v, "title": titles[r],
              "path": ["direct"] if v == "direct" else paths[(r, v)]} for r, a, v in oracle.reach(ex, fin)]
    page = report.html(fin, reach, [], ["finance"], [], ["Disable the account everywhere"])
    for g in ("budget-owners", "spend-approvers", "leadership"):
        assert f">{g}</text>" in page
    assert page.count(">CHILD_OF</text>") >= 3 and ">MEMBER_OF</text>" in page
    assert 'class="n grp sole"' in page and "no other active admin" in page
    assert "</b>group left without an admin" in page


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
@pytest.mark.parametrize("which", ["demo", "deep"])
def test_engine_paths_match_oracle(ex, tmp_path, which):
    from offgraph.cli import _reach
    data = ex if which == "demo" else load(write_exports(tmp_path / "x", **DEEP), demo.prices())
    store = tmp_path / "p.rl"
    engine.load(store, data)
    for p in data.people:
        e = p["email"]
        want = oracle.reach_paths(data, e)
        for r in _reach(store, e):
            if r["via"] == "direct":
                assert r["path"] == ["direct"]
            else:
                assert r["path"] == want[(r["resource"], r["via"])], (e, r)


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_cli_shows_paths_properties_and_empty_answers(tmp_path, capsys):
    from offgraph.cli import main
    store = str(tmp_path / "t.rl")
    assert main(["--store", store, "load", str(write_exports(tmp_path / "x", **DEEP))]) == 0
    capsys.readouterr()
    assert main(["--store", store, "reach", "max@example.com"]) == 0
    assert "path=x > c" in capsys.readouterr().out
    assert main(["--store", store, "sole-admin", "max@example.com"]) == 0
    assert capsys.readouterr().out.startswith("none: ")
    assert main(["--store", store, "orphans", "max@example.com"]) == 0
    assert capsys.readouterr().out.startswith("none: ")
    assert main(["--store", store, "cypher",
                 "MATCH path = (p:Person {email: 'max@example.com'})-[:MEMBER_OF]->(g:Group {name: 'x'}) RETURN p, path"]) == 0
    out = capsys.readouterr().out
    assert "(:Person {email: 'max@example.com'" in out and "-[:MEMBER_OF {role: 'member'}]->(:Group {name: 'x'})" in out
    assert main(["--store", store, "cypher", "MATCH (p:Person) RETURN p.email", "--limit", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[1] == "... 3 more rows (--limit 0 shows all)"
    assert main(["--store", store, "--json", "cypher", "MATCH (g:Group {name: 'x'}) RETURN g"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"g": {"labels": ["Group"], "name": "x"}}]


def test_load_problems_are_capped(tmp_path, capsys):
    from offgraph.cli import MAX_PROBLEMS, main
    rows = [f"p{i}@example.com,ghost,member" for i in range(40)]
    bad = write_exports(tmp_path / "bad", **{**BASE, "memberships": rows,
                                             "accounts": [f"q{i}@example.com,Chat,yes,no,x" for i in range(5)]})
    assert main(["--store", str(tmp_path / "b.rl"), "load", str(bad)]) == 2
    err = capsys.readouterr().err.splitlines()
    assert len(err) <= MAX_PROBLEMS + 2


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_mcp_tools_answer_like_the_cli(ex, tmp_path):
    import io
    import subprocess
    import sys
    from offgraph import mcp
    from offgraph.cli import _checklist, _reach
    store = tmp_path / "m.rl"
    engine.load(store, ex)
    fin = next(m["email"] for m in ex.memberships if m["group"] == "finance" and m["role"] == "admin")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": mcp.PROTOCOL, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "offboarding_checklist", "arguments": {"email": fin.upper()}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "access_paths", "arguments": {"email": fin}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "who_takes_over", "arguments": {"email": "nobody@example.com"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "run_cypher", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "leaver_seats"}}]
    stdin = "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"
    # the real command, as an assistant would start it
    res = subprocess.run([sys.executable, "-m", "offgraph.cli", "--store", str(store), "mcp"], input=stdin,
                         capture_output=True, text=True, encoding="utf-8", timeout=60,
                         cwd=Path(__file__).parent.parent)
    assert res.returncode == 0, res.stderr
    replies = [json.loads(l) for l in res.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7, None]  # no reply to the notification
    assert replies[0]["result"]["capabilities"] == {"tools": {}}
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert names == {"offboarding_checklist", "who_takes_over", "access_paths", "sole_admin_groups", "leaver_seats",
                     "single_admin_resources", "what_if_removed"}
    assert replies[2]["result"]["structuredContent"]["steps"] == _checklist(store, fin)
    assert replies[3]["result"]["structuredContent"]["resources"] == _reach(store, fin)
    assert replies[4]["result"]["isError"] is True
    assert replies[5]["error"]["code"] == -32602
    assert replies[6]["result"]["structuredContent"]["total_monthly_usd"] == 180.0
    assert replies[7]["error"]["code"] == -32700
    out = io.StringIO()
    assert mcp.serve(store, io.StringIO('{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), out) == 0
    assert json.loads(out.getvalue()) == {"jsonrpc": "2.0", "id": 9, "result": {}}
    r = mcp.handle(store, {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                           "params": {"name": "who_takes_over", "arguments": {}}})
    assert r["result"]["isError"] and "needs an email" in r["result"]["content"][0]["text"]
    r = mcp.handle(store, {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                           "params": {"name": "single_admin_resources", "arguments": {}}})
    assert {x["resource"]: x["admin"] for x in r["result"]["structuredContent"]["resources"]} == oracle.single_admins(ex)
    tia = next(p["email"] for p in ex.people if p["status"] == "active")
    r = mcp.handle(store, {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                           "params": {"name": "what_if_removed", "arguments": {"emails": [tia.upper()]}}})
    got, want = r["result"]["structuredContent"], oracle.whatif(ex, [tia])
    assert got["emails"] == [tia] and [x["resource"] for x in got["no_editor"]] == want["no_editor"]
    assert got["no_admin_groups"] == want["no_admin_groups"]
    for bad in ({}, {"emails": []}, {"emails": "a@example.com"}, {"emails": [""]}):
        r = mcp.handle(store, {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                               "params": {"name": "what_if_removed", "arguments": bad}})
        assert r["result"]["isError"] and "needs emails" in r["result"]["content"][0]["text"]


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_audit_matches_oracle_and_the_per_person_answers(ex, tmp_path, capsys):
    from offgraph import audit
    from offgraph.cli import main
    store = tmp_path / "a.rl"
    engine.load(store, ex)
    s = audit.summary(store)
    assert audit.plain(s) == oracle.audit(ex)
    assert s["seats_usd"] == round(sum(l["seats_usd"] for l in s["leavers"]), 2) == 180.0
    out = tmp_path / "audit.html"
    assert main(["--store", str(store), "audit", "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("5 people marked as left; 19 paid seats still held, 180.00 USD a month")
    page = out.read_text(encoding="utf-8")
    assert page.count("<svg") == 5 and "<script" not in page
    for l in s["leavers"]:
        assert f"id='{l['email']}'" in page
    assert main(["--store", str(store), "--json", "audit"]) == 0
    assert json.loads(capsys.readouterr().out) == oracle.audit(ex)
    assert main(["--store", str(store), "audit", "-o", str(tmp_path / "no" / "such" / "a.html")]) == 2
    res = capsys.readouterr()
    assert res.out == "" and res.err.startswith("offgraph: cannot write ")


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_cypher_write_is_refused_in_offgraph_words(ex, tmp_path, capsys):
    from offgraph.cli import main
    store = tmp_path / "w.rl"
    engine.load(store, ex)
    before = store.read_bytes()
    assert main(["--store", str(store), "cypher", "MATCH (n) DETACH DELETE n"]) == 2
    assert "cypher is read-only" in capsys.readouterr().err
    assert store.read_bytes() == before


# --- GitHub import (documented REST shapes; examples/github is made up in that shape) ---

GH = Path(__file__).parent.parent / "examples" / "github"


def _gh_tables(tmp_path):
    from offgraph import github
    out = tmp_path / "gh"
    github.write(github.convert(GH, GH / "logins.csv"), out,
                 {"people.csv": GH / "people.csv", "prices.csv": GH / "prices.csv"})
    return out


def test_import_github_maps_teams_roles_and_permissions(tmp_path):
    out = _gh_tables(tmp_path)
    from offgraph.data import read_prices
    ex = load(out, read_prices(out, demo.prices()))
    assert {(g["group"], g["parent"]) for g in ex.groups} == {
        ("engineering", ""), ("platform", "engineering"), ("release-managers", "platform")}
    roles = {(m["email"], m["group"]): m["role"] for m in ex.memberships}
    assert roles[("ana.k@example.com", "engineering")] == "admin"      # maintainer
    assert roles[("cy.m@example.com", "engineering")] == "member"
    assert len(ex.accounts) == 4                                        # two pages back to back
    assert {a["email"] for a in ex.accounts if a["admin"] == "yes"} == {"ana.k@example.com"}
    acc = {(g["resource"], g["grantee"]): g["access"] for g in ex.grants}
    assert acc[("GitHub:example-org/api", "engineering")] == "edit"     # write
    assert acc[("GitHub:example-org/docs-site", "engineering")] == "view"  # read
    assert acc[("GitHub:example-org/deploy-scripts", "di.n@example.com")] == "admin"
    owners = {r["resource"]: r["owner"] for r in ex.resources}
    assert owners["GitHub:example-org/docs-site"] == "cy.m@example.com"  # the direct admin, not the writer
    assert oracle.orphans_with_reason(ex, "di.n@example.com") == {
        "GitHub:example-org/deploy-scripts": ("bo.l@example.com", "admin of a group that can edit it")}
    assert oracle.reach_paths(ex, "di.n@example.com")[("GitHub:example-org/api", "engineering")] == [
        "release-managers", "platform", "engineering"]


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_import_github_end_to_end(tmp_path, capsys):
    from offgraph.cli import _checklist, _reach, main
    E = str(GH)
    out = tmp_path / "gh"
    assert main(["import-github", E, "--logins", f"{E}/logins.csv", "--people", f"{E}/people.csv",
                 "--prices", f"{E}/prices.csv", "-o", str(out)]) == 0
    store = tmp_path / "gh.rl"
    assert main(["--store", str(store), "load", str(out)]) == 0
    from offgraph.data import read_prices
    ex = load(out, read_prices(out, demo.prices()))
    for p in ex.people:
        e = p["email"]
        assert {(r["resource"], r["access"], r["via"]) for r in engine.rows(store, "reach", e)} == oracle.reach(ex, e)
        assert _checklist(store, e) == oracle.checklist(ex, e)
    assert any(r["path"] == ["release-managers", "platform", "engineering"] for r in _reach(store, "di.n@example.com"))
    capsys.readouterr()
    assert main(["import-github", E, "--logins", f"{E}/logins.csv", "-o", str(out)]) == 2
    assert "already has groups.csv" in capsys.readouterr().err


def test_import_github_refuses_what_it_cannot_map(tmp_path):
    import shutil as sh
    from offgraph import github
    from offgraph.data import ExportError
    src = tmp_path / "src"
    sh.copytree(GH, src)
    (src / "logins.csv").write_text("login,email\nexample-ana-k,ana.k@example.com\n", encoding="utf-8")
    (src / "collaborators" / "docs-site.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ExportError) as e:
        github.convert(src, src / "logins.csv")
    assert "no email for example-bo-l, example-cy-m, example-di-n" in str(e.value) and "no owner for docs-site" in str(e.value)
    (src / "repo-owners.csv").write_text("repo,owner\ndocs-site,example-ana-k\n", encoding="utf-8")
    (src / "logins.csv").write_text((GH / "logins.csv").read_text(encoding="utf-8"), encoding="utf-8")
    t = github.convert(src, src / "logins.csv", src / "repo-owners.csv")
    assert {r["title"]: r["owner"] for r in t["resources.csv"]}["docs-site"] == "ana.k@example.com"
    (src / "teams.json").write_text('{"message": "Not Found"}', encoding="utf-8")
    with pytest.raises(ExportError, match="expected a JSON array"):
        github.convert(src, src / "logins.csv")


def test_demo_refuses_to_overwrite_exports(tmp_path, capsys):
    from offgraph.cli import main
    (tmp_path / "people.csv").write_text("email,name,team,status\nme@example.com,Me,Ops,active\n", encoding="utf-8")
    assert main(["demo", str(tmp_path)]) == 2
    assert "already has people.csv" in capsys.readouterr().err
    assert (tmp_path / "people.csv").read_text(encoding="utf-8").startswith("email,name,team,status\nme@")
    assert main(["demo", str(tmp_path), "--force"]) == 0


def test_mcp_malformed_params_get_an_error(tmp_path):
    from offgraph import mcp
    for params in ([1], {"name": ["x"]}, {"name": "access_paths", "arguments": "dev"}):
        r = mcp.handle(tmp_path / "none.rl", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert r["error"]["code"] == -32602


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_whatif_matches_oracle_and_leaves_the_store_untouched(ex, tmp_path, capsys):
    from offgraph.cli import main
    store = tmp_path / "w.rl"
    engine.load(store, ex)
    before = store.read_bytes()
    groups = [[p["email"]] for p in ex.people] + [[p["email"] for p in ex.people if p["team"] == "Ops"]]
    for emails in groups:
        got = engine.whatif(store, emails)
        want = oracle.whatif(ex, emails)
        assert [r["resource"] for r in got["unowned"]] == want["unowned"], emails
        assert got["no_admin_groups"] == want["no_admin_groups"], emails
        assert [r["resource"] for r in got["no_editor"]] == want["no_editor"], emails
        assert [r["resource"] for r in got["no_admin"]] == want["no_admin"], emails
        assert sorted((r["email"], r["app"], float(r["monthly_usd"])) for r in got["seats"]) == want["seats"]
        assert got["seats_usd"] == want["seats_usd"]
    assert store.read_bytes() == before
    ops = groups[-1]
    assert oracle.whatif(ex, ops)["no_editor"], "removing a whole team must leave something nobody can edit"
    fin_tia = next(m["email"] for m in ex.memberships if m["group"] == "leadership" and m["role"] == "admin")
    assert main(["--store", str(store), "whatif", fin_tia.upper()]) == 0
    out = capsys.readouterr().out
    assert "groups left with no active admin: budget-owners, leadership, spend-approvers" in out
    assert "(Company budget)" in out and "rolled back" in out
    assert main(["--store", str(store), "whatif", "nobody@example.com"]) == 2
    assert store.read_bytes() == before


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_example_mcp_client_talks_to_the_server():
    import subprocess
    import sys
    root = Path(__file__).parent.parent
    res = subprocess.run([sys.executable, "examples/mcp_client.py", "dev.ruiz@example.com",
                          sys.executable, "-m", "offgraph.cli", "--store", "demo/offboarding.rl", "mcp"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    assert "tools/list: 7 tools" in res.stdout and "1. Transfer ownership of R001" in res.stdout
    bare = subprocess.run([sys.executable, "examples/mcp_client.py"], cwd=root, capture_output=True, text=True, timeout=60)
    assert bare.returncode == 1 and bare.stderr.startswith("usage:") and "Traceback" not in bare.stderr


# --- Re-audit 43bb616c: RO-52, RO-53, RO-54 ---

@pytest.mark.parametrize("change", ["empty-members", "missing-members", "missing-maintainers",
                                    "missing-team-repos", "missing-collaborators"])
def test_import_github_refuses_empty_or_missing_files(tmp_path, change):
    import shutil as sh
    from offgraph import github
    from offgraph.data import ExportError
    src = tmp_path / "src"
    sh.copytree(GH, src)
    target = {"empty-members": "team-members/release-managers.json",
              "missing-members": "team-members/release-managers.json",
              "missing-maintainers": "team-maintainers/release-managers.json",
              "missing-team-repos": "team-repos/platform.json",
              "missing-collaborators": "collaborators/api.json"}[change]
    if change.startswith("empty"):
        (src / target).write_text("", encoding="utf-8")
    else:
        (src / target).unlink()
    with pytest.raises(ExportError) as e:
        github.convert(src, src / "logins.csv", src / "repo-owners.csv" if (src / "repo-owners.csv").exists() else None)
    assert target in str(e.value)


def test_import_github_skips_inherited_team_members(tmp_path):
    from offgraph import github
    t = github.convert(GH, GH / "logins.csv")
    pairs = {(m["email"], m["group"]) for m in t["memberships.csv"]}
    # di-n is listed under engineering and platform with inherited: true (a member of release-managers)
    assert ("di.n@example.com", "release-managers") in pairs
    assert ("di.n@example.com", "platform") not in pairs and ("di.n@example.com", "engineering") not in pairs
    raw = json.loads((GH / "team-members" / "engineering.json").read_text(encoding="utf-8"))
    assert any(u.get("inherited") for u in raw), "the example must carry the documented inherited flag"


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_whatif_on_someone_already_left_says_so(ex, tmp_path, capsys):
    from offgraph.cli import main
    store = tmp_path / "l.rl"
    engine.load(store, ex)
    left = next(p["email"] for p in ex.people if p["status"] == "left")
    assert engine.whatif(store, [left])["already_left"] == [left]
    assert main(["--store", str(store), "whatif", left]) == 0
    out = capsys.readouterr().out
    assert f"{left} is already marked as left" in out and "offgraph report" in out


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_mcp_server_survives_a_cp1252_console(ex, tmp_path):
    import os
    import subprocess
    import sys
    store = tmp_path / "c.rl"
    engine.load(store, ex)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "access_paths", "arguments": {"email": "zoë.ñ@example.com"}}}
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    res = subprocess.run([sys.executable, "-m", "offgraph.cli", "--store", str(store), "mcp"],
                         input=(json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"),
                         capture_output=True, env=env, timeout=60, cwd=Path(__file__).parent.parent)
    assert res.returncode == 0, res.stderr
    reply = json.loads(res.stdout.decode("utf-8"))
    assert reply["result"]["isError"] and "zoë.ñ@example.com" in reply["result"]["content"][0]["text"]


# --- The browser page (docs/index.html, `offgraph web`) ---

def test_web_page_is_the_generated_page_for_the_demo(tmp_path):
    from offgraph import web
    root = Path(__file__).parent.parent
    ex = load(root / "demo" / "exports", demo.prices())
    want = web.page(ex, "an invented 60-person demo company", "dev.ruiz@example.com", root / "examples.cypher",
                    "the demo company")
    assert (root / "docs" / "index.html").read_text(encoding="utf-8") == want, \
        "regenerate: offgraph web demo/exports -o docs/index.html --title 'an invented 60-person demo company' --name 'the demo company' --start dev.ruiz@example.com"
    assert "__DATA__" not in want and "__RELATE__" not in want and "__TITLE__" not in want
    blob = want.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0]
    data = json.loads(blob)
    assert data["questions"]["reach"] == graph.QUESTIONS["reach"].strip() and data["remove"] == graph.REMOVE
    assert len(data["examples"]) == 5 and len(data["people"]) == 60
    assert "@relate/relate-wasm@1.0.1/relate.js" in want and "__" + "SRI" not in want
    for name, digest in web.SRI.items():  # every CDN file is pinned by its hash
        assert want.count(digest) >= 1, name
    assert 'integrity="' + web.SRI["relate.js"] + '" crossorigin="anonymous"' in want


def test_web_command_and_escaping(tmp_path, capsys):
    from offgraph.cli import main
    from offgraph import web
    folder = write_exports(tmp_path / "x", **{**BASE, "resources": ["r1,Chat,channel,</script><!--<script><b>x &amp;,lea@example.com"]})
    out = tmp_path / "p.html"
    assert main(["web", str(folder), "-o", str(out), "--title", "<Acme>", "--start", "LEA@example.com"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "&lt;Acme&gt;" in page and page.count("</script>") == 4  # import map, CDN timer, data, module: the data cannot close its own tag
    blob = page.split('id="data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in blob and ">" not in blob and "&" not in blob
    assert any("<!--<script>" in r[0]["title"] for r in [json.loads(blob)["load"][6][1]["rows"]])
    assert main(["web", str(folder), "-o", str(out), "--start", "nobody@example.com"]) == 2


# --- Peribolos import (GitHub access kept as code; examples/peribolos is made up in that format) ---

PB = Path(__file__).parent.parent / "examples" / "peribolos"


def _pb_exports(tmp_path):
    from offgraph import peribolos
    from offgraph.data import read_prices
    out = tmp_path / "pb"
    peribolos.write(peribolos.convert(PB), out)
    return out, load(out, read_prices(out, {}))


def test_import_peribolos_maps_teams_nesting_and_permissions(tmp_path):
    _out, ex = _pb_exports(tmp_path)
    assert {(g["group"], g["parent"]) for g in ex.groups} == {
        ("docs-admins", ""), ("platform", ""), ("platform-leads", "platform"), ("platform-oncall", "platform"),
        ("publishing", ""), ("release", "")}
    roles = {(m["email"], m["group"]): m["role"] for m in ex.memberships}
    assert roles[("ben-sample", "platform")] == "admin" and roles[("cara-sample", "platform")] == "member"
    assert {a["email"] for a in ex.accounts if a["admin"] == "yes"} == {"org-owner", "release-bot"}
    assert all(p["status"] == "active" for p in ex.people) and len(ex.people) == 8
    acc = {(g["resource"], g["grantee"]): g["access"] for g in ex.grants}
    assert acc[("GitHub:peribolos/api", "platform")] == "edit"            # write
    assert acc[("GitHub:peribolos/deploy-scripts", "platform")] == "view"  # read
    assert acc[("GitHub:peribolos/deploy-scripts", "platform-oncall")] == "comment"  # triage
    assert all(r["owner"] == "" for r in ex.resources) and ex.prices == {"GitHub": 0.0}
    # a child team reaches its parent's repositories
    assert oracle.reach_paths(ex, "dev-sample")[("GitHub:peribolos/deploy-scripts", "platform")] == [
        "platform-leads", "platform"]
    # org admins are not counted; release-tools has no other admin, so it is not listed
    assert oracle.single_admins(ex) == {"GitHub:peribolos/docs-site": "ana-sample",
                                        "GitHub:peribolos/api": "dev-sample",
                                        "GitHub:peribolos/mirror": "publish-bot"}


def test_import_peribolos_single_file_forms_and_refusals(tmp_path):
    from offgraph import peribolos
    from offgraph.data import ExportError
    one = tmp_path / "config.yaml"
    one.write_text("orgs:\n  acme:\n    admins: [boss]\n    teams:\n      t:\n        members: [x]\n"
                   "        repos: {app: maintain}\n  other:\n    admins: [y]\n", encoding="utf-8")
    with pytest.raises(ExportError, match="holds 2 organisations"):
        peribolos.convert(one)
    t = peribolos.convert(one, "acme")
    assert t["grants.csv"] == [{"resource": "GitHub:acme/app", "grantee": "t", "grantee_type": "group",
                                "access": "edit"}]
    with pytest.raises(ExportError, match="no organisation 'nope'"):
        peribolos.convert(one, "nope")
    bad = tmp_path / "bad.yaml"
    bad.write_text("admins: [a]\nteams:\n  t:\n    repos: {app: owner}\n  u: just-text\n", encoding="utf-8")
    with pytest.raises(ExportError) as e:
        peribolos.convert(bad)
    assert "unknown permission 'owner'" in str(e.value) and "team 'u' is not a mapping" in str(e.value)
    dup = tmp_path / "dup"
    (dup / "a").mkdir(parents=True)
    (dup / "org.yaml").write_text("admins: [a]\nteams:\n  t: {members: [b]}\n", encoding="utf-8")
    (dup / "a" / "teams.yaml").write_text("teams:\n  t: {members: [c]}\n", encoding="utf-8")
    with pytest.raises(ExportError, match="also defined in org.yaml"):
        peribolos.convert(dup)
    with pytest.raises(ExportError, match="has no org.yaml"):
        peribolos.convert(dup / "a")
    (tmp_path / "broken.yaml").write_text("teams: [unclosed\n", encoding="utf-8")
    with pytest.raises(ExportError, match="not YAML"):
        peribolos.convert(tmp_path / "broken.yaml")
    out = tmp_path / "out"
    peribolos.write(t, out)
    with pytest.raises(ExportError, match="already has people.csv"):
        peribolos.write(t, out)


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_import_peribolos_end_to_end_matches_oracle(tmp_path, capsys):
    from offgraph.cli import main
    out = tmp_path / "pb"
    assert main(["import-peribolos", str(PB), "--org", "acme", "-o", str(out)]) == 0
    store = tmp_path / "pb.rl"
    assert main(["--store", str(store), "load", str(out)]) == 0
    from offgraph.data import read_prices
    ex = load(out, read_prices(out, {}))
    for p in ex.people:   # resources without an owner load, and reach still matches
        e = p["email"]
        assert {(r["resource"], r["access"], r["via"]) for r in engine.rows(store, "reach", e)} == oracle.reach(ex, e)
    got = {r["resource"]: r["admin"] for r in engine.rows(store, "single_admins")}
    assert got == oracle.single_admins(ex) and "GitHub:acme/api" in got
    capsys.readouterr()
    assert main(["--store", str(store), "single-admins"]) == 0
    text = capsys.readouterr().out
    assert "resource=GitHub:acme/api  title=api  admin=dev-sample" in text and "3 resources rest on one admin" in text
    w = engine.whatif(store, ["dev-sample"])
    assert [r["resource"] for r in w["no_admin"]] == ["GitHub:acme/api"] == oracle.whatif(ex, ["dev-sample"])["no_admin"]
    assert w["unowned"] == [] and w["seats_usd"] == 0.0
    capsys.readouterr()
    assert main(["--store", str(store), "whatif", "ana-sample"]) == 0
    assert "resources left with no admin (app admins not counted): GitHub:acme/docs-site (docs-site)" in capsys.readouterr().out


@pytest.mark.engine
@pytest.mark.skipif(not engine.available(), reason="Relate not installed")
def test_single_admins_matches_oracle_on_the_demo(ex, tmp_path):
    store = tmp_path / "s.rl"
    engine.load(store, ex)
    assert {r["resource"]: r["admin"] for r in engine.rows(store, "single_admins")} == oracle.single_admins(ex)
