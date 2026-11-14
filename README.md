# Offboarding Graph

**What does a leaver still have, and what breaks if you remove it?** Your SaaS exports in one Relate graph, five answers from the command line, one HTML page to act on.

## Use case
Someone leaves. Their access is spread over a docs suite, chat, an issue tracker, a wiki and a code host, and each tool exports a flat list. The risks are in the connections between those lists:

- a shared drive **only they own**, and who should own it next;
- a group where **they are the only active admin**;
- **paid seats** still assigned to people who have left;
- everything they can reach **through nested groups**, not just directly.

The relationships that matter: person `MEMBER_OF` group (with a role), group `CHILD_OF` group (nesting), group or person `CAN` resource (access level), person `OWNS` resource, person `HAS_ACCOUNT` account `IN_APP` app (seat, price, admin flag). The question "who should take over this drive?" needs ownership, grants, group admin roles and person status together; a single table can't answer it.

| Command | Answers |
|---|---|
| `offgraph reach <email>` | every resource the person can reach, directly or through nested groups, and through which group |
| `offgraph orphans <email>` | resources only they own, with a suggested new owner (an active admin of a group that holds an edit grant on the resource, else an admin in their team, else any active person who can edit it) and why |
| `offgraph sole-admin <email>` | groups whose only active admin they are |
| `offgraph seats` | paid seats still held by people marked as left, with the monthly cost |
| `offgraph checklist <email>` | the steps in a safe order (works before HR marks them as left: their own paid seats are included): transfer ownership → second admin → remove admin rights → remove seats → revoke direct grants → disable |
| `offgraph report <email>` | one self-contained HTML page: the access graph, the risks and the checklist |
| `offgraph cypher "<statement>"` | your own read-only Cypher on the same store |

## How it uses Relate
- **Mode: single file** (`.rl`), through the Python library (`relate-db`, the engine in-process).
- **Why this mode:** offboarding data is a snapshot an IT person rebuilds from fresh exports, keeps next to the ticket and may hand to an auditor. One file does that: copy it, attach it, open it read-only for reports while nothing else can write to it. No server to run, and nothing leaves the machine.
- **What the graph models:** `(:Person {email, name, team, status})`, `(:Group {name})`, `(:App {name, price})`, `(:Account {key, paid, admin, last_active})`, `(:Resource {id, app, kind, title})`, joined by `MEMBER_OF {role}`, `CHILD_OF`, `CAN {access}`, `OWNS`, `HAS_ACCOUNT`, `IN_APP`.
- **How:** the CSVs load as nine parameterised `UNWIND … CREATE` statements in one transaction (a reload replaces the graph). Each question is one Cypher query in `offgraph/graph.py`: reach walks `MEMBER_OF` then `CHILD_OF*0..6` to `CAN`; the owner suggestion matches admins of the groups that hold an edit grant on the resource, then admins in the leaver's team, then any active person who can edit it (a direct grant, or through nested groups); sole-admin counts the other active admins on each group.
- **Checked:** `offgraph/oracle.py` computes every answer again in plain Python from the CSVs, and the engine tests compare the two for all 60 demo people, both on a fresh load and on the committed store.

## Setup and run
Prerequisites: Python 3.10 or later on Linux, macOS or Windows, and git. Clone this repository and open a terminal in its folder, then copy the commands for your system; each line is one command.

macOS and Linux:
```
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[test]"
```
Windows (PowerShell or cmd). These lines call the programs inside `.venv` directly, so nothing needs activating and PowerShell's script policy does not matter. If `py` is not found (Python from the Microsoft Store has no `py` launcher), use `python` in the third line.
```
py -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[test]"
```
On Windows, write `.venv\Scripts\offgraph` wherever the commands below say `offgraph`, and `.venv\Scripts\pytest` for `pytest`.

The install brings in relate-db (Relate's Python package) and pytest. Then, on any system, the first result from the committed store, and its one-page HTML report (it writes `report-dev.ruiz.html`; open it in a browser):
```
offgraph --store demo/offboarding.rl seats
offgraph --store demo/offboarding.rl report dev.ruiz@example.com
```
Rebuild everything from scratch: the first command writes the seeded, invented 60-person company (the same files as `demo/exports/`), the second loads it into `./offgraph.rl`, and `seats` then shows 19 paid seats, 180.00 USD a month, still held by 5 leavers.
```
offgraph demo exports
offgraph load exports
offgraph seats
offgraph orphans dev.ruiz@example.com
offgraph checklist dev.ruiz@example.com
```
Explore: `examples.cypher` has five queries to paste into `offgraph cypher "…"` (graph shape, group chains, leaver cost, orphaned resources with their active editors, groups with no admin).

Your own data: six CSV files in one folder: `people.csv` (email, name, team, status active|left), `groups.csv` (group, parent; one row per parent, so a group can have several), `memberships.csv` (email, group, role member|admin), `accounts.csv` (email, app, paid yes|no, admin yes|no, last_active), `resources.csv` (resource, app, kind, title, owner), `grants.csv` (resource, grantee, grantee_type group|person, access view|comment|edit|admin; edit and admin count as able to edit). Optional `prices.csv` (app, monthly_usd); without it the demo prices in `offgraph/demo.py` apply. Emails are matched without regard to case, and exact duplicate rows (two merged exports) are removed and counted.

`offgraph load` refuses exports it would otherwise answer wrongly, and lists every problem: a missing column, a row pointing at a person, group or resource that is not in the files, an app with no price, a group that is its own ancestor, or nesting deeper than 6 levels (the depth reach follows). A person who already left must stay in `people.csv` with status `left`, so what they owned is still found. An email that is not in the store is an error, not an empty answer.

Prefer the `relate` CLI? Set `RELATE_BIN` to the binary and offgraph drives it instead (`--jsonl` output). This path is experimental: it has not been run against the binary, its load is not one transaction, and `cypher` is not read-only there. The Python library is the tested path.

## Where the data lives
- `demo/offboarding.rl`: the persistent Relate store of the demo company (single file), ready to query.
- `demo/exports/`: the six CSV files it was built from.
- `demo/offboarding-graph-demo.mp4`: a 2-minute demo recording with captions. Narration: AI-generated voice (ElevenLabs).
- The demo data is invented: every name starts with "[Sample]" and every address ends in `example.com`.

## Tests
`pytest` runs everything (installed by the `test` extra above). The tests marked `engine` load data into a real Relate store and check every Cypher answer against the plain-Python reference: for all 60 demo people on a fresh load and on the committed store, and on small hand-made cases (a group with two parents, editors through a direct grant or nested groups, quotes in values). Other tests cover the CLI end to end, the example queries, and exports that must be refused.

## Built with
Built with AI coding assistance. Third-party open-source components are listed in NOTICE under their own licences; all other rights in the submission, to the extent they exist, are held by Sweekly Apps LLC, which permitted the entrant to submit it.

Relate (`relate-db` 1.0.1, Python library, single-file store) and Python's standard library. Claude, an AI coding assistant, wrote the code, tests and documentation and recorded the demo video; its narration is an AI-generated voice (ElevenLabs). MIT licence (see LICENSE); third-party components and their licences are listed in NOTICE. Not affiliated with or endorsed by Neo4j. Every person, tool name and price in the demo data is invented.
