"""Task delegation: plan parsing and capability-based routing.

The planner returns a JSON array of subtasks. The router maps each stage to a
persona by declared capability, so adding an agent is a config change rather
than a code change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .registry import Persona, Stage, Workflow, render_prompt

_DECORATION = re.compile(r"^(?:[-*\u2022]|\d+[.)])\s*")
_SUBTASK_KEYS = ("task", "question", "step", "subtask", "objective")


class DelegationError(ValueError):
    """Raised when no persona can take a task or a stage is unusable."""


@dataclass(frozen=True)
class DelegatedTask:
    id: str
    persona_id: str
    instruction: str
    depends_on: tuple[str, ...] = ()


def _json_values(text: str) -> list[Any]:
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        pass

    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        try:
            candidates.append(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue

    values: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            values.extend(candidate)
    return values


def parse_plan(raw: str, limit: int) -> list[str]:
    """Extract at most ``limit`` unique subtasks from a planner reply."""

    limit = max(0, limit)
    if limit == 0:
        return []

    text = (raw or "").strip()
    if not text:
        return []

    candidates: list[str] = []
    for value in _json_values(text):
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, Mapping):
            for key in _SUBTASK_KEYS:
                entry = value.get(key)
                if isinstance(entry, str) and entry.strip():
                    candidates.append(entry)
                    break

    if not candidates:
        for line in text.splitlines():
            line = _DECORATION.sub("", line.strip()).strip()
            if not line or line.startswith("```"):
                continue
            candidates.append(line)

    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        item = " ".join(candidate.split())
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
        if len(cleaned) >= limit:
            break

    return cleaned


class TaskRouter:
    """Resolve personas by id or by declared capability."""

    def __init__(self, personas: Mapping[str, Persona]) -> None:
        if not personas:
            raise DelegationError("no personas are configured")
        self.personas = dict(personas)

    def require(self, persona_id: str) -> Persona:
        persona = self.personas.get(persona_id)
        if persona is None:
            raise DelegationError(f"persona '{persona_id}' is not defined")
        return persona

    def by_capability(self, capability: str) -> Persona:
        matches = sorted(
            (persona for persona in self.personas.values() if capability in persona.capabilities),
            key=lambda persona: persona.id,
        )
        if not matches:
            raise DelegationError(f"no persona declares the capability '{capability}'")
        return matches[0]

    def assign(
        self,
        stage: Stage,
        objective: str,
        plan_items: Sequence[str],
        limit: int,
        *,
        workflow: Workflow,
    ) -> list[DelegatedTask]:
        """Build the delegated work for one stage."""

        self.require(stage.persona)
        plan_text = "\n".join(f"- {item}" for item in plan_items) or f"- {objective}"

        def instruction(subtask: str) -> str:
            return render_prompt(
                stage.instruction,
                objective=objective,
                subtask=subtask,
                plan_text=plan_text,
                max_tasks=workflow.max_tasks,
            )

        if stage.mode == "single":
            return [
                DelegatedTask(
                    id=f"{stage.id}:0",
                    persona_id=stage.persona,
                    instruction=instruction(objective),
                )
            ]

        items = [item.strip() for item in plan_items if item.strip()]
        items = items[: max(1, min(limit, workflow.max_tasks))] or [objective]
        return [
            DelegatedTask(
                id=f"{stage.id}:{index}",
                persona_id=stage.persona,
                instruction=instruction(item),
            )
            for index, item in enumerate(items)
        ]
