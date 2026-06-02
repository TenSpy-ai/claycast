# Cookie Setup Guide

Clay's internal API authenticates via a session cookie from your browser. No tokens, no OAuth — just your existing browser session.

---

## Step 1 — Log in to Clay

Go to [app.clay.com](https://app.clay.com) and log in normally.

---

## Step 2 — Open DevTools

Press **F12** (Windows/Linux) or **Cmd + Option + I** (Mac) to open Chrome DevTools.

---

## Step 3 — Find the session cookie

**Option A — Application tab (easiest):**

1. Click the **Application** tab in DevTools
2. In the left sidebar, expand **Storage → Cookies**
3. Click **`https://clay.com`** (or **`https://app.clay.com`**)
4. In the cookie list, find the row named **`claysession`**
5. Copy the full value from the **Value** column

**Option B — Network tab (alternative):**

1. Click the **Network** tab in DevTools
2. Refresh the page or click anything in Clay
3. Click any request to `api.clay.com`
4. Go to the **Headers** tab → **Request Headers**
5. Find the `Cookie:` header
6. Copy the `claysession=...` portion (everything from `claysession=` to the next `;`)

---

## Step 4 — Put the cookie in `.env` at your project root

Create a `.env` file in the **root of the project you're working in** (the directory Claude Code is invoked from) with:

```
CLAY_SESSION=s%3Ayour-full-cookie-value-here...
```

The value typically starts with `s%3A` (URL-encoded `s:`). Do not wrap it in quotes unless the value itself contains spaces — the loader strips surrounding single or double quotes if present.

A template lives at `references/.env.example` — copy it to `<project-root>/.env` and paste the cookie value.

**Lookup rules.** The loader resolves `CLAY_SESSION` in this order:
1. Process environment variable `CLAY_SESSION` (export in your shell if you prefer).
2. `.env` walk-up from the current working directory — resolves symlinks first, checks `./.env`, then each parent directory for `CLAY_SESSION=`, and stops at the first ancestor containing a `.git` directory, at `$HOME`, or at the filesystem root. If a candidate `.env` exists but cannot be read, the loader raises a `RuntimeError` naming that path instead of skipping it silently.

This path-walking behavior means the skill is portable: it does not look inside its own install directory, so moving the skill between `~/.claude/skills/` or any other install location does not change which `.env` is read. The cookie always belongs to the project, not the skill.

This skill does NOT read `clay-session.json`. The only accepted source is the `CLAY_SESSION` env var (process or `.env`).

---

## How long does it last?

The session cookie expires after a few weeks. When requests start returning 401 errors, repeat Steps 2–4 to refresh the `CLAY_SESSION` value.

---

## Security note

`.env` must never be committed. The skill directory should include a `.gitignore` entry for it if the skill is ever placed under version control.
