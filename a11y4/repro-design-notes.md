# a11y4 / succ-001-S1 repro rig — design notes

Pinned source rev: `e00a7636053d44c48b523c20b24fde9d3548062a` (mozilla-firefox/firefox).
Target: Firefox parent-process null-pointer dereference reachable from a
content-process a11y cache update.

Crash path (verified in pinned sources, unpatched):

- `RemoteAccessible::RelationByType` -> `GetDirectRelationFromCache` lambda
  (`accessible/ipc/RemoteAccessible.cpp:1392-1397`): for the `target`
  (DETAILS) atom, `aAcc->mDoc->GetAccessible(maybeIds->ElementAt(0))` is
  passed to `nsAccUtils::IsValidDetailsTargetForAnchor` with **no null
  check**.
- `nsAccUtils::IsValidDetailsTargetForAnchor` (`accessible/base/nsAccUtils.cpp:668-670`)
  immediately calls `aAnchor->IsAncestorOf(aTarget)`.
- `Accessible::IsAncestorOf` (`accessible/basetypes/Accessible.h:265-273`)
  does `aAcc->Parent()` — a pure-virtual call on `aAcc`; with
  `aAcc == nullptr` this is a null-pointer dereference in the **parent**
  process.

A content process can make `GetAccessible()` return nullptr by sending an
Update cache for an element whose `target` attribute names a nonzero id that
was never mapped in the parent (e.g. an id for an accessible that never
existed, or was already removed).

## Arms

- **ARM 1** (sender + test patches, NO fix): 5 runs. Expect parent SIGSEGV on
  the consumer query; gdb backtrace should contain
  `Accessible::IsAncestorOf` <- `nsAccUtils::IsValidDetailsTargetForAnchor` <-
  `RemoteAccessible::RelationByType` (xpc entry: `xpcAccessible::GetRelationByType`).
- **ARM 2** (adds the guard patch, incremental rebuild): 1 run. Expect the
  guard to reject the null target, empty DETAILS relation, no crash.

## Patches

- `s1-sender.patch` (content side, test-only):
  - `LocalAccessible::HandleAccEvent` `EVENT_HIDE` case: when the hidden
    accessible's element has id `s1-trigger`, forge an Update cache for the
    still-visible `#s1-poison` element carrying `target` =
    `[0xDEADBEEFCAFE]` and send it via `ipcDoc->SendCache`. Probes via
    `printf_stderr` (`S1PROBE forge sent ...`).
  - `RemoteAccessible` instrumentation:
    - Probe A (parent, in the DETAILS branch of `GetDirectRelationFromCache`,
      immediately before the unguarded call): `S1PROBE details
      acc=<id> firstTarget=<id> resolved=0|1`.
    - Probe B (parent, in `ApplyCache`, after the field merge, before
      `IsText()`): `S1PROBE poison-arrived acc=<id> n=<len>`; plus a
      pref-gated (`a11y4.p0.enabled`, default false) direct
      `RelationByType(DETAILS)` fallback trigger.
- `s1-test.patch` (test):
  - Adds `accessible/tests/browser/relations/browser_a11y4_s1.js`
    (browser-chrome, a11y subsuite) + manifest entry.
  - The page inline script hides `#s1-trigger` at +2 s (fires the forge even
    if the harness task stalls); the test additionally drives the hide via
    `invokeContentTask` at +1 s, waits 2.5 s for the cache flush, then
    performs the consumer query (`poison.getRelationByType(RELATION_DETAILS)`)
    and asserts survival + empty relation.
- `s1-fix.patch` (candidate guard):
  - `nsAccUtils::IsValidDetailsTargetForAnchor`: `if (!aTarget) return
    false;` at entry (+ `S1PROBE fix rejected null target` marker).

## Deviations from the generic plan (documented for evidence review)

1. Test lives in `accessible/tests/browser/relations/` (not the browser/
   top level) so it inherits the directory manifest prefs, `head.js`, and
   shared-head wiring (`addAccessibleTask`, `findAccessibleChildByID`,
   `invokeContentTask`) without touching top-level files.
2. Forge is driven by `EVENT_HIDE` of a marked element rather than a custom
   IPC hook; the page also self-hides the trigger on a timer so the forge
   does not depend on the harness reaching the task body.
3. Forged target uses a never-mapped id (`0xDEADBEEFCAFE`) — no
   detach/unmap sequencing needed; this is the V-A variant.
4. The plan's P0 fallback (parent self-query instead of the xpc consumer
   path) is compiled in but **off by default**; ARM1 auto-engages it from
   run 3 only if runs 1-2 show the poison signal (`poison-arrived` or
   `resolved=0`) with zero crashes. Evidence from P0-engaged runs is
   classified as fallback (bt marked `p0_bt`).
5. `hang` counters are informational: the shutdown-time browser hang after
   test completion is a known harness behavior in this environment (rc=124
   on `timeout`), not a repro outcome.

## Verdict classes (report-only step writes out/verdict.txt)

- ARM1: `NULLDEREF-CONFIRMED` (crashes>0 & frames>0 & resolved0>0) /
  `FALSIFIED-QUERY-SURVIVED` (resolved0>0 & poison>0 & crashes==0) /
  `INCONCLUSIVE` / `PACKAGING-BLOCKED` (testcase filtered by manifest).
- ARM2: `GUARD-HELD` (fix marker + resolved0 lines + survived + no parent
  crash) / `GUARD FALSIFIED` (parent crash with guard) / `GUARD
  INCONCLUSIVE` / `GUARD NO-RESULT`.

## Evidence (artifact `a11y4-repro-evidence`)

Per run: full mochitest transcript (`run-N.log`), filtered `S1PROBE` lines
(`run-N-probes.txt`), core + gdb backtrace when a crash produces a core
(`run-N-bt-core.*`), dmesg tail, counters (`result.txt`); plus build log,
patched-source snapshots, patch sha256s, binary sha256, `meta.json`,
`verdict.txt`.

Detections: crash = `application terminated with exit code` in transcript,
`PROCESS-CRASH`, or a core dump; frames = gdb backtrace matching
`IsValidDetailsTargetForAnchor|RemoteAccessible::RelationByType|Accessible::IsAncestorOf`.
