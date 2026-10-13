# a11y5 repro design — C4-S3V2 "wrong-embedder bind" (AddChildDoc target-identity gap)

ARM-AUTHOR (orch-a / cand4-deepdive) · 2026-10-13 · draft-only package (dynamic run pending)

## Claim (from CANDIDATE-4-DEEPDIVE §3, survivor C4-S3V2-WRONG-EMBEDDER-BIND)

`DocAccessibleParent::AddChildDoc` (`accessible/ipc/DocAccessibleParent.cpp:1051-1124`,
pin `e00a7636053d44c48b523c20b24fde9d3548062a`) validates the child doc
(fresh/TopLevel/shutdown) and the target OuterDoc's SHAPE, but never ties the
bind target to the child doc's actual embedding context, nor consults the
child's binding history. **Arm 1 (implemented here)**: a forged
`RecvBindChildDoc(D2, O3)` with D2 a live iframe document whose real embedder is
O2, and O3 a *different, still-attached* valid-shape OuterDoc, passes every
release guard (`:1054/:1058/:1061/:1067-1071/:1079-1083`) and silently binds D2
under the wrong OuterDoc; O3's previous child doc is silently unbound at
`:1085-1088`. **S3's proposed guard (reject detached target) does not close
this**: the forged target O3 is attached. No crash is claimed; the corruption
is state-visible (wrong-embedder placement), which is what the parent probes
evidence.

## Verified preconditions (pin bytes; slice sha256 `1449fe6d…` = mirror)

- `AddChildDoc` guards exactly as above; no target-identity check.
- `RecvBindChildDoc :1035-1039` only checks the child's BC-embedder WGP ==
  receiver Manager() — process-level, true for both O2 and O3 (same receiver A).
- `ShutdownOrPrepareForMove :405-446` + `RemoteAccessible::Shutdown` OuterDoc
  branch (`childCount==1 -> D->Unbind()`): a hide of an OuterDoc (no WillMove)
  unbinds its child doc but leaves it alive — feed for the forged rebind.
- `Unbind()` (h `:179-188`) is the only site where a child doc loses
  `RemoteParent()` besides shutdown (design doc S3); used by the fix probe to
  record the embedder.
- `CheckDocTree()` (doc-level `ParentDoc()` walk `:1278-1290`) stays true for
  the wrong-embedder bind: D2->ParentDoc() == O3->Document() == A.

## Forged sequence (s3v2-sender.patch; test-only)

Content side — in `LocalAccessible::HandleAccEvent`, EVENT_HIDE case, only for
the marked trigger iframe `#a11y5-forge` (its OuterDoc O1):

1. natural `PushMutationEventData(Hide{O1})` (pre-existing code; O1 is hidden
   for real — no WillMove is sent: Arm 1 needs no detach anywhere).
2. forged `PushMutationEventData(Hide{O2})` — O2 = `#a11y5-victim`'s OuterDoc
   (victim itself untouched/visible) -> parent unbinds D2 (alive).
3. `SendQueuedMutationEvents()` — force-flush so both hides are on the wire
   before the bind (`NotificationController.cpp:1033` flush is too late).
4. `SendBindChildDoc(D2.actor, O3id)` — O3 = `#a11y5-target`'s OuterDoc,
   still attached, valid shape (1 doc child D3).

Parent side (same channel, in order): `Hide(O1)` -> O1 shutdown; `Hide(O2)` ->
O2 shutdown -> `D2->Unbind()`; `BindChildDoc(D2, O3)`: `:1054` D2 unparented,
`:1058` not top level, `:1061` not shutdown, `:1067-1071` O3 found, `:1079-1083`
O3 valid shape -> **bind accepted**: D3 unbound/kept alive, D2 placed under O3,
reorder fires on O3. Deliberate deviation from the S3 rig: **no
`SendAccessiblesWillMove`** — the S3 rig needed it to leave O1 detached (its
target); Arm 1's target is attached, so the WillMove is unnecessary and the
forge is strictly simpler (documented deviation; the deep-dive's §3 rig brief
agrees — "no detach needed").

## Probes (printf_stderr only; NS_WARNING is compiled out in OPT)

Content (s3v2-sender.patch, LocalAccessible.cpp):
- `S3V2PROBE hide id=<O1> forge=<0|1>`
- `S3V2PROBE forge trigger-o1=<O1> victim-o2=<O2> d2-ipcdoc=<0|1> target-o3=<O3>`
  (`victim-o2` = the child's TRUE embedder as declared by the forging page;
  `ipcdoc=1` + `target-o3!=0` are required for the bind to be sent)
- `S3V2PROBE forge bind-sent target-o3=<O3>`

Parent (s3v2-sender.patch, DocAccessibleParent::AddChildDoc, post-bind):
- `S3V2PROBE bind child=<cid> embedder=<eid> in-map=.. target-attached=..
  target-is-doc=.. s3-guard-inert=.. child-under-embedder=..
  outer-child0-is-child=.. in-childdocs=.. target-prev-child=<id>
  target-prev-differs=.. prev-embedder=<id> prev-mismatch=.. checkdoctree=..
  rootwalk-reaches-child=.. rootchain-len=.. rootchain-reaches-doc=..`
- First-seen `child->embedder` history is kept in function-local static arrays
  so the forged line carries **prev-embedder=<O2> prev-mismatch=1** — a
  parent-internal truth anchor that does not rely on cross-referencing content
  probes (forged bind: embedder=O3 != prev-embedder=O2, accepted).
- Normal binds (load-time) show `prev-embedder=0 prev-mismatch=0
  child-under-embedder=1 rootwalk-reaches-child=1 rootchain-reaches-doc=1` —
  non-vacuousness control.
- Forged bind additionally: `target-attached=1 s3-guard-inert=1`
  (S3's detached-target guard would NOT fire — the bypass, directly evidenced)
  and `target-prev-differs=1` (O3's prior child doc D3 was displaced).

## Fix probe (s3v2-fix.patch) — ARM2

Target-identity guard + secondary detached check, placed after the shape check
and before any state mutation (`:1085-1092`):

```cpp
if (aChildDoc->mLastEmbedderID && aChildDoc->mLastEmbedderID != aParentID) {
  printf_stderr("S3V2PROBE fix rejected wrong-embedder bind …");
  return IPC_FAIL(this, "binding to wrong embedder");
}
if (!outerDoc->RemoteParent() && !outerDoc->IsDoc()) {
  printf_stderr("S3V2PROBE fix rejected detached embedder");
  return IPC_FAIL(this, "binding to detached embedder");
}
```

`mLastEmbedderID` (new DocAccessibleParent member, 0 default) is recorded in
`Unbind()` while `RemoteParent()` still names the embedder. A doc that has
never been unbound has 0 and any first bind passes; a re-bind must target the
same OuterDoc. This closes Arm 1 (wrong-O; the detached check alone cannot) and
Arm 2 (stale-child re-bind). The S3-style detached check is kept as a
secondary defence with its own marker.

Honest scope caveats (probe guard, NOT the upstream fix — mirrors the S3
package's caveat): (a) direction (2) of the deep-dive — resolving the expected
embedder from BC topology — would be the strongest form but needs
element->acc resolution the parent side does not currently expose; (b) an
exotic legit re-bind under a different OuterDoc ID (e.g. cross-document iframe
adoption) could be over-rejected by this probe guard; (c) a forged bind to an
EMPTY attached target (no displaced child) is still accepted on first re-bind
paths unless the same-embedder requirement is kept — the rig demonstrates and
closes the characterized Arm-1 shape.

## Test (s3v2-test.patch)

`testing/mochitest/tests/Harness_sanity/test_a11y5_forge.html` + manifest
entry. Three **srcdoc** iframes (same content process; about:srcdoc inherits
the page principal — required for the same-process bind path): `#a11y5-victim`,
`#a11y5-target` (both left visible/untouched), `#a11y5-forge` (hidden at T+3s
to fire the forge). Plain mochitest: the forge must run in the CONTENT process
where `ipcDoc` exists.

## Expected outcomes (verdict classes)

- ARM1 (unguarded): no crash; forged bind accepted; per-run probe line with
  `prev-mismatch=1 s3-guard-inert=1 target-prev-differs=1` ->
  **WRONG-EMBEDDER-BIND-CONFIRMED** (5/5 expected; deterministic state machine,
  no races needed).
- ARM2 (guarded): `S3V2PROBE fix rejected wrong-embedder bind` + generic
  IPC_FAIL channel signature; no `prev-mismatch=1` bind line; normal load
  binds still accepted; no parent crash -> **GUARD-HELD** (child termination +
  harness shutdown tolerated, a11y1/S3 lessons).

## Patch application order (lockstep with a11y5-repro.yml)

1. Build stage: pristine + `s3v2-sender.patch` + `s3v2-test.patch`; the fix
   patch is only `--check`ed here (against the sender-patched tree — it is
   authored against that state).
2. ARM2: `s3v2-fix.patch` applied on the same tree + incremental rebuild.
   Validated locally with real `git apply` in this order (all rc=0).

## Pins

- Mirror rev (CI clone): `e00a7636053d44c48b523c20b24fde9d3548062a` == hg
  `1a0832246d36…`. Byte source: local slice + mirror raw fetches; sha256s
  match EVIDENCE §2 (`DocAccessibleParent.cpp` `1449fe6d…`; patch index bases
  `3997019`/`1d3ff06`/`2ae68a9` match the mirror blobs).
- Public content only: no secrets; CI does read-only public fetches.
