# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json as _json
import datetime as _dt

DEFAULT_DEADLINE_SECONDS = 604800  # 7 days
CONFIDENCE_TOLERANCE = 15


def _current_timestamp() -> u256:
    return u256(int(_dt.datetime.now(_dt.timezone.utc).timestamp()))


def _validate_url(url: str) -> str:
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise gl.vm.UserError(f"invalid URL: {url!r}")
    return url


def _coerce_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, str):
        return Address(value)
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(bytes(value))


def _parse_json_result(data) -> dict | None:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            s = data.strip()
            if s.startswith("```"):
                first_newline = s.find("\n")
                s = s[first_newline + 1:] if first_newline != -1 else s[3:]
                if s.endswith("```"):
                    s = s[:-3]
                s = s.strip()
            parsed = _json.loads(s)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def _validate_verification_fields(result: dict) -> bool:
    if "decision" not in result:
        return False
    if result["decision"] not in ("pass", "partial", "fail"):
        return False
    if not isinstance(result.get("confidence"), (int, float)):
        return False
    if not (0 <= result["confidence"] <= 100):
        return False
    if not isinstance(result.get("criteria_met"), list):
        return False
    if not isinstance(result.get("criteria_unmet"), list):
        return False
    if not isinstance(result.get("reasoning"), str):
        return False
    if len(result["reasoning"].strip()) == 0:
        return False
    return True


def _build_verification_prompt(title: str, description: str, criteria: str, evidence_text: str) -> str:
    return f"""
You are a performance verifier for a task-based contract. Based on the deliverable evidence provided,
determine whether the work meets the pre-agreed acceptance criteria.

Task: {title}
Description: {description}
Acceptance Criteria: {criteria}

Deliverable Evidence:
{evidence_text}

Verify the deliverable and return JSON with the following fields:
{{
    "decision": "pass" | "partial" | "fail",
    "confidence": 0-100,
    "criteria_met": list of strings describing which criteria were met,
    "criteria_unmet": list of strings describing which criteria were not met,
    "reasoning": "your detailed reasoning explaining the decision"
}}

Decision rules:
- "pass" means all major acceptance criteria are clearly satisfied by the deliverable
- "partial" means some criteria are met but not all
- "fail" means the deliverable does not satisfy the acceptance criteria

Respond only with valid JSON, no markdown formatting, no extra text.
"""


@allow_storage
class TaskData:
    task_id: str
    title: str
    description: str
    acceptance_criteria: str
    reward: u256
    sponsor: Address
    worker: Address
    status: str
    created_at: u256
    deadline: u256

    def __init__(
        self,
        task_id: str,
        title: str,
        description: str,
        acceptance_criteria: str,
        reward: u256,
        sponsor: Address,
        worker: Address,
        created_at: u256,
        deadline: u256,
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.acceptance_criteria = acceptance_criteria
        self.reward = reward
        self.sponsor = sponsor
        self.worker = worker
        self.status = "open"
        self.created_at = created_at
        self.deadline = deadline

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "reward": int(self.reward),
            "sponsor": str(self.sponsor),
            "worker": str(self.worker),
            "status": self.status,
            "created_at": int(self.created_at),
            "deadline": int(self.deadline),
        }


@allow_storage
class SubmissionData:
    task_id: str
    worker: Address
    deliverable_url: str
    summary: str
    status: str
    submitted_at: u256
    evidence_frozen: bool

    def __init__(
        self,
        task_id: str,
        worker: Address,
        deliverable_url: str,
        summary: str,
        submitted_at: u256,
    ):
        self.task_id = task_id
        self.worker = worker
        self.deliverable_url = deliverable_url
        self.summary = summary
        self.status = "submitted"
        self.submitted_at = submitted_at
        self.evidence_frozen = False

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "worker": str(self.worker),
            "deliverable_url": self.deliverable_url,
            "summary": self.summary,
            "status": self.status,
            "submitted_at": int(self.submitted_at),
            "evidence_frozen": self.evidence_frozen,
        }


@allow_storage
class VerificationData:
    task_id: str
    decision: str
    confidence: u256
    criteria_met: DynArray[str]
    criteria_unmet: DynArray[str]
    reasoning: str
    verified_by: Address

    def __init__(
        self,
        task_id: str,
        decision: str,
        confidence: u256,
        criteria_met: DynArray[str],
        criteria_unmet: DynArray[str],
        reasoning: str,
        verified_by: Address,
    ):
        self.task_id = task_id
        self.decision = decision
        self.confidence = confidence
        self.criteria_met = criteria_met
        self.criteria_unmet = criteria_unmet
        self.reasoning = reasoning
        self.verified_by = verified_by

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "decision": self.decision,
            "confidence": int(self.confidence),
            "criteria_met": self.criteria_met,
            "criteria_unmet": self.criteria_unmet,
            "reasoning": self.reasoning,
            "verified_by": str(self.verified_by),
        }


class TaskSettlement(gl.Contract):
    sponsor: Address
    tasks: TreeMap[str, TaskData]
    submissions: TreeMap[str, SubmissionData]
    verifications: TreeMap[str, VerificationData]
    task_count: i32
    submission_count: i32

    def __init__(self):
        self.sponsor = gl.message.sender_address
        self.task_count = 0
        self.submission_count = 0

    @gl.public.view
    def get_task_count(self) -> i32:
        return self.task_count

    @gl.public.view
    def get_submission_count(self) -> i32:
        return self.submission_count

    @gl.public.view
    def get_task_status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return ""
        return task.status

    @gl.public.view
    def get_task_reward(self, task_id: str) -> u256:
        task = self.tasks.get(task_id)
        if task is None:
            return 0
        return task.reward

    @gl.public.view
    def get_task_title(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return ""
        return task.title

    @gl.public.view
    def get_task_description(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return ""
        return task.description

    @gl.public.view
    def get_task_criteria(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return ""
        return task.acceptance_criteria

    @gl.public.view
    def get_task_deadline(self, task_id: str) -> u256:
        task = self.tasks.get(task_id)
        if task is None:
            return 0
        return task.deadline

    @gl.public.view
    def get_task_is_expired(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        return _current_timestamp() > task.deadline and task.status not in ("expired", "settled")

    @gl.public.view
    def get_submission_status(self, task_id: str) -> str:
        sub = self.submissions.get(task_id)
        if sub is None:
            return ""
        return sub.status

    @gl.public.view
    def get_submission_deliverable_url(self, task_id: str) -> str:
        sub = self.submissions.get(task_id)
        if sub is None:
            return ""
        return sub.deliverable_url

    @gl.public.view
    def get_submission_evidence_frozen(self, task_id: str) -> bool:
        sub = self.submissions.get(task_id)
        if sub is None:
            return False
        return sub.evidence_frozen

    @gl.public.view
    def get_verification_decision(self, task_id: str) -> str:
        ver = self.verifications.get(task_id)
        if ver is None:
            return ""
        return ver.decision

    @gl.public.view
    def get_verification_decision_dict(self, task_id: str) -> dict:
        ver = self.verifications.get(task_id)
        if ver is None:
            return {}
        return ver.as_dict()

    @gl.public.view
    def get_contract_balance(self) -> u256:
        return self.balance

    @gl.public.write.payable
    def create_task(
        self,
        title: str,
        description: str,
        acceptance_criteria: str,
        deadline_seconds: u256,
        worker: Address,
    ) -> str:
        assert gl.message.sender_address == self.sponsor, "Only sponsor can create tasks"
        assert len(title.strip()) > 0, "Title cannot be empty"
        assert len(description.strip()) > 0, "Description cannot be empty"
        assert len(acceptance_criteria.strip()) > 0, "Acceptance criteria cannot be empty"
        assert deadline_seconds > 0, "Deadline must be positive"

        reward = gl.message.value
        assert reward > 0, "Reward must be greater than zero (send value with transaction)"

        now = _current_timestamp()
        deadline = now + deadline_seconds

        task_id = str(self.task_count)
        self.task_count = self.task_count + 1

        self.tasks[task_id] = TaskData(
            task_id,
            title,
            description,
            acceptance_criteria,
            reward,
            gl.message.sender_address,
            _coerce_address(worker),
            now,
            deadline,
        )

        return task_id

    @gl.public.write
    def submit_deliverable(
        self,
        task_id: str,
        deliverable_url: str,
        summary: str,
    ) -> None:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status == "open", "Task is not open"

        now = _current_timestamp()
        assert now <= task.deadline, "Task deadline has passed"

        assert gl.message.sender_address == task.worker, \
            "Only the assigned worker can submit deliverables"

        deliverable_url = _validate_url(deliverable_url)
        assert len(summary.strip()) > 0, "Summary cannot be empty"

        assert task_id not in self.submissions, "Submission already exists for this task"

        self.submissions[task_id] = SubmissionData(
            task_id,
            gl.message.sender_address,
            deliverable_url,
            summary,
            now,
        )

        self.submission_count = self.submission_count + 1
        task.status = "in_progress"

    @gl.public.write
    def verify_performance(self, task_id: str) -> dict:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status in ("in_progress", "rejected"), \
            "Task must be in_progress or rejected to verify"
        assert task_id not in self.verifications, "Task already verified"

        sub = self.submissions.get(task_id)
        assert sub is not None, "No submission found for this task"

        deliverable_url = sub.deliverable_url
        title = task.title
        description = task.description
        criteria = task.acceptance_criteria

        def leader_fn() -> dict:
            response = gl.nondet.web.get(deliverable_url)
            evidence_text = response.body.decode("utf-8")

            prompt = _build_verification_prompt(title, description, criteria, evidence_text)
            result = gl.nondet.exec_prompt(prompt, response_format="json")
            return result

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False

            leader_data = leader_result.calldata
            parsed_leader = _parse_json_result(leader_data)
            if parsed_leader is None:
                return False

            if not _validate_verification_fields(parsed_leader):
                return False

            validator_response = gl.nondet.web.get(deliverable_url)
            validator_text = validator_response.body.decode("utf-8")

            validator_prompt = _build_verification_prompt(title, description, criteria, validator_text)
            validator_result = gl.nondet.exec_prompt(validator_prompt, response_format="json")
            parsed_validator = _parse_json_result(validator_result)
            if parsed_validator is None:
                return False

            if not _validate_verification_fields(parsed_validator):
                return False

            if parsed_leader["decision"] != parsed_validator["decision"]:
                return False

            if abs(parsed_leader["confidence"] - parsed_validator["confidence"]) > CONFIDENCE_TOLERANCE:
                return False

            if not parsed_leader["criteria_met"] and not parsed_leader["criteria_unmet"]:
                return False

            n_met = len(parsed_leader["criteria_met"])
            n_unmet = len(parsed_leader["criteria_unmet"])

            if parsed_leader["decision"] == "pass":
                if n_met == 0:
                    return False
                if n_unmet > n_met:
                    return False
            elif parsed_leader["decision"] == "fail":
                if n_unmet == 0:
                    return False
                if n_met > n_unmet:
                    return False
            elif parsed_leader["decision"] == "partial":
                if n_met == 0 or n_unmet == 0:
                    return False

            return True

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        parsed_result = _parse_json_result(result)
        assert parsed_result is not None, "Failed to parse verification result"
        assert _validate_verification_fields(parsed_result), "Invalid verification fields"

        n_met = len(parsed_result["criteria_met"])
        n_unmet = len(parsed_result["criteria_unmet"])
        if parsed_result["decision"] == "pass":
            assert n_met > 0, "Pass requires at least one criteria met"
            assert n_unmet <= n_met, "Pass requires met >= unmet"
        elif parsed_result["decision"] == "fail":
            assert n_unmet > 0, "Fail requires at least one criteria unmet"
            assert n_met < n_unmet or n_met == 0, "Fail requires unmet > met"
        elif parsed_result["decision"] == "partial":
            assert n_met > 0, "Partial requires at least one criteria met"
            assert n_unmet > 0, "Partial requires at least one criteria unmet"

        ver_data = VerificationData(
            task_id,
            parsed_result["decision"],
            u256(parsed_result["confidence"]),
            parsed_result["criteria_met"],
            parsed_result["criteria_unmet"],
            parsed_result["reasoning"],
            gl.message.sender_address,
        )
        self.verifications[task_id] = ver_data

        task = self.tasks[task_id]
        sub = self.submissions[task_id]

        if parsed_result["decision"] == "pass":
            task.status = "verified"
            sub.status = "verified"
        elif parsed_result["decision"] == "partial":
            task.status = "partial"
            sub.status = "partial"
        else:
            task.status = "rejected"
            sub.status = "rejected"

        sub.evidence_frozen = True

        return parsed_result

    @gl.public.write
    def release_payment(self, task_id: str) -> u256:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status == "verified", "Task is not verified"
        assert gl.message.sender_address == task.sponsor, "Only sponsor can release payment"

        reward = task.reward
        task.status = "settled"

        _EOA(task.worker).emit_transfer(value=reward)

        return reward

    @gl.public.write
    def reject_submission(self, task_id: str) -> None:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert gl.message.sender_address == task.sponsor, "Only sponsor can reject"
        assert task.status in ("in_progress", "partial", "rejected"), \
            "Task must be in_progress, partial, or rejected to reject"

        sub = self.submissions.get(task_id)
        assert sub is not None, "No submission found"

        if task_id in self.verifications:
            del self.verifications[task_id]

        task.status = "rejected"
        sub.status = "rejected"
        sub.evidence_frozen = False

    @gl.public.write
    def expire_task(self, task_id: str) -> None:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status not in ("expired", "settled"), \
            "Task is already in a terminal state"

        now = _current_timestamp()
        assert now > task.deadline, "Task deadline has not passed yet"

        task.status = "expired"

        sub = self.submissions.get(task_id)
        if sub is not None:
            sub.evidence_frozen = True

    @gl.public.write
    def resubmit_deliverable(
        self,
        task_id: str,
        deliverable_url: str,
        summary: str,
    ) -> None:
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status == "rejected", "Task must be rejected to resubmit"
        assert gl.message.sender_address == task.worker, \
            "Only the assigned worker can resubmit"

        now = _current_timestamp()
        assert now <= task.deadline, "Task deadline has passed"

        sub = self.submissions.get(task_id)
        assert sub is not None, "No submission found"
        assert not sub.evidence_frozen, "Evidence is frozen and cannot be replaced"

        deliverable_url = _validate_url(deliverable_url)
        assert len(summary.strip()) > 0, "Summary cannot be empty"

        sub.deliverable_url = deliverable_url
        sub.summary = summary
        sub.status = "submitted"
        sub.submitted_at = now
        sub.evidence_frozen = False

        if task_id in self.verifications:
            del self.verifications[task_id]

        task.status = "in_progress"

    @gl.public.write
    def reverify_task(self, task_id: str) -> dict:
        assert gl.message.sender_address == self.sponsor, "Only sponsor can reverify"
        task_id = str(task_id)
        task = self.tasks.get(task_id)
        assert task is not None, "Task does not exist"
        assert task.status in ("partial", "rejected"), \
            "Only partial or rejected tasks can be reverified"

        sub = self.submissions.get(task_id)
        assert sub is not None, "No submission found"

        task.status = "in_progress"
        sub.status = "submitted"
        sub.evidence_frozen = False

        if task_id in self.verifications:
            del self.verifications[task_id]

        return self.verify_performance(task_id)


@gl.evm.contract_interface
class _EOA:
    class View:
        pass

    class Write:
        pass
