# Offboarding Graph: reference

The details behind the [README](../README.md): every command, the CSV files, the two importers and what is refused.

## Commands
| Command | Answers |
|---|---|
| `offgraph reach <email>` | every resource the person can reach, directly or through nested groups, with the chain of groups that leads there (one row per grant, so a resource reached two ways appears twice) |
| `offgraph orphans <email>` | resources only they own, with a suggested new owner (an active admin of a group that holds an edit grant on the resource, else an admin in their team, else any active person who can edit it) and why |
| `offgraph sole-admin <email>` | groups they administer where no other active admin remains |
| `offgraph whatif <email> [<email> …]` | what breaks if these people are removed: resources left with no active owner, groups left with no active admin, resources nobody active can edit any more, resources left with no admin (app admins not counted), the paid seats to remove. Simulated in a Relate transaction on a copy of the store, then rolled back |
| `offgraph single-admins` | resources whose admin access rests on one active person, through nested groups or directly, not counting the app's admins (who administer everything): who cannot leave without a handover |
| `offgraph seats` | paid seats still held by people marked as left, with the monthly cost |
| `offgraph checklist <email>` | the steps in a safe order (works before HR marks them as left: their own paid seats are included): transfer ownership → second admin → remove admin rights → remove seats → revoke direct grants → disable |
| `offgraph audit [-o audit.html]` | everyone marked as left at once: what each owns alone and who should take it, the groups they administer where no other active admin remains, how many resources they can still edit through grants, their seats; the groups with no active admin; the company total. With `-o`, one HTML page with each leaver's graph and checklist |
| `offgraph import-github <folder> --logins logins.csv -o <out>` | turns JSON saved from GitHub's REST API (teams and their nesting, maintainers, members, org admins, repositories, team and collaborator permissions) into the CSV files below; see "From GitHub's REST API" |
| `offgraph import-peribolos <folder or file> -o <out>` | turns a Peribolos config (GitHub access kept as code, as kubernetes/org publishes it) into the seven CSV files; see "From a Peribolos config" |
| `offgraph web <folder> [-o page.html]` | one self-contained HTML page that loads the exports into Relate's WebAssembly build in the browser and answers the same questions there (reach with chains, what they own alone, groups with no other admin, what breaks if they leave, the checklist, the company view, your own Cypher). `docs/index.html` is this page for the demo company |
| `offgraph report <email>` | one self-contained HTML page: the access graph, the risks and the checklist |
| `offgraph mcp` | the answers as seven read-only tools for an AI assistant, over the Model Context Protocol (stdin/stdout); see the README |
| `offgraph cypher "<statement>"` | your own read-only Cypher on the same store (nodes and relationships printed with their properties; the first 50 rows unless `--limit`) |

Every question command (`reach`, `orphans`, `sole-admin`, `seats`, `single-admins`, `checklist`, `whatif`, `audit`, `cypher`) also takes `--json` for scripts and agents.

## Windows
Windows (PowerShell or cmd). These lines call the programs inside `.venv` directly, so nothing needs activating and PowerShell's script policy does not matter. If `py` is not found (Python from the Microsoft Store has no `py` launcher), use `python` in the third line.
```
py -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[test]"
```
On Windows, write `.venv\Scripts\offgraph` wherever the commands below say `offgraph`, and `.venv\Scripts\pytest` for `pytest`.

## Your own data: six CSV files
Pick and rename columns from the exports you have (a spreadsheet does it), or let an importer below write them.

| File | Columns | Where it usually comes from |
|---|---|---|
| `people.csv` | email, name, team, status (active or left) | the directory's or HR system's user list; people who have gone stay in it with status `left` |
| `groups.csv` | group, parent | the directory's group list; one row per parent, so a group can have several |
| `memberships.csv` | email, group, role (member or admin) | the directory's group members; group owners and managers become `admin` |
| `accounts.csv` | email, app, paid (yes or no), admin (yes or no), last_active (any date text, shown as written, e.g. 2026-08-17) | each tool's user or seat list from its admin console |
| `resources.csv` | resource, app, kind, title, owner | each tool's shared drives, channels, projects, spaces or repositories, with their owner |
| `grants.csv` | resource, grantee, grantee_type (group or person), access (view, comment, edit or admin) | each tool's sharing or permission list; edit and admin count as able to edit |
| `prices.csv` (optional) | app, monthly_usd | your price per paid seat; without it the demo prices in `offgraph/demo.py` apply |

Emails and the fixed values (active, left, yes, no, member, admin, group, person, view, comment, edit) are matched without regard to case, and exact duplicate rows (two merged exports) are removed and counted. A resource's owner may be left empty when nobody owns it. `offgraph demo` will not write over CSV files already in its folder unless you add `--force`.

`offgraph load` refuses exports it would otherwise answer wrongly, and lists the problems (the first 20, then how many more): a missing column, a row pointing at a person, group or resource that is not in the files, an app with no price, a group that is its own ancestor, or nesting deeper than 6 levels (the depth reach follows). A person who already left must stay in `people.csv` with status `left`, so what they owned is still found. An email that is not in the store is an error, not an empty answer, and a question with no answer says so in one line.

## From a Peribolos config (GitHub access kept as code)
Peribolos, part of Kubernetes' Prow, keeps a GitHub organisation's access in YAML: the org's admins and members, and every team with its maintainers, members, child teams and repository permissions. `offgraph import-peribolos` reads either a folder laid out like kubernetes/org's `config/ORG/` (`org.yaml` plus any `*/teams.yaml`) or one YAML file (the org's settings, or the full `orgs:` form with `--org`), and writes all seven CSV files. It needs PyYAML (`python -m pip install -e ".[peribolos]"`; the `test` extra includes it).

People are identified by their GitHub login (the config has no emails) and are all written as `active`: mark leavers as `left` in `people.csv` yourself. Team maintainers become group admins, child teams become `CHILD_OF`, org admins become app admins, and permissions map as for the GitHub importer (admin → admin, maintain and write → edit, triage → comment, read → view). The config says nothing about who owns a repository or what a seat costs, so resources have no owner and the price of `GitHub` is 0. A team defined twice, a permission Peribolos does not have, a team that is not a mapping, a file that is not YAML, or an output folder that already has the files is refused with a list of what to fix. `examples/peribolos/` is a made-up organisation in this format.

## From GitHub's REST API
Save the JSON bodies of these documented REST endpoints for your organisation `ORG` into one folder (the GitHub CLI's `gh api --paginate <endpoint>` prints them; several pages one after another in a file are fine), with one file per team (named after its slug) and per repository (named after it) in the subfolders:

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

## The relate CLI
Prefer the `relate` binary? Set `RELATE_BIN` to it and offgraph drives it instead (`--jsonl` output). This path is experimental: it has not been run against the binary, its load is not one transaction, and `cypher` is not read-only there. `whatif` needs the Python library. The Python library is the tested path.
