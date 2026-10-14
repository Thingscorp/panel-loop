# panel-loop

A **tribunal of AI decision helpers for coding agents** — five judges, one verdict. `panel-loop` wraps them in a **recursive six-phase QA loop**: the agent does the legwork (reads code, writes the test sheet, runs cases, applies fixes) and the panel makes every judgment call — coverage, deduplication, risk triage, failure triage, severity, fix review, regression verdict, confidence, and loop termination.

The judges: **Clef** (the thorough reader), **Clef Flash** (the fast 9B for high-volume gates), **Jev** (risk triage and adversarial second opinions), **Luna** (structured scored audits), **SubQ** (SubQ Snap, SystemOne-compatible fast decisions on a separate provider). Routine gates go to the best-fit judge; the termination verdict goes to the full tribunal — **one judge, one vote, majority wins**.

The rule of the loop: **the agent is the hands, the panel is the judge.** No gated decision is made on the agent's judgment alone, and no phase exits without its gate passing. None of these are chat models — they judge; they do not chat, code, or complete.

## Verified claims

Every claim below is backed by a test in `tests/` — fully mocked, no network, no key, no charges. Run them yourself: `python3 -m unittest discover -s tests` (79/79 pass, ~0.1s).

| Claim | Backing data |
|---|---|
| POSTs only to the three known endpoints | `test_request_shape` asserts the exact URL and POST method; `test_endpoints_are_the_only_real_urls_in_source` scans `bin/clef-decide` for any other URL — none exists |
| SubQ is a fifth judge on its own endpoint, with its own key chain | `test_subq_posts_to_subq_endpoint_with_systemone_shape`, `test_subq_key_missing_raises`, `test_subq_endpoint_precedence`, `test_panel_fans_out_to_five_judges`; `test_model_allowlist_pinned` pins `JUDGES` and `SUBQ_MODEL` |
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

`--judge` selects which judge drives the gate (default `clef`). Clef, Clef Flash, Jev, and Luna share the one Experiential API key; SubQ is a separate provider with its own key (`$SUBQ_API_KEY` or `subq_api_key=` in the config file).

```bash
bin/clef-decide --state-file state.md --questions questions.json --judge jev
bin/clef-decide --state-file state.md --questions questions.json --model clef-flash
```

- `clef` — the full-size decision model. The thorough reader; keep it for the highest-stakes verdicts.
- `clef-flash` — the 9B variant ([model card](https://huggingface.co/Cloudflare/clef-flash)): same SystemOne wire, no free-form generation, built for speed — median request latency 38.8ms vs 209.3ms for `clef` on the Decision Index suite. Pick it for cheap, fast, high-volume gates.
- `jev` — SystemOne model `jev-latest`. The risk and adversarial judge: risk triage, second opinions, catching what the others miss.
- `luna` — the `/v1/decisions` route (model `gpt-6-luna-decisions`, fixed). Structured scored audits with rationale — the audit trail. Choice questions only; use a SystemOne judge for `noul`/`score`.
- `subq` — SubQ Snap (`subq-snap-preview`, fixed) on `https://api.subq.ai/v1/systemone`, a SystemOne-compatible endpoint. Fast triage judge; supports `choice`, `noul`, and `score` like the other SystemOne judges. Needs its own key: `$SUBQ_API_KEY` or `subq_api_key=` in `~/.config/clef-decide/config`.

Only `clef`, `clef-flash`, and `jev-latest` (plus a `:free` suffix spelling, preserved verbatim) are accepted on the SystemOne route — anything else is rejected, since none of these are chat models. Request shape reference: the [SystemOne quickstart](https://docs.typesafe.ai/introduction/quickstart).

### Panel votes

`--panel` fans one choice question out to all five judges in parallel and applies the tribunal rule: **one judge, one vote; majority wins.** A tie re-runs once (new votes, not retried requests); a second tie escalates to the owner — the helper exits 2 and never auto-picks.

```bash
bin/clef-decide --state-file report.md --questions done.json --panel
```

`done.json` must hold exactly one choice question, e.g. `{"done": {"type": "choice", "instructions": "...", "criteria": {"done": "...", "not_done": "..."}}}`. Use the panel for the loop-termination gate (phase 6) and other high-stakes verdicts; routine gates stay single-judge. `--panel` cannot be combined with `--judge` or `--model` — it always uses all five judges, and contradictory flags fail loudly instead of being silently ignored.

### Skeleton-of-judgment

`--sot` implements skeleton-of-thought for decision models: the agent proposes a skeleton of independent sub-questions, each point is judged in its own request in parallel, and a final gate aggregates the per-point verdicts into one goal question. The judges judge; they do not invent the skeleton — that stays the agent's job, per the loop's rule.

```bash
bin/clef-decide --sot --state-file report.md --skeleton-file skeleton.json --goal-file goal.json
bin/clef-decide --sot --state-file report.md --skeleton-file skeleton.json --goal-file goal.json --final-panel
```

- `--skeleton-file`: map of point ID → question (choice/noul/score), ≤32. One request per point, so each point sees the full state under its own 16,384-token context budget instead of sharing one request's budget.
- `--goal-file`: exactly one question, the final verdict. Its state is the original state plus the per-point verdicts, each rendered as `point_id -> verdict`.
- `--final-panel`: the goal goes to the full tribunal (one judge, one vote, majority wins); a tie re-runs once, a second tie exits 2. Without it, the goal goes to `--judge` (default `clef`).
- Expansion uses `--judge`/`--model`; with `--final-panel` the expansion still uses that judge while the verdict uses all five. `--sot` cannot be combined with `--questions` or `--panel` — contradictory flags fail loudly.
- A failing point names itself: `[skeleton:<point_id>] <error>`.

Use `--sot` when a gate's evidence is too large for one request's context budget, or when the sub-judgments are genuinely independent and deserve the full state each. When the questions fit one request and share evidence, the plain batched form is cheaper (fewer requests).

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
3. `$EXPERIENTIAL_API_KEY` environment variable (legacy name, still honored)
4. `api_key=` in `~/.config/clef-decide/config`

```ini
# ~/.config/clef-decide/config — KEY=VALUE lines, # comments
api_key=...
# subq_api_key=...            # SubQ's own key (or $SUBQ_API_KEY)
# endpoint=https://...        # override the SystemOne endpoint (https:// only)
# luna_endpoint=https://...   # override the decisions endpoint (https:// only)
# subq_endpoint=https://...   # override the SubQ endpoint (https:// only)
```

The SystemOne endpoint is likewise overridable: `--endpoint` flag → `$CLEF_ENDPOINT`
→ config file → default `https://api.experientiallabs.ai/v1/systemone`.
Luna's decisions endpoint: `--luna-endpoint` flag → `$LUNA_ENDPOINT`
→ config file → default `https://api.experientiallabs.ai/v1/decisions`.
SubQ's endpoint: `$SUBQ_ENDPOINT` → config file → default
`https://api.subq.ai/v1/systemone`. SubQ's key chain is separate:
`$SUBQ_API_KEY` → `subq_api_key=` in the config file; the Experiential key
is never consulted for the SubQ judge.
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
  decisions (default `https://api.experientiallabs.ai/v1/decisions`) for Luna,
  SubQ (default `https://api.subq.ai/v1/systemone`) for SubQ.
  No other endpoints.
- Bearer key comes from the standard config chain above, shared by Clef,
  Clef Flash, Jev, and Luna. SubQ uses its own key chain (`$SUBQ_API_KEY` or
  `subq_api_key=` in the config file) and never consults the Experiential
  key. Missing key stops the helper — it never prompts, never logs.
- SystemOne model is sent as `clef`, `clef-flash`, or `jev-latest` exactly
  (a `:free` suffix spelling is preserved if explicitly selected). Luna's model
  is fixed (`gpt-6-luna-decisions`); SubQ's is fixed (`subq-snap-preview`).
  Anything else is rejected.
- **No retries, no idempotency key.** An unknown outcome may already be charged, so the helper never re-sends on its own. (A panel tie triggers one fresh vote round, not a retried request.)
- Context budget: 16,384 tokens shared by state + question definitions. Over budget is refused before sending.
- Gateway bounds: ≤32 questions per request, ≤64 options per choice, 2–10 levels per score.
- Panel: one judge one vote, majority wins, double tie escalates with exit 2.

## Testing

Fully mocked — no network, no key, no charges:

```bash
python3 -m unittest discover -s tests
```

79 tests cover request shape, key handling, context budget, gateway bounds, judge routing, the Luna decisions route, the SubQ route and its key isolation, panel voting, skeleton-of-judgment, flag conflicts, and error surfacing. Live calls are always opt-in: ask before any potentially charged call, and never claim it worked without a real response.

## Layout

- `SKILL.md` — the drop-in agent skill (clone the URL, set a key, drive the loop).
- `bin/clef-decide` — the CLI. Python 3, stdlib only, zero dependencies.
- `docs/quality-loop.md` — the six-phase workflow in full.
- `docs/question-sets.md` — copy-paste question sets per phase, with state shapes.
- `examples/launch-readiness/` — worked example: a launch-readiness gate.
- `llms.txt` — the short machine-readable summary for LLMs and agents.
- `tests/` — the mocked test suite (`test_clef_mock.py`, `test_claims.py`, `test_config.py`, `test_judges.py`, `test_sot.py`).

## License

MIT. See `LICENSE`.
