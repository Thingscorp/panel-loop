# The panel-loop quality process

Six phases, recursive. The agent does the legwork; the panel makes every
judgment call. Clef is one judge on the panel, not the panel: routine gates
go to the best-fit judge (Clef, Clef Flash, Jev, or Luna, per the default
judge table in `docs/question-sets.md`), and the loop-termination verdict
goes to the full tribunal. No gated decision is made on the agent's
judgment alone, and no phase exits without its gate passing.

## Setup

Work in the target repo's checkout. Keep the tracking sheet **outside** the
repo — e.g. `<repo>-qa/features.csv` (a spreadsheet works too). Never commit
a living sheet into the product tree.

Sheet columns (exact names):

| Column | Purpose |
|---|---|
| Feature ID | `F-001`, `D-001`, sequential, never reused |
| Feature Name | short label |
| User Story | who does what |
| Expected Behaviour | derived from the code only, never invented |
| Edge Cases | known boundary conditions |
| Test Cases | the suite written for this feature |
| Current Status | `undiscovered` / `documented` / `tests-written` / `tested` / `failing` / `fixed` / `waived` / `regressed` |
| Defect Count | defects filed against this feature |
| Severity | highest open severity |
| Notes | waivers and context |
| Last Tested Date | when the suite last ran green |

## Driving the gates

Every gate is one `bin/clef-decide` call against the question sets in
`docs/question-sets.md`, with the IDs filled in. Pick the judge with
`--judge clef|clef-flash|jev|luna` (or `--model clef-flash` for the fast
9B variant); the default judge is `clef`, and each phase has a best-fit
default in the table in `docs/question-sets.md`. The termination verdict
uses `--panel` instead: all four judges vote, majority wins. Rules for
every call:

- **State is concise.** Diffs, test summaries, failure records, iteration
  reports. Never the whole repo. The helper refuses anything over the
  16,384-token shared budget.
- **Batch independent questions** — up to 32 per request. Questions in one
  request must be independent: never chain. Ask triage first, then ask
  severity in the next call with the triage outcome in state.
- **Read answers by question ID** (`answers.<id>`). Probabilities and
  confidence are estimates, not guarantees.
- **The judges are stochastic.** If a gate doesn't approve, keep trying:
  re-run the verdict; if the refusal is stable across runs, strengthen the
  work or the evidence and re-run. Never bypass a failed gate silently, and
  never merge, ship, or close on a refused gate without explicit human
  order.

## Phase 1 — Feature discovery

Inventory features from every surface: routes, screens, workflows, APIs,
configs, permissions, empty and error states, background jobs. One row per
feature; expected behaviour comes from the code only.

- Gate `coverage` (noul): is anything still undocumented? While false, keep
  digging and re-ask.
- Gate `dedup_<A>_<B>` (choice: merge / keep) for each suspected duplicate
  pair of rows.

Exit: `coverage` true.

## Phase 2 — Test generation

For each row, gate `risk_<F-ID>` (score), batching up to 32 features per
call. Map the level to suite depth:

- **Highest** — all 7 categories: happy, error, boundary, invalid input,
  permission/security, performance, mobile.
- **Next** — happy + error + boundary + invalid input.
- **Next** — happy + error.
- **Lowest** — happy path only.

Major journeys get an end-to-end case crossing features regardless of level.
Status becomes `tests-written`. Exit: every feature has a suite (mechanical
check).

## Phase 3 — Execution

Run every case. On failure, gate `triage_<T-ID>` (choice):

- `real-defect` — the production code behaves incorrectly; file a Defect ID,
  then gate `severity_<D-ID>` (choice: critical / high / medium / low) in a
  **separate** call.
- `bad-test` — the test itself is wrong; rewrite the test and re-execute.
- `environment-flake` — re-run in a clean environment.

Update the sheet immediately on every outcome. Exit: every case executed,
every defect documented.

## Phase 4 — Remediation

Per defect: investigate, propose the smallest fix as a diff, gate
`fix_review_<D-ID>` (choice):

- `accept` — apply, verify locally, mark `fixed`.
- `review` — rework the fix and re-ask.

Waive only with an explicit reason in Notes. On a docs-only repo this phase
stops at documenting defects; each fix needs the owner's explicit order.
Exit: every defect `fixed` or `waived`.

## Phase 5 — Regression

Re-run every case and every major journey. Gate `regression_clean` (noul):
all green, no new failures, no broken journeys. New failures become new
D-IDs and go back to Phase 3. Exit: `regression_clean` true.

## Phase 6 — Recursive loop

Write the iteration report next to the sheet: coverage summary, features
tested, defects found, defects fixed, remaining risks, plus Luna's fix
audits. Gate `confidence` (score, single judge) and the tribunal vote on
`done` (`--panel`, one choice question: `done` / `not_done` — **all** exit
criteria true: no undiscovered features, no failing tests, no open
critical/high defects, no unresolved UX issues, no incomplete journeys).
One judge, one vote; majority wins. A tie re-runs once; a second tie
escalates to the owner, never auto-pick. The sheet must agree mechanically;
both must say go.

If the panel votes `not_done`, loop back to Phase 1. A high confidence
score with open highs is a lie — fix the sheet first. Nothing reaches the
owner as "done" without the `done` gate passing on the exact sheet state.

## Operating rules

1. One phase — or one surface — per session. The next session starts from
   the sheet, not the transcript.
2. The agent never substitutes its own judgment for a gated panel verdict.
3. No fake evals: a case that didn't run the production path stays
   `tests-written`, never `tested`.
4. Fix nothing without a D-ID on the sheet.
5. Honor repo-specific edit bans.
6. Keep the helper's own rules: no retries, no whole-repo state,
   independent questions only.
