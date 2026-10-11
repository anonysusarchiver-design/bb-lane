# A11Y-3 forge design — forged `BindChildDoc` under a move-detached OuterDoc

ARM-AUTHOR (Zero-Day Foundry wave-14) · draft-only package · 2026-10-12

## Claim

`DocAccessibleParent::AddChildDoc` (accessible/ipc/DocAccessibleParent.cpp) never
checks that the target embedder proxy (the OuterDoc at `aParentID`) is still
attached to the tree. A same-process child-document bind can therefore be
processed against an embedder that the move-prepare branch left detached
(`SetParent(nullptr)`, :444, still present in `mAccessibles`). Result: the new
child doc is bound under a **detached** OuterDoc — a bound-but-unreachable
document subtree in the parent process (silent corruption; not a crash), and
the previous child doc is unbound without shutdown (:1085-1088). No
attacker-controlled pointer; whole-tree consistency damage under the standard
e10s threat model (sandboxed content, a11y active).

## Verified preconditions (all line refs @ hg tip `1a0832246d36`)

- `AddChildDoc` guards: `:1054` child already parented / `:1058` top-level /
  `:1061` shutdown / `:1067-1071` `mAccessibles.GetEntry(aParentID)` /
  `:1079-1083` OuterDoc shape. **No attachment check on `outerDoc`.**
- `ShutdownOrPrepareForMove` `:405-446`: `:410` OuterDoc children skip (the
  child doc survives the move), `:428-432` non-moving → shutdown,
  `:434-445` moving → detach (`:444 SetParent(nullptr)`); map entry stays
  (proven live by the a11y1 crash at `ProcessHideEvent:508` with a moved acc).
- `ProcessHideEvent` `:448-518`: `:508 parent->RemoveChild(root)`.
- `RecvBindChildDoc` `:1017-1049`: `:1035-1039` requires
  `childDoc->GetBrowsingContext()->GetEmbedderWindowGlobal() == Manager()`
  (same-process, same-embedder binds only).
- Real bind send path: `NotificationController.cpp` `:1076-1103`
  (`:1091 SendBindChildDoc`) runs after the mutation batch (`:1032-1034`) and
  event queue (`:1055`) — i.e. legit binds occur with the embedder attached;
  an attachment guard is not expected to misfire on real flows (probe-level
  verification only; see Risks).
- `RemoteAccessible::Shutdown` OuterDoc branch: exactly one doc child →
  `Unbind()` (normal un-parent; used by the forged hide of O2).
- `RemoteAccessible::RemoteParent()` ≡ `mParent` (RemoteAccessible.cpp
  `:236-244`); only `SetParent(nullptr)` sites: `:444` and `Unbind()`
  (DocAccessibleParent.h `:179-188`).
- ID space: `LocalAccessible::ID()` = `reinterpret_cast<uintptr_t>(this)`
  (docs = 0) — shared by events, `AccessiblesWillMove`, `BindChildDoc`.

## Forged sequence (s3-sender.patch; test-only)

Content side — in `LocalAccessible::HandleAccEvent`, EVENT_HIDE case, only for
the marked iframe element `#a11y3-forge` (its OuterDoc **O1**):

1. `SendAccessiblesWillMove([O1id])` — immediate; parent marks O1 moving.
2. natural `PushMutationEventData(Hide{O1id})` (pre-existing code).
3. forged `PushMutationEventData(Hide{O2id})` — O2 = victim iframe
   `#a11y3-victim`'s OuterDoc (victim itself untouched/visible).
4. `SendQueuedMutationEvents()` — force-flush so the batch is on the wire
   **before** the bind (normal flush is at NotificationController.cpp:1033,
   too late).
5. `SendBindChildDoc(D2.actor, O1id)` — D2 = victim's inner doc, freshly
   un-parented by (3)'s parent-side processing.

Parent side (same channel, in order): `AccessiblesWillMove` → batch
[Hide(O1) → move branch: O1 detached, D1 kept; Hide(O2) → shutdown →
`D2->Unbind()`] → `BindChildDoc(D2, O1)`: passes `:1054` (D2 unparented),
`:1058`, `:1061`; `:1067` finds O1 (zombie, still in map); `:1079-1083` O1 is
a 1-child OuterDoc (child D1 is a doc); `:1085-1088` unbinds D1; `:1090-1092`
binds D2 under detached O1. D2 subtree is now unreachable from the root.

## Expected outcomes

- ARM1 (unguarded): no crash; parent probe line for the crafted bind:
  `zombie=1 … rootwalk-reaches-child=0 rootchain-reaches-doc=0 rootchain-len=1`
  → **ZOMBIE-CONFIRMED** (5/5 runs expected, zero crashes).
- ARM2 (guard): `S3PROBE fix rejected detached embedder` + generic
  `PDocAccessible::Msg_BindChildDoc … handler returned false` + no zombie
  line → **GUARD-HELD**. Child termination and harness shutdown
  (`MOZ_CRASHREPORTER_SHUTDOWN`) are tolerated (a11y1 lesson: judge rejection
  + no parent crash, not clean exit).

## Fix probe (s3-fix.patch)

Placed after the shape check and before the unbind mutation
(`:1083`→`:1085` window; also before `:1090`), so a rejected bind mutates no
state:

```cpp
if (!outerDoc->RemoteParent() && !outerDoc->IsDoc()) {
  NS_WARNING("S3PROBE fix rejected detached embedder");
  return IPC_FAIL(this, "binding to detached embedder");
}
```

The explicit `NS_WARNING` exists because custom IPC_FAIL reason strings never
reach the log (a11y1 false-negative). `!IsDoc()` exemption leaves doc-embedder
flows untouched.

## Test (s3-test.patch)

`testing/mochitest/tests/Harness_sanity/test_a11y3_forge.html` + manifest
entry. Two **srcdoc** iframes: about:srcdoc frames inherit the page principal
and are allocated to the page's content process
(`dom/ipc/ProcessIsolation.cpp` `:274-284` → `WebContent` on the inherited
principal) — required for the same-process bind path; `data:` was rejected as
placement is pref/behavior-dependent (`:329-335`). Trigger:
`#a11y3-forge.style.display="none"` → one EVENT_HIDE for its OuterDoc.

## Probes

See `s3-probes.md` (P1 forge-fire, P2 bind-sent, P3 parent per-bind
transcript, P4 guard rejection + generic IPC_FAIL signatures).

## Pins

- Source fetched from hg.mozilla.org tip: `1a0832246d36398fd7fd5acfa11f5f7a517d0537`
  (first parent `bb234c0905b55721c4bbf93d6163c901c0f971b9` = A5 pin).
- Workflow default `source_rev` = `e00a7636053d44c48b523c20b24fde9d3548062a`
  (GitHub mirror rev used by the proven a11y1 build).
- Byte-level validation results + full sha256s: see `README.md` §Validation.

## Risks / unknowns (honest)

- **D1 liveness**: design assumes `display:none` on the iframe element does
  not tear down F1's inner document. If it does, `:1079-1083` rejects with
  "Binding to parent that isn't a valid OuterDoc!" — visible in logs;
  classified INCONCLUSIVE, not a falsification.
- **srcdoc child-doc timing**: 3 s wait before the trigger; if D2's ipcdoc is
  not resolvable (`S3PROBE forge … ipcdoc=0`), the bind is skipped →
  INCONCLUSIVE.
- **IPC_FAIL semantics**: the rejection kills the sending content process
  (expected); the mochitest harness may shut down the browser early — by
  design not treated as failure.
- **Guard scoping**: the probe guard is *not* the upstream fix; it
  demonstrates rejection over the crafted sequence. A real fix needs review
  of exotic reattach flows (print docs, OOP transitions use the separate
  BrowserBridge overload and are untouched by this probe).
- **Standalone**: s3 patches are NOT layered with a11y1's (both edit the same
  EVENT_HIDE hunk); the workflow builds an s3-only tree.
