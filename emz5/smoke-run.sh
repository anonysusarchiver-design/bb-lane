#!/usr/bin/env bash
# emz5/smoke-run.sh — run "js --version" + emz5/smoke.js under a fetched/built
# jsshell, writing evidence files:
#   emz5-<label>-version.txt        (version + binary info)
#   emz5-<label>-smoke-output.txt   (raw smoke output)
# usage: smoke-run.sh <jsshell-dir> <label>
set -u

DIR="${1:?jsshell dir}"
LABEL="${2:?label}"

JS=""
[ -f "$DIR/js-path.txt" ] && JS=$(head -1 "$DIR/js-path.txt")
case "$JS" in /*) ;; "") ;; *) JS="$(pwd)/$JS" ;; esac
if [ ! -x "$JS" ] && [ -x "$DIR/js" ]; then JS="$(pwd)/$DIR/js"; fi
if [ ! -x "$JS" ]; then
  echo "SMOKE-ERROR: no executable js binary under $DIR"
  exit 2
fi

{
  echo "== js --version (label=$LABEL bin=$JS) =="
  timeout 30 "$JS" --version < /dev/null 2>&1 || timeout 30 "$JS" -v < /dev/null 2>&1 || echo "(version query rc=$?)"
  echo "== binary info =="
  file "$JS" || true
  if ldd "$JS" 2>/dev/null | grep -i "not found"; then
    echo "LD-MISSING-LIBS"
  else
    echo "ldd ok"
  fi
} | tee "emz5-${LABEL}-version.txt"

timeout 300 "$JS" emz5/smoke.js > "emz5-${LABEL}-smoke-output.txt" 2>&1
RC=$?
echo "smoke_rc=$RC" | tee -a "emz5-${LABEL}-smoke-output.txt"
tail -5 "emz5-${LABEL}-smoke-output.txt"

if [ $RC -ne 0 ]; then exit $RC; fi
if ! grep -q "EMZ5-SMOKE OK" "emz5-${LABEL}-smoke-output.txt"; then
  echo "SMOKE-ERROR: success marker missing"
  exit 3
fi
echo "SMOKE-PASS label=$LABEL"
