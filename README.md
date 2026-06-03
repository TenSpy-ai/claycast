# ClayCast

**An unofficial Python toolkit for driving Clay's internal REST API using your browser session cookie.**

ClayCast does the schema-level work the official Clay MCP connector can't: creating and modifying tables, columns, and action columns from code; running and waiting on enrichments; exporting/importing table schemas; and discovering undocumented action-input shapes by capturing live requests against the app.

It ships as a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) **skill** (so an agent can use it directly), and the underlying `ClayClient` is a plain Python SDK you can import and use on its own.

> ⚠️ **Unofficial — use at your own risk.** ClayCast is not affiliated with, endorsed by, or supported by Clay. It drives **undocumented internal endpoints** that can change or break without notice, and it authenticates with your own browser session cookie. You are responsible for staying within Clay's Terms of Service and for any actions you run (enrichment runs cost real credits).

## What it can do

- **Schema operations** — create/modify/clone tables, columns, formula columns, and action columns programmatically.
- **Enrichment** — trigger runs and wait for completion (`run_column`, `run_and_wait`).
- **Export / import** — serialize a table's column structure (the portable **ClayPrint** format) to copy or clone structure across tables; export rows to CSV/JSON; export whole workspaces.
- **Discovery** — a Playwright-based browser daemon (`clay_browser.py`) that runs Clay with your session cookie and auto-captures every `api.clay.com` request/response, so you can reverse the shape of endpoints ClayCast doesn't wrap yet.

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

Either way the skill must end up at `…/skills/claycast/` with `SKILL.md` at its root. Claude Code reads `SKILL.md`'s front-matter and loads ClayCast automatically when a task matches it. Requires Python 3.10+.

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
└── scripts/
    ├── clay_client.py         # the ClayClient SDK (authenticated REST client)
    └── clay_browser.py        # Playwright daemon for request-capture / discovery
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

## Contributing

Issues and pull requests are welcome. **[`references/feature-gaps.md`](references/feature-gaps.md) is the roadmap** — it lists missing capabilities (tiered by impact) with proposed method signatures, so it's the best place to find something worth building. Please open an issue to discuss substantial changes before submitting a PR.

## License

[MIT](LICENSE) © 2026 TenSpy-ai
