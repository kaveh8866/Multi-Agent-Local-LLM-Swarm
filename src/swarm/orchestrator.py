"""The orchestration loop.

One run is: the entry persona plans, each later stage is delegated by
capability (fan-out stages run once per planned subtask), and every model reply
is recorded as a turn. Step, time and tool budgets are enforced here, not in
the prompts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import Settings
from .delegation import TaskRouter
from .delegation import parse_plan
from .llm import LLMError
from .registry import Persona, RegistryError, Task, Workflow, render_prompt
from .tools import execute, extract_tool_request

TRANSCRIPT_ITEM_CHARS = 1200
MAX_TRANSCRIPT_ITEMS = 6
TERMINAL_STATUSES = {"failed", "timeout", "budget_exhausted"}


class OrchestratorError(ValueError):
    """Raised when a run cannot start for a configuration reason."""


@dataclass
class Turn:
    stage: str
    persona_id: str
    instruction: str
    response: str
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class RunResult:
    run_id: str
    workflow: str
    objective: str
    status: str
    dry_run: bool
    plan: list[str]
    turns: list[Turn]
    steps: int
    started_at: str
    duration_ms: int
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        personas: Mapping[str, Persona],
        workflows: Mapping[str, Workflow],
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.personas = dict(personas)
        self.workflows = dict(workflows)
        self.llm = llm
        self.router = TaskRouter(self.personas)

    def run(self, task: Task, dry_run: bool = False) -> RunResult:
        workflow = self.workflows.get(task.workflow)
        if workflow is None:
            raise OrchestratorError(f"workflow '{task.workflow}' is not loaded")
        if not dry_run and self.llm is None:
            raise OrchestratorError("no LLM client is configured for a live run")

        budget = self._step_budget(task, workflow)
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        turns: list[Turn] = []
        errors: list[str] = []
        steps = 0
        plan: list[str] = []
        status = "completed"

        plan_stage = workflow.stages[0]
        planner = self.router.require(plan_stage.persona)

        if dry_run:
            plan = [
                f"{stage.id}: {stage.mode} stage for persona {stage.persona}"
                for stage in workflow.stages[1:]
            ] or [task.objective]
            turns.append(
                Turn(
                    stage=plan_stage.id,
                    persona_id=planner.id,
                    instruction="plan (dry run)",
                    response="[dry-run] no model call was made",
                )
            )
            return self._finish(
                task,
                status="planned",
                turns=turns,
                plan=plan,
                steps=steps,
                errors=errors,
                started=started,
                started_at=started_at,
                dry_run=True,
                budget=budget,
            )

        plan_prompt = render_prompt(
            plan_stage.instruction,
            objective=task.objective,
            subtask=task.objective,
            plan_text=f"- {task.objective}",
            max_tasks=workflow.max_tasks,
        )
        turn_started = time.monotonic()
        try:
            plan_reply = self._chat(planner, plan_prompt)
        except LLMError as exc:
            errors.append(f"{plan_stage.id}: {exc}")
            return self._finish(
                task,
                status="failed",
                turns=turns,
                plan=plan,
                steps=steps,
                errors=errors,
                started=started,
                started_at=started_at,
                dry_run=False,
                budget=budget,
            )

        steps += 1
        turns.append(
            Turn(
                stage=plan_stage.id,
                persona_id=planner.id,
                instruction=plan_prompt,
                response=plan_reply,
                duration_ms=int((time.monotonic() - turn_started) * 1000),
            )
        )
        plan = parse_plan(plan_reply, workflow.max_tasks)
        if not plan:
            plan = [task.objective]
            errors.append("planner returned no parseable subtasks; the objective was used directly")

        for stage in workflow.stages[1:]:
            if steps >= budget:
                status = "budget_exhausted"
                errors.append(f"step budget {budget} exhausted before stage '{stage.id}'")
                break

            assignments = self.router.assign(
                stage,
                task.objective,
                plan,
                budget - steps,
                workflow=workflow,
            )

            for assignment in assignments:
                if steps >= budget:
                    status = "budget_exhausted"
                    errors.append(f"step budget {budget} exhausted during stage '{stage.id}'")
                    break
                if time.monotonic() - started > self.settings.task_timeout_seconds:
                    status = "timeout"
                    errors.append(
                        f"task timeout of {self.settings.task_timeout_seconds:.0f}s exceeded during stage '{stage.id}'"
                    )
                    break

                persona = self.router.require(assignment.persona_id)
                prompt = self._contextualise(assignment.instruction, turns)
                turn_started = time.monotonic()
                try:
                    reply = self._chat(persona, prompt)
                except LLMError as exc:
                    status = "failed"
                    errors.append(f"{assignment.id}: {exc}")
                    break

                steps += 1
                observations = self._tool_observations(reply, persona, task)
                turns.append(
                    Turn(
                        stage=stage.id,
                        persona_id=persona.id,
                        instruction=assignment.instruction,
                        response=reply,
                        tool_observations=observations,
                        duration_ms=int((time.monotonic() - turn_started) * 1000),
                    )
                )
            else:
                continue
            break

        if status == "completed" and errors:
            status = "completed_with_warnings"

        return self._finish(
            task,
            status=status,
            turns=turns,
            plan=plan,
            steps=steps,
            errors=errors,
            started=started,
            started_at=started_at,
            dry_run=False,
            budget=budget,
        )

    # --- internals ---------------------------------------------------------

    def _chat(self, persona: Persona, prompt: str) -> str:
        messages = [
            {"role": "system", "content": persona.system_prompt},
            {"role": "user", "content": prompt},
        ]
        return self.llm.chat(messages, temperature=persona.temperature, max_tokens=persona.max_tokens)

    def _contextualise(self, instruction: str, turns: list[Turn]) -> str:
        if not turns:
            return instruction
        recent = turns[-MAX_TRANSCRIPT_ITEMS:]
        block = "\n\n".join(
            f"[{turn.stage}/{turn.persona_id}]\n{turn.response[:TRANSCRIPT_ITEM_CHARS]}" for turn in recent
        )
        return f"{instruction}\n\nTranscript so far:\n{block}"

    def _tool_observations(self, reply: str, persona: Persona, task: Task) -> list[dict[str, Any]]:
        request = extract_tool_request(reply)
        if request is None:
            return []
        name, arguments = request
        if name not in persona.tools:
            return [{"tool": name, "ok": False, "error": "tool is not enabled for this persona"}]
        if task.allowed_tools is not None and name not in task.allowed_tools:
            return [{"tool": name, "ok": False, "error": "tool is not allowed by the task constraints"}]
        return [execute(name, arguments)]

    def _step_budget(self, task: Task, workflow: Workflow) -> int:
        candidates = [workflow.max_steps, self.settings.max_steps]
        if task.max_steps is not None:
            candidates.append(task.max_steps)
        return max(1, min(candidates))

    def _finish(
        self,
        task: Task,
        *,
        status: str,
        turns: list[Turn],
        plan: list[str],
        steps: int,
        errors: list[str],
        started: float,
        started_at: str,
        dry_run: bool,
        budget: int,
    ) -> RunResult:
        if steps > budget:  # defensive: never report a step count above the budget
            steps = budget
        return RunResult(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            workflow=task.workflow,
            objective=task.objective,
            status=status,
            dry_run=dry_run,
            plan=plan,
            turns=turns,
            steps=steps,
            started_at=started_at,
            duration_ms=int((time.monotonic() - started) * 1000),
            errors=errors,
        )


__all__ = ["Orchestrator", "OrchestratorError", "RunResult", "Turn", "RegistryError"]
