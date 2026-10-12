# Concurrency Probe — RESULTS (empirical)

**Question:** What is the TRUE concurrent-job ceiling for GitHub-Actions standard runners on this
account, and is it per-REPO or per-ACCOUNT?
**Verdict (empirical): ~20 concurrent jobs ACCOUNT-WIDE (not per-repo).** Observed union maximum
across two repos = **20**, never higher. A second repo does **not** raise the ceiling — it shares
the same 20 slots (FIFO). Cold start grants ≈19 runners; the 20th slot appears ~90 s later; the
cap is then 20 sustained.

All timestamps UTC (machine local = UTC+5). Raw evidence (final API JSON snapshots + union table):
`./evidence/` — `A1-run.json`, `A1-jobs.json`, `A2-run.json`, `A2-jobs.json`, `B1-run.json`,
`B1-jobs.json`, `union-analysis.txt`, dispatch bodies. Mirror: `anonysusarchiver-design/bb-lane`
→ `evidence/concurrency-probe/`.

## Setup
- Repos: `bb-lane` (workflow `concurrency-probe.yml`, commit `8f562f5`) and `bb-lane-probe`
  (twin repo, commit `d269289`), both public, account `anonysusarchiver-design`.
- Workflow: `workflow_dispatch`; matrix `shard: [1..20]`, `ubuntu-latest`, `max-parallel: 20`;
  each job prints START/END markers (epoch + ISO) around `sleep <hold_seconds>`.
- Dispatch: `curl POST /repos/{owner}/{repo}/actions/workflows/concurrency-probe.yml/dispatches`
  with body-file `{"ref":"main","inputs":{"hold_seconds":"N"}}`.
- Measurement: Actions API (`/actions/runs[/{id}[/jobs]]`) polled every ~25-30 s; snapshots saved
  per cycle; numbers computed by event-sweep over the saved JSONs (`tools/compute-concurrency.ps1`,
  `tools/compute-union.ps1`) — not eyeballed.

## Runs

| run | repo | run_id | jobs | hold | max simultaneous | first-wave starts (UTC) | wall |
|---|---|---|---|---|---|---|---|
| A1 | bb-lane | 38222253608 | 20 | 90 s | **19** | 08:13:25-26 (19 jobs) | 189 s |
| A2 | bb-lane | 38223169628 | 20 | 300 s | **20** | 08:22:30-31 (19 jobs) + #20 at 08:24:00 | 394 s |
| B1 | bb-lane-probe | 38223402594 | 20 | 60 s | 18 (queued behind A2) | 08:27:35-38 (18) + 2 at 08:28:40 | 128 s |

Queue waits (started_at − created_at): A1 avg 8 s, max 98 s (the 20th job); A2 avg 7 s, max 92 s
(the 20th job); B1 avg 173 s, max 230 s (whole run queued behind the account cap while A2 held 20).
All three runs concluded `success` (20/20 jobs each).

## The decisive evidence (A2 + B1, overlapping windows)
1. A2 dispatched 08:22:26. By 08:22:31, nineteen A2 jobs were running; the 20th (`probe-14`)
   started 08:24:00 → **20/20 A2 jobs ran simultaneously 08:24:00 → 08:27:33** (3.5 min at 20).
2. B1 dispatched 08:24:46 — *while A2 held 20*. B1 stayed **queued=20 / in_progress=0 for ~2.8 min**
   (zero runners granted to the second repo despite it being public and idle).
3. A2's first 19 jobs completed 08:27:33-35. **B1's first 18 runners appeared in the same seconds**
   (08:27:35-38) — a drain-refill through the SAME account pool.
4. A2's straggler (`probe-14`) ran until 08:29:04; B1's remaining 2 jobs started 08:28:40 as B1's
   first wave drained.
5. **Event-sweep union of all 40 job intervals across both repos: MAX = 20 (at 08:24:00Z).
   Never 21+.** → the 20 slots are shared account-wide; a second repo buys isolation, not throughput.

## Side observations
- Cold-start pattern (seen twice): 19 jobs get runners in 4-10 s; the 20th slot materializes
  ~90 s later. Treat usable burst as 19 immediately / 20 within ~1.5 min. A1's 19-concurrent result
  was NOT contention: the account had **no other active runs** at 08:13-08:16 (verified via the
  runs API for bb-lane and bb-forge).
- Runner grant latency after dispatch: ~4-10 s (fast, subject to the account window).
- API flakiness on this network: many `curl (28)` 443 timeouts / `000` codes; every call needed
  2-4 retries (`--retry 4 --retry-delay 3 --retry-all-errors`). Zero impact on run execution.
- No throttling, no platform errors, no partial failures in any run.

## Strategy implications
- Saturate ONE repo with ≤20 matrix jobs per dispatch. For N > 20, wave in ≤20-job chunks:
  wall ≈ ceil(N/20) × job_duration (+ ~1 min startup/queue).
- Keep `bb-lane-probe` as an isolation/priority lane (e.g., short probes never blocked by long
  a11y builds) — but remember it competes for the SAME 20 slots.
- Cancel stale runs to free slots immediately (recipe live-verified in LANE-RUNBOOK.md).
- Public repos ⇒ unlimited standard minutes; concurrency (20) is the only real ceiling.

## Repro (exact commands)
```powershell
# dispatch (body-file mandatory on Windows; inline JSON gets mangled by PowerShell)
$pat=(Get-Content 'D:\cache\temp\opencode\bb-forge\secrets\github.pat' -Raw).Trim()
curl.exe -sS -o resp.txt -w "%{http_code}" -X POST `
  -H "Authorization: token $pat" -H "Accept: application/vnd.github+json" `
  -H "Content-Type: application/json" --data-binary "@dispatch.json" `
  https://api.github.com/repos/anonysusarchiver-design/bb-lane/actions/workflows/concurrency-probe.yml/dispatches
# poll
curl.exe -sS --retry 4 --retry-delay 3 --retry-all-errors -o run.json `
  -H "Authorization: token $pat" https://api.github.com/repos/anonysusarchiver-design/bb-lane/actions/runs/{RUN_ID}
curl.exe -sS --retry 4 --retry-delay 3 --retry-all-errors -o jobs.json `
  -H "Authorization: token $pat" "https://api.github.com/repos/anonysusarchiver-design/bb-lane/actions/runs/{RUN_ID}/jobs?per_page=100"
# compute (from saved jobs.json only)
powershell -File tools\compute-concurrency.ps1 -JobsFile jobs.json
powershell -File tools\compute-union.ps1 -FilesCsv "A2-jobs.json;B1-jobs.json" -CloseTime (Get-Date -Format o)
```
