# A11Y-3 repro — probes & log-line map (s3 arm)

All probes print via `NS_WARNING` to stderr of the emitting process (browser/parent
or content). The mochitest harness captures both process streams into the run log
(proven channel: the a11y1 repro verdict shows parent `###!!! [Parent]` and
`GECKO(pid)` lines inside `run-N.log`). Grep for the substring `S3PROBE` — the
log-line prefix (`WARNING:`, `GECKO(pid)`) varies by process.

## P1 — forge-fire (content; s3-sender.patch)

- Line: `S3PROBE forge outer=<O1id> victim=<O2id> ipcdoc=<0|1>`
- Grep: `S3PROBE forge outer=`
- Meaning: the crafted EVENT_HIDE path ran for the marked element (`#a11y3-forge`);
  the victim embedder O2 was found in the local tree; `ipcdoc=1` means D2's
  `DocAccessibleChild` was located, so all three forged senders will fire.
- `ipcdoc=0` → victim child doc not found content-side (a11y tree timing /
  process placement). The forged bind is skipped → verdict INCONCLUSIVE.

## P2 — bind-sent (content; s3-sender.patch)

- Line: `S3PROBE forge bind-sent`
- Grep: `S3PROBE forge bind-sent`
- Meaning: `DocAccessibleChild::SendBindChildDoc(D2, O1)` was invoked — the forged
  bind is on the wire (sent after the forced-flushed mutation batch, so the parent
  processes Hide(O1), Hide(O2) first).

## P3 — parent bind transcript (parent; s3-sender.patch; runs for every AddChildDoc)

- Line: `S3PROBE bind child=<cid> embedder=<eid> in-map=<0|1> zombie=<0|1> child-under-embedder=<0|1> rootwalk-reaches-child=<0|1> rootchain-reaches-doc=<0|1> rootchain-len=<n>`
- Grep: `S3PROBE bind child=`
- Normal binds (the two srcdoc iframe binds at load): `zombie=0`,
  `child-under-embedder=1`, `rootwalk-reaches-child=1`,
  `rootchain-reaches-doc=1`, `rootchain-len=2` (D→O→doc).
- Forged bind (the finding): `zombie=1` (embedder O1 has no `RemoteParent()` —
  the move branch did `SetParent(nullptr)` at DocAccessibleParent.cpp:444),
  `child-under-embedder=1`, `rootwalk-reaches-child=0` (a downward walk from the
  doc root cannot reach O1's subtree), `rootchain-reaches-doc=0`,
  `rootchain-len=1` (D2→O1→detached). That full tuple = the ZOMBIE:
  a bound-but-unreachable document subtree.
- The presence of normal (zombie=0) lines alongside proves the probe executes
  and discriminates, i.e. the transcript is not vacuous.

## P4 — guard rejection (parent; s3-fix.patch; ARM2 only)

- Line: `S3PROBE fix rejected detached embedder` (explicit marker; new vs a11y1,
  whose IPC_FAIL reason string never reached the log — lesson applied)
- Grep: `S3PROBE fix rejected detached embedder`
- Generic IPC_FAIL channel signature (proven in a11y1 verdict; custom IPC_FAIL
  reason strings are NOT printed):
  - `IPDL protocol error: Handler returned error code!`
  - `PDocAccessible::Msg_BindChildDoc Processing error: message was deserialized, but the handler returned false (indicating failure)`
  - `<pid> exited on signal 15` (forged child terminated by the rejection — tolerated)
  - possible `A content process crashed and MOZ_CRASHREPORTER_SHUTDOWN is set, shutting down`
  - possible `Main app process: exit 245` (harness shutdown — tolerated, NOT a guard failure)
- Grep set: `S3PROBE fix rejected detached embedder` / `Msg_BindChildDoc` / `handler returned false`

## Expected transcripts

| Signal | ARM1 (unguarded) | ARM2 (guarded) |
|---|---|---|
| P1 forge-fire | present, ipcdoc=1 | present, ipcdoc=1 |
| P2 bind-sent | present | present |
| P3 bind zombie=1 | **present (the finding)** | absent |
| P3 normal zombie=0 | present (2 iframe binds) | present |
| P4 fix rejected | absent | **present** |
| parent crash / cores | none | none (child kill tolerated) |
| exit | test ok() + finish (rc 0) | early exit possible (rc != 0) — tolerated |

## Classification use

- No `S3PROBE` lines at all → patches not applied / test not executed → PACKAGING-BLOCKED.
- P3 zombie=1 in ≥1 of 5 ARM1 runs, 0 crashes → ZOMBIE-CONFIRMED (5/5 = strong).
- P4 present + no P3 zombie=1 in ARM2 + no parent crash → GUARD-HELD.
- P1 or P3 absent/partial while the run otherwise healthy → INCONCLUSIVE (inspect
  P1 `ipcdoc=` value first: 0 = process placement / tree timing, not a falsification).
