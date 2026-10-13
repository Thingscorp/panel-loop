# panel-loop

A **tribunal of AI decision helpers for coding agents** — four judges, one verdict. `panel-loop` wraps them in a **recursive six-phase QA loop**: the agent does the legwork (reads code, writes the test sheet, runs cases, applies fixes) and the panel makes every judgment call — coverage, deduplication, risk triage, failure triage, severity, fix review, regression verdict, confidence, and loop termination.

The judges: **Clef** (the thorough reader), **Clef Flash** (the fast 9B for high-volume gates), **Jev** (risk triage and adversarial second opinions), **Luna** (structured scored audits). Routine gates go to the best-fit judge; the termination verdict goes to the full tribunal — **one judge, one vote, majority wins**.

The rule of the loop: **the agent is the hands, the panel is the judge.** No gated decision is made on the agent's judgment alone, and no phase exits without its gate passing. None of these are chat models — they judge; they do not chat, code, or complete.

## Verified claims

Every claim below is backed by a test in `tests/` — fully mocked, no network, no key, no charges. Run them yourself: `python3 -m unittest discover -s tests` (115/115 pass, ~1.5s).

| Claim | Backing data |
|---|---|
| POSTs only to the two known endpoints | `test_request_shape` asserts the exact URL and POST method; `test_endpoints_are_the_only_real_urls_in_source` scans `bin/clef-decide` for any other URL — none exists |
| API key from standard config, never prompted or logged | `test_missing_key_stops` (helper refuses without it); `test_key_never_prompted_interactively` (no `input()`/`getpass` anywhere in source); `tests/test_config.py` proves the full precedence chain |
| No retries, no idempotency key | `test_no_retry_on_transport_error`: a failed transport produces exactly 1 attempt, never a resend; `test_no_idempotency_header_sent` |
| 16,384-token budget enforced before sending | `test_context_budget_refuses`; `test_constants_match_claims` pins the constant |
| ≤32 questions, ≤64 options, 2–10 score levels | `test_gateway_bounds` exercises each bound; `test_constants_match_claims` pins them |
| `bin/clef-decide` is executable and stdlib-only | `test_helper_is_executable`; `test_stdlib_only_imports` (imports: argparse, concurrent.futures, json, os, sys, urllib — nothing else) |
| SystemOne model is `clef`, `clef-flash`, or `jev-latest` only (`:free` preserved); anything else rejected | `test_flash_model_sent_exactly`, `test_flash_free_spelling_preserved`, `test_unknown_clef_variant_rejected`, `test_model_allowlist_pinned` |
| Luna uses the decisions route with the proven choice shape; other types rejected | `test_luna_posts_to_decisions_with_proven_shape`, `test_luna_rejects_non_choice` |
| Panel: one judge one vote, majority wins, double tie escalates | `test_majority_wins`, `test_tie_reruns_then_decides`, `test_double_tie_escalates` |
| `--panel` rejects `--judge`/`--model` instead of silently ignoring them | `test_panel_rejects_judge_flag`, `test_panel_rejects_model_flag` |
| Luna answer-count mismatch is refused, never silently dropped | `test_luna_answer_count_mismatch_raises` |
| Panel worker errors name the failing judge | `test_panel_error_names_the_judge`, `test_panel_error_before_any_round_completes` |
| `--sot`: each skeleton point is judged in its own single-question request, in parallel | `test_each_point_is_its_own_request`, `test_skeleton_order_preserved` |
| The aggregation goal's state carries every point's verdict | `test_goal_state_carries_point_verdicts` |
| A failing skeleton point names itself (`[skeleton:<id>]`) | `test_error_names_failing_point`, `test_context_budget_per_request` |
| `--sot` rejects `--questions`/`--panel`; the goal file must hold exactly one question; goal IDs cannot collide with skeleton IDs | `test_sot_rejects_questions_flag`, `test_sot_rejects_panel_flag`, `test_sot_needs_skeleton_and_goal_files`, `test_goal_must_be_single_question`, `test_goal_id_collision_rejected` |
| `--final-panel` sends the goal to the full tribunal; a tie exits 2, a winner exits 0 | `test_final_panel_uses_tribunal`, `test_final_panel_tie_exits_2`, `test_final_panel_winner_exits_0` |

## Quickstart

```bash
export CLEF_API_KEY=...   # your key; never committed, never logged

# Ask a judge to decide a state blob against structured questions
bin/clef-decide --state-file state.md --questions questions.json
bin/clef-decide --state-file state.md --questions questions.json --json
```

`--state-file` holds a concise state (a diff, a test summary, an iteration report) — never the whole repo. `--questions` is a JSON map of question ID to question. Answers come back by question ID. See `examples/launch-readiness/` for a worked example and `docs/question-sets.md` for the full phase-by-phase question library.

## Judges

`--judge` selects which judge drives the gate (default `clef`). All four share the one API key.

```bash
bin/clef-decide --state-file state.md --questions questions.json --judge jev
bin/clef-decide --state-file state.md --questions questions.json --model clef-flash
```

- `clef` — the full-size decision model. The thorough reader; keep it for the highest-stakes verdicts.
- `clef-flash` — the 9B variant ([model card](https://huggingface.co/Cloudflare/clef-flash)): same SystemOne wire, no free-form generation, built for speed — median request latency 38.8ms vs 209.3ms for `clef` on the Decision Index suite. Pick it for cheap, fast, high-volume gates.
- `jev` — SystemOne model `jev-latest`. The risk and adversarial judge: risk triage, second opinions, catching what the others miss.
- `luna` — the `/v1/decisions` route (model `gpt-6-luna-decisions`, fixed). Structured scored audits with rationale — the audit trail. Choice questions only; use a SystemOne judge for `noul`/`score`.

Only `clef`, `clef-flash`, and `jev-latest` (plus a `:free` suffix spelling, preserved verbatim) are accepted on the SystemOne route — anything else is rejected, since none of these are chat models. Request shape reference: the [SystemOne quickstart](https://docs.typesafe.ai/introduction/quickstart).

### Panel votes

`--panel` fans one choice question out to all four judges in parallel and applies the tribunal rule: **one judge, one vote; majority wins.** A tie re-runs once (new votes, not retried requests); a second tie escalates to the owner — the helper exits 2 and never auto-picks.

```bash
bin/clef-decide --state-file report.md --questions done.json --panel
```

`done.json` must hold exactly one choice question, e.g. `{"done": {"type": "choice", "instructions": "...", "criteria": {"done": "...", "not_done": "..."}}}`. Use the panel for the loop-termination gate (phase 6) and other high-stakes verdicts; routine gates stay single-judge. `--panel` cannot be combined with `--judge` or `--model` — it always uses every judge that is switched on (all four by default; see [Switching judges](#switching-judges-on-and-off)), and contradictory flags fail loudly instead of being silently ignored.

### Skeleton-of-judgment

`--sot` implements skeleton-of-thought for decision models: the agent proposes a skeleton of independent sub-questions, each point is judged in its own request in parallel, and a final gate aggregates the per-point verdicts into one goal question. The judges judge; they do not invent the skeleton — that stays the agent's job, per the loop's rule.

```bash
bin/clef-decide --sot --state-file report.md --skeleton-file skeleton.json --goal-file goal.json
bin/clef-decide --sot --state-file report.md --skeleton-file skeleton.json --goal-file goal.json --final-panel
```

- `--skeleton-file`: map of point ID → question (choice/noul/score), ≤32. One request per point, so each point sees the full state under its own 16,384-token context budget instead of sharing one request's budget.
- `--goal-file`: exactly one question, the final verdict. Its state is the original state plus the per-point verdicts, each rendered as `point_id -> verdict`.
- `--final-panel`: the goal goes to the full tribunal (one judge, one vote, majority wins); a tie re-runs once, a second tie exits 2. Without it, the goal goes to `--judge` (default `clef`).
- Expansion uses `--judge`/`--model`; with `--final-panel` the expansion still uses that judge while the verdict uses all four. `--sot` cannot be combined with `--questions` or `--panel` — contradictory flags fail loudly.
- A failing point names itself: `[skeleton:<point_id>] <error>`.

Use `--sot` when a gate's evidence is too large for one request's context budget, or when the sub-judgments are genuinely independent and deserve the full state each. When the questions fit one request and share evidence, the plain batched form is cheaper (fewer requests).

## Pipeline (composable stages)

`clef-decide` stays the one-shot front door. Underneath, the same work is
available as small single-purpose programs that talk JSONL on stdin/stdout,
so gates can be composed in a shell, sharded across processes, and resumed
after a crash. Every stage reuses the core in `bin/clef-decide` — same
validation, same routes, same key chain, same no-retry POST, same tribunal
rule — so the Clef API contract lives in exactly one place.

| Stage | Does one thing | stdin → stdout | Network |
|---|---|---|---|
| `pl-judges` | lists the judges that are switched on | — → one name per line | no |
| `pl-split` | turns a state + questions file into requests; validates bounds and the 16,384-token budget up front | files → request JSONL | no |
| `pl-ask` | sends each request once to its judge's native route | request JSONL → result JSONL | **yes** (the only stage that is) |
| `pl-aggregate` | joins skeleton verdicts into the goal request (the `--sot` join) | result JSONL → request JSONL | no |
| `pl-tally` | one judge, one vote, majority wins; exit 2 on a tie | result JSONL → verdict JSON | no |
| `pl-render` | prints the same human summary as `clef-decide` | result JSONL → text | no |

### Stream formats

Request (one per line):

```json
{"id": "risk", "judge": "jev", "model": null, "round": 1, "state": "...", "questions": {"risk": {"type": "score", "...": "..."}}}
```

Result (one per request, **in input order** even with `--jobs`):

```json
{"id": "risk", "judge": "jev", "model": null, "round": 1, "key": "<sha256>", "ok": true, "answers": {"risk": {"...": "..."}}, "usage": {"...": "..."}, "cached": false}
{"id": "fix", "judge": "clef", "ok": false, "error": {"error": "[fix:clef] HTTP 429 from ...", "exit": 75, "status": 429, "stage": "pl-ask", "judge": "clef", "endpoint": "...", "cause": "...", "fix": "..."}}
```

Failed requests still produce a result line, so downstream stages see
exactly what failed; `pl-aggregate` and `pl-tally` refuse partial input
rather than judging on a subset.

### Composing

```bash
# Plain gate (same as clef-decide --questions q.json --judge jev)
pl-split --state-file s.md --questions q.json --batch --judge jev | pl-ask | pl-render

# Skeleton-of-judgment (same as clef-decide --sot)
pl-split --state-file s.md --questions skeleton.json \
  | pl-ask --jobs 8 \
  | pl-aggregate --state-file s.md --goal-file goal.json \
  | pl-ask | pl-render

# Tribunal (same as clef-decide --panel), with the tie re-run made explicit
vote() { pl-split --state-file r.md --questions done.json --per-judge --round "$1" | pl-ask --jobs 4 | pl-tally; }
vote 1 || { [ $? -eq 2 ] && vote 2; }   # second tie (exit 2) → escalate to the owner

# Skeleton + tribunal verdict (same as --sot --final-panel)
pl-split --state-file s.md --questions skeleton.json | pl-ask -P 8 \
  | pl-aggregate --state-file s.md --goal-file goal.json --per-judge | pl-ask -P 4 | pl-tally
```

### Scaling and resuming

- **Parallel:** `pl-ask --jobs N` (alias `-P`) runs N requests at once; output order is still input order.
- **Sharding:** `pl-ask --shard I/N` handles only lines where `index % N == I`. Run N copies (separate processes, `xargs -P`, or machines) over the same request stream and `cat` the results:
  ```bash
  pl-split --state-file s.md --questions risk.json > reqs.jsonl
  seq 0 3 | xargs -P 4 -I{} sh -c 'pl-ask --shard {}/4 --state-dir .pl-state < reqs.jsonl > out.{}.jsonl'
  cat out.*.jsonl | pl-render
  ```
- **Resumable, idempotent state:** `--state-dir DIR` (or `$PANEL_LOOP_STATE_DIR`) stores each *successful* result as `DIR/<key>.json`, written atomically (temp file + rename). The key is a SHA-256 of judge, model, round, state, and questions — never the API key. Re-running the same pipeline skips cached keys, so a crash halfway through a 32-point skeleton does not re-send (or re-pay for) the points that already succeeded. Failures are not cached.
- **No hidden retries.** Resuming is an explicit re-run you choose; `pl-ask` never re-sends on its own (the core rule: an unknown outcome may already be charged). A timeout is reported, not retried — re-running it may charge twice.
- `--round` is part of the cache key on purpose: the panel's tie re-run is a *new vote*, so it must not be served from cache.
- Keep concurrency modest: these are metered production endpoints, and HTTP 429 exits 75. There is no built-in load test, and you should not point one at the live service.

Design sources: the single-request-per-point fan-out is the existing `--sot`
design; the majority rule and tie handling are the existing `--panel`
tribunal rule (`tally()` is now shared by both); "no retries, no idempotency
key" and the budget/bounds checks are the existing hard rules. The stages
only make those steps separately runnable.

## Switching judges on and off

Each judge has an on/off switch; no code change is needed. `judges.conf` at
the repo root ships with all four on:

```ini
clef=true
clef-flash=true
jev=true
luna=true
```

First source found wins, per judge:

1. `$PANEL_LOOP_JUDGE_<NAME>` — e.g. `PANEL_LOOP_JUDGE_LUNA=false`, `PANEL_LOOP_JUDGE_CLEF_FLASH=off`
2. `judge.<name>=` in `~/.config/clef-decide/config` — e.g. `judge.jev=false`
3. `<name>=` in `judges.conf` (or the file named by `$PANEL_LOOP_JUDGES_FILE`)
4. on

Accepted values: `true/false`, `on/off`, `yes/no`, `1/0`; anything else exits 78.
`pl-judges --all` shows the effective switches.

Effects: `--judge X` (and `pl-ask` requests for X) on a switched-off judge
fail before any request is sent (exit 78, with the switch to flip in the
message). `--panel`, `--final-panel`, and `pl-split --per-judge` vote with
the judges that are on — still one judge, one vote, majority wins. With an
even number of judges on, ties are likelier; the tie rule is unchanged.

## Errors and exit codes

Every stage writes errors to stderr as a human line, a `fix:` line, and one
JSON line you can parse:

```
clef-decide: [jev] HTTP 401 from https://api.experientiallabs.ai/v1/systemone: ...
clef-decide: fix: check CLEF_API_KEY / EXPLABS_API_KEY and that the key has access to the model
{"cause": "API key missing, invalid, or not allowed for this model", "endpoint": "https://api.experientiallabs.ai/v1/systemone", "error": "[jev] HTTP 401 from ...", "exit": 77, "fix": "...", "judge": "jev", "model": "jev-latest", "stage": "panel", "status": 401}
```

Fields: `stage`, `judge`, `model`, `status` (HTTP), `endpoint`, `cause`
(likely cause), `fix` (suggested fix), `exit`. Fields that don't apply are
omitted. Panel and skeleton errors keep the existing `[judge]` /
`[skeleton:<id>]` prefixes.

`--verbose` / `-v` on any stage (or `PANEL_LOOP_DEBUG=1`, which `--verbose`
also passes to child stages) prints `debug:` lines: judge, route, model,
question IDs, endpoint, request size, cache hits. The key is never printed.

| Exit | Meaning | Typical cause | What to do |
|---|---|---|---|
| 0 | success / panel has a winner | — | — |
| 1 | internal error | bug in the helper | run with `--verbose`, file a bug |
| 2 | **panel tie — escalate** (unchanged) | judges split twice | re-run once (`--round 2`), then escalate to the owner; never auto-pick |
| 64 | usage | unknown or contradictory flags (`--panel --judge`, `--sot --questions`) | see `--help` |
| 65 | data | invalid questions, bounds, over the 16,384-token budget, bad JSON, HTTP 400/422, partial input to `pl-aggregate`/`pl-tally` | fix the question file or slim the state |
| 66 | no input | state/questions file missing or unreadable | check the paths |
| 69 | unavailable | transport/DNS/TLS error, timeout, HTTP 5xx | check connectivity; re-run deliberately (a timeout may already be charged) |
| 75 | rate limited | HTTP 429 | lower `--jobs`, wait, re-run (with `--state-dir` to keep successes) |
| 76 | protocol | non-JSON response, Luna answer-count mismatch | check `--endpoint`; report the response with `--verbose` |
| 77 | auth | no key anywhere, HTTP 401/403 | set `CLEF_API_KEY` or source `~/.config/explabs/key.env` |
| 78 | config | non-https endpoint, model not on the allowlist, HTTP 404, judge switched off, every judge off, bad switch value | fix the config / `judges.conf` |

Codes 64–78 follow BSD `sysexits.h`. **Changed in this version:** usage and
input errors used to exit 2, the same code as a panel tie; 2 now means only
"tie, escalate", so scripts can tell them apart.

## How a gate works

Three question types:

| Type | Judges | Example |
|------|--------|---------|
| `choice` | picks one named option | should this diff `accept` or get `review`? |
| `noul` | a proposition is true or false | "every case passes" — true or false |
| `score` | rates along an ordered scale | blast radius from "none" to "user funds at risk" |

Questions in one request are independent — never chain answers within a request. Read results with `bin/clef-decide`, which prints each decision plus reported token usage, or `--json` for the raw payload. Probabilities and confidence are estimates, not guarantees.

## The loop

1. **Discovery** — inventory every user-facing feature from the code into a sheet; gate: `coverage` (noul) until nothing is undocumented.
2. **Test generation** — gate each feature's `risk` (score); risk level sets suite depth.
3. **Execution** — run every case; failures gate `triage` (choice: real-defect / bad-test / environment-flake), then `severity` (choice).
4. **Remediation** — each defect gets a minimal proposed diff; gate `fix_review` (choice: accept / review).
5. **Regression** — re-run everything; gate `regression_clean` (noul). New failures loop back to phase 3.
6. **Recursive loop** — write the iteration report (including Luna's fix audits); gate `confidence` (score, `--judge clef`), then the tribunal vote on `done` (`--panel`: one choice question, `done`/`not_done`). Majority wins; a tie re-runs once, a second tie escalates to the owner. If the panel votes `not_done`, loop back to phase 1.

Full workflow: `docs/quality-loop.md`. If a judge doesn't approve, keep trying: re-run the stochastic verdict; if the refusal is stable, strengthen the work or the evidence and re-ask. Never bypass a failed gate silently — and never merge, ship, or call something done on a refused gate without explicit human order.

## Configuration

The key follows standard CLI practices — first source found wins:

1. `--api-key` flag (explicit per-call; prefer the options below — flags can
   leak into shell history and process listings)
2. `$CLEF_API_KEY` environment variable
3. `$EXPERIENTIAL_API_KEY` environment variable (legacy name, still honored), then `$EXPLABS_API_KEY`
   (the name used in `~/.config/explabs/key.env`; load it with `set -a; . ~/.config/explabs/key.env; set +a`)
4. `api_key=` in `~/.config/clef-decide/config`

```ini
# ~/.config/clef-decide/config — KEY=VALUE lines, # comments
api_key=...
# endpoint=https://...        # override the SystemOne endpoint (https:// only)
# luna_endpoint=https://...   # override the decisions endpoint (https:// only)
```

The SystemOne endpoint is likewise overridable: `--endpoint` flag → `$CLEF_ENDPOINT`
→ config file → default `https://api.experientiallabs.ai/v1/systemone`.
Luna's decisions endpoint: `--luna-endpoint` flag → `$LUNA_ENDPOINT`
→ config file → default `https://api.experientiallabs.ai/v1/decisions`.
Missing key everywhere stops the helper with setup instructions — it never
prompts, never logs.

## For agents

`SKILL.md` at the repo root is the drop-in agent skill: hand any agent the
repo URL and it can clone, configure a key, and drive the loop. It covers
the sheet format, all six phases, the question sets, and the operating
rules — everything the README and `docs/` describe, in the form agent
harnesses auto-discover.

## Hard rules (enforced in code)

- POSTs only to the configured endpoints: SystemOne (default
  `https://api.experientiallabs.ai/v1/systemone`) for clef/clef-flash/jev,
  decisions (default `https://api.experientiallabs.ai/v1/decisions`) for Luna.
  No other endpoints.
- Bearer key comes from the standard config chain above, shared by all four
  judges. Missing key stops the helper — it never prompts, never logs.
- SystemOne model is sent as `clef`, `clef-flash`, or `jev-latest` exactly
  (a `:free` suffix spelling is preserved if explicitly selected). Luna's model
  is fixed (`gpt-6-luna-decisions`). Anything else is rejected.
- **No retries, no idempotency key.** An unknown outcome may already be charged, so the helper never re-sends on its own. (A panel tie triggers one fresh vote round, not a retried request.)
- Context budget: 16,384 tokens shared by state + question definitions. Over budget is refused before sending.
- Gateway bounds: ≤32 questions per request, ≤64 options per choice, 2–10 levels per score.
- Panel: one judge one vote, majority wins, double tie escalates with exit 2. Error exits are never 2 (see [Errors and exit codes](#errors-and-exit-codes)).

## Testing

Fully mocked — no network, no key, no charges:

```bash
python3 -m unittest discover -s tests
```

115 tests cover the pipeline stages (stream formats, ordering, sharding, resume), judge switches, structured errors and exit codes, request shape, key handling, context budget, gateway bounds, judge routing, the Luna decisions route, panel voting, flag conflicts, and error surfacing. Live calls are always opt-in: ask before any potentially charged call, and never claim it worked without a real response.

## Layout

- `SKILL.md` — the drop-in agent skill (clone the URL, set a key, drive the loop).
- `bin/clef-decide` — the CLI and the shared core. Python 3, stdlib only, zero dependencies.
- `bin/pl-split`, `bin/pl-ask`, `bin/pl-aggregate`, `bin/pl-tally`, `bin/pl-render`, `bin/pl-judges` — the pipeline stages (see [Pipeline](#pipeline-composable-stages)); `bin/pl_common.py` is their shared plumbing.
- `judges.conf` — judge on/off switches.
- `docs/quality-loop.md` — the six-phase workflow in full.
- `docs/question-sets.md` — copy-paste question sets per phase, with state shapes.
- `examples/launch-readiness/` — worked example: a launch-readiness gate.
- `tests/` — the mocked test suite (`test_clef_mock.py`, `test_claims.py`, `test_config.py`, `test_judges.py`, `test_sot.py`, `test_pipeline.py`, `test_errors_switches.py`).

## License

MIT. See `LICENSE`.
