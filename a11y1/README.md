# A11Y-1 dynamic repro (bb-lane)

Reproduction harness for the missing detached-root guard in
`DocAccessibleParent::ProcessHideEvent` (mozilla-central, pinned mirror commit
`e00a7636053d44c48b523c20b24fde9d3548062a`).

## What this lane does

One job, two arms, on a single OPT browser build:

1. **Build** (`build-browser.sh`): shallow clone of the public
   `mozilla-firefox/firefox` mirror at the pinned rev, apply
   `a11y1-sender.patch` (test-only forge in
   `LocalAccessible::HandleAccEvent` EVENT_HIDE) + `a11y1-test.patch`
   (Harness_sanity test page `a11y1_forge.html`), `./mach build` with
   `--enable-project=browser --enable-optimize --disable-debug
   --disable-crashreporter --enable-bootstrap`.
2. **ARM 1** (`run-arm1.sh`): 5x `mach mochitest` runs (plain, xvfb,
   `accessibility.force_disabled=-1`). The forged element sends
   `AccessiblesWillMove([X])` + one `Hide(X), Hide(X)` batch; unguarded parent
   SEGVs in `ProcessHideEvent` on the second hide. Cores + gdb backtraces are
   captured per run. Expectation: 5/5 parent crashes, frame
   `DocAccessibleParent::ProcessHideEvent`.
3. **ARM 2** (`run-arm2.sh`): applies `a11y1-fix.patch` (guard:
   `if (!parent) return IPC_FAIL(this, "hide for detached accessible")`),
   incremental rebuild, one run. Expectation: IPC_FAIL rejection
   (`hide for detached accessible` in the log), no parent SEGV, browser exits
   normally.

## Evidence

Artifact `a11y1-repro-evidence` (7 days) contains `out/meta.json`,
`out/arm1/` (per-run logs, cores backtraces, `result.txt`), `out/arm2/`
(rebuild log, run log, backtraces, `result.txt`), snapshot of the patched
source regions and all three patches with sha256s.

## Notes

- Test-only patches; not for upstream submission.
- `--disable-crashreporter` is required so kernel cores are produced;
  `core_pattern=/tmp/cores/core.%p`, `ulimit -c unlimited`.
- `security.sandbox.content.level=0` is set as insurance on the runner.
