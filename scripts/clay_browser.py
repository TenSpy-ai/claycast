"""
Clay Browser Helper — Self-Healing API Discovery

UNIX socket daemon wrapping Playwright/Chromium. Captures Clay API traffic
so Claude Code can discover correct input parameter names for new action types
without manual HAR exports.

Control channel: a UNIX socket at <runtime dir>/server.sock when that path fits the
platform's AF_UNIX limit (103 bytes on macOS/BSD, 107 on Linux); otherwise -- and always
on Windows -- a loopback TCP socket whose port is written to server.port (launch prints a
NOTE when a long CLAY_BROWSER_DIR forces that fallback). Every command carries the
per-daemon secret from server.token (created 0600 before the endpoint is advertised);
anything else is answered "unauthorized", so another local process that can reach
127.0.0.1:<port> cannot drive the logged-in browser, and pre-auth reads are bounded (5 s
idle, 1 MiB line) so it can stall the daemon for at most 5 s per connection. The runtime
dir is /tmp/clay-browser (POSIX) or %TEMP%\\clay-browser (Windows); set CLAY_BROWSER_DIR
to override it. On Windows os.chmod cannot restrict access: the files are protected only
by the NTFS ACL of the runtime dir (per-user under %TEMP% by default), so keep
CLAY_BROWSER_DIR under your own profile there.

Usage:
    python clay_browser.py launch [--headless]
    python clay_browser.py goto <url>
    python clay_browser.py snapshot
    python clay_browser.py screenshot [path]
    python clay_browser.py click <text> [--role button] [--nth 0]
    python clay_browser.py fill <text> [--placeholder "Search"]
    python clay_browser.py press <key>            # e.g. Enter, Escape, Control+a
    python clay_browser.py requests [--filter fields] [--last 5]
    python clay_browser.py close
"""

import argparse
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

# Control channel selection: a UNIX socket when the platform has AF_UNIX and server.sock fits
# sun_path (USE_UNIX_SOCKET below), otherwise -- always on Windows, which has no AF_UNIX in CPython
# -- a loopback TCP socket whose port is recorded in server.port. Both carry the secret written to
# server.token, required on every command. Windows also has no /tmp, so the runtime dir moves to
# the OS temp dir there; POSIX default paths are unchanged.
IS_WINDOWS = os.name == "nt"
RUNTIME_DIR = os.environ.get("CLAY_BROWSER_DIR") or (
    os.path.join(tempfile.gettempdir(), "clay-browser") if IS_WINDOWS else "/tmp/clay-browser"
)
SOCK_PATH = os.path.join(RUNTIME_DIR, "server.sock")
PORT_PATH = os.path.join(RUNTIME_DIR, "server.port")
TOKEN_PATH = os.path.join(RUNTIME_DIR, "server.token")
PID_PATH = os.path.join(RUNTIME_DIR, "server.pid")
REQUESTS_PATH = os.path.join(RUNTIME_DIR, "requests.jsonl")
LOG_PATH = os.path.join(RUNTIME_DIR, "daemon.log")
SCREENSHOT_PATH = os.path.join(RUNTIME_DIR, "screenshot.png")
RUNTIME_FILES = [SOCK_PATH, PORT_PATH, TOKEN_PATH, PID_PATH, REQUESTS_PATH, LOG_PATH]

# sun_path is 104 bytes on macOS/BSD and 108 on Linux, NUL included, and CPython rejects
# len(path) >= sizeof(sun_path) with "AF_UNIX path too long" — so the longest bindable path is one
# byte shorter. Verified live 2026-09-29 on macOS: bind succeeds at 103 bytes and fails at 104; the
# Linux figure is derived from sizeof(sun_path), not run. A CLAY_BROWSER_DIR that pushes server.sock
# past the limit falls back to the loopback TCP channel Windows uses (launch prints a NOTE) instead
# of killing the daemon at bind(). Keep the flag one line: tests/e2e_browser.sh regenerates it.
_SUN_PATH_MAX = 107 if sys.platform.startswith("linux") else 103
_SOCK_PATH_LEN = len(os.fsencode(SOCK_PATH))
USE_UNIX_SOCKET = not IS_WINDOWS and hasattr(socket, "AF_UNIX") and _SOCK_PATH_LEN <= _SUN_PATH_MAX

_CONN_TIMEOUT = 5.0            # seconds a connected peer gets to deliver its one-line command
_MAX_COMMAND_BYTES = 1 << 20   # longest accepted command line (eval JS / fill text are far smaller)


def _control_endpoint_exists() -> bool:
    return os.path.exists(SOCK_PATH if USE_UNIX_SOCKET else PORT_PATH)


def _connect_control_socket() -> socket.socket:
    """Connect to the daemon's control channel: the UNIX socket, or loopback TCP on the port in
    server.port when AF_UNIX is unavailable or the socket path exceeds sun_path. The caller sends the
    token from server.token with its command; the connection itself is unauthenticated.
    Raises FileNotFoundError / ConnectionRefusedError / OSError when no daemon is listening — also
    for a malformed or half-written server.port, never a ValueError."""
    if USE_UNIX_SOCKET:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK_PATH)
        return s
    with open(PORT_PATH) as f:
        raw = f.read().strip()
    try:
        port = int(raw)
        if not 0 < port <= 65535:
            raise ValueError(raw)
    except ValueError:
        raise OSError(f"malformed port file {PORT_PATH}: {raw!r}") from None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    return s


def _path_over_sun_path() -> bool:
    """The measured reason for a TCP fallback on a platform that has AF_UNIX: server.sock does not
    fit sun_path. Keyed on the measurement, not on USE_UNIX_SOCKET, so a test shim that forces the
    flag cannot make a short path print a false explanation."""
    return not IS_WINDOWS and hasattr(socket, "AF_UNIX") and _SOCK_PATH_LEN > _SUN_PATH_MAX


def _transport_notice():
    """One line explaining why a platform that has AF_UNIX is on loopback TCP (the socket path does
    not fit sun_path); None when the transport is the expected one for the platform."""
    if not _path_over_sun_path():
        return None
    return (
        f"[clay-browser] NOTE: {SOCK_PATH} is {_SOCK_PATH_LEN} bytes, over this platform's AF_UNIX "
        f"limit of {_SUN_PATH_MAX}; using a loopback TCP control socket instead (port in {PORT_PATH}, "
        f"guarded by {TOKEN_PATH}). Set CLAY_BROWSER_DIR to at most {_SUN_PATH_MAX - len('/server.sock')} "
        "bytes for a UNIX socket."
    )


def _acl_warning():
    """Windows only: os.chmod merely toggles the read-only bit, so server.token and the capture files
    are private only through the NTFS ACL of the runtime dir — per-user when it sits under the
    profile: %LOCALAPPDATA%, %APPDATA% or %USERPROFILE% (which %TEMP% does by default; the first two
    can be redirected off the profile drive, so all three set roots count). One line when it sits
    under none of them; None otherwise, and None when no root is set (cannot judge)."""
    if not IS_WINDOWS:
        return None
    roots = [os.environ.get(v) for v in ("LOCALAPPDATA", "APPDATA", "USERPROFILE")]
    roots = [os.path.normcase(os.path.abspath(r)).rstrip("\\/") for r in roots if r]
    if not roots:
        return None
    here = os.path.normcase(os.path.abspath(RUNTIME_DIR)).rstrip("\\/")
    if any(here == root or here.startswith(root + os.sep) for root in roots):
        return None
    return (
        f"[clay-browser] WARNING: {RUNTIME_DIR} is outside your user profile; on Windows only its NTFS "
        "ACL protects server.token and the capture files (chmod is a no-op there). Prefer a "
        "CLAY_BROWSER_DIR under %LOCALAPPDATA%, %APPDATA% or %USERPROFILE%."
    )


def _bind_control_socket():
    """Bind the daemon's control channel and return (listening socket, printable endpoint): a UNIX
    socket at SOCK_PATH, or loopback TCP on an OS-assigned port advertised in PORT_PATH. Raises
    OSError carrying the remedy when neither is possible."""
    if USE_UNIX_SOCKET:
        # The path fits sun_path by construction here (USE_UNIX_SOCKET), so a failure is the dir
        # itself: not writable by this user, read-only filesystem, or a stale non-socket at SOCK_PATH
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Clean up stale socket
            if os.path.exists(SOCK_PATH):
                os.unlink(SOCK_PATH)
            sock.bind(SOCK_PATH)
        except OSError as e:
            sock.close()
            raise OSError(
                f"cannot bind UNIX socket {SOCK_PATH}: {e}. The runtime dir {RUNTIME_DIR} must be "
                "writable by this user."
            ) from e
        return sock, SOCK_PATH
    # No AF_UNIX (Windows) or the path is too long: loopback TCP on an OS-assigned port, advertised via
    # server.port and protected by the token in server.token (0600)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except OSError as e:
        sock.close()
        if _path_over_sun_path():
            why = (
                f" (AF_UNIX was skipped: {SOCK_PATH} is {_SOCK_PATH_LEN} bytes, limit {_SUN_PATH_MAX})"
            )
            remedy = (
                f"Free 127.0.0.1, or set CLAY_BROWSER_DIR to at most "
                f"{_SUN_PATH_MAX - len('/server.sock')} bytes for a UNIX socket."
            )
        else:
            why, remedy = "", "Free 127.0.0.1 and launch again."
        raise OSError(f"cannot bind a loopback TCP control socket{why}: {e}. {remedy}") from e
    port = sock.getsockname()[1]
    # Atomic publish: a reader (the launcher's poll, a client) must never see an empty or partial
    # server.port, so write <port>.tmp (0600 from its first byte) and rename it into place
    tmp = PORT_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(str(port))
    os.chmod(tmp, 0o600)   # os.open's mode only applies when the file did not exist yet
    os.replace(tmp, PORT_PATH)
    return sock, f"127.0.0.1:{port}"


def _write_token() -> str:
    """Create the per-daemon control secret at TOKEN_PATH (0600 from its first byte) and return it.
    Runs before server.sock / server.port exist, so no client ever finds an endpoint without it."""
    token = secrets.token_hex(32)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(TOKEN_PATH, 0o600)   # os.open's mode only applies when the file did not exist yet
    return token


def _read_token() -> str:
    with open(TOKEN_PATH) as f:
        return f.read().strip()


def _unlink_runtime_files(keep=()):
    """Remove every RUNTIME_FILES entry that exists (socket / port / token / pid, capture, log) except
    those in `keep`, tolerating a concurrent removal. Shared by `close` and the bind-failure exit."""
    for path in RUNTIME_FILES:
        if path in keep or not os.path.exists(path):
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


def _force_exit_tree() -> None:
    """Kill this process together with its children (the Playwright driver and the browser).
    The daemon is spawned as a session/process-group leader (start_new_session on POSIX,
    CREATE_NEW_PROCESS_GROUP on Windows), so the group/tree is exactly the daemon's own."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(os.getpid())], capture_output=True)
        else:
            import signal

            os.killpg(os.getpgid(0), signal.SIGKILL)
    finally:
        os._exit(0)


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID is running. NOT os.kill(pid, 0) on Windows: CPython maps
    any signal other than CTRL_C/CTRL_BREAK to TerminateProcess, so the 'liveness check' would
    kill the daemon."""
    if IS_WINDOWS:
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

sys.path.insert(0, os.path.dirname(__file__))
from clay_client import _load_claysession  # noqa: E402


# ── Server (daemon) ──────────────────────────────────────────────────────────


class ClayBrowserServer:
    """Daemon: holds Playwright browser, listens on the control channel (UNIX socket, or loopback TCP
    when AF_UNIX is unavailable or the socket path is too long); every command needs the token."""

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.pw = None
        self._pending = {}      # id(request) → entry dict
        self._captured = []     # completed entries (also in requests.jsonl)
        self._shutdown_requested = False
        self._token = None      # per-daemon control secret, see _write_token()

    def start(self, headless=False):
        """Launch browser, set up capture, serve forever."""
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        os.chmod(RUNTIME_DIR, 0o700)

        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))
        os.chmod(PID_PATH, 0o600)

        # Must exist before _serve_forever advertises server.sock / server.port
        self._token = _write_token()
        print("[clay-browser] control token written", flush=True)
        for line in (_transport_notice(), _acl_warning()):   # same two lines launch prints
            if line:
                print(line, flush=True)

        open(REQUESTS_PATH, "w").close()
        os.chmod(REQUESTS_PATH, 0o600)

        self._setup_browser(headless)
        self._setup_capture()
        self._serve_forever()

    def _setup_browser(self, headless):
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        # Inject session cookie
        cookie_value = self._load_cookie()
        self.context.add_cookies([{
            "name": "claysession",
            "value": cookie_value,
            "domain": ".clay.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }])

        # Apply stealth
        try:
            from playwright_stealth import stealth_sync
            self.page = self.context.new_page()
            stealth_sync(self.page)
        except ImportError:
            self.page = self.context.new_page()

        print("[clay-browser] browser ready", flush=True)

    def _load_cookie(self) -> str:
        return _load_claysession()

    def _setup_capture(self):
        """Attach request/response listeners for api.clay.com traffic."""
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)

    def _on_request(self, request):
        if "api.clay.com" not in request.url:
            return
        entry = {
            "id": id(request),
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "url": request.url,
            "req_body": None,
        }
        if request.post_data:
            try:
                entry["req_body"] = json.loads(request.post_data)
            except (json.JSONDecodeError, TypeError):
                entry["req_body"] = request.post_data
        self._pending[id(request)] = entry

    def _on_response(self, response):
        if "api.clay.com" not in response.url:
            return
        req_id = id(response.request)
        entry = self._pending.pop(req_id, None)
        if not entry:
            return
        entry["status"] = response.status
        try:
            body = response.body()
            if len(body) < 1_000_000:
                entry["resp_body"] = json.loads(body)
            else:
                entry["resp_body"] = "(too large)"
        except Exception:
            entry["resp_body"] = None
        # Remove internal tracking id before persisting
        entry.pop("id", None)
        with open(REQUESTS_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._captured.append(entry)

    def _serve_forever(self):
        """Accept commands on the control channel (UNIX socket, or loopback TCP when AF_UNIX is
        unavailable / the path is too long). Each connection delivers one JSON line that must carry
        the control token (see _handle); the reads are bounded so an unauthenticated peer can hold
        the loop for at most _CONN_TIMEOUT. 0.5s timeout between accepts to let Playwright event
        handlers fire."""
        try:
            sock, endpoint = _bind_control_socket()
        except OSError as e:
            print(f"[clay-browser] {e}", flush=True)
            # No endpoint was ever advertised, so nothing can reach this daemon: remove the token, pid
            # and capture files as `close` would. daemon.log stays — the launcher's "check log" line
            # points at it and it now holds the reason.
            _unlink_runtime_files(keep=(LOG_PATH,))
            self._shutdown(code=1)   # tear the browser down instead of orphaning it
            return
        sock.listen(1)
        sock.settimeout(0.5)

        print(f"[clay-browser] listening on {endpoint}", flush=True)

        while True:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                # Flush pending Playwright events (request/response callbacks)
                try:
                    self.page.evaluate("1")
                except Exception:
                    pass
                continue
            except OSError:
                break

            try:
                # accept() hands back a *blocking* socket (socket.py forces it when the listener has a
                # timeout), so an idle or dribbling peer would otherwise stall this single-threaded
                # loop for good; with the timeout a pre-auth stall is bounded to _CONN_TIMEOUT per
                # connection (a flood of idle peers still serialises behind it — see the client)
                conn.settimeout(_CONN_TIMEOUT)
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in data:
                        break
                    if len(data) > _MAX_COMMAND_BYTES:
                        raise ValueError("command too long")

                line = data.decode("utf-8").strip()
                if not line:
                    continue

                cmd = json.loads(line)
                result = self._handle(cmd)
                conn.sendall((json.dumps(result) + "\n").encode("utf-8"))
            except Exception as e:
                try:
                    conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode("utf-8"))
                except Exception:
                    pass
            finally:
                conn.close()

            if self._shutdown_requested:
                self._shutdown()

    def _handle(self, cmd: dict) -> dict:
        """Dispatch to _cmd_* methods. Every command must carry the daemon's control token; anything
        else — from a process that can reach the loopback port but not read server.token — gets one
        generic refusal, before command names are even looked at. A payload that is not a JSON object
        gets the same reply (no interpreter error text leaks pre-auth)."""
        if not isinstance(cmd, dict) or not self._token_ok(cmd.get("token")):
            return {"ok": False, "error": "unauthorized: control token missing or wrong (client reads server.token)"}
        name = cmd.get("cmd", "")
        args = cmd.get("args", {})
        handler = getattr(self, f"_cmd_{name}", None)
        if not handler:
            return {"ok": False, "error": f"Unknown command: {name}"}
        try:
            return handler(args)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _token_ok(self, token) -> bool:
        # constant-time; bytes because compare_digest rejects non-ASCII str and the peer picks the text
        if not self._token or not isinstance(token, str):
            return False
        return hmac.compare_digest(token.encode("utf-8", "replace"), self._token.encode("ascii"))

    # ── Commands ──────────────────────────────────────────────────────────────

    def _cmd_goto(self, args) -> dict:
        url = args.get("url", "")
        if not url:
            return {"ok": False, "error": "url required"}
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"ok": True, "title": self.page.title(), "url": self.page.url}

    def _cmd_snapshot(self, args) -> dict:
        snap = self.page.locator("body").aria_snapshot()
        return {"ok": True, "snapshot": snap}

    def _cmd_screenshot(self, args) -> dict:
        path = args.get("path", SCREENSHOT_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.page.screenshot(path=path, full_page=False)
        return {"ok": True, "path": path}

    def _cmd_click(self, args) -> dict:
        text = args.get("text", "")
        role = args.get("role")
        nth = args.get("nth")  # None means "not specified"

        if not text:
            return {"ok": False, "error": "text required"}

        if role:
            loc = self.page.get_by_role(role, name=text)
        else:
            # Try button first, fall back to text
            loc = self.page.get_by_role("button", name=text)
            if loc.count() == 0:
                loc = self.page.get_by_text(text)

        count = loc.count()
        if count == 0:
            return {"ok": False, "error": f"No element found: '{text}'"}
        if count > 1 and nth is None:
            return {
                "ok": False,
                "error": f"{count} matches found, use --nth to pick one",
                "count": count,
            }

        loc.nth(nth or 0).click()
        # Best-effort wait for triggered API calls
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return {"ok": True, "clicked": text, "nth": nth}

    def _cmd_fill(self, args) -> dict:
        text = args.get("text", "")
        placeholder = args.get("placeholder")

        if not text:
            return {"ok": False, "error": "text required"}

        if placeholder:
            loc = self.page.get_by_placeholder(placeholder)
            count = loc.count()
            if count == 0:
                return {"ok": False, "error": f"No input with placeholder '{placeholder}'"}
            loc.first.fill(text)
        else:
            self.page.keyboard.type(text)

        return {"ok": True, "filled": text}

    def _cmd_requests(self, args) -> dict:
        filter_str = args.get("filter")
        last_n = args.get("last")

        entries = []
        if os.path.exists(REQUESTS_PATH):
            with open(REQUESTS_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))

        if filter_str:
            entries = [e for e in entries if filter_str in e.get("url", "")]

        if last_n:
            entries = entries[-last_n:]

        return {"ok": True, "count": len(entries), "requests": entries}

    def _cmd_eval(self, args) -> dict:
        # Playwright's page.evaluate treats the argument as an expression, not a
        # function body — top-level `return` is a SyntaxError. Wrap multi-statement
        # logic in an IIFE: `(() => { ...; return X; })()`.
        js = args.get("js", "")
        if not js:
            return {"ok": False, "error": "js required"}
        result = self.page.evaluate(js)
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return {"ok": True, "result": result}

    def _cmd_press(self, args) -> dict:
        # Real key press via Playwright (synthetic KeyboardEvents from eval do not submit
        # dialogs/forms). Key names as in Playwright: "Enter", "Escape", "Tab", "Control+a".
        key = args.get("key", "")
        if not key:
            return {"ok": False, "error": "key required"}
        self.page.keyboard.press(key)
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return {"ok": True, "pressed": key}

    def _cmd_click_selector(self, args) -> dict:
        selector = args.get("selector", "")
        if not selector:
            return {"ok": False, "error": "selector required"}
        self.page.locator(selector).first.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return {"ok": True, "clicked": selector}

    def _cmd_close(self, args) -> dict:
        # Unlink runtime + capture files (RUNTIME_FILES: server.{sock,port,token,pid},
        # requests.jsonl, daemon.log) BEFORE the response goes out, so any
        # immediate post-close check (ls / CI test / automation) sees the files
        # gone. Unlinking SOCK_PATH does not break this active connection —
        # the open fd stays valid until the response is sent and conn.close()
        # fires. Browser teardown still happens in _shutdown() after the
        # response, since Playwright teardown is slow and racy to do inline.
        _unlink_runtime_files()
        self._shutdown_requested = True
        return {"ok": True, "message": "shutting down"}

    def _shutdown(self, code=0):
        """Clean shutdown — called after close response is sent (code 0; capture files were already
        unlinked synchronously in _cmd_close()) or when the control channel could not be bound
        (code 1; see _serve_forever).

        Playwright teardown can hang (seen on Windows after interacting with a modal dialog),
        which left the daemon, its driver and the browser alive and holding daemon.log open.
        A watchdog kills the whole process tree if the polite path has not finished within
        10 seconds — plain os._exit would orphan the driver and browser (observed)."""
        import threading

        threading.Timer(10.0, _force_exit_tree).start()
        try:
            self.page.close()
        except Exception:
            pass
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.pw.stop()
        except Exception:
            pass
        if code == 0:
            print("[clay-browser] closed (capture files deleted)", flush=True)
        else:
            print(f"[clay-browser] exited with code {code} (reason above)", flush=True)
        os._exit(code)


# ── Client (CLI) ──────────────────────────────────────────────────────────────


class ClayBrowserClient:
    """Connect to daemon, send command, return result. Attaches the control token read from
    server.token to every command (both transports); a daemon that is alive but whose token file
    or endpoint is unusable is reported as such, distinctly from "Daemon not running"."""

    def send(self, cmd: str, **kwargs) -> dict:
        # After an unclean daemon death server.port (or server.sock) may name an endpoint some other
        # process now owns — never hand the token to it. server.pid alive is the precondition.
        pid = _read_pid()
        if pid is None or not _pid_alive(pid):
            return {"ok": False, "error": "Daemon not running. Run: python clay_browser.py launch"}
        try:
            token = _read_token()
        except OSError:
            return {
                "ok": False,
                "error": f"Daemon is running (see {PID_PATH}) but {TOKEN_PATH} is unreadable, so `close` "
                         f"cannot authenticate; kill that PID, delete {RUNTIME_DIR}, and launch again",
            }
        try:
            sock = _connect_control_socket()
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            # The pid gate just held, so "not running" would be wrong: the endpoint is stale or
            # half-written, or idle peers are holding the single-threaded loop (5 s each)
            endpoint = SOCK_PATH if USE_UNIX_SOCKET else f"the loopback port in {PORT_PATH}"
            return {
                "ok": False,
                "error": f"Daemon alive (PID {pid}) but {endpoint} is not accepting connections ({e}); "
                         "retry, or kill the PID and launch again",
            }

        payload = json.dumps({"cmd": cmd, "args": kwargs, "token": token}) + "\n"
        sock.sendall(payload.encode("utf-8"))

        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        sock.close()

        text = data.decode("utf-8").strip()
        if not text:
            return {"ok": True, "message": "connection closed (daemon may have shut down)"}
        return json.loads(text)


# ── Launcher ──────────────────────────────────────────────────────────────────


def _read_pid():
    """The PID in server.pid, or None when the file is missing, truncated or corrupt (daemon killed
    mid-write) or holds a non-positive number: os.kill(0, 0) and os.kill(-1, 0) both succeed, so a
    '0' / '-1' pid file would otherwise read as a live daemon and defeat the client's stale-endpoint
    gate."""
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _is_daemon_alive() -> bool:
    """Check if a daemon process is still running."""
    pid = _read_pid()
    return pid is not None and _pid_alive(pid)


def _cleanup_stale():
    """Remove stale socket/pid if daemon is dead."""
    if os.path.exists(PID_PATH) and not _is_daemon_alive():
        for path in RUNTIME_FILES:
            if os.path.exists(path):
                os.unlink(path)


def launch_daemon(headless=False):
    """Fork a daemon process that runs the browser server."""
    _cleanup_stale()

    if _is_daemon_alive():
        with open(PID_PATH) as f:
            pid = f.read().strip()
        print(f"[clay-browser] already running (PID {pid})")
        return

    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.chmod(RUNTIME_DIR, 0o700)

    open(LOG_PATH, "w").close()
    os.chmod(LOG_PATH, 0o600)

    for line in (_transport_notice(), _acl_warning()):
        if line:
            print(line, flush=True)

    print(
        "[clay-browser] WARNING: capturing api.clay.com request + response "
        f"bodies to {REQUESTS_PATH} (mode 0600). May contain auth cookies "
        "and scraped PII. Run `clay_browser.py close` to delete the capture "
        "files."
        + ("" if USE_UNIX_SOCKET else
           f" The control channel is a loopback TCP port guarded only by {TOKEN_PATH}; keep the "
           "runtime dir private."),
        flush=True,
    )

    popen_kwargs = {"stdout": open(LOG_PATH, "a"), "stderr": subprocess.STDOUT}
    if IS_WINDOWS:
        # start_new_session is POSIX-only; detach so the daemon outlives this CLI process
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, __file__, "--daemon"] + (["--headless"] if headless else []),
        **popen_kwargs,
    )

    # Wait for the control endpoint to appear (up to 15s — browser launch can be slow)
    for _ in range(30):
        time.sleep(0.5)
        if proc.poll() is not None:
            break   # died already (bind failure, missing Playwright): report it now, not after 15 s
        if _control_endpoint_exists():
            # Verify it is connectable
            try:
                s = _connect_control_socket()
                s.close()
                print(f"[clay-browser] launched. PID: {proc.pid}")
                return
            except (ConnectionRefusedError, OSError):
                continue

    # Timeout — check if process died
    if proc.poll() is not None:
        print(f"[clay-browser] daemon exited with code {proc.returncode}")
        print(f"[clay-browser] check log: {LOG_PATH}")
    else:
        print(f"[clay-browser] timeout waiting for socket (PID {proc.pid})")
        print(f"[clay-browser] check log: {LOG_PATH}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Clay Browser Helper — self-healing API discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Hidden daemon flag (used by launch_daemon)
    parser.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--headless", action="store_true", help="Run browser headless")

    sub = parser.add_subparsers(dest="command")

    # launch
    launch_p = sub.add_parser("launch", help="Start daemon + browser")
    launch_p.add_argument("--headless", action="store_true")

    # close
    sub.add_parser("close", help="Shutdown daemon")

    # goto
    goto_p = sub.add_parser("goto", help="Navigate to URL")
    goto_p.add_argument("url", help="Full Clay URL")

    # snapshot
    sub.add_parser("snapshot", help="aria_snapshot() of page body")

    # screenshot
    ss_p = sub.add_parser("screenshot", help="Save PNG screenshot")
    ss_p.add_argument("path", nargs="?", default=SCREENSHOT_PATH)

    # click
    click_p = sub.add_parser("click", help="Click element by text")
    click_p.add_argument("text", help="Text to click")
    click_p.add_argument("--role", help="ARIA role (button, menuitem, link, etc.)")
    click_p.add_argument("--nth", type=int, default=None, help="0-indexed match (default: disambiguate)")

    # fill
    fill_p = sub.add_parser("fill", help="Type text")
    fill_p.add_argument("text", help="Text to type")
    fill_p.add_argument("--placeholder", help="Target input by placeholder text")

    # requests
    req_p = sub.add_parser("requests", help="Show captured API calls")
    req_p.add_argument("--filter", help="Filter URLs containing this string")
    req_p.add_argument("--last", type=int, help="Show only last N requests")

    # eval
    eval_p = sub.add_parser("eval", help="Evaluate JavaScript in browser context")
    eval_p.add_argument("js", help="JavaScript to evaluate")

    # click_selector
    sel_p = sub.add_parser("click_selector", help="Click element by CSS selector")
    sel_p.add_argument("selector", help="CSS selector")

    # press
    press_p = sub.add_parser("press", help="Press a key (Playwright key name, e.g. Enter, Escape, Control+a)")
    press_p.add_argument("key", help="Key to press")

    args = parser.parse_args()

    # Daemon mode (internal — spawned by launch_daemon)
    if args.daemon:
        server = ClayBrowserServer()
        server.start(headless=args.headless)
        return

    if not args.command:
        parser.print_help()
        return

    # Launch is special — forks daemon
    if args.command == "launch":
        launch_daemon(headless=args.headless)
        return

    # All other commands go through the client
    client = ClayBrowserClient()

    if args.command == "close":
        result = client.send("close")
    elif args.command == "goto":
        result = client.send("goto", url=args.url)
    elif args.command == "snapshot":
        result = client.send("snapshot")
    elif args.command == "screenshot":
        result = client.send("screenshot", path=args.path)
    elif args.command == "click":
        kwargs = {"text": args.text}
        if args.nth is not None:
            kwargs["nth"] = args.nth
        if args.role:
            kwargs["role"] = args.role
        result = client.send("click", **kwargs)
    elif args.command == "fill":
        kwargs = {"text": args.text}
        if args.placeholder:
            kwargs["placeholder"] = args.placeholder
        result = client.send("fill", **kwargs)
    elif args.command == "requests":
        kwargs = {}
        if args.filter:
            kwargs["filter"] = args.filter
        if args.last:
            kwargs["last"] = args.last
        result = client.send("requests", **kwargs)
    elif args.command == "eval":
        result = client.send("eval", js=args.js)
    elif args.command == "click_selector":
        result = client.send("click_selector", selector=args.selector)
    elif args.command == "press":
        result = client.send("press", key=args.key)
    else:
        parser.print_help()
        return

    # Pretty-print result
    if result.get("ok"):
        # Special formatting for specific commands
        if args.command == "snapshot":
            print(result.get("snapshot", ""))
        elif args.command == "requests":
            reqs = result.get("requests", [])
            if not reqs:
                print("(no captured requests)")
            else:
                for r in reqs:
                    status = r.get("status", "?")
                    method = r.get("method", "?")
                    url = r.get("url", "")
                    # Shorten URL for display
                    short_url = url.replace("https://api.clay.com/v3", "")
                    print(f"\n{method} {short_url}  [{status}]")
                    if r.get("req_body"):
                        print(f"  req: {json.dumps(r['req_body'], indent=2)[:2000]}")
                    if r.get("resp_body") and r["resp_body"] != "(too large)":
                        body_str = json.dumps(r["resp_body"], indent=2)
                        if len(body_str) > 2000:
                            body_str = body_str[:2000] + "\n  ... (truncated)"
                        print(f"  resp: {body_str}")
                print(f"\n({result.get('count', 0)} total)")
        else:
            print(json.dumps(result, indent=2))
    else:
        print(f"ERROR: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
