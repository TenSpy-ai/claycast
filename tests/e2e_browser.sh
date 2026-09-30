#!/bin/bash
# Live end-to-end harness for scripts/clay_browser.py -- macOS only (it uses `stat -f %Lp` and
# `lsof`). Not run by pytest. Launches a real headless Chromium through the daemon, drives it
# (goto / eval / press / screenshot) and checks that `close` tears the whole process tree down
# and removes the runtime files.
#
#   tests/e2e_browser.sh unix   # default POSIX path: UNIX control socket under /tmp/clay-browser
#   tests/e2e_browser.sh tcp    # the Windows path on this machine: a temp copy of clay_browser.py
#                               # with USE_UNIX_SOCKET forced to False (loopback TCP + server.port)
#
# Needs Playwright with Chromium for $PYTHON (default: python3; `playwright install chromium`)
# and a CLAY_SESSION value that RESOLVES (env var, or a .env on the walk-up path from the repo
# root, which is where this script runs from). It does not have to be a working cookie: the only
# page opened is a local data: URL, so no request to Clay has to succeed. Spends no credits.
# Aborts if a daemon is already running in the runtime dir it would use.
set -u
PY=${PYTHON:-python3}
MODE=${1:-unix}
REPO=$(cd "$(dirname "$0")/.." && pwd)
CB="$REPO/scripts/clay_browser.py"
case "$MODE" in
  unix)
    unset CLAY_BROWSER_DIR
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
    echo "usage: $0 [unix|tcp]"; exit 2
    ;;
esac
RUNDIR=${CLAY_BROWSER_DIR:-/tmp/clay-browser}
[ -e "$RUNDIR/server.pid" ] && { echo "daemon already running in $RUNDIR, aborting"; exit 1; }; rm -rf "$RUNDIR"
cd "$REPO"   # the cookie loader walks up from the current directory
cb() { $PY "$CB" "$@"; }
pass=0; fail=0
check() { if eval "$2"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }

echo "== mode: $MODE  (dir $RUNDIR)"
$PY -c "import sys; sys.path.insert(0, '$(dirname "$CB")'); import clay_browser as b; print('  USE_UNIX_SOCKET =', b.USE_UNIX_SOCKET)"
cb launch --headless 2>&1 | sed 's/^/  /'
DPID=$(cat "$RUNDIR/server.pid" 2>/dev/null)
check "daemon pid file" '[ -n "$DPID" ] && kill -0 $DPID'
if [ "$MODE" = tcp ]; then
  check "server.port written, no server.sock" '[ -s "$RUNDIR/server.port" ] && [ ! -e "$RUNDIR/server.sock" ]'
  check "server.port is 0600" '[ "$(stat -f %Lp "$RUNDIR/server.port")" = 600 ]'
  check "daemon listens on 127.0.0.1 only" 'lsof -nP -a -p $DPID -iTCP -sTCP:LISTEN | grep -q "127.0.0.1:$(cat $RUNDIR/server.port)"'
else
  check "server.sock present" '[ -S "$RUNDIR/server.sock" ]'
fi

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
check "runtime files removed (screenshot aside)" '[ -z "$REM" ]'
OUT=$(cb eval 1 2>&1); check "client says not running after close" 'echo "$OUT" | grep -q "Daemon not running"'
echo "== $MODE: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
