#!/usr/bin/env bash
# emz5/fetch-jsshell.sh — obtain a prebuilt SpiderMonkey js shell (jsshell) from
# public Mozilla infrastructure. Read-only fetches, no auth, no secrets.
#
# Probes candidate sources in order (recording PROBE lines for evidence),
# downloads the first working zip, extracts it under <target-dir>, verifies
# the binary exists, and writes <target-dir>/js-path.txt.
#
# usage: fetch-jsshell.sh <target-dir> [explicit-url]
# exit: 0 when a working shell was obtained; 1 otherwise.
set -u

DIR="${1:-jsshell}"
EXPLICIT="${2:-}"
UA="bb-lane-emz5/0.1"
mkdir -p "$DIR"
rm -f "$DIR/js-path.txt"

if ! command -v unzip >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq unzip
fi

NAMES=(); URLS=()
add() { NAMES+=("$1"); URLS+=("$2"); }

[ -n "$EXPLICIT" ] && add explicit "$EXPLICIT"
add archive-latest-plain   "https://archive.mozilla.org/pub/firefox/nightly/latest-mozilla-central/jsshell-linux-x86_64.zip"
add archive-latest-fuzzing "https://archive.mozilla.org/pub/firefox/nightly/latest-mozilla-central/fuzzing/jsshell-linux-x86_64.zip"
add archive-latest-fuzzing-target "https://archive.mozilla.org/pub/firefox/nightly/latest-mozilla-central/fuzzing/target.jsshell.zip"

TC="https://firefox-ci-tc.services.mozilla.com"
for ROUTE in linux64-fuzzing-asan-opt linux64-fuzzing-opt; do
  IDX="$TC/api/index/v1/task/gecko.v2.mozilla-central.latest.firefox.$ROUTE"
  TASKID=$(curl -fsS --max-time 60 -A "$UA" "$IDX" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null || true)
  if [ -n "$TASKID" ]; then
    echo "INDEX $ROUTE taskId=$TASKID"
    add "tc-$ROUTE" "$TC/api/queue/v1/task/$TASKID/artifacts/public/build/target.jsshell.zip"
  else
    echo "INDEX $ROUTE unresolved"
  fi
done

probe_size() { # url -> prints "<code> <bytes>"
  local url="$1" code clen cr
  code=$(curl -sS -o /dev/null -w '%{http_code}' -I -L --max-time 60 -A "$UA" "$url" 2>/dev/null || echo 000)
  clen=$(curl -sS -I -L --max-time 60 -A "$UA" "$url" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')
  if [ "$code" != "200" ] && [ "$code" != "206" ] && [ "${clen:-0}" -lt 100 ]; then
    code=$(curl -sS -o /dev/null -w '%{http_code}' -L -r 0-0 --max-time 60 -A "$UA" "$url" 2>/dev/null || echo 000)
  fi
  if [ "${clen:-0}" -lt 100 ]; then
    cr=$(curl -sS -o /dev/null -w '%{header_json}' -L -r 0-0 --max-time 60 -A "$UA" "$url" 2>/dev/null \
      | python3 -c "import sys,json;h=json.load(sys.stdin);print((h.get('content-range') or [''])[0].split('/')[-1])" 2>/dev/null || echo 0)
    case "$cr" in ''|*[!0-9]*) cr=0;; esac
    clen=$cr
  fi
  echo "$code ${clen:-0}"
}

CHOSEN=""; CHOSEN_NAME=""
i=0
while [ $i -lt ${#NAMES[@]} ]; do
  NAME="${NAMES[$i]}"; URL="${URLS[$i]}"; i=$((i+1))
  read -r CODE CLEN <<EOF
$(probe_size "$URL")
EOF
  echo "PROBE $NAME status=$CODE bytes=$CLEN url=$URL"
  if [ -z "$CHOSEN" ] && [ "$CODE" = "200" ] && [ "$CLEN" -ge 5000000 ]; then
    CHOSEN="$URL"; CHOSEN_NAME="$NAME"
  fi
done

if [ -z "$CHOSEN" ]; then
  echo "STATUS no-prebuilt-source-worked"
  exit 1
fi

echo "DOWNLOADING $CHOSEN_NAME $CHOSEN"
curl -fsSL --retry 3 --retry-delay 3 --max-time 900 -A "$UA" -o "$DIR/jsshell.zip" "$CHOSEN" \
  || { echo "STATUS download-failed"; exit 1; }

python3 - "$DIR/jsshell.zip" <<'PY' || { echo "STATUS zip-corrupt"; exit 1; }
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
sys.exit(1 if z.testzip() is not None else 0)
PY

unzip -o -q "$DIR/jsshell.zip" -d "$DIR" || { echo "STATUS unzip-failed"; exit 1; }
JS=$(find "$DIR" -type f -name js | head -1)
if [ -z "$JS" ]; then echo "STATUS no-js-binary"; exit 1; fi
chmod +x "$JS"
DSIZE=$(stat -c%s "$JS")
echo "STATUS prebuilt-ok name=$CHOSEN_NAME bin=$JS size=$DSIZE"
{
  echo "$JS"
  sha256sum "$DIR/jsshell.zip"
} > "$DIR/js-path.txt"
exit 0
