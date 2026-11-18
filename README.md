# Offboarding Graph

**What does a leaver still have, and what breaks if you remove it?** For the IT person handling someone's last day: your SaaS exports in one Relate graph, five answers from the command line or an AI assistant, one HTML page to act on. In the demo company it finds 19 paid seats, 180.00 USD a month, still held by 5 people who left, and a budget drive one of them can still edit through three nested groups.

**Try it in your browser, nothing to install:** [sweekly-app.github.io/offboarding-graph](https://sweekly-app.github.io/offboarding-graph/) (Relate's WebAssembly build runs the same Cypher in the page; or open `docs/index.html` from a clone) · **Demo video (1:59):** [demo/offboarding-graph-demo.mp4](demo/offboarding-graph-demo.mp4) · **Try it in a minute:** [Setup and run](#setup-and-run), then `offgraph --store demo/offboarding.rl report dev.ruiz@example.com`

![The report for one leaver: the person on the left, the groups they are a member of, the groups those nest under, and the resources each grant opens; the drive only they own and the group where they are the only admin are outlined in red](demo/report.png)

## Use case
Someone leaves. Their access is spread over a docs suite, chat, an issue tracker, a wiki and a code host, and each tool exports a flat list. The risks are in the connections between those lists:

- a shared drive **only they own**, and who should own it next;
- a group where **they are the only admin**, so no active admin remains once they go;
- **paid seats** still assigned to people who have left;
- everything they can reach **through nested groups**, not just directly.

### Three questions a flat export cannot answer
Each answer joins several exports; the output is from the committed store (`offgraph --store demo/offboarding.rl …`).

**1. What can Dev still edit, through groups inside groups?** The Docs sharing list says "leadership"; the directory says Dev is in "budget-owners". Only the chain joins them.
```
MATCH (p:Person {email: $email})-[:MEMBER_OF]->(g0:Group)
MATCH path = (g0)-[:CHILD_OF*0..6]->(g:Group)
MATCH (g)-[:CAN]->(r:Resource)
RETURN DISTINCT r.id AS resource, g.name AS via, [n IN nodes(path) | n.name] AS chain
```
`offgraph reach dev.ruiz@example.com` →
`resource=R027  title=Company budget  app=Docs  access=edit  via=leadership  path=budget-owners > spend-approvers > leadership`

**2. Who should own the drive only Dev owns?** Dev is in Finance; the drive is Engineering's. The suggestion follows the edit grants, not the org chart (ownership × grants × admin roles × status; the full query, with two fallbacks, is `orphans` in `offgraph/graph.py`).
```
MATCH (p:Person {email: $email})-[:OWNS]->(r:Resource)
OPTIONAL MATCH (a:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(:Group)-[c:CAN]->(r)
WHERE a.email <> p.email AND c.access IN ['edit', 'admin']
…
```
`offgraph orphans dev.ruiz@example.com` →
`resource=R001  title=Engineering shared drive  app=Docs  suggested_owner=ben.weber@example.com  reason=admin of a group that can edit it`

**3. Which groups are left with no other active admin?**
```
MATCH (p:Person {email: $email})-[:MEMBER_OF {role: 'admin'}]->(g:Group)
OPTIONAL MATCH (o:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(g)
WHERE o.email <> p.email
WITH g, count(o) AS other_admins WHERE other_admins = 0
RETURN g.name AS group_name
```
`offgraph sole-admin dev.ruiz@example.com` → `group_name=finance`

**And before anyone leaves: what breaks if they do?** `whatif` sets their status to left inside a Relate transaction on a temporary copy of the store, asks the questions again, and rolls back; the store itself is never written.
```
offgraph --store demo/offboarding.rl whatif tia.novak@example.com
```
```
If tia.novak@example.com is removed (simulated in a Relate transaction on a copy of the store, then rolled back):
  resources left with no active owner: R027 (Company budget)
  groups left with no active admin: budget-owners, leadership, spend-approvers
  resources nobody active can edit any more: none
  paid seats to remove: 5, 43.00 USD a month
```

### The model
Graph tools for security teams, BloodHound for example, map attack paths through directory groups. Offboarding Graph asks the IT question on someone's last day instead: what they own, who should take it over, which groups lose their only admin, and what their seats still cost.

The relationships that matter: person `MEMBER_OF` group (with a role), group `CHILD_OF` group (nesting), group or person `CAN` resource (access level), person `OWNS` resource, person `HAS_ACCOUNT` account `IN_APP` app (seat, price, admin flag). The question "who should take over this drive?" needs ownership, grants, group admin roles and person status together; a single table can't answer it.

| Command | Answers |
|---|---|
| `offgraph reach <email>` | every resource the person can reach, directly or through nested groups, with the chain of groups that leads there (one row per grant, so a resource reached two ways appears twice) |
| `offgraph orphans <email>` | resources only they own, with a suggested new owner (an active admin of a group that holds an edit grant on the resource, else an admin in their team, else any active person who can edit it) and why |
| `offgraph sole-admin <email>` | groups they administer where no other active admin remains |
| `offgraph whatif <email> [<email> …]` | what breaks if these people are removed: resources left with no active owner, groups left with no active admin, resources nobody active can edit any more, the paid seats to remove. Simulated in a Relate transaction on a copy of the store, then rolled back |
| `offgraph seats` | paid seats still held by people marked as left, with the monthly cost |
| `offgraph checklist <email>` | the steps in a safe order (works before HR marks them as left: their own paid seats are included): transfer ownership → second admin → remove admin rights → remove seats → revoke direct grants → disable |
| `offgraph audit [-o audit.html]` | everyone marked as left at once: what each owns alone and who should take it, the groups they administer where no other active admin remains, how many resources they can still edit through grants, their seats; the groups with no active admin; the company total. With `-o`, one HTML page with each leaver's graph and checklist |
| `offgraph import-github <folder> --logins logins.csv -o <out>` | turns JSON saved from GitHub's REST API (teams and their nesting, maintainers, members, org admins, repositories, team and collaborator permissions) into the CSV files below; see "From GitHub" |
| `offgraph web <folder> [-o page.html]` | one self-contained HTML page that loads the exports into Relate's WebAssembly build in the browser and answers the same questions there (reach with chains, what they own alone, groups with no other admin, what breaks if they leave, the checklist, the company view, your own Cypher). `docs/index.html` is this page for the demo company |
| `offgraph report <email>` | one self-contained HTML page: the access graph, the risks and the checklist |
| `offgraph mcp` | the per-person answers and the seat total as five read-only tools for an AI assistant, over the Model Context Protocol (stdin/stdout); see below |
| `offgraph cypher "<statement>"` | your own read-only Cypher on the same store (nodes and relationships printed with their properties; the first 50 rows unless `--limit`) |

## How it uses Relate
- **Mode: single file** (`.rl`), through the Python library (`relate-db`, the engine in-process).
- **Why this mode:** offboarding data is a snapshot an IT person rebuilds from fresh exports, keeps next to the ticket and may hand to an auditor. One file does that: copy it, attach it, open it read-only for reports while nothing else can write to it. No server to run, and nothing leaves the machine.
- **What the graph models:** `(:Person {email, name, team, status})`, `(:Group {name})`, `(:App {name, price})`, `(:Account {key, paid, admin, last_active})`, `(:Resource {id, app, kind, title})`, joined by `MEMBER_OF {role}`, `CHILD_OF`, `CAN {access}`, `OWNS`, `HAS_ACCOUNT`, `IN_APP`.
- **How:** the CSVs load as nine parameterised `UNWIND … CREATE` statements in one transaction (a reload replaces the graph). Each question is one Cypher query in `offgraph/graph.py`: reach walks `MEMBER_OF` then `CHILD_OF*0..6` to `CAN`, and a path query returns the chain of groups (the shortest is shown); the owner suggestion matches admins of the groups that hold an edit grant on the resource, then admins in the leaver's team, then any active person who can edit it (a direct grant, or through nested groups); sole-admin counts the other active admins on each group.
- **Checked:** `offgraph/oracle.py` computes every answer again in plain Python from the CSVs, and the engine tests compare the two for all 60 demo people, both on a fresh load and on the committed store (the group chains on a fresh load).

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
Rebuild everything from scratch: the first command writes the seeded, invented 60-person company (the same files as `demo/exports/`), the second loads it into `./offgraph.rl`, and `seats` then shows the demo company's 19 paid seats, 180.00 USD a month, still held by 5 leavers.
```
offgraph demo exports
offgraph load exports
offgraph seats
offgraph orphans dev.ruiz@example.com
offgraph checklist dev.ruiz@example.com
offgraph audit -o audit.html
```
Explore: `examples.cypher` has five queries to paste into `offgraph cypher "…"` (graph shape, group chains, leaver cost, orphaned resources with their active editors, groups with no admin).

Your own data: six CSV files in one folder. For GitHub, `offgraph import-github` writes five of them for you (below); for other tools, pick and rename columns from the exports you have (a spreadsheet does it).

| File | Columns | Where it usually comes from |
|---|---|---|
| `people.csv` | email, name, team, status (active or left) | the directory's or HR system's user list; people who have gone stay in it with status `left` |
| `groups.csv` | group, parent | the directory's group list; one row per parent, so a group can have several |
| `memberships.csv` | email, group, role (member or admin) | the directory's group members; group owners and managers become `admin` |
| `accounts.csv` | email, app, paid (yes or no), admin (yes or no), last_active (any date text, shown as written, e.g. 2026-08-17) | each tool's user or seat list from its admin console |
| `resources.csv` | resource, app, kind, title, owner | each tool's shared drives, channels, projects, spaces or repositories, with their owner |
| `grants.csv` | resource, grantee, grantee_type (group or person), access (view, comment, edit or admin) | each tool's sharing or permission list; edit and admin count as able to edit |
| `prices.csv` (optional) | app, monthly_usd | your price per paid seat; without it the demo prices in `offgraph/demo.py` apply |

Emails and the fixed values (active, left, yes, no, member, admin, group, person, view, comment, edit) are matched without regard to case, and exact duplicate rows (two merged exports) are removed and counted. `offgraph demo` will not write over CSV files already in its folder unless you add `--force`. Every question command (`reach`, `orphans`, `sole-admin`, `seats`, `checklist`, `whatif`, `audit`, `cypher`) also takes `--json` for scripts and agents.

From GitHub: save the JSON bodies of these documented REST endpoints for your organisation `ORG` into one folder (the GitHub CLI's `gh api --paginate <endpoint>` prints them; several pages one after another in a file are fine), with one file per team (named after its slug) and per repository (named after it) in the subfolders:

| File | Endpoint |
|---|---|
| `teams.json` | `GET /orgs/ORG/teams` |
| `members.json` | `GET /orgs/ORG/members` |
| `admins.json` | `GET /orgs/ORG/members?role=admin` |
| `repos.json` | `GET /orgs/ORG/repos` |
| `team-members/SLUG.json` | `GET /orgs/ORG/teams/SLUG/members` |
| `team-maintainers/SLUG.json` | `GET /orgs/ORG/teams/SLUG/members?role=maintainer` |
| `team-repos/SLUG.json` | `GET /orgs/ORG/teams/SLUG/repos` |
| `collaborators/REPO.json` | `GET /repos/ORG/REPO/collaborators?affiliation=direct` |

Every team and repository needs its file; save `[]` for an endpoint with no rows. The API gives logins, not emails, so add `logins.csv` (login, email). Team nesting becomes `CHILD_OF` (members the API marks `inherited`, who come from a child team, stay in that child team, so the chain is kept), maintainers become group admins, org owners become app admins, and repository roles map to access: admin → admin, maintain and write → edit, triage → comment, read → view. A repository's owner is its direct admin collaborator (the first by login), or the one you name in an optional `repo-owners.csv` (repo, owner). Then add your `people.csv` (with `left` for people who have gone) and a price for `GitHub` in `prices.csv` (or pass both with `--people` and `--prices`), and load:
```
offgraph import-github examples/github --logins examples/github/logins.csv --people examples/github/people.csv --prices examples/github/prices.csv -o gh
offgraph --store gh.rl load gh
offgraph --store gh.rl reach di.n@example.com
```
On the example, the leaver `di.n` reaches the `api` repository through release-managers > platform > engineering, and `deploy-scripts`, which only they administer directly, goes to the platform team's maintainer. A missing or empty file, a login with no email, a repository with no owner, a response that is not a list, or an output folder that already has the files is refused with a list of what to fix. What it reads from GitHub: team names, slugs and parent teams; the logins of members, maintainers and organisation owners; repository names; and the permission each team and direct collaborator has on each repository. It reads no code, issues, commits or tokens. It runs locally: nothing is fetched and nothing is sent anywhere; it only reads the files you saved and writes CSV files into the output folder. It is tested on `examples/github`, which is made up in the shape of the API's documented responses, not on a live organisation.

`offgraph load` refuses exports it would otherwise answer wrongly, and lists the problems (the first 20, then how many more): a missing column, a row pointing at a person, group or resource that is not in the files, an app with no price, a group that is its own ancestor, or nesting deeper than 6 levels (the depth reach follows). A person who already left must stay in `people.csv` with status `left`, so what they owned is still found. An email that is not in the store is an error, not an empty answer, and a question with no answer says so in one line.

Ask an AI assistant: `offgraph mcp` is a Model Context Protocol server on stdin/stdout, with five read-only tools (`offboarding_checklist`, `who_takes_over`, `access_paths`, `sole_admin_groups`, `leaver_seats`) answered by the same Cypher queries. It takes no Cypher from the assistant and opens the store read-only. In an MCP client that starts local commands, add a server whose command is the `offgraph` inside `.venv` (`.venv/bin/offgraph`, or `.venv\Scripts\offgraph.exe` on Windows) with the arguments `--store` and the full path to a `.rl` file, then `mcp`. Then ask, for example, "what breaks if dev.ruiz@example.com leaves?" To see the exchange without an assistant, `python examples/mcp_client.py dev.ruiz@example.com offgraph --store demo/offboarding.rl mcp` starts the server, lists the tools and calls the checklist tool.

Prefer the `relate` CLI? Set `RELATE_BIN` to the binary and offgraph drives it instead (`--jsonl` output). This path is experimental: it has not been run against the binary, its load is not one transaction, and `cypher` is not read-only there. The Python library is the tested path.

## In the browser
`docs/index.html` (published at [sweekly-app.github.io/offboarding-graph](https://sweekly-app.github.io/offboarding-graph/)) runs Relate in the page: it imports `@relate/relate-wasm` 1.0.1 from the jsDelivr CDN, creates an in-memory graph, loads the demo company's rows with the same nine `UNWIND` statements as `offgraph load`, and runs the same Cypher as the command line (each answer shows its query). Each person's access graph is drawn in the page the way `offgraph report` draws it. "What breaks if they leave" sets the person's status to left in the page's graph, asks again, then rebuilds the graph. The page needs no server: it works from GitHub Pages or opened as a local file. `offgraph web <folder> -o page.html` makes the same page for your own exports; the data stays inside that file, and only the Relate library is fetched from the CDN. `tests/web/check.cjs` opens the page in headless Chromium (over http and from a file) and compares its reach, chains, checklist, what-if answers and graph labels with the command line's for all 60 demo people; the last run found no difference.

## Where the data lives
- `demo/offboarding.rl`: the persistent Relate store of the demo company (single file), ready to query.
- `demo/exports/`: the six CSV files it was built from.
- `demo/offboarding-graph-demo.mp4`: a 2-minute demo recording with captions. Narration: AI-generated voice (ElevenLabs).
- `examples/mcp_client.py`: a minimal MCP client (the one in the video).
- `examples/github/`: made-up accounts and teams in the shape of GitHub's REST API responses, for `import-github`; its seat price is a placeholder. Logins and teams are invented; any match with a real account is coincidental.
- `docs/index.html`: the browser page for the demo company (`offgraph web`), with the demo exports embedded.
- `demo/report.png`: the top of the report for `dev.ruiz@example.com`, as `offgraph report` writes it.
- The demo data is invented: every name starts with "[Sample]" and every address ends in `example.com`.

## Tests
`pytest` runs everything (installed by the `test` extra above). The tests marked `engine` load data into a real Relate store and check every Cypher answer against the plain-Python reference: for all 60 demo people on a fresh load and on the committed store, and on small hand-made cases (a group with two parents, editors through a direct grant or nested groups, quotes in values). The `whatif` answers are checked against the reference for every one of the 60 people and for a whole team leaving at once, and the store's bytes are checked to be unchanged. Other tests cover the CLI end to end, the MCP server (started as a real process), the example queries, and exports that must be refused.

## Built with
Built with AI coding assistance. Third-party open-source components are listed in NOTICE under their own licences; all other rights in the submission, to the extent they exist, are held by Sweekly Apps LLC, which permitted the entrant to submit it. Relate (`relate-db` 1.0.1, Python library, single-file store; `@relate/relate-wasm` 1.0.1, Apache-2.0, loaded by the browser page from the jsDelivr CDN, not copied into this repository) and Python's standard library. Claude, an AI coding assistant, wrote the code, tests and documentation and recorded the demo video; its narration is an AI-generated voice (ElevenLabs). MIT licence (see LICENSE). Not affiliated with or endorsed by Neo4j. Every person, tool name and price in the demo data is invented. BloodHound is a trademark of its owner; Neo4j and Relate are trademarks of Neo4j, Inc.; Claude is a trademark of Anthropic; GitHub is a trademark of GitHub, Inc. Used for identification only.
