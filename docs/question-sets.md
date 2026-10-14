# Panel question sets for the quality loop

Use these verbatim, filling in the IDs. The sets are judge-agnostic: any
judge on the panel (Clef, Clef Flash, Jev, Luna, SubQ) can answer any set, and
the default judge table below assigns each gate to its best-fit judge.
State shapes are suggestions — keep state concise (diffs, summaries,
records, reports), never the repo.

Call pattern:

```bash
bin/clef-decide --state-file state.json --questions questions.json
bin/clef-decide --state-file state.json --questions questions.json --judge jev
bin/clef-decide --state-file report.md --questions done.json --panel
```

`--model` is sent exactly as given, so a `clef:free` spelling survives verbatim.

## Default judge per phase

Specialize: one judge, one strength. Routine gates stay single-judge;
the panel (`--panel`) is for the loop-termination verdict and other
high-stakes calls.

| Phase | Gate | Judge | Why |
|---|---|---|---|
| 1 discovery | `coverage` (noul) | `clef` | nuanced judgment about what's missing |
| 1 discovery | `dedup_<pair>` (choice) | `clef-flash` | high-volume binary calls, speed wins |
| 2 risk triage | `risk_<F-ID>` (score) | `jev` | risk scoring is Jev's home turf |
| 3 triage | `triage_<T-ID>` (choice) | `clef-flash` | high-volume three-way classification |
| 3 severity | `severity_<D-ID>` (choice) | `clef` | severity needs the thorough reader |
| 4 fix review | `fix_review_<D-ID>` (choice) | `clef` | the thorough reader reviews the diff |
| 4 fix audit | (record) | `luna` | scored audit appended to the iteration report |
| 5 regression | `regression_clean` (noul) | `clef-flash` | fast re-verdict |
| 5 adversarial | `slipped_<area>` (choice) | `jev` | "what could still be broken?" |
| 6 termination | `done` (choice) | `--panel` | one judge one vote, majority wins |

Luna takes choice questions only; give her the fix audit as a choice
(`sound` / `needs_work`) and keep her scored rationale in the report.

---

## Phase 1 — discovery gates

State: `{repo, surfaces_covered[], feature_count, features: [{id, name,
surface}], discovery_method, areas_not_yet_examined[]}`

```json
{
  "coverage": {
    "type": "noul",
    "instructions": "Judge the proposition: this inventory documents every identifiable user-facing feature, screen, route, workflow, API interaction, configuration option, and business process in the codebase — nothing remains undocumented. The inventory summary and the areas not yet examined are in state.",
    "criteria": {
      "true": "The inventory is complete; no screen, route, workflow, API, or config is missing",
      "false": "At least one identifiable feature, screen, route, workflow, API, or config is missing or only partially documented"
    }
  }
}
```

For each suspected duplicate pair (`<A>`/`<B>` are the two Feature IDs):

```json
{
  "dedup_<A>_<B>": {
    "type": "choice",
    "instructions": "Two inventory rows may describe the same feature. Their names, user stories, and expected behaviour are in state. Decide whether to merge them.",
    "criteria": {
      "merge": "Both rows describe the same user-facing feature; merge into one row",
      "keep": "They are distinct features; keep both rows"
    }
  }
}
```

Exit: `coverage` true.

---

## Phase 2 — risk triage (batch up to 32 features per call)

One question per feature; `<F>` is the Feature ID. State per feature:
`{id, name, user_story, expected_behaviour, edge_cases, dependencies}`.

```json
{
  "risk_<F>": {
    "type": "score",
    "instructions": "Rate the defect risk of this feature from its complexity, dependencies, and user impact (details in state).",
    "criteria": [
      "Negligible: trivial, isolated, low impact",
      "Low: simple logic, few dependencies",
      "Medium: meaningful logic or several dependencies",
      "High: complex logic, critical path, or data-integrity risk"
    ]
  }
}
```

Suite-depth mapping: High → all 7 categories (happy, error, boundary,
invalid input, permission/security, performance, mobile); Medium →
happy + error + boundary + invalid input; Low → happy + error;
Negligible → happy path only. Major journeys get an end-to-end case
crossing features regardless of level.

---

## Phase 3 — failure triage, then severity (two separate calls)

`<T>` is a failure record ID. State: `{test_case, feature_id,
reproduction_steps, expected_result, actual_result, environment}`.

```json
{
  "triage_<T>": {
    "type": "choice",
    "instructions": "A test case failed. The failure record is in state. Judge what the failure is.",
    "criteria": {
      "real-defect": "The production code behaves incorrectly; file a Defect ID",
      "bad-test": "The test itself is wrong (bad expectation, wrong setup); rewrite the test and re-execute",
      "environment-flake": "The failure comes from the environment (missing dep, port conflict, timing); re-run in a clean environment"
    }
  }
}
```

Only for `real-defect` outcomes, in a follow-up call with the triage
outcome in state (`<D>` is the Defect ID):

```json
{
  "severity_<D>": {
    "type": "choice",
    "instructions": "Assign severity to this confirmed defect. The defect record is in state.",
    "criteria": {
      "critical": "Data loss, security breach, or core journey completely broken",
      "high": "Major feature broken or broken for a large user segment; workaround painful",
      "medium": "Feature degraded but usable; workaround exists",
      "low": "Cosmetic, typo-level, or edge case with minimal impact"
    }
  }
}
```

---

## Phase 4 — fix review

`<D>` is the Defect ID. State: `{defect_record, proposed_diff,
root_cause, files_touched}`.

```json
{
  "fix_review_<D>": {
    "type": "choice",
    "instructions": "Review the proposed fix for this defect. The defect record and the full diff are in state.",
    "criteria": {
      "accept": "Smallest safe fix: addresses the root cause, no scope creep, no new risk",
      "review": "Needs rework: too broad, misses the root cause, or introduces new risk"
    }
  }
}
```

`accept` → apply, verify locally, mark `fixed`. `review` → rework the
fix and re-ask. On a docs-only repo this phase stops at documenting
defects; each fix needs the owner's explicit order.

---

## Phase 5 — regression verdict

State: `{cases_rerun, passed, failed[], journeys_rerun[], new_failures[]}`.

```json
{
  "regression_clean": {
    "type": "noul",
    "instructions": "Judge the proposition: the regression re-run shows every test case passing, every major end-to-end journey green, and no new failures introduced by the fixes. The results are in state.",
    "criteria": {
      "true": "All green, no new failures, no broken journeys",
      "false": "Something still fails or a journey is broken"
    }
  }
}
```

New failures become new D-IDs and go back to Phase 3.

---

## Phase 6 — loop control (independent — same request is fine)

Single-judge form: `confidence` and `done` in one request to one judge.
For the termination verdict itself, prefer the panel variant below.

State: the iteration report — `{coverage_summary, features_tested,
defects_found[], defects_fixed[], remaining_risks[], sheet_summary:
{open_critical, open_high, failing_tests, undocumented_features}}`.

```json
{
  "confidence": {
    "type": "score",
    "instructions": "Rate overall confidence in this codebase's quality after this iteration, from the iteration report in state.",
    "criteria": [
      "0-20: major gaps remain",
      "20-40: many areas untested or defects open",
      "40-60: core covered, notable risks remain",
      "60-80: broad coverage, minor issues only",
      "80-100: complete coverage, no meaningful risks"
    ]
  },
  "done": {
    "type": "noul",
    "instructions": "Judge the proposition: ALL of the following are true — no undiscovered features, no failing tests, no open critical defects, no open high-severity defects, no unresolved UX issues, no incomplete user journeys. The iteration report and sheet summary are in state.",
    "criteria": {
      "true": "Every exit criterion is satisfied; the loop may stop",
      "false": "At least one exit criterion is not satisfied; the loop must continue"
    }
  }
}
```

If `done` is false, loop back to Phase 1. If the gate refuses `done`,
keep trying: re-run the verdict; if the refusal is stable, strengthen
the work (more discovery, more fixes) and re-ask. The sheet must agree
mechanically — both must say go before anything is called done.

### Panel variant (recommended for termination)

`--panel` needs exactly one choice question. Reframe `done` as a choice
and let the tribunal vote — one judge, one vote, majority wins:

```json
{
  "done": {
    "type": "choice",
    "instructions": "ALL of the following are true — no undiscovered features, no failing tests, no open critical defects, no open high-severity defects, no unresolved UX issues, no incomplete user journeys. The iteration report and sheet summary are in state. Vote done only if every criterion is satisfied.",
    "criteria": {
      "done": "Every exit criterion is satisfied; the loop may stop",
      "not_done": "At least one exit criterion is not satisfied; the loop must continue"
    }
  }
}
```

```bash
bin/clef-decide --state-file report.md --questions done.json --panel
```

A tie re-runs once; a second tie escalates to the owner (exit 2) —
never auto-pick, never call it done on a tie.
