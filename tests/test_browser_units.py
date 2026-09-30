"""clay_browser.py helpers, POSIX side. The Windows-only branches (ctypes OpenProcess liveness,
taskkill, DETACHED_PROCESS) cannot execute here; the tests that bind a UNIX socket, assert the
/tmp defaults or send POSIX signals skip where AF_UNIX is missing or on Windows."""
import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import SCRIPTS

PY = sys.executable
SCRIPTS_DIR = str(SCRIPTS)


def posix_only(reason):
    return pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason=reason)


def _probe(code, env=None):
    full = f"import sys; sys.path.insert(0, {SCRIPTS_DIR!r}); import clay_browser as b\n" + textwrap.dedent(code)
    e = dict(os.environ, **(env or {}))
    return subprocess.run([PY, "-c", full], capture_output=True, text=True, env=e, timeout=60)


@posix_only("asserts the POSIX defaults: /tmp/clay-browser and the UNIX control socket")
def test_posix_default_paths():
    code = "print(b.RUNTIME_DIR, b.SOCK_PATH, b.PID_PATH, b.REQUESTS_PATH, b.LOG_PATH)"
    out = _probe(code, {"CLAY_BROWSER_DIR": ""})  # an empty override falls back to the default
    assert out.stdout.split() == ["/tmp/clay-browser", "/tmp/clay-browser/server.sock", "/tmp/clay-browser/server.pid",
                                  "/tmp/clay-browser/requests.jsonl", "/tmp/clay-browser/daemon.log"], out.stderr
    assert _probe("print(b.USE_UNIX_SOCKET, b.IS_WINDOWS)").stdout.split() == ["True", "False"]


def test_runtime_dir_override(tmp_path):
    out = _probe("print(b.RUNTIME_DIR); print(b.PORT_PATH); print(b.SCREENSHOT_PATH)", {"CLAY_BROWSER_DIR": str(tmp_path)})
    # one path per line: a temp dir may contain spaces (Windows user names), so no split()
    assert out.stdout.splitlines() == [str(tmp_path), str(tmp_path / "server.port"), str(tmp_path / "screenshot.png")]


def test_runtime_files_include_port_file():
    out = _probe("print(b.PORT_PATH in b.RUNTIME_FILES, len(b.RUNTIME_FILES))")
    assert out.stdout.split() == ["True", "5"]


@posix_only("POSIX liveness probe: os.kill(pid, 0)")
def test_pid_alive_posix():
    child = subprocess.Popen([PY, "-c", "pass"])
    child.wait()
    out = _probe(f"import os; print(b._pid_alive(os.getpid()), b._pid_alive({child.pid}))")
    assert out.stdout.split() == ["True", "False"], out.stderr


def test_client_reports_not_running_when_no_daemon(tmp_path):
    # runs on every platform: both transports raise before any socket is bound, and send()
    # turns FileNotFoundError / ConnectionRefusedError / OSError into the same message
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
