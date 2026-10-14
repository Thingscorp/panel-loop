---
name: panel-loop
description: >-
  Run the 6-phase recursive QA loop with a tribunal of AI decision judges.
  The agent does the legwork (reads code, writes the sheet, runs tests,
  implements fixes); the panel makes every judgment call — coverage, dedup,
  risk triage, failure triage, severity, fix approval, regression verdict,
  confidence, and loop termination — through bin/clef-decide. Judges: Clef,
  Clef Flash, Jev, Luna, SubQ; the termination verdict goes to a full tribunal
  vote. Use when the owner says "let the panel drive", "tribunal QA",
  or drops panel-loop into a repo for a full quality pass.
license: MIT
---

# panel-loop

## Purpose

Execute the 6-phase recursive quality loop (discover → test → execute →
fix → regress → repeat) with the tribunal as the driver. The agent is the
hands; the panel is the judge. No gated decision is made on the agent's
judgment alone, and no phase exits without its gate passing.

## Setup

```bash
git clone https://github.com/Thingscorp/panel-loop.git
export CLEF_API_KEY=...   # or api_key= in ~/.config/clef-decide/config
```

Key precedence (standard): `--api-key` flag → `$CLEF_API_KEY` →
`$EXPERIENTIAL_API_KEY` (legacy) → config file. The key is never prompted
for, never logged. SubQ is the exception: its own chain (`$SUBQ_API_KEY` →
`subq_api_key=` in the config file), never the Experiential key. Every claim in this skill's README is backed by a test
in `tests/` — run `python3 -m unittest discover -s tests` to see the
evidence.

## Workflow

**Sheet.** Confirm cwd is the target repo. Create the sheet outside the
repo: `<repo>-qa/features.csv` (xlsx is fine). Never commit a living
sheet into the product tree. Columns (exact names): Feature ID, Feature
Name, User Story, Expected Behaviour, Edge Cases, Test Cases, Current
Status, Defect Count, Severity, Notes, Last Tested Date. IDs `F-001`,
`D-001`, sequential, never reused. Status: `undiscovered` | `documented` |
`tests-written` | `tested` | `failing` | `fixed` | `waived` | `regressed`.

**Driving.** Every gate is one `bin/clef-decide` call. Concrete question sets live in
`docs/question-sets.md` — use them verbatim, filling in the IDs, with the
default judge per phase from the table there (`--judge clef|clef-flash|jev|luna|subq`;
default `clef`). Luna takes choice questions only and POSTs to the decisions
route; her scored audit goes into the iteration report. SubQ runs on its own
provider and key chain (`$SUBQ_API_KEY` or `subq_api_key=` in the config
file); the Experiential key is never consulted for it. Rules for every call:

- State is concise: diffs, test summaries, failure records, iteration
  reports. Never the whole repo. Budget is 16384 tokens shared by state
  and all question definitions; the helper refuses before sending.
- Batch independent questions, up to 32 per request. Questions in one
  request must be independent — never chain (ask triage first, then ask
  severity in the next call with the triage outcome in state).
- Read answers by question ID (`answers.<id>`). Probabilities and
  confidence are estimates, not guarantees.
- The judges are stochastic. If a gate doesn't approve, keep trying:
  re-run the verdict; if the refusal is stable across runs, strengthen
  the work or the evidence and re-run. Never bypass a failed gate
  silently, and never merge, ship, or close on a refused gate without
  the owner's explicit order.
- When a gate's evidence is too large for one request's context budget, or
  its sub-judgments are genuinely independent, use `--sot`: you propose
  the skeleton (one file, point ID → question), each point is judged in
  its own request in parallel, and one goal question aggregates the
  per-point verdicts. Add `--final-panel` when the goal itself deserves
  the tribunal.
  `bin/clef-decide --sot --state-file report.md --skeleton-file skeleton.json --goal-file goal.json`

**Phase 1 — Feature discovery.** Agent inventories from surfaces (routes,
screens, workflows, APIs, configs, permissions, empty/error states,
background jobs), one row per feature, expected behaviour from the code
only. Then gate `coverage` (noul): is anything undocumented? While
false, keep digging and re-ask. Gate `dedup_<pair>` (choice:
merge/keep) for suspected duplicate rows. Exit: `coverage` true.

**Phase 2 — Test generation.** For each row, gate `risk_<F-ID>` (score,
batched up to 32 features per call). Map the level to suite depth: top
level → all 7 categories (happy, error, boundary, invalid input,
permission/security, performance, mobile); next → happy + error +
boundary + invalid; next → happy + error; bottom → happy path only.
Major journeys get an end-to-end case crossing features. Status =
`tests-written`. Exit: every feature has a suite (mechanical check).

**Phase 3 — Execution.** Run every case. On failure, gate
`triage_<T-ID>` (choice: real-defect / bad-test / environment-flake).
Flake → re-run clean; bad test → rewrite the test and re-execute; real
defect → file a D-ID, then gate `severity_<D-ID>` (choice:
critical/high/medium/low) in a separate call. Update the sheet
immediately. Exit: every case executed, every defect documented.

**Phase 4 — Remediation.** Per defect: agent investigates, proposes the
smallest fix as a diff, gates `fix_review_<D-ID>` (choice:
accept/review). Accept → apply, verify locally, status `fixed`. Review →
rework the fix. Waive only with an explicit Notes reason. Exit: every
defect `fixed` or `waived`.

**Phase 5 — Regression.** Re-run every case and every major journey.
Gate `regression_clean` (noul): all green, no new failures, no broken
journeys. New failures become new D-IDs (back to Phase 3). Exit:
`regression_clean` true.

**Phase 6 — Recursive loop.** Write the iteration report next to the
sheet: coverage summary, features tested, defects found, defects fixed,
remaining risks, plus Luna's fix audits. Gate `confidence` (score,
`--judge clef`) and the tribunal vote:
`bin/clef-decide --state-file report.md --questions done.json --panel`
(one choice question, `done`/`not_done`; one judge one vote, majority
wins). A tie re-runs once; a second tie escalates to the owner — never
auto-pick, never call it done on a tie.
The sheet must agree mechanically; both must say go. If the panel votes
`not_done`, loop back to Phase 1. Never declare completion unless all exit criteria
are satisfied. A high confidence score with open highs is a lie — fix
the sheet first.

## Output Contract

- The sheet: current after every fact change. It is the handoff.
- Per iteration: the 6-part report + the panel's confidence score and
  `done` verdict with their probabilities.
- Nothing reaches the owner as "done" without the `done` gate passing
  on the exact sheet state.

## Operating Rules

1. One phase — or one surface — per session. The next session starts
   from the sheet, not the transcript.
2. The agent never substitutes its own judgment for a gated panel
   verdict.
3. No fake evals: a case that didn't run the production path stays
   `tests-written`, never `tested`.
4. Fix nothing without a D-ID on the sheet.
5. Honor repo-specific edit bans: on a docs-only repo, Phase 4 stops
   at documenting defects; each fix needs the owner's explicit order.
6. Keep the helper's own rules: no retries, no whole-repo state,
   independent questions only.
