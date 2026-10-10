#!/usr/bin/env bash
# a11y1/run-arm2.sh — ARM 2: apply the fix-probe guard to the SAME tree, do an
# incremental rebuild, and run the forged repro once. Expectation: the guard
# rejects the forged second hide via IPC_FAIL; the parent process survives
# (no parent SEGV core, browser exits normally).
#
# Distinguishing signals (out/arm2/run-1.log):
#   guard marker : "hide for detached accessible"  (IPC_FAIL reason)
#   parent crash : core backtrace contains DocAccessibleParent::ProcessHideEvent
#   clean exit   : "Main app process exited normally"
# result file: out/arm2/result.txt
set -uo pipefail

WORK="$(pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
OBJDIR_NAME="obj-a11y1"
BIN="$WORK/mc/$OBJDIR_NAME/dist/bin/firefox"
TEST="testing/mochitest/tests/Harness_sanity/a11y1_forge.html"
OUT="$WORK/out/arm2"
mkdir -p "$OUT"

export MOZCONFIG="$HOME/mozconfigs/a11y1"
export MOZBUILD_STATE_PATH="$HOME/.mozbuild"

test -x "$BIN" || { echo "ARM2-ERROR: binary missing: $BIN"; exit 3; }

echo "== apply fix probe =="
git -C mc apply --verbose "$HERE/a11y1-fix.patch" || { echo "ARM2-ERROR: fix patch failed to apply"; exit 4; }
grep -n "A11Y-1 FIX-PROBE" mc/accessible/ipc/DocAccessibleParent.cpp
cp "$HERE/a11y1-fix.patch" "$WORK/" 2>/dev/null || true

echo "== incremental rebuild =="
date -u
cd "$WORK/mc"
./mach build > "$OUT/rebuild.log" 2>&1
build_rc=$?
cd "$WORK"
date -u
tail -n 60 "$OUT/rebuild.log"
if [ "$build_rc" -ne 0 ]; then
  echo "ARM2 BUILD-FAILED rc=$build_rc (full log: out/arm2/rebuild.log)"
  exit 4
fi
ls -l "$BIN"
sha256sum "$BIN" | tee "$OUT/binary-sha256.txt"

echo "== core capture setup =="
sudo mkdir -p /tmp/cores
sudo chmod 777 /tmp/cores
echo '/tmp/cores/core.%p' | sudo tee /proc/sys/kernel/core_pattern >/dev/null
ulimit -c unlimited || true
rm -f /tmp/cores/core.* 2>/dev/null || true

echo "================ ARM2 RUN (guarded build) ================"
LOG="$OUT/run-1.log"
cd "$WORK/mc"
xvfb-run -a -s "-screen 0 1280x1024x24" ./mach mochitest \
  --setpref accessibility.force_disabled=-1 \
  --setpref security.sandbox.content.level=0 \
  "$TEST" > "$LOG" 2>&1
rc=$?
cd "$WORK"
echo "arm2 run rc=$rc"

guard="no"
parent_crash="no"
clean_exit="no"
test_pass="no"

grep -a -q "hide for detached accessible" "$LOG" && guard="yes"
grep -a -q "Main app process exited normally" "$LOG" && clean_exit="yes"
grep -a -q "parent process survived" "$LOG" && test_pass="yes"

for CORE in $(ls /tmp/cores/core.* 2>/dev/null); do
  BTF="$OUT/run-1-bt-$(basename "$CORE").txt"
  echo "--- core: $CORE ---" > "$BTF"
  file "$CORE" >> "$BTF" 2>&1 || true
  gdb -batch -ex "set pagination off" -ex "bt 60" "$BIN" "$CORE" >> "$BTF" 2>&1 || true
  if grep -q "ProcessHideEvent" "$BTF"; then parent_crash="yes"; fi
done
cp /tmp/cores/core.* "$OUT/" 2>/dev/null || true

if grep -a -q "application terminated with exit code" "$LOG"; then
  echo "arm2: browser-terminated marker present (unexpected for guarded build)"
fi

echo "== notable log lines =="
grep -a -n -E "hide for detached|ProcessHideEvent|IPC|processing error|Application (ran|terminated|shut down)|Main app process|crashed" "$LOG" | head -n 80 || true

{
  echo "guard_fired=$guard"
  echo "parent_crash=$parent_crash"
  echo "clean_exit=$clean_exit"
  echo "test_pass=$test_pass"
  echo "mochitest_rc=$rc"
} > "$OUT/result.txt"
cat "$OUT/result.txt"
sudo dmesg 2>/dev/null | tail -n 50 > "$OUT/dmesg.txt" || true
echo "ARM2 DONE"
exit 0
