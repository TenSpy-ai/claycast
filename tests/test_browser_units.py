"""clay_browser.py helpers, POSIX side. The Windows-only branches (ctypes OpenProcess liveness,
taskkill, DETACHED_PROCESS) cannot execute here; the tests that bind a UNIX socket, assert the
/tmp defaults or send POSIX signals skip where AF_UNIX is missing or on Windows, and so does every
test that takes the short_dir / long_dir fixtures (the fixture itself skips there).

Two ways at the module: `_probe` runs a subprocess (for stdout and import-time assertions) and
`_load(env)` imports scripts/clay_browser.py in-process under a given CLAY_BROWSER_DIR — the path
constants and USE_UNIX_SOCKET are computed at import, so a test that needs one transport loads its
own module object. UNIX-mode tests use `short_dir` (tempfile.mkdtemp under /tmp): a pytest tmp_path
is already >103 bytes on macOS and would silently select TCP mode. Exact-length dirs are derived
from a mkdtemp base too, so concurrent runs cannot collide.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types

import pytest

from conftest import SCRIPTS, load_module

PY = sys.executable
SCRIPTS_DIR = str(SCRIPTS)
LIMIT = 107 if sys.platform.startswith("linux") else 103   # mirrors _SUN_PATH_MAX; a real bind validates it below
UNAUTH = {"ok": False, "error": "unauthorized: control token missing or wrong (client reads server.token)"}


def posix_only(reason):
    return pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason=reason)


def _probe(code, env=None):
    full = f"import sys; sys.path.insert(0, {SCRIPTS_DIR!r}); import clay_browser as b\n" + textwrap.dedent(code)
    e = dict(os.environ, **(env or {}))
    return subprocess.run([PY, "-c", full], capture_output=True, text=True, env=e, timeout=60)


def _load(env):
    """Import scripts/clay_browser.py in-process under CLAY_BROWSER_DIR=env, as its own module object."""
    old = os.environ.get("CLAY_BROWSER_DIR")
    os.environ["CLAY_BROWSER_DIR"] = env
    try:
        return load_module(f"clay_browser_{abs(hash(env))}", SCRIPTS, filename="clay_browser.py")
    finally:
        if old is None:
            del os.environ["CLAY_BROWSER_DIR"]
        else:
            os.environ["CLAY_BROWSER_DIR"] = old


def _dir_for_sock_len(base, n):
    """A directory under base whose server.sock path is exactly n bytes long."""
    pad = n - len(os.fsencode(os.path.join(base, "server.sock"))) - 1  # -1 for the separator
    assert pad > 0, "base too long for this length"
    d = os.path.join(base, "d" * pad)
    assert len(os.fsencode(os.path.join(d, "server.sock"))) == n
    return d


@pytest.fixture
def short_dir():
    """~14-byte /tmp/cbXXXXXX: server.sock fits sun_path, so the module loads in UNIX-socket mode.

    POSIX only (long_dir derives from it): the dir lives under /tmp and its users bind UNIX sockets
    or assert 0600 file modes, so on Windows / without AF_UNIX every test that takes either fixture
    skips here instead of erroring at setup.
    """
    if not hasattr(socket, "AF_UNIX") or os.name == "nt":
        pytest.skip("short_dir/long_dir: /tmp dirs, UNIX sockets and 0600 modes are POSIX-only")
    d = tempfile.mkdtemp(prefix="cb", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def long_dir(short_dir):
    """A dir under short_dir long enough to push server.sock past sun_path: the module loads in TCP mode."""
    d = os.path.join(short_dir, "x" * 120)
    os.makedirs(d)
    return d


# ── existing surface ──────────────────────────────────────────────────────────


@posix_only("asserts the POSIX defaults: /tmp/clay-browser and the UNIX control socket")
def test_posix_default_paths():
    code = "print(b.RUNTIME_DIR, b.SOCK_PATH, b.PID_PATH, b.REQUESTS_PATH, b.LOG_PATH)"
    out = _probe(code, {"CLAY_BROWSER_DIR": ""})  # an empty override falls back to the default
    assert out.stdout.split() == ["/tmp/clay-browser", "/tmp/clay-browser/server.sock", "/tmp/clay-browser/server.pid",
                                  "/tmp/clay-browser/requests.jsonl", "/tmp/clay-browser/daemon.log"], out.stderr
    assert _probe("print(b.USE_UNIX_SOCKET, b.IS_WINDOWS, b._transport_notice())", {"CLAY_BROWSER_DIR": ""}).stdout.split() == [
        "True", "False", "None"]


def test_runtime_dir_override(tmp_path):
    out = _probe("print(b.RUNTIME_DIR); print(b.PORT_PATH); print(b.SCREENSHOT_PATH)", {"CLAY_BROWSER_DIR": str(tmp_path)})
    # one path per line: a temp dir may contain spaces (Windows user names), so no split()
    assert out.stdout.splitlines() == [str(tmp_path), str(tmp_path / "server.port"), str(tmp_path / "screenshot.png")]


def test_runtime_files_include_port_and_token_files():
    out = _probe("print(b.PORT_PATH in b.RUNTIME_FILES, b.TOKEN_PATH in b.RUNTIME_FILES, len(b.RUNTIME_FILES))")
    assert out.stdout.split() == ["True", "True", "6"]


@posix_only("POSIX liveness probe: os.kill(pid, 0)")
def test_pid_alive_posix():
    child = subprocess.Popen([PY, "-c", "pass"])
    child.wait()
    out = _probe(f"import os; print(b._pid_alive(os.getpid()), b._pid_alive({child.pid}))")
    assert out.stdout.split() == ["True", "False"], out.stderr


def test_client_reports_not_running_when_no_daemon(tmp_path):
    # runs on every platform: no server.pid means the client never even tries to connect
    out = _probe("print(b.ClayBrowserClient().send('snapshot'))", {"CLAY_BROWSER_DIR": str(tmp_path)})
    assert "Daemon not running" in out.stdout, out.stderr


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie still answers kill(0); treat it as dead
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def _hung_daemon(tmp_path, arm_after):
    """Spawn a process in its own session (like launch_daemon does) that starts a grandchild
    `sleep`, then arms the shutdown watchdog (_force_exit_tree) and hangs — simulating a stuck
    Playwright teardown."""
    pidfile = tmp_path / "kids"
    code = textwrap.dedent(f"""
        import os, subprocess, sys, threading, time
        sys.path.insert(0, {SCRIPTS_DIR!r})
        import clay_browser as b
        kid = subprocess.Popen(["sleep", "300"])
        open({str(pidfile)!r}, "w").write(f"{{os.getpid()}} {{kid.pid}}")
        threading.Timer({arm_after}, b._force_exit_tree).start()
        time.sleep(300)   # 'hung teardown'
    """)
    proc = subprocess.Popen([PY, "-c", code], start_new_session=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    daemon, kid = map(int, pidfile.read_text().split())
    return proc, daemon, kid


@posix_only("watchdog test uses start_new_session, killpg and ps")
def test_watchdog_kills_hung_daemon_and_its_children(tmp_path):
    proc, daemon, kid = _hung_daemon(tmp_path, arm_after=1.0)
    assert _alive(daemon) and _alive(kid)
    t0 = time.time()
    proc.wait(timeout=15)
    elapsed = time.time() - t0
    time.sleep(0.3)
    assert not _alive(kid), "grandchild (stand-in for driver/browser) survived"
    assert elapsed < 5
    # SIGKILL (-9) via killpg, or 0 if the `finally: os._exit(0)` wins the race with the
    # self-delivered signal — either way the process is gone
    assert proc.returncode in (0, -9)
    assert not _alive(daemon)


@posix_only("watchdog test uses start_new_session, killpg and ps")
def test_watchdog_group_kill_does_not_touch_the_parent(tmp_path):
    """killpg targets the daemon's own group; the test runner (a different group) survives."""
    proc, daemon, kid = _hung_daemon(tmp_path, arm_after=0.5)
    proc.wait(timeout=15)
    assert os.getpgid(0) != daemon
    assert _alive(os.getpid())


# ── M1: sun_path detection and the TCP fallback ──────────────────────────────


@posix_only("binds AF_UNIX sockets at the sun_path limit")
def test_sun_path_constant_matches_this_kernel(short_dir):
    """The threshold is only right if bind() really succeeds AT the limit and fails one byte over."""
    at = _dir_for_sock_len(short_dir, LIMIT)
    over = _dir_for_sock_len(short_dir, LIMIT + 1)
    for d, ok in ((at, True), (over, False)):
        os.makedirs(d, exist_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(os.path.join(d, "server.sock"))
            bound = True
        except OSError as e:
            bound = False
            assert "AF_UNIX path too long" in str(e)
        finally:
            s.close()
        assert bound is ok, (d, LIMIT)


@posix_only("UNIX-socket mode does not exist on Windows")
def test_boundary_at_limit_unix_over_limit_tcp(short_dir):
    at = _dir_for_sock_len(short_dir, LIMIT)
    over = _dir_for_sock_len(short_dir, LIMIT + 1)
    assert _probe("print(b.USE_UNIX_SOCKET, b._SOCK_PATH_LEN)", {"CLAY_BROWSER_DIR": at}).stdout.split() == ["True", str(LIMIT)]
    assert _probe("print(b.USE_UNIX_SOCKET, b._SOCK_PATH_LEN)", {"CLAY_BROWSER_DIR": over}).stdout.split() == ["False", str(LIMIT + 1)]


@posix_only("the NOTE only exists on platforms that have AF_UNIX")
def test_long_dir_falls_back_to_tcp_with_notice_and_port_endpoint(long_dir):
    out = _probe(
        """
        print(b.USE_UNIX_SOCKET)
        n = b._transport_notice(); print(bool(n)); print(n)
        open(b.PORT_PATH, "w").write("1"); print(b._control_endpoint_exists())
        """,
        {"CLAY_BROWSER_DIR": long_dir},
    )
    lines = out.stdout.splitlines()
    assert lines[0] == "False", out.stderr
    assert lines[1] == "True"
    assert "AF_UNIX" in lines[2] and str(LIMIT) in lines[2] and "CLAY_BROWSER_DIR" in lines[2] and "server.token" in lines[2]
    assert str(LIMIT - len("/server.sock")) in lines[2]   # the byte budget for CLAY_BROWSER_DIR is computed, not hardcoded
    assert lines[3] == "True"  # endpoint existence keys on server.port in this mode


def test_import_is_silent_even_for_long_dir(long_dir):
    out = _probe("pass", {"CLAY_BROWSER_DIR": long_dir})
    assert out.stdout == "" and out.returncode == 0, out.stderr


@posix_only("a forced transport flag must not change the measured NOTE")
def test_transport_notice_keyed_on_measured_length_not_the_flag(short_dir, long_dir, monkeypatch):
    b_short = _load(short_dir)
    monkeypatch.setattr(b_short, "USE_UNIX_SOCKET", False)   # the e2e tcp shim does exactly this
    assert b_short._transport_notice() is None
    b_long = _load(long_dir)
    monkeypatch.setattr(b_long, "USE_UNIX_SOCKET", True)
    assert b_long._transport_notice() is not None and "AF_UNIX limit" in b_long._transport_notice()


@posix_only("needs AF_UNIX present so the Windows guard is the only reason for TCP")
def test_use_unix_socket_false_on_windows_even_with_af_unix(short_dir, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")   # computed at import: IS_WINDOWS = os.name == "nt"
    b = _load(short_dir)
    assert b.IS_WINDOWS is True and hasattr(socket, "AF_UNIX") and b._SOCK_PATH_LEN <= b._SUN_PATH_MAX
    assert b.USE_UNIX_SOCKET is False
    assert b._transport_notice() is None   # TCP is the expected transport there, nothing to explain


@posix_only("binds a UNIX socket")
def test_bind_control_socket_unix(short_dir):
    b = _load(short_dir)
    assert b.USE_UNIX_SOCKET
    open(b.SOCK_PATH, "w").close()  # stale non-socket file is replaced
    sock, endpoint = b._bind_control_socket()
    try:
        assert endpoint == b.SOCK_PATH and sock.family == socket.AF_UNIX
        assert os.path.exists(b.SOCK_PATH) and not os.path.exists(b.PORT_PATH)
    finally:
        sock.close()


def test_bind_control_socket_tcp_writes_0600_port_atomically(long_dir):
    b = _load(long_dir)
    assert not b.USE_UNIX_SOCKET
    open(b.PORT_PATH + ".tmp", "w").close()   # a leftover from a crash is overwritten, then renamed away
    sock, endpoint = b._bind_control_socket()
    try:
        port = int(open(b.PORT_PATH).read())
        assert endpoint == f"127.0.0.1:{port}" and sock.getsockname() == ("127.0.0.1", port)
        assert oct(os.stat(b.PORT_PATH).st_mode & 0o777) == "0o600"
        assert not os.path.exists(b.PORT_PATH + ".tmp")
    finally:
        sock.close()


@posix_only("UNIX-branch bind error")
def test_unix_bind_failure_names_the_dir_not_the_length(short_dir):
    b = _load(os.path.join(short_dir, "missing-subdir"))  # dir absent -> bind ENOENT
    with pytest.raises(OSError) as ei:
        b._bind_control_socket()
    msg = str(ei.value)
    assert "cannot bind UNIX socket" in msg and "writable by this user" in msg and "Errno" in msg
    assert "at most" not in msg   # the path fits by construction here; a length remedy would be wrong


class _Unbindable(socket.socket):
    """socket.socket whose bind() fails the way a busy loopback does."""

    def bind(self, address):
        raise OSError(48, "Address already in use")


@posix_only("the AF_UNIX clause only exists on platforms that have AF_UNIX")
def test_tcp_bind_failure_under_a_long_dir_names_the_skipped_unix_socket_and_the_byte_budget(long_dir, monkeypatch):
    b = _load(long_dir)
    assert not b.USE_UNIX_SOCKET
    monkeypatch.setattr(b.socket, "socket", _Unbindable)
    with pytest.raises(OSError) as ei:
        b._bind_control_socket()
    msg = str(ei.value)
    assert "cannot bind a loopback TCP control socket" in msg and "Address already in use" in msg
    assert "AF_UNIX was skipped" in msg and str(LIMIT - len("/server.sock")) in msg and "Free 127.0.0.1" in msg
    assert not os.path.exists(b.PORT_PATH)   # nothing was advertised


def test_tcp_bind_failure_on_a_short_dir_has_no_af_unix_clause(short_dir, monkeypatch):
    b = _load(short_dir)
    monkeypatch.setattr(b, "USE_UNIX_SOCKET", False)   # the Windows layout: TCP although the path fits
    monkeypatch.setattr(b.socket, "socket", _Unbindable)
    with pytest.raises(OSError) as ei:
        b._bind_control_socket()
    msg = str(ei.value)
    assert msg.startswith("cannot bind a loopback TCP control socket: ") and "Address already in use" in msg
    assert msg.endswith("Free 127.0.0.1 and launch again.")
    assert "AF_UNIX was skipped" not in msg and "at most" not in msg   # a length remedy would be wrong here


@pytest.mark.parametrize("content", ["", "abc", "12 34", "99999", "-5"])
def test_malformed_port_file_is_oserror_not_valueerror(long_dir, content):
    b = _load(long_dir)
    with open(b.PORT_PATH, "w") as f:
        f.write(content)
    with pytest.raises(OSError) as ei:
        b._connect_control_socket()
    assert not isinstance(ei.value, ValueError) and "malformed port file" in str(ei.value)


def test_half_written_port_file_with_live_pid_yields_error_dict_and_launcher_does_not_raise(long_dir, monkeypatch, capsys):
    """The port-file twin of the corrupt-pid case: an empty server.port next to a live pid used to
    escape both send() and the launch wait loop as an uncaught ValueError."""
    b = _load(long_dir)
    b._write_token()
    open(b.PORT_PATH, "w").close()
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    r = b.ClayBrowserClient().send("eval", js="1")
    assert r["ok"] is False and "not accepting connections" in r["error"] and "malformed port file" in r["error"]
    # launcher: no pid file (else "already running"), empty server.port, a child that stays alive
    os.unlink(b.PID_PATH)
    stub = types.SimpleNamespace(pid=4242, returncode=None, poll=lambda: None)
    monkeypatch.setattr(b, "subprocess", types.SimpleNamespace(Popen=lambda *a, **k: stub, STDOUT=subprocess.STDOUT))
    monkeypatch.setattr(b, "time", types.SimpleNamespace(sleep=lambda s: None))   # 30 polls, no waiting
    b.launch_daemon(headless=True)   # must not raise
    out = capsys.readouterr().out
    assert "timeout waiting for socket (PID 4242)" in out and "Traceback" not in out


def test_bind_failure_cleans_runtime_files_keeps_log_and_exits_1(short_dir, monkeypatch, capsys):
    b = _load(short_dir)
    s = _bare_server(b, token=b._write_token())
    for p in (b.PID_PATH, b.REQUESTS_PATH, b.LOG_PATH):
        open(p, "w").close()
    codes = []
    monkeypatch.setattr(b.ClayBrowserServer, "_shutdown", lambda self, code=0: codes.append(code))

    def boom():
        raise OSError(f"cannot bind UNIX socket {b.SOCK_PATH}: [Errno 13] Permission denied. The runtime dir must be writable by this user.")

    monkeypatch.setattr(b, "_bind_control_socket", boom)
    s._serve_forever()
    assert codes == [1]
    out = capsys.readouterr().out
    assert "[clay-browser] cannot bind UNIX socket" in out and "Permission denied" in out
    assert "deleted" not in out
    left = [p for p in b.RUNTIME_FILES if os.path.exists(p)]
    assert left == [b.LOG_PATH]   # the launcher's "check log" pointer must still resolve


def test_shutdown_line_depends_on_exit_code(short_dir, monkeypatch, capsys):
    b = _load(short_dir)
    s = _bare_server(b)
    s.browser = s.context = s.pw = None   # every close()/stop() is tolerated
    codes = []
    monkeypatch.setattr(b.os, "_exit", lambda code: codes.append(code))
    monkeypatch.setattr(b, "_force_exit_tree", lambda: None)
    try:
        s._shutdown(code=1)
        s._shutdown()
    finally:
        for t in threading.enumerate():   # the 10 s watchdog timers armed by _shutdown
            if isinstance(t, threading.Timer):
                t.cancel()
    assert codes == [1, 0]
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["[clay-browser] exited with code 1 (reason above)", "[clay-browser] closed (capture files deleted)"]


def test_launch_reports_immediate_daemon_death_within_a_second(long_dir, monkeypatch, capsys):
    b = _load(long_dir)
    stub = types.SimpleNamespace(pid=4242, returncode=1, poll=lambda: 1)
    monkeypatch.setattr(b, "subprocess", types.SimpleNamespace(Popen=lambda *a, **k: stub, STDOUT=subprocess.STDOUT))
    t0 = time.time()
    b.launch_daemon(headless=True)
    elapsed = time.time() - t0
    out = capsys.readouterr().out
    assert elapsed < 2, elapsed   # one 0.5 s poll, not the full 15 s wait
    assert "daemon exited with code 1" in out and f"check log: {b.LOG_PATH}" in out


# ── M2: control token ─────────────────────────────────────────────────────────


def test_write_token_0600_64_hex(short_dir):
    b = _load(short_dir)
    tok = b._write_token()
    assert len(tok) == 64 and int(tok, 16) >= 0
    assert open(b.TOKEN_PATH).read() == tok and b._read_token() == tok
    assert oct(os.stat(b.TOKEN_PATH).st_mode & 0o777) == "0o600"
    assert b._write_token() != tok  # rotates per call (per daemon start)
    assert b.TOKEN_PATH in b.RUNTIME_FILES and len(b.RUNTIME_FILES) == 6


def _bare_server(b, token="a" * 64):
    s = b.ClayBrowserServer.__new__(b.ClayBrowserServer)
    s._token = token
    s._shutdown_requested = False
    s.page = None
    return s


def test_handle_requires_token_before_anything_else(short_dir):
    b = _load(short_dir)
    s = _bare_server(b)
    assert s._handle({"cmd": "eval", "args": {"js": "1"}}) == UNAUTH
    assert s._handle({"cmd": "eval", "args": {}, "token": "b" * 64}) == UNAUTH
    assert s._handle({"cmd": "eval", "args": {}, "token": ["a" * 64]}) == UNAUTH  # non-str never raises
    assert s._handle({"cmd": "eval", "args": {}, "token": "é" * 64}) == UNAUTH  # non-ASCII never raises
    assert s._handle({"cmd": "nope", "args": {}}) == UNAUTH  # no command enumeration before auth
    for not_an_object in ([1, 2], 5, "eval", None, True):   # valid JSON, not a dict: same generic refusal
        assert s._handle(not_an_object) == UNAUTH
    assert s._handle({"cmd": "nope", "args": {}, "token": "a" * 64}) == {"ok": False, "error": "Unknown command: nope"}
    ok = s._handle({"cmd": "requests", "args": {}, "token": "a" * 64})
    assert ok["ok"] is True and ok["count"] == 0
    s._token = None
    assert s._handle({"cmd": "requests", "args": {}, "token": "a" * 64}) == UNAUTH  # unset token = closed


def _run_loop(b, monkeypatch):
    """Run the real accept loop on a bare server (no Playwright) in a daemon thread."""
    s = _bare_server(b, token=b._write_token())
    # the real _shutdown tears down Playwright and os._exit()s; here the loop just keeps serving
    monkeypatch.setattr(b.ClayBrowserServer, "_shutdown", lambda self, code=0: None)
    t = threading.Thread(target=s._serve_forever, daemon=True)
    t.start()
    for _ in range(100):
        # server.sock / server.port appear at bind(), a moment before listen(): wait for a connect to
        # succeed, or the first test connection can be refused. Then send an empty line — the server
        # drops it without a reply and closes — and wait for that EOF: listen(1) on macOS AF_UNIX
        # refuses a second connection while this one is still queued unaccepted (measured), so the
        # test's first connection must not race the probe
        if b._control_endpoint_exists():
            try:
                probe = b._connect_control_socket()
            except OSError:
                time.sleep(0.05)
                continue
            probe.settimeout(5)
            probe.sendall(b"\n")
            assert probe.recv(1) == b"", "the server should drop an empty line and close"
            probe.close()
            break
        time.sleep(0.05)
    return s, t


def _raw(b, line, wait_reply=True):
    """One raw line to the control endpoint, bypassing the client: the parsed reply, or None when
    the server closed the connection without one. A reset counts as "no reply": when the server
    closes with unread bytes in its receive buffer (a capped or multi-line payload) TCP answers
    the peer with RST, which on a loaded host can arrive before — and discard — the reply."""
    sock = b._connect_control_socket()
    sock.settimeout(10)
    try:
        sock.sendall(line)
    except OSError:
        # the server capped the line and closed while we were still sending: EPIPE / ECONNRESET, or
        # ENOTCONN (errno 57) when macOS tears an AF_UNIX peer down under a blocked sendall
        pass
    data = b""
    if wait_reply:
        try:
            while b"\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (BrokenPipeError, ConnectionResetError):
            data = b""   # the reset discarded whatever the server had queued
    sock.close()
    return json.loads(data) if b"\n" in data else None


@posix_only("binds real control sockets")
@pytest.mark.parametrize("mode", ["unix", "tcp"])
def test_serve_loop_rejects_unauthenticated_and_survives_abuse(mode, short_dir, long_dir, monkeypatch):
    b = _load(short_dir if mode == "unix" else long_dir)
    assert b.USE_UNIX_SOCKET is (mode == "unix")
    monkeypatch.setattr(b, "_CONN_TIMEOUT", 0.5)
    monkeypatch.setattr(b, "_MAX_COMMAND_BYTES", 4096)
    s, t = _run_loop(b, monkeypatch)

    # 1. no token / wrong token / non-object payload: generic refusal, no command executed
    assert _raw(b, b'{"cmd":"requests","args":{}}\n')["error"] == UNAUTH["error"]
    assert _raw(b, json.dumps({"cmd": "requests", "args": {}, "token": "f" * 64}).encode() + b"\n")["error"] == UNAUTH["error"]
    assert _raw(b, b"[1,2]\n")["error"] == UNAUTH["error"]
    assert _raw(b, b"5\n")["error"] == UNAUTH["error"]
    # 2. garbage and HTTP request lines (a web page hitting the port) do not crash the loop — the
    #    bytes after the first newline stay unread, so the close may reach the peer as a reset
    #    (see _raw): either a reply or a reset, and step 5 proves the loop is still serving
    r = _raw(b, b"POST / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert r is None or r["ok"] is False, r
    # 3. oversized line without newline -> capped, generic error (or a reset, same reason)
    r = _raw(b, b"x" * 10000)
    assert r is None or r["error"] == "command too long", r
    # 4. connect-and-hang peer -> recv times out, loop continues
    hang = b._connect_control_socket()
    time.sleep(0.8)
    # 5. legit client works before and after the abuse (attaches the token itself)
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    r = b.ClayBrowserClient().send("requests")
    assert r["ok"] is True and r["count"] == 0
    hang.close()
    # 6. close with the token: files removed, loop ends
    assert b.ClayBrowserClient().send("close")["ok"] is True
    assert s._shutdown_requested is True
    assert not any(os.path.exists(p) for p in b.RUNTIME_FILES)
    assert b.ClayBrowserClient().send("requests")["error"].startswith("Daemon not running")  # pid file gone


@posix_only("binds real control sockets")
@pytest.mark.parametrize("mode", ["unix", "tcp"])
def test_dribbling_peer_is_cut_off_at_the_deadline(mode, short_dir, long_dir, monkeypatch):
    """The bound is a per-connection deadline, not a per-recv idle timeout: a peer sending one byte
    every 0.15 s and never a newline kept every recv() inside the old 5 s idle timeout and held the
    single-threaded loop until the 1 MiB cap; now it is cut off _CONN_TIMEOUT after accept and the
    client queued behind it is served."""
    b = _load(short_dir if mode == "unix" else long_dir)
    monkeypatch.setattr(b, "_CONN_TIMEOUT", 0.5)
    s, t = _run_loop(b, monkeypatch)
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    res = {}
    t0 = time.monotonic()

    def dribble():
        peer = b._connect_control_socket()
        peer.settimeout(0.05)
        outcome = None
        while time.monotonic() - t0 < 4.0:   # far past the 0.5 s deadline; the old code let it run to here
            try:
                peer.sendall(b"x")
                chunk = peer.recv(65536)
            except socket.timeout:
                time.sleep(0.1)               # one byte every ~0.15 s
                continue
            except OSError:
                outcome = "closed"            # reset while sending or reading
                break
            outcome = "closed" if not chunk else chunk
            break
        res["outcome"], res["cut_at"] = outcome, time.monotonic() - t0
        peer.close()

    th = threading.Thread(target=dribble, daemon=True)
    th.start()
    time.sleep(0.2)                           # the dribbler has been accepted and is in the read loop
    t1 = time.monotonic()
    r = b.ClayBrowserClient().send("requests")   # queues behind the dribbler
    served_after = time.monotonic() - t1
    th.join(6)
    assert r["ok"] is True and r["count"] == 0
    assert served_after < 1.5, served_after   # ~0.3 s: the deadline, not the dribbler's 4 s
    assert res["cut_at"] < 2.0, res           # cut off at the deadline, not at the 1 MiB cap
    assert res["outcome"] == "closed" or b"timed out" in res["outcome"], res
    assert b.ClayBrowserClient().send("close")["ok"] is True


@posix_only("binds real control sockets")
@pytest.mark.parametrize("mode", ["unix", "tcp"])
def test_peer_that_never_reads_its_reply_is_cut_off(mode, short_dir, long_dir, monkeypatch):
    """The reply is bounded too (_send_line): an authenticated peer that asks for a reply larger
    than the socket buffers and never reads it holds the loop for at most _CONN_TIMEOUT."""
    b = _load(short_dir if mode == "unix" else long_dir)
    monkeypatch.setattr(b, "_CONN_TIMEOUT", 0.5)
    s, t = _run_loop(b, monkeypatch)
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    with open(b.REQUESTS_PATH, "w") as f:   # a 16 MiB capture: no loopback buffer holds the `requests` reply
        for _ in range(256):
            f.write(json.dumps({"url": "https://api.clay.com/v3/x", "resp_body": "y" * 65536}) + "\n")
    lazy = b._connect_control_socket()
    lazy.sendall(json.dumps({"cmd": "requests", "args": {}, "token": b._read_token()}).encode() + b"\n")
    time.sleep(0.2)                           # ... and never reads the reply
    t1 = time.monotonic()
    r = b.ClayBrowserClient().send("requests", last=1)
    served_after = time.monotonic() - t1
    lazy.close()
    assert r["ok"] is True and r["count"] == 1
    assert served_after < 2.0, served_after   # ~1 s: the reply budget plus the error reply's, not forever
    assert b.ClayBrowserClient().send("close")["ok"] is True


def test_client_reports_alive_daemon_with_missing_token(short_dir):
    b = _load(short_dir)
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    r = b.ClayBrowserClient().send("close")
    assert r["ok"] is False and "server.token is unreadable" in r["error"] and "kill that PID" in r["error"]


@posix_only("needs both transports")
@pytest.mark.parametrize("mode", ["unix", "tcp"])
def test_client_reports_alive_daemon_whose_endpoint_refuses(mode, short_dir, long_dir):
    """The pid gate just held, so a refused connect is not 'Daemon not running' (a stale endpoint, or
    idle peers holding the single-threaded loop)."""
    b = _load(short_dir if mode == "unix" else long_dir)
    b._write_token()
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    if mode == "unix":
        open(b.SOCK_PATH, "w").close()   # a path with no listener behind it
    else:
        # bound but never listen()ed: the kernel refuses every connect to it while it is open, and
        # holding it open keeps another process from taking the port (bind, close and hope the
        # port stays free was a race on a busy host)
        unlistened = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unlistened.bind(("127.0.0.1", 0))
        with open(b.PORT_PATH, "w") as f:
            f.write(str(unlistened.getsockname()[1]))
    try:
        r = b.ClayBrowserClient().send("eval", js="1")
    finally:
        if mode == "tcp":
            unlistened.close()
    assert r["ok"] is False
    assert r["error"].startswith(f"Daemon alive (PID {os.getpid()}) but ") and "is not accepting connections" in r["error"]
    assert "kill the PID and launch again" in r["error"] and "Daemon not running" not in r["error"]


@pytest.mark.parametrize("content", ["", "abc", "0", "-1"])
def test_corrupt_or_nonpositive_pid_file_is_not_alive(short_dir, content):
    """'0' and '-1' matter: os.kill(0, 0) / os.kill(-1, 0) succeed, so they used to read as alive."""
    b = _load(short_dir)
    with open(b.PID_PATH, "w") as f:
        f.write(content)
    assert b._read_pid() is None
    assert b._is_daemon_alive() is False
    assert b.ClayBrowserClient().send("eval", js="1")["error"].startswith("Daemon not running")


def test_client_never_sends_token_to_a_stale_endpoint(long_dir):
    """Stale server.port after an unclean death, port now owned by a stranger, server.pid dead."""
    b = _load(long_dir)
    b._write_token()
    stranger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stranger.bind(("127.0.0.1", 0))
    stranger.listen(1)
    stranger.settimeout(0.5)
    with open(b.PORT_PATH, "w") as f:
        f.write(str(stranger.getsockname()[1]))
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    with open(b.PID_PATH, "w") as f:
        f.write(str(dead.pid))
    r = b.ClayBrowserClient().send("eval", js="1")
    assert r == {"ok": False, "error": "Daemon not running. Run: python clay_browser.py launch"}
    with pytest.raises(socket.timeout):
        stranger.accept()  # nothing ever connected, so nothing could have read the token
    stranger.close()


def test_client_attaches_token_when_daemon_alive(long_dir):
    b = _load(long_dir)
    tok = b._write_token()
    fake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    fake.bind(("127.0.0.1", 0))
    fake.listen(1)
    with open(b.PORT_PATH, "w") as f:
        f.write(str(fake.getsockname()[1]))
    with open(b.PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    got = {}

    def serve():
        conn, _ = fake.accept()
        got["line"] = conn.makefile("rb").readline()
        conn.sendall(b'{"ok": true}\n')
        conn.close()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    assert b.ClayBrowserClient().send("goto", url="u") == {"ok": True}
    th.join(2)
    fake.close()
    assert json.loads(got["line"]) == {"cmd": "goto", "args": {"url": "u"}, "token": tok}


def test_cleanup_stale_removes_token(short_dir):
    b = _load(short_dir)
    b._write_token()
    for p in (b.PORT_PATH, b.LOG_PATH, b.REQUESTS_PATH):
        open(p, "w").close()
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    with open(b.PID_PATH, "w") as f:
        f.write(str(dead.pid))
    b._cleanup_stale()
    assert not any(os.path.exists(p) for p in b.RUNTIME_FILES)


def test_acl_warning_only_on_windows_outside_profile_roots(short_dir, monkeypatch):
    b = _load(short_dir)
    for v in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(v, raising=False)
    # fictional profile roots; the containment logic is path-shape only, nothing is touched on disk
    monkeypatch.setenv("USERPROFILE", "/prof/x")
    assert b._acl_warning() is None                      # not Windows -> never
    monkeypatch.setattr(b, "IS_WINDOWS", True)
    monkeypatch.setattr(b, "RUNTIME_DIR", "/prof/x/AppData/Local/Temp/clay-browser")
    assert b._acl_warning() is None                      # default %TEMP% layout -> quiet
    monkeypatch.setattr(b, "RUNTIME_DIR", "/prof/x")
    assert b._acl_warning() is None                      # the profile root itself counts as inside
    monkeypatch.setattr(b, "RUNTIME_DIR", "/prof/xy/tmp")  # prefix trick: /prof/xy is NOT inside /prof/x
    assert "outside your user profile" in b._acl_warning()
    monkeypatch.setattr(b, "RUNTIME_DIR", "/Temp/clay-browser")
    assert "NTFS" in b._acl_warning() and "chmod is a no-op" in b._acl_warning()
    # a redirected %LOCALAPPDATA% (corporate laptops) is a root too: following the advice stays quiet
    monkeypatch.setenv("LOCALAPPDATA", "/Volumes/D/LocalAppData")
    monkeypatch.setattr(b, "RUNTIME_DIR", "/Volumes/D/LocalAppData/Temp/clay-browser")
    assert b._acl_warning() is None
    monkeypatch.setattr(b, "RUNTIME_DIR", "/Volumes/D/Other")
    assert "%LOCALAPPDATA%" in b._acl_warning() and "%USERPROFILE%" in b._acl_warning()
    for v in ("LOCALAPPDATA", "USERPROFILE"):
        monkeypatch.delenv(v)
    assert b._acl_warning() is None                      # no root known -> cannot judge, stay quiet


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names exist only on Windows")
def test_acl_warning_expands_8dot3_short_names(tmp_path, monkeypatch):
    """Windows hands many processes their %TEMP% in 8.3 form (the user segment spelled like
    USERNA~1) while %LOCALAPPDATA% / %APPDATA% / %USERPROFILE% carry the long name, so a plain
    prefix test on the two spellings says the DEFAULT runtime dir is outside the profile when it
    is the profile's own Temp. Measured 2026-09-30 on a Windows 11 laptop: every launch printed
    the WARNING under an untouched %TEMP%. Both sides have to be realpath()ed, which expands
    short names on Windows; the test builds a real directory and asks the kernel for its 8.3 form.
    """
    import ctypes
    from ctypes import wintypes

    long_root = tmp_path / "ProfileWithALongName"
    long_root.mkdir()
    long_root = os.path.realpath(str(long_root))          # fully long-named spelling
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    k32.GetShortPathNameW.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(1024)
    assert k32.GetShortPathNameW(long_root, buf, 1024), ctypes.get_last_error()
    short_root = buf.value                                # fully short-named spelling
    if os.path.normcase(short_root) == os.path.normcase(long_root):
        pytest.skip("8.3 name generation is disabled on this volume")

    b = _load(str(tmp_path))
    monkeypatch.setattr(b, "IS_WINDOWS", True)
    for v in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("USERPROFILE", long_root)
    monkeypatch.setattr(b, "RUNTIME_DIR", os.path.join(short_root, "AppData", "Local", "Temp", "clay-browser"))
    assert b._acl_warning() is None                      # same directory, spelled short: inside
    monkeypatch.setenv("USERPROFILE", short_root)        # and the other way round
    monkeypatch.setattr(b, "RUNTIME_DIR", os.path.join(long_root, "AppData", "Local", "Temp", "clay-browser"))
    assert b._acl_warning() is None
    monkeypatch.setattr(b, "RUNTIME_DIR", os.path.join(os.path.dirname(long_root), "Elsewhere"))
    assert "outside your user profile" in b._acl_warning()   # a sibling dir is still outside


def _start_without_playwright(b, monkeypatch):
    s = b.ClayBrowserServer()
    for name in ("_setup_browser", "_setup_capture", "_serve_forever"):
        monkeypatch.setattr(s, name, lambda *a, **k: None)
    s.start(headless=True)
    return s


@posix_only("drives start() without Playwright")
def test_start_prints_token_marker_then_the_same_lines_launch_prints(long_dir, monkeypatch, capsys):
    """start() logs the transport NOTE / ACL WARNING that launch prints (so daemon.log carries them
    too), after the 'control token written' marker the e2e harness orders against 'listening on'.
    The two lines are exclusive by design — the NOTE only on a POSIX host with a long path, the
    WARNING only on Windows — so each gets its own start()."""
    b = _load(long_dir)
    s = _start_without_playwright(b, monkeypatch)   # POSIX, long path -> NOTE
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[clay-browser] control token written"
    assert lines[1].startswith("[clay-browser] NOTE:") and "AF_UNIX limit" in lines[1] and len(lines) == 2
    assert s._token == b._read_token() and oct(os.stat(b.TOKEN_PATH).st_mode & 0o777) == "0o600"
    assert open(b.PID_PATH).read() == str(os.getpid())

    monkeypatch.setattr(b, "IS_WINDOWS", True)   # Windows, runtime dir outside every profile root -> WARNING
    for v in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("USERPROFILE", os.path.join(long_dir, "not-the-runtime-dir"))
    _start_without_playwright(b, monkeypatch)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[clay-browser] control token written"
    assert lines[1].startswith("[clay-browser] WARNING:") and "NTFS" in lines[1] and len(lines) == 2
