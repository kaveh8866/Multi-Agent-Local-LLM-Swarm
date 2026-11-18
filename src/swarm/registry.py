"""Personas, workflows and task descriptors loaded from ``configs/``.

Every descriptor is validated at load time: unknown personas, unknown tools,
unbound prompt placeholders and malformed tasks fail before a model is called.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .tools import TOOLS

MAX_OBJECTIVE_CHARS = 4000
MAX_STEPS_CEILING = 64
MAX_TASKS_CEILING = 8
MAX_TOKENS_CEILING = 8192
STAGE_MODES = {"single", "fanout"}


class RegistryError(ValueError):
    """Raised when a persona, workflow or task descriptor is invalid."""


def render_prompt(template: str, **values: object) -> str:
    """Render a prompt template, reporting unbound placeholders clearly."""

    try:
        return template.format(**values)
    except KeyError as exc:
        raise RegistryError(f"prompt template references an unbound placeholder: {exc.args[0]}") from exc
    except (IndexError, ValueError) as exc:
        raise RegistryError(f"prompt template is malformed: {exc}") from exc


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    system_prompt: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    max_tokens: int = 800
    temperature: float = 0.2


@dataclass(frozen=True)
class Stage:
    id: str
    persona: str
    instruction: str
    mode: str = "single"


@dataclass(frozen=True)
class Workflow:
    id: str
    description: str
    entry: str
    stages: tuple[Stage, ...]
    max_steps: int = 8
    max_tasks: int = 3


@dataclass(frozen=True)
class Task:
    id: str
    objective: str
    workflow: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    max_steps: int | None = None
    allowed_tools: tuple[str, ...] | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], known_workflows: Iterable[str]) -> "Task":
        if not isinstance(payload, Mapping):
            raise RegistryError("task must be a JSON object")

        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise RegistryError("task.objective is required and must be a non-empty string")
        objective = " ".join(objective.split())
        if len(objective) > MAX_OBJECTIVE_CHARS:
            raise RegistryError(f"task.objective exceeds {MAX_OBJECTIVE_CHARS} characters")

        known = set(known_workflows)
        workflow = payload.get("workflow")
        if not isinstance(workflow, str) or workflow not in known:
            raise RegistryError(f"task.workflow must be one of: {', '.join(sorted(known))}")

        task_id = payload.get("id") or f"task-{uuid.uuid4().hex[:8]}"
        if not isinstance(task_id, str) or not task_id.strip():
            raise RegistryError("task.id must be a non-empty string when provided")

        constraints = payload.get("constraints") or {}
        if not isinstance(constraints, Mapping):
            raise RegistryError("task.constraints must be an object")

        max_steps = constraints.get("max_steps")
        if max_steps is not None:
            if isinstance(max_steps, bool) or not isinstance(max_steps, int):
                raise RegistryError("task.constraints.max_steps must be an integer")
            if not 1 <= max_steps <= MAX_STEPS_CEILING:
                raise RegistryError(f"task.constraints.max_steps must be between 1 and {MAX_STEPS_CEILING}")

        allowed_tools = constraints.get("allowed_tools")
        if allowed_tools is not None:
            if not isinstance(allowed_tools, (list, tuple)) or not all(isinstance(item, str) for item in allowed_tools):
                raise RegistryError("task.constraints.allowed_tools must be a list of tool names")
            unknown = sorted({name for name in allowed_tools if name not in TOOLS})
            if unknown:
                raise RegistryError(f"task.constraints.allowed_tools has unknown tools: {', '.join(unknown)}")
            allowed_tools = tuple(allowed_tools)

        inputs = payload.get("inputs") or {}
        if not isinstance(inputs, Mapping):
            raise RegistryError("task.inputs must be an object")

        return cls(
            id=task_id.strip(),
            objective=objective,
            workflow=workflow,
            inputs=dict(inputs),
            max_steps=max_steps,
            allowed_tools=allowed_tools,
        )


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise RegistryError(f"configuration file is missing: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise RegistryError(f"{path.name} must contain a mapping at the top level")
    return document


def _required_text(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{context}: '{key}' is required and must be a non-empty string")
    return value.strip()


def _string_list(value: Any, context: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise RegistryError(f"{context}: '{key}' must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _bounded_int(value: Any, context: str, key: str, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{context}: '{key}' must be an integer")
    if not 1 <= value <= maximum:
        raise RegistryError(f"{context}: '{key}' must be between 1 and {maximum}")
    return value


def load_personas(path: Path) -> dict[str, Persona]:
    """Load and validate the persona registry."""

    document = _load_yaml(path)
    raw = document.get("personas")
    if not isinstance(raw, list) or not raw:
        raise RegistryError(f"{path.name}: 'personas' must be a non-empty list")

    defaults = document.get("defaults") or {}
    if not isinstance(defaults, Mapping):
        raise RegistryError(f"{path.name}: 'defaults' must be a mapping")
    default_max_tokens = _bounded_int(defaults.get("max_tokens"), path.name, "defaults.max_tokens", 800, MAX_TOKENS_CEILING)
    default_temperature = defaults.get("temperature", 0.2)
    if isinstance(default_temperature, bool) or not isinstance(default_temperature, (int, float)):
        raise RegistryError(f"{path.name}: defaults.temperature must be a number")
    if not 0.0 <= float(default_temperature) <= 2.0:
        raise RegistryError(f"{path.name}: defaults.temperature must be between 0 and 2")

    personas: dict[str, Persona] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise RegistryError(f"{path.name}: persona #{index} must be a mapping")
        persona_id = _required_text(entry, "id", f"{path.name} persona #{index}")
        if persona_id in personas:
            raise RegistryError(f"{path.name}: duplicate persona id '{persona_id}'")
        context = f"{path.name} persona '{persona_id}'"

        name = entry.get("name") or persona_id
        if not isinstance(name, str) or not name.strip():
            raise RegistryError(f"{context}: 'name' must be a non-empty string")

        prompt_file = _required_text(entry, "prompt", context)
        prompt_path = (path.parent / prompt_file).resolve()
        if not prompt_path.is_file():
            raise RegistryError(f"{context}: prompt file not found: {prompt_file}")
        system_prompt = prompt_path.read_text(encoding="utf-8").strip()
        if not system_prompt:
            raise RegistryError(f"{context}: prompt file is empty: {prompt_file}")

        capabilities = _string_list(entry.get("capabilities"), context, "capabilities")
        if not capabilities:
            raise RegistryError(f"{context}: at least one capability is required")

        tools = _string_list(entry.get("tools"), context, "tools")
        unknown = sorted({tool for tool in tools if tool not in TOOLS})
        if unknown:
            raise RegistryError(f"{context}: unknown tools: {', '.join(unknown)}")

        max_tokens = _bounded_int(entry.get("max_tokens"), context, "max_tokens", default_max_tokens, MAX_TOKENS_CEILING)
        temperature = entry.get("temperature", default_temperature)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise RegistryError(f"{context}: 'temperature' must be a number")
        if not 0.0 <= float(temperature) <= 2.0:
            raise RegistryError(f"{context}: 'temperature' must be between 0 and 2")

        personas[persona_id] = Persona(
            id=persona_id,
            name=name.strip(),
            system_prompt=system_prompt,
            capabilities=capabilities,
            tools=tools,
            max_tokens=max_tokens,
            temperature=float(temperature),
        )

    return personas


def load_workflows(path: Path, personas: Mapping[str, Persona]) -> dict[str, Workflow]:
    """Load and validate workflow definitions against the persona registry."""

    document = _load_yaml(path)
    raw = document.get("workflows")
    if not isinstance(raw, Mapping) or not raw:
        raise RegistryError(f"{path.name}: 'workflows' must be a non-empty mapping")

    workflows: dict[str, Workflow] = {}
    for workflow_id, body in raw.items():
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise RegistryError(f"{path.name}: workflow ids must be non-empty strings")
        context = f"{path.name} workflow '{workflow_id}'"
        if not isinstance(body, Mapping):
            raise RegistryError(f"{context}: must be a mapping")

        entry = _required_text(body, "entry", context)
        if entry not in personas:
            raise RegistryError(f"{context}: entry persona '{entry}' is not defined")

        description = body.get("description") or ""
        if not isinstance(description, str):
            raise RegistryError(f"{context}: 'description' must be a string")

        stages_raw = body.get("stages")
        if not isinstance(stages_raw, list) or not stages_raw:
            raise RegistryError(f"{context}: 'stages' must be a non-empty list")

        stages: list[Stage] = []
        seen_stage_ids: set[str] = set()
        for index, stage_body in enumerate(stages_raw):
            if not isinstance(stage_body, Mapping):
                raise RegistryError(f"{context}: stage #{index} must be a mapping")
            stage_id = stage_body.get("id") or f"stage-{index}"
            if not isinstance(stage_id, str) or not stage_id.strip():
                raise RegistryError(f"{context}: stage #{index} id must be a non-empty string")
            stage_id = stage_id.strip()
            if stage_id in seen_stage_ids:
                raise RegistryError(f"{context}: duplicate stage id '{stage_id}'")
            seen_stage_ids.add(stage_id)

            persona_id = _required_text(stage_body, "persona", f"{context} stage '{stage_id}'")
            if persona_id not in personas:
                raise RegistryError(f"{context} stage '{stage_id}': persona '{persona_id}' is not defined")

            instruction = _required_text(stage_body, "instruction", f"{context} stage '{stage_id}'")
            mode = stage_body.get("mode") or "single"
            if mode not in STAGE_MODES:
                raise RegistryError(f"{context} stage '{stage_id}': mode must be one of {sorted(STAGE_MODES)}")

            stages.append(Stage(id=stage_id, persona=persona_id, instruction=instruction, mode=mode))

        if stages[0].persona != entry:
            raise RegistryError(f"{context}: the first stage must run the entry persona '{entry}'")

        workflows[workflow_id] = Workflow(
            id=workflow_id,
            description=description.strip(),
            entry=entry,
            stages=tuple(stages),
            max_steps=_bounded_int(body.get("max_steps"), context, "max_steps", 8, MAX_STEPS_CEILING),
            max_tasks=_bounded_int(body.get("max_tasks"), context, "max_tasks", 3, MAX_TASKS_CEILING),
        )

    return workflows
