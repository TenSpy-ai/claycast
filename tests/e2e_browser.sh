#!/bin/bash
# Live end-to-end harness for scripts/clay_browser.py -- macOS only (it uses `stat -f %Lp` and
# `lsof`). Not run by pytest. Launches a real headless Chromium through the daemon, checks that the
# control channel refuses anything without the token in server.token, drives the browser
# (goto / eval / press / screenshot) and checks that `close` tears the whole process tree down and
# removes the runtime files.
#
#   tests/e2e_browser.sh unix      # default POSIX path: UNIX control socket under /tmp/clay-browser
#   tests/e2e_browser.sh longpath  # CLAY_BROWSER_DIR long enough that server.sock exceeds sun_path:
#                                  # the daemon must take the loopback-TCP fallback natively and
#                                  # `launch` must print the NOTE (no shim involved)
#   tests/e2e_browser.sh tcp       # the Windows layout on this machine: a temp copy of clay_browser.py
#                                  # with USE_UNIX_SOCKET forced to False on a SHORT dir (loopback TCP
#                                  # + server.port, and no NOTE because the path fits)
#
# Needs Playwright with Chromium for $PYTHON (default: python3; `playwright install chromium`)
# and a CLAY_SESSION value that resolves: export CLAY_SESSION, or keep a .env at the repo root
# (this script cd's there; the loader stops at the nearest directory with a .git entry, and a
# git worktree's .git *file* counts). It does not have to be a working cookie: the only
# page opened is a local data: URL, so no request to Clay has to succeed. Spends no credits.
# Aborts if a daemon is already running in the runtime dir it would use.
set -u
PY=${PYTHON:-python3}
MODE=${1:-unix}
REPO=$(cd "$(dirname "$0")/.." && pwd)
CB="$REPO/scripts/clay_browser.py"
TMP=""
case "$MODE" in
  unix)
    unset CLAY_BROWSER_DIR
    ;;
  longpath)
    TMP=$(mktemp -d)
    export CLAY_BROWSER_DIR="$TMP/$(printf 'l%.0s' $(seq 1 170))/cb"   # ~230 bytes: server.sock is far over sun_path
    ;;
  tcp)
    TMP=$(mktemp -d)
    sed 's/^USE_UNIX_SOCKET = .*/USE_UNIX_SOCKET = False  # TEST: force the Windows TCP path/' "$CB" > "$TMP/clay_browser.py" \
      && cp "$REPO/scripts/clay_client.py" "$TMP/"
    grep -q '^USE_UNIX_SOCKET = False' "$TMP/clay_browser.py" || { echo "tcp shim: no USE_UNIX_SOCKET line found in $CB"; exit 1; }
    CB="$TMP/clay_browser.py"
    export CLAY_BROWSER_DIR="$TMP/clay-browser"
    ;;
  *)
    echo "usage: $0 [unix|longpath|tcp]"; exit 2
    ;;
esac
RUNDIR=${CLAY_BROWSER_DIR:-/tmp/clay-browser}
[ -e "$RUNDIR/server.pid" ] && { echo "daemon already running in $RUNDIR, aborting"; exit 1; }; rm -rf "$RUNDIR"
cd "$REPO"   # the cookie loader walks up from the current directory
cb() { $PY "$CB" "$@"; }
pass=0; fail=0
check() { if eval "$2"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
# one raw line to the control endpoint, bypassing the client: no token unless the line carries one
raw() { $PY - "$RUNDIR" "$1" <<'EOF'
import os, socket, sys
rundir, line = sys.argv[1], sys.argv[2]
sock_path = os.path.join(rundir, "server.sock")
if os.path.exists(sock_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(sock_path)
else:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", int(open(os.path.join(rundir, "server.port")).read())))
s.settimeout(10); s.sendall(line.encode() + b"\n")
data = b""
while b"\n" not in data:
    c = s.recv(65536)
    if not c: break
    data += c
print(data.decode().strip())
EOF
}

echo "== mode: $MODE  (dir $RUNDIR, $(printf %s "$RUNDIR/server.sock" | wc -c | tr -d ' ') bytes for server.sock)"
$PY -c "import sys; sys.path.insert(0, '$(dirname "$CB")'); import clay_browser as b; print('  USE_UNIX_SOCKET =', b.USE_UNIX_SOCKET)"
LAUNCH=$(cb launch --headless 2>&1); echo "$LAUNCH" | sed 's/^/  /'
DPID=$(cat "$RUNDIR/server.pid" 2>/dev/null)
check "daemon pid file" '[ -n "$DPID" ] && kill -0 $DPID'
check "server.token present, 0600, 64 hex" '[ -s "$RUNDIR/server.token" ] && [ "$(stat -f %Lp "$RUNDIR/server.token")" = 600 ] && grep -qE "^[0-9a-f]{64}$" "$RUNDIR/server.token"'
TL=$(grep -n "control token written" "$RUNDIR/daemon.log" | head -1 | cut -d: -f1); LL=$(grep -n "listening on" "$RUNDIR/daemon.log" | head -1 | cut -d: -f1)
check "token written before endpoint advertised (log line $TL < $LL)" '[ -n "$TL" ] && [ -n "$LL" ] && [ "$TL" -lt "$LL" ]'
if [ "$MODE" = longpath ]; then
  check "launch printed the AF_UNIX fallback NOTE" 'echo "$LAUNCH" | grep -q "over this platform.s AF_UNIX limit"'
else
  check "no AF_UNIX fallback NOTE (the path fits)" '! echo "$LAUNCH" | grep -q "AF_UNIX limit"'
fi
if [ "$MODE" != unix ]; then
  check "server.port written, no server.sock" '[ -s "$RUNDIR/server.port" ] && [ ! -e "$RUNDIR/server.sock" ]'
  check "server.port is 0600, no server.port.tmp left" '[ "$(stat -f %Lp "$RUNDIR/server.port")" = 600 ] && [ ! -e "$RUNDIR/server.port.tmp" ]'
  check "daemon listens on 127.0.0.1 only" 'lsof -nP -a -p $DPID -iTCP -sTCP:LISTEN | grep -q "127.0.0.1:$(cat $RUNDIR/server.port)"'
else
  check "server.sock present, no server.port" '[ -S "$RUNDIR/server.sock" ] && [ ! -e "$RUNDIR/server.port" ]'
fi

# -- the endpoint refuses anything without the token, and stays healthy --
OUT=$(raw '{"cmd":"eval","args":{"js":"1+1"}}'); echo "  raw no-token -> $OUT"
check "no token -> unauthorized, JS not run" 'echo "$OUT" | grep -q "unauthorized" && ! echo "$OUT" | grep -q "\"result\""'
OUT=$(raw "{\"cmd\":\"eval\",\"args\":{\"js\":\"1+1\"},\"token\":\"$(printf 'f%.0s' $(seq 1 64))\"}")
check "wrong token -> unauthorized" 'echo "$OUT" | grep -q "unauthorized"'
OUT=$(raw '{"cmd":"snapshot","args":{}}')
check "snapshot without token -> unauthorized (no command enumeration)" 'echo "$OUT" | grep -q "unauthorized"'
OUT=$(raw 'POST / HTTP/1.1'); echo "  raw http-line -> $(echo $OUT | cut -c1-80)"
check "HTTP request line -> ok:false reply, daemon alive" 'echo "$OUT" | grep -q "\"ok\": false" && kill -0 $DPID'
OUT=$(raw "{\"cmd\":\"eval\",\"args\":{\"js\":\"1+1\"},\"token\":\"$(cat $RUNDIR/server.token)\"}")
check "correct token via raw socket -> result 2" 'echo "$OUT" | grep -q "\"result\": 2"'
check "real client attaches the token itself: eval 2+2 -> 4" 'cb eval "2+2" | grep -q "\"result\": 4"'

PAGE='data:text/html,<title>start</title><input id=q autofocus><script>q.addEventListener("keydown",e=>{document.title="key:"+e.key+(e.isTrusted?":trusted":":synthetic")})</script>'
cb goto "$PAGE" | grep -q '"ok": true' ; check "goto local page" '[ $? -eq 0 ]'
cb eval 'document.getElementById("q").focus()' >/dev/null
OUT=$(cb press Enter); echo "  press -> $(echo $OUT | tr -d '\n' | cut -c1-80)"
check "press returns ok" 'echo "$OUT" | grep -q "\"pressed\": \"Enter\""'
T=$(cb eval 'document.title')
check "real (trusted) Enter reached the page" 'echo "$T" | grep -q "key:Enter:trusted"'
OUT=$(cb press "" 2>&1); check "press with empty key rejected" 'echo "$OUT" | grep -q "key required"'
OUT=$(cb press NotARealKey 2>&1); echo "  press NotARealKey -> $(echo $OUT | tr -d '\n' | cut -c1-100)"
check "daemon survives a bad key" 'cb eval "1+1" | grep -q 2'
cb screenshot >/dev/null
check "screenshot in runtime dir" '[ -s "$RUNDIR/screenshot.png" ]'

desc() { for c in $(pgrep -P $1); do echo $c; desc $c; done; }
KIDS=$(desc $DPID | tr '\n' ' ')
echo "  daemon descendants before close: $(echo $KIDS | wc -w | tr -d ' ') ($(ps -o comm= -p $(echo $KIDS | tr ' ' ',') | xargs -n1 basename | sort | uniq -c | tr -s ' ' | tr '\n' ';'))"
t0=$($PY -c 'import time;print(time.time())')
cb close | sed 's/^/  /'
for i in $(seq 1 60); do kill -0 $DPID 2>/dev/null || break; sleep 0.25; done
t1=$($PY -c 'import time;print(time.time())')
secs=$($PY -c "print(round($t1-$t0,1))")
echo "  daemon exit after close: ${secs}s"
check "daemon exited" '! kill -0 $DPID 2>/dev/null'
check "exit well before the 10s watchdog" '$PY -c "import sys; sys.exit(0 if $secs < 8 else 1)"'
sleep 1
LEFT=""; for p in $KIDS; do kill -0 $p 2>/dev/null && LEFT="$LEFT $p"; done
check "no leftover driver/browser processes" '[ -z "$LEFT" ]'
[ -n "$LEFT" ] && ps -o pid,command -p $(echo $LEFT | tr ' ' ',') | cut -c1-120
REM=$(ls -A "$RUNDIR" 2>/dev/null | grep -v screenshot.png | tr '\n' ' ')
check "runtime files removed incl. server.token (screenshot aside)" '[ -z "$REM" ]'
OUT=$(cb eval 1 2>&1); check "client says not running after close" 'echo "$OUT" | grep -q "Daemon not running"'
[ -n "$TMP" ] && rm -rf "$TMP"
echo "== $MODE: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
