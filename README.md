# ClayCast

**An unofficial Python toolkit for driving Clay.com's internal REST API using your browser session cookie.**

ClayCast does the schema-level work the official Clay MCP connector can't: creating and modifying tables, columns, and action columns from code; running and waiting on enrichments; exporting/importing table schemas; and discovering undocumented action-input shapes by capturing live requests against the app.

It ships as a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) **skill** (so an agent can use it directly), and the underlying `ClayClient` is a plain Python SDK you can import and use on its own.

> ⚠️ **Unofficial — use at your own risk.** ClayCast is not affiliated with, endorsed by, or supported by Clay. It drives **undocumented internal endpoints** that can change or break without notice, and it authenticates with your own browser session cookie. You are responsible for staying within Clay's Terms of Service and for any actions you run (enrichment runs cost real credits).

## What it can do

- **Schema operations** — create/modify/clone tables, columns, formula columns, and action columns programmatically.
- **Enrichment** — trigger runs and wait for completion (`run_column`, `run_and_wait`).
- **Export / import** — serialize a table's column structure (the portable **ClayPrint** format) to copy or clone structure across tables; export rows to CSV/JSON; export whole workspaces.
- **Discovery** — a Playwright-based browser daemon (`clay_browser.py`) that runs Clay with your session cookie and auto-captures every `api.clay.com` request/response, so you can reverse the shape of endpoints ClayCast doesn't wrap yet. Runs on macOS, Linux and Windows (see [Windows](#windows) for what has been tested on Windows and the known issues).
- **Audiences ↔ Salesforce sync** — map more Salesforce fields into the People / Companies audience (`add_salesforce_import_fields`), something neither the official CLI nor the public API can do; read each external-source import's sync state (`get_audience_import_sync_status`, `get_audience_import_history`) — and count a newly mapped field's values before relying on it: the backfill is not guaranteed, and the sync state does not show the fill.
- **Audiences segments** — build filter ASTs in code with the `af_*` helpers (the exact shapes the UI writes, so segments stay editable), count them before saving, prove an exclusion list and its complement partition the audience, then create / update / delete segments.

## Install as a Claude Code skill

Claude Code auto-discovers skills placed under a `skills/` directory. Install ClayCast at one of two scopes:

**Global** (available in every project on your machine):

```bash
git clone https://github.com/TenSpy-ai/claycast.git ~/.claude/skills/claycast
pip install -r ~/.claude/skills/claycast/references/requirements.txt
# For the browser/discovery daemon only:
playwright install
```

**Project-level** (only this project; commit it with the repo to share with your team):

```bash
# from your project root
git clone https://github.com/TenSpy-ai/claycast.git .claude/skills/claycast
pip install -r .claude/skills/claycast/references/requirements.txt
```

Either way the skill must end up at `…/skills/claycast/` with `SKILL.md` at its root. Claude Code reads `SKILL.md`'s front-matter and loads ClayCast automatically when a task matches it. Requires Python 3.10+. On Windows, `clay_browser.py` uses a loopback TCP control socket and `%TEMP%\clay-browser\` instead of a UNIX socket and `/tmp` (set `CLAY_BROWSER_DIR` to override on any platform; on POSIX a dir long enough to push `server.sock` past the AF_UNIX limit — 103 bytes on macOS, 107 on Linux — also switches to the loopback TCP channel, with a NOTE at launch). Both channels require a per-daemon token from `server.token` (0600 in the runtime dir) on every command, so other local processes cannot drive the browser — which runs JS in your logged-in Clay session; on Windows only the NTFS ACL of `%TEMP%` protects that file.

> Project-level beats global when two projects need different versions, or when you want the skill version-controlled alongside the project. Global is simplest for personal use across many projects.

### Installed layout

```
claycast/                      # ~/.claude/skills/claycast/  (or  <project>/.claude/skills/claycast/)
├── SKILL.md                   # skill manifest + full capability map (what Claude loads)
├── README.md                  # this file
├── .gitignore
├── references/
│   ├── clay-api-reference.md  # the underlying Clay endpoints ClayCast wraps
│   ├── action-registry.md     # action-column input shapes + integration gotchas
│   ├── cookie-setup.md        # how to grab & configure CLAY_SESSION
│   ├── feature-gaps.md        # roadmap: what's missing / what to build next
│   ├── requirements.txt       # Python dependencies
│   └── .env.example           # CLAY_SESSION placeholder
├── requirements-dev.txt       # pytest, for the offline test suite
├── scripts/
│   ├── clay_client.py         # the ClayClient SDK (authenticated REST client)
│   └── clay_browser.py        # Playwright daemon for request-capture / discovery
└── tests/                     # offline pytest suite + e2e_browser.sh + tools/check_partition.py
```

## Authentication

ClayCast authenticates with your Clay **session cookie** (`claysession` in your browser — grab it from DevTools → Application → Cookies; full steps in [`references/cookie-setup.md`](references/cookie-setup.md)). Nothing is hardcoded and no cookie is stored in the repo.

It resolves the cookie in this order:

1. The **`CLAY_SESSION` environment variable** — simplest, works from anywhere:
   ```bash
   export CLAY_SESSION='s%3A...your-cookie...'
   ```
2. Otherwise, the nearest **`.env`** file containing `CLAY_SESSION=`, found by walking **up from your current directory to your project root** (the walk stops at the first `.git`, your home dir, or `/`).

### Activate it with a `.env` file

- **Already have a `.env` containing `CLAY_SESSION=` somewhere from your working dir up to your project root? You're done — nothing to do.**
- **Otherwise**, turn the bundled template into a real `.env`:
  ```bash
  cp references/.env.example .env        # place it at your PROJECT root
  ```
  Open the new `.env`, set your cookie, and save:
  ```
  CLAY_SESSION=s%3A...your-cookie-value...
  ```
  ClayCast picks it up on the next run.

> ⚠️ `references/.env.example` is only the **format template**. Copy it out to a `.env` that sits **on the walk-up path** — your project root (or your clone's root if you run ClayCast standalone). Just renaming it in place inside `references/` won't be found: the loader searches *up the directory tree from where you run it*, not inside the skill's own folder. `.env` is gitignored — never commit it.

## Use it directly from Python

You don't need Claude Code to use the client — point Python at the `scripts/` dir and import it:

```python
import sys; sys.path.insert(0, "scripts")   # or .../skills/claycast/scripts
from clay_client import ClayClient

clay = ClayClient()                      # reads CLAY_SESSION from env / .env
table = clay.create_table(workbook_name="My Workbook", table_name="Leads")
clay.create_formula_column(table["tableId"], name="Domain", formula='...')
```

## Documentation

| Doc | What's in it |
|-----|--------------|
| [`SKILL.md`](SKILL.md) | Full capability map + design principles (also the Claude Code skill manifest). |
| [`references/clay-api-reference.md`](references/clay-api-reference.md) | The underlying Clay endpoints ClayCast wraps (URLs, payloads, response shapes). |
| [`references/action-registry.md`](references/action-registry.md) | Action-column input shapes and integration gotchas. |
| [`references/cookie-setup.md`](references/cookie-setup.md) | Getting and configuring `CLAY_SESSION`. |
| [`references/feature-gaps.md`](references/feature-gaps.md) | **Roadmap** — capabilities not yet built, tiered by impact. Start here if you want to contribute a feature. |

## Running the tests

The offline suite under `tests/` needs no Clay cookie, makes no network calls and spends no credits: it drives `ClayClient` through a recording fake session and asserts the exact request each method sends, checks the `af_*` filter builders, and probes `clay_browser.py`'s helpers in subprocesses.

```bash
pip install -r requirements-dev.txt   # pytest
python -m pytest tests -q
```

On Windows, 44 of the 48 tests in `tests/test_browser_units.py` skip by design: they bind sockets, use `/tmp` test folders, assert POSIX file modes or send POSIX signals (see [Windows](#windows)). Two more tools live next to the suite and are not run by `pytest`:

- **`tests/e2e_browser.sh [unix|tcp|longpath]`** — the macOS-only live harness for `scripts/clay_browser.py` (it uses `stat -f %Lp` and `lsof`). It launches headless Chromium through the daemon, drives it and checks the teardown; `tcp` runs a temp copy with `USE_UNIX_SOCKET` forced off to exercise, on a Mac, the loopback TCP channel Windows uses (none of the Windows-only code runs). Needs Playwright and a `CLAY_SESSION` value that resolves, but not a working cookie — the only page it opens is a local `data:` URL.
- **`python tests/tools/check_partition.py scripts/clay_client.py`** — the exclusion-pair partition checker: for every rule table in its battery it evaluates both sides of `af_exclusion_pair()` over a small record domain under every blank-value model for the operators not measured live (16 by default — `False` is fixed to "matches a blank", as measured 2026-09-29; `--all-models` enumerates all 32 for reference) and reports whether each record lands on exactly one side and whether boolean tables stay unpinned (`--json` for machine-readable output). Any `af_*` candidate module can be checked the same way; `tests/test_partition_property.py` and `tests/test_af_fuzz.py` run the same checker inside `pytest`.
- **`tests/live/*.py`** — read-only checks against a real workspace (segment counts take the entity from the segment, where the Salesforce sync settings live, the `dry_run=True` validation paths of `add_salesforce_import_fields`, and the blank-semantics probe behind the `af_*` design). Each needs a real cookie, a workspace with Audiences and `--workspace <id>`; they only list and count, never write, and spend no credits. Share their PASS/FAIL lines, not the raw counts or ids they print. `tests/test_live_scripts_offline.py` byte-compiles them and runs the probe's dry run offline as part of the suite.

## Windows

ClayCast works on Windows. `clay_client.py` has no Windows-specific code paths (its one known Windows difference, in CSV exports, is listed below). `clay_browser.py` has a Windows mode: commands reach the browser over a local TCP port (127.0.0.1) and must carry a secret per-daemon token, and its runtime folder is `%TEMP%\clay-browser\` by default (details under [Install](#install-as-a-claude-code-skill)). The commands in this README are written for bash/zsh; in PowerShell, set environment variables with `$env:CLAY_SESSION = '...'`.

**Tested on Windows** (2026-09-30; Windows 11, Python 3.13 via the `py` launcher, Playwright 1.63, PowerShell 7 plus one check from Git Bash):

- the browser daemon: `launch`, `eval` and `close` with the default `%TEMP%` runtime folder; `eval` from a second shell; a command sent without the token, or with a wrong one, answered `unauthorized`; and the warning when `CLAY_BROWSER_DIR` points outside your user profile;
- the offline test suite as it stood then (`py -m pytest tests -q`: 293 passed, 44 skipped; the skips are by design, see [Running the tests](#running-the-tests));
- the read-only live scripts `tests/live/f3_import_read.py`, `f7_segment_counts.py` and `f45_dry_run.py`.

**Known issue (confirmed on Windows):** `close` leaves `daemon.log` in the runtime folder, because the daemon and its Playwright driver still hold it open while they shut down. It holds only status lines and errors, not the token or the captured requests, and the next `launch` empties it; you can also delete it once the daemon has exited.

**Possible issues, not yet verified on Windows** (found by code review or by simulating Windows behavior on macOS; none has been reproduced on a Windows machine):

- `snapshot` may fail with `UnicodeEncodeError` when its output goes to a pipe or a file instead of a console window (as when Claude Code runs it) and the page has characters your Windows code page cannot encode, such as arrows, check marks or emoji. Other commands print page text as ASCII-escaped JSON and are not affected. Set `PYTHONUTF8=1` to avoid it (`setx PYTHONUTF8 1` keeps it for new shells).
- CSV files written by `export_rows` and `export_audience_segment` with `format="csv"` may get an extra carriage return on every line, so Python's `csv.reader` (and possibly spreadsheet apps) show a blank row after each record. `format="json"` is not affected.
- If a daemon was ended without `close` (for example in Task Manager), the next `launch` may stop once with `PermissionError`, because a leftover Playwright process still has `daemon.log` open. Run `launch` again.
- After a crash, reboot or sign-out without `close`, Windows may have reused the old daemon's process ID, so `launch` says `already running (PID n)` and other commands suggest killing that PID. Only end it if its command line is `clay_browser.py --daemon` (check with `Get-CimInstance Win32_Process -Filter "ProcessId=n" | Select-Object CommandLine`). Otherwise delete `server.pid`, `server.port`, `server.token` and `requests.jsonl` from the runtime folder (or the whole folder if it is the default `%TEMP%\clay-browser\`) and run `launch` again.
- Commands from a normal prompt may report `Daemon not running` for a daemon launched from an Administrator prompt, and a `launch` from the normal prompt could then delete the running daemon's control files. Run `launch`, the other commands and `close` from the same kind of prompt.
- With a virtual environment's `python.exe` (the tests above used the system Python), the daemon may open its own blank console window, and `launch` may print a different PID from the one in `server.pid`. Leave the window open and stop the daemon with `close`: closing the window stops it but leaves its files, including the captured requests, on disk.
- If the browser takes more than 10 seconds to close, a watchdog force-stops the daemon with `taskkill`. That path has not run on Windows yet; when it does, a console window may flash briefly.

**Not yet tested on Windows:**

- The other browser commands on the current version: `goto`, `snapshot`, `requests` and `press` ran only on an earlier version (2026-09-29, before the token was added), and `screenshot`, `click`, `click_selector` and `fill` have not been run on Windows.
- Code written or changed after the 2026-09-30 tests: the saved-Claygent column methods (`create_claygent_column` and the rest) and the rewritten `get_audience_import_history`. Nothing in it is OS-specific, but its tests have not been run on Windows.
- Automated tests of the Windows code: 44 of the 48 tests in `tests/test_browser_units.py` skip on Windows, including every test that binds or connects to the loopback port Windows always uses. No test on any OS runs the `%TEMP%` default, the Windows check for a running daemon, the detached launch or the forced shutdown; only the profile-folder warning has a test that runs on Windows. The manual checks above covered the first three; the forced shutdown has not been seen to run.
- Other setups: only Windows 11 with Python 3.13 and Playwright 1.63 has been tried; other Windows versions, other Python and Playwright versions, cmd.exe, Windows PowerShell 5.1 and virtual environments have no recorded Windows run.

## Contributing

Issues and pull requests are welcome — ClayCast wraps a moving, undocumented API, so there's always more to wrap.

### Start with the roadmap

**[`references/feature-gaps.md`](references/feature-gaps.md) is the best place to find something worth building.** It's a living gap list of capabilities ClayCast doesn't implement yet, **tiered by impact** (Tier 1 = actually blocks common headless workflows), and most gaps come with a **proposed method signature** so a new wrapper slots into the SDK's existing conventions. Skim the open tiers, pick a gap, and you have a scoped first PR. The "Recently closed" section at the top shows the cadence and the shape a finished contribution takes.

For anything substantial, open an issue first so we can agree on the approach before you build.

### Fork & PR workflow

```bash
# 1. Fork the repo on GitHub — the "Fork" button on github.com/TenSpy-ai/claycast

# 2. Clone YOUR fork
git clone https://github.com/<your-username>/claycast.git
cd claycast

# 3. Track upstream so you can stay in sync
git remote add upstream https://github.com/TenSpy-ai/claycast.git

# 4. Branch for your change
git checkout -b feature/views-crud      # e.g. tackling feature-gaps Tier 1 #1

# 5. Install deps and point CLAY_SESSION at YOUR OWN workspace (see Authentication)
pip install -r references/requirements.txt
cp references/.env.example .env         # then set your cookie; .env is gitignored

# 6. Make the change, test it against your own Clay workspace

# 7. Commit and push to your fork
git commit -am "Add views CRUD (feature-gaps Tier 1 #1)"
git push origin feature/views-crud

# 8. Open a PR: <your-username>:feature/views-crud  ->  TenSpy-ai:main
```

Before opening (or updating) a PR, sync your fork with `git fetch upstream && git merge upstream/main`.

### Ground rules for PRs

- **Never commit secrets or workspace data.** Your `.env` and `CLAY_SESSION` cookie stay local (both gitignored). In any "verified live" comment, doc, or example, use a **placeholder** workspace id (e.g. `12345`) and placeholder table/field/audience ids — never your real ones.
- **Show how you verified an endpoint.** These are internal endpoints, so note how you confirmed the request/response shape — `scripts/clay_browser.py` captures live `api.clay.com` traffic for exactly this. Date your "verified live" comments like the existing code does.
- **Match the conventions** already in `clay_client.py` — the proposed signature in `feature-gaps.md`, the return shapes, the two-step record-write pattern, and credit-cost callouts on anything that runs.
- **Mind the credits.** Anything that triggers a run costs real Clay credits — guard it and document the cost; don't run it implicitly.

## License

[MIT](LICENSE) © 2026 TenSpy-ai
