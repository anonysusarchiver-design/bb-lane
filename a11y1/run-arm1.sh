#!/usr/bin/env bash
# a11y1/run-arm1.sh — ARM 1: 5 consecutive mochitest-plain runs of the forged
# repro against the UNGUARDED build. Expectation: parent-process SEGV, 5/5.
# Each run is classified; parent cores get a gdb backtrace.
#
# Classification per run (out/arm1/run-N*.txt):
#   crash=yes  <=> "application terminated with exit code" in the log
#               OR a core dump exists (any process)
#   frame=yes  <=> a gdb backtrace contains DocAccessibleParent::ProcessHideEvent
# test_discovered=no <=> mochitest filtered the testcase ("is not a valid test" /
#               "running 0 tests") — a packaging failure, NOT a crash outcome
# result file: out/arm1/result.txt ("crashes=N/5", "frames=N/5", "test_discovered=...")
set -uo pipefail

WORK="$(pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
OBJDIR_NAME="obj-a11y1"
BIN="$WORK/mc/$OBJDIR_NAME/dist/bin/firefox"
TEST="testing/mochitest/tests/Harness_sanity/test_a11y1_forge.html"
OUT="$WORK/out/arm1"
mkdir -p "$OUT"

export MOZCONFIG="$HOME/mozconfigs/a11y1"
export MOZBUILD_STATE_PATH="$HOME/.mozbuild"

test -x "$BIN" || { echo "ARM1-ERROR: binary missing: $BIN"; exit 3; }

echo "== core capture setup =="
sudo mkdir -p /tmp/cores
sudo chmod 777 /tmp/cores
echo '/tmp/cores/core.%p' | sudo tee /proc/sys/kernel/core_pattern >/dev/null
ulimit -c unlimited || true
echo "core_pattern: $(cat /proc/sys/kernel/core_pattern)"
echo "ulimit -c: $(ulimit -c)"

crashes=0
frames=0
runs=5
discovered="yes"
for i in $(seq 1 $runs); do
  echo "================ ARM1 RUN $i of $runs ================"
  rm -f /tmp/cores/core.* 2>/dev/null || true
  LOG="$OUT/run-$i.log"
  cd "$WORK/mc"
  xvfb-run -a -s "-screen 0 1280x1024x24" ./mach mochitest \
    --setpref accessibility.force_disabled=-1 \
    --setpref security.sandbox.content.level=0 \
    "$TEST" > "$LOG" 2>&1
  rc=$?
  cd "$WORK"
  echo "run $i: mochitest rc=$rc"

  crash="no"
  frame="no"
  if grep -a -q "application terminated with exit code" "$LOG"; then crash="yes"; fi

  ncores=0
  for CORE in $(ls /tmp/cores/core.* 2>/dev/null); do
    ncores=$((ncores+1))
    BTF="$OUT/run-$i-bt-$(basename "$CORE").txt"
    echo "--- core: $CORE ---" > "$BTF"
    file "$CORE" >> "$BTF" 2>&1 || true
    gdb -batch -ex "set pagination off" -ex "bt 60" "$BIN" "$CORE" >> "$BTF" 2>&1 || true
    if grep -q "ProcessHideEvent" "$BTF"; then frame="yes"; crash="yes"; fi
  done
  if [ "$ncores" -gt 0 ]; then crash="yes"; fi

  filtered="no"
  if grep -a -q "is not a valid test" "$LOG" || grep -a -q "running 0 tests" "$LOG"; then
    filtered="yes"
    discovered="no"
    echo "run $i: WARNING: mochitest filtered the testcase (0 tests executed) - packaging problem, NOT a crash outcome"
  fi

  if grep -a -q "TEST-PASS" "$LOG" && grep -a -q "parent process survived" "$LOG"; then
    echo "run $i: test reached the survived-assertion (no crash)"
  fi

  echo "run $i: cores=$ncores crash=$crash frame=$frame filtered=$filtered"
  [ "$crash" = "yes" ] && crashes=$((crashes+1))
  [ "$frame" = "yes" ] && frames=$((frames+1))
  echo "run $i tail:"
  tail -n 15 "$LOG" | sed 's/^/  tail| /'
done

echo "ARM1 RESULT: crashes=$crashes/$runs frames=$frames/$runs test_discovered=$discovered"
{
  echo "crashes=$crashes/$runs"
  echo "frames=$frames/$runs"
  echo "test_discovered=$discovered"
} > "$OUT/result.txt"
cat "$OUT/result.txt"
sudo dmesg 2>/dev/null | tail -n 50 > "$OUT/dmesg.txt" || true
ls -la "$OUT"
exit 0
