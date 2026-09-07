# TaskSettlement

A GenLayer Intelligent Contract for **Performance-based Contracting** — payouts are based on the completion of pre-agreed jobs (developing a website, creating content, building features), with performance verified through **automated AI consensus checks**. The sponsor creates tasks with explicit acceptance criteria, the assigned worker submits deliverables, and payment is released only when independent AI validators agree that the work meets the criteria.

## Why this is more than "AI decides X"

| Concern | How it's handled |
|---|---|
| Fund-liveness (no locked stakes) | `expire_task` can void **any** non-terminal task (`open`, `in_progress`, `partial`, `rejected`) after the deadline passes. Once expired, evidence is frozen and the task cannot be resubmitted or reverified — funds are atomically refunded to the sponsor via `_EOA(sponsor).emit_transfer(reward)`. Terminal states (`expired`, `settled`) are immutable. |
| Evidence freezing | Once `verify_performance` runs, the submission's `evidence_frozen` flag is set to `True`. `resubmit_deliverable` explicitly checks this flag and reverts if frozen — deliverable URLs cannot be replaced after verification. `expire_task` also freezes evidence on the submission. |
| Terminal evaluation finality | `reverify_task` is restricted to `partial` or `rejected` tasks only. A `verified` task (payment eligible) or `settled` task (paid) cannot be reopened — the sponsor cannot retroactively change a verification outcome after payment is released. |
| Validator criteria-ratio consistency | The validator does not just check that `decision` and `confidence` match within tolerance — it also enforces that the **criteria counts are consistent with the stated decision**: `pass` requires `met ≥ unmet` and `met > 0`; `fail` requires `unmet > met`; `partial` requires both `met > 0` and `unmet > 0`. The same checks run deterministically post-consensus before writing to storage. |
| Assigned-worker-only submission | `submit_deliverable` and `resubmit_deliverable` both assert `gl.message.sender_address == task.worker` — a third party cannot submit or replace someone else's deliverable. |
| Multi-validator consensus | The non-deterministic block only **acquires** the deliverable (`gl.nondet.web.get`) and **extracts** a structured evaluation (`gl.nondet.exec_prompt`). Validators independently re-acquire and re-evaluate, comparing `decision`, `confidence` (±15 tolerance), and criteria-ratio consistency. Divergent readings are rejected. |
| Clean nondeterministic separation | Only evidence acquisition and LLM extraction occur inside `gl.vm.run_nondet_unsafe`. All storage writes, status transitions, and payment transfers happen in deterministic code after the consensus block returns. |
| Resubmission with cleared verification | When a worker resubmits after rejection, the old verification record is deleted, `evidence_frozen` is reset to `False`, and the task returns to `in_progress` — the next `verify_performance` runs a fresh consensus against the new deliverable. |

## State design

```
Task
  task_id:              str
  title:                str
  description:          str
  acceptance_criteria:  str
  reward:               u256
  sponsor:              Address
  worker:               Address
  status:               str        # "open" | "in_progress" | "verified" | "partial" | "rejected" | "expired" | "settled"
  created_at:           u256
  deadline:             u256

Submission
  task_id:              str
  worker:               Address
  deliverable_url:      str
  summary:              str
  status:               str        # "submitted" | "verified" | "partial" | "rejected"
  submitted_at:         u256
  evidence_frozen:      bool

Verification
  task_id:              str
  decision:             str        # "pass" | "partial" | "fail"
  confidence:           u256
  criteria_met:         DynArray[str]
  criteria_unmet:       DynArray[str]
  reasoning:            str
  verified_by:          Address
  evidence_hash:        bytes      # Keccak256 hash of evidence content for content-addressing

TaskSettlement
  sponsor:              Address
  tasks:                TreeMap[str, Task]
  submissions:          TreeMap[str, Submission]
  verifications:        TreeMap[str, Verification]
  task_count:           i32
  submission_count:     i32
```

## Lifecycle

```
create_task(sponsor) ──► task-0 [open]
      │
      └── submit_deliverable(task-0, url, summary)  (assigned worker only, before deadline)
              │
              └─► task-0 [in_progress], submission-0 [submitted]

verify_performance(task-0)    (anyone can trigger; consensus runs)
      │
      ├── "pass"   ──► task-0 [verified], submission-0 [verified], evidence_frozen = True, evidence_hash = keccak256(content)
      │                    │
      │                    ├── release_payment(task-0)  (sponsor only)
      │                    │       └─► task-0 [settled]; _EOA(worker).emit_transfer(reward)
      │                    │
      │                    └── claim_reward(task-0)    (worker only)
      │                            └─► task-0 [settled]; _EOA(worker).emit_transfer(reward)
      │
      ├── "partial" ──► task-0 [partial], submission-0 [partial], evidence_frozen = True
      │                    │
      │                    ├── reverify_task(task-0)     (sponsor only; resets to in_progress, re-runs consensus)
      │                    └── reject_submission(task-0)  (sponsor only; allows resubmission)
      │
      └── "fail"    ──► task-0 [rejected], submission-0 [rejected], evidence_frozen = True
                           │
                           ├── resubmit_deliverable(task-0, new_url, summary)  (worker only, before deadline)
                           │       └─► evidence_frozen reset, old verification cleared, task-0 [in_progress]
                           │
                           └── reject_submission(task-0)  (sponsor only; confirms rejection)

expire_task(task-0)   (anyone; requires deadline passed; works on open/in_progress/partial/rejected)
      └─► task-0 [expired], evidence_frozen = True, atomic refund: _EOA(sponsor).emit_transfer(reward)
```

## Public interface

Sponsor side:
- `create_task(title, description, acceptance_criteria, reward, deadline_seconds, worker) -> str` — caller becomes the sponsor; returns `task_id`.
- `release_payment(task_id) -> u256` — sponsor only, task must be `verified`; pays the worker via `_EOA(worker).emit_transfer(reward)` and sets task to `settled`.
- `reject_submission(task_id) — sponsor only; resets task to `rejected` for resubmission.
- `reverify_task(task_id) -> dict` — sponsor only; restricted to `partial` or `rejected` tasks; resets verification and re-runs consensus.

Worker side:
- `submit_deliverable(task_id, deliverable_url, summary) — assigned worker only, before deadline; sets task to `in_progress`.
- `resubmit_deliverable(task_id, deliverable_url, summary) — worker only, task must be `rejected`, evidence must not be frozen, before deadline; clears old verification.
- `claim_reward(task_id) -> u256` — worker only, task must be `verified`; atomically pays the worker and sets task to `settled`. Enables atomic claim without sponsor action.

Public triggers:
- `verify_performance(task_id) -> dict` — anyone can call; runs the AI consensus verification. Task must be `in_progress` or `rejected`. Verification record now stores `evidence_hash` (Keccak256 of evidence content) for content-addressing.

Lifecycle:
- `expire_task(task_id) -> u256` — anyone can call after deadline; works on any non-terminal state (`open`, `in_progress`, `partial`, `rejected`); freezes evidence; atomically refunds sponsor via `_EOA(sponsor).emit_transfer(reward)`.

Views:
- `get_task_count() -> i32`, `get_submission_count() -> i32`
- `get_task_status(task_id) -> str`, `get_task_reward(task_id) -> u256`, `get_task_title(task_id) -> str`, `get_task_description(task_id) -> str`, `get_task_criteria(task_id) -> str`, `get_task_deadline(task_id) -> u256`, `get_task_is_expired(task_id) -> bool`
- `get_submission_status(task_id) -> str`, `get_submission_deliverable_url(task_id) -> str`, `get_submission_evidence_frozen(task_id) -> bool`
- `get_verification_decision(task_id) -> str`, `get_verification_decision_dict(task_id) -> dict`, `get_verification_evidence_hash(task_id) -> str`

## The consensus block (the interesting part)

`verify_performance` closes over the submission's `deliverable_url`, the task's `title`, `description`, and `acceptance_criteria`, then runs:

```python
result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
```

- **Leader** (`leader_fn`): fetches the deliverable URL via `gl.nondet.web.get(url)`, sends the page content + acceptance criteria to an LLM via `gl.nondet.exec_prompt(prompt, response_format="json")`, and returns the structured evaluation.
- **Validator** (`validator_fn`): independently re-fetches the same URL, re-runs the same LLM evaluation, then checks:
  1. Leader's result is a valid dict with all required fields.
  2. `decision` matches exactly (pass/partial/fail).
  3. `confidence` agrees within 15-point tolerance.
  4. `criteria_met` and `criteria_unmet` lists are both present.
  5. **Criteria-ratio consistency**: `pass` requires `met > 0` and `met ≥ unmet`; `fail` requires `unmet > met`; `partial` requires both `met > 0` and `unmet > 0`.

Post-consensus, the deterministic code re-validates all fields and criteria ratios before writing to storage — a second guard against drift.

The failure mode is biased toward **not paying**: divergent decisions, confidence outside tolerance, or inconsistent criteria ratios all cause the validator to reject. A task only reaches `verified` (payment-eligible) when a majority of validators agree on the exact same decision and consistent criteria analysis.

## Deployed instance (GenLayer Studio)

| Network | Address | Explorer |
|---|---|---|
| Studio | `0x1855A1E90523361B44728544a7CBd09E7336d4eB` | [View on Explorer](https://explorer-studio.genlayer.com/address/0x1855A1E90523361B44728544a7CBd09E7336d4eB) |

## Testing

41 direct-mode tests covering all write and view methods:

- **Task lifecycle**: create, create multiple, empty title revert, zero reward revert, non-sponsor create revert
- **Submission**: submit deliverable, unauthorized worker revert, double submit revert, deadline revert, non-worker revert, submission count
- **Verification**: pass/partial/fail outcomes, double verify revert, verify without submission revert, evidence hash stored
- **Payment**: full release flow, not-yet-verified revert, wrong-sponsor revert, settled-task-expire revert
- **Claim reward**: full flow, non-worker revert, not-verified revert
- **Resubmission**: reject → resubmit flow, resubmit with frozen evidence revert
- **Reverification**: partial task reverify, rejected task reverify, verified-task reverify revert, open-task reverify revert
- **Expiry**: deadline revert, expire settled task revert, expire in_progress task, expire partial task, atomic refund verification
- **Views**: all 14 view methods tested, verification dict output

```bash
cd task-settlement
pip install -r requirements.txt
pytest tests/direct/ -v
```

## Known limitations (by design)

- The sponsor is trusted to configure honest acceptance criteria. The contract verifies that the deliverable meets the criteria, not that the criteria themselves are reasonable or complete.
- Payment is released in the same transaction that the sponsor calls `release_payment`. A composing contract could add a dispute window or multi-sig approval on top.
- The model is shared: the LLM evaluates both leader and validator prompts from the same model, so multi-source-style cross-checking is simulated through independent prompt runs, not truly independent sources.
- No partial payout: a `partial` decision does not automatically release a fraction of the reward. The sponsor must either reverify, reject (allowing resubmission), or wait for expiry.
- `evidence_hash` is computed from the raw HTML/text content of the deliverable URL. If the same content is hosted at different URLs, the hashes will match — but if the content changes at the same URL (dynamic page), the hash reflects the state at verification time.

## Dependency pin

The contract pins `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` in its `Depends` header. Update this hash if you're targeting a different SDK version.
