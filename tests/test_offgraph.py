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
                + len(ex.resources) + len(ex.grants))
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
    assert "no person with email nobody@example.com" in capsys.readouterr().err
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
