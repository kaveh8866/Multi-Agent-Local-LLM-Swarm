"""Whitelisted, side-effect-free tools.

A language model may *request* a tool, but the orchestrator only executes names
listed in ``TOOLS`` with validated arguments. There is no subprocess, no shell,
no filesystem access and no network call in this module.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable

MAX_EXPRESSION_CHARS = 128
MAX_ABS_RESULT = 1e12
MAX_EXPONENT = 12
MAX_TEXT_CHARS = 20_000

TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "calculator": ("expression",),
    "text_stats": ("text",),
}

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ToolError(ValueError):
    """Raised when a tool is unknown or its arguments are rejected."""


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError("only numeric literals are allowed")
        return node.value

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value

    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or abs(left) > 1_000_000):
            raise ToolError(f"exponent is bounded to {MAX_EXPONENT}")
        try:
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right
            elif isinstance(node.op, ast.Mod):
                result = left % right
            else:
                result = left ** right
        except ZeroDivisionError as exc:
            raise ToolError("division by zero") from exc

        if isinstance(result, complex):
            raise ToolError("complex results are not allowed")
        if abs(result) > MAX_ABS_RESULT:
            raise ToolError("result exceeds the tool limit")
        return result

    raise ToolError(f"unsupported expression element: {type(node).__name__}")


def calculator(expression: str) -> dict[str, Any]:
    """Evaluate a numeric arithmetic expression without ``eval``."""

    if not isinstance(expression, str):
        raise ToolError("expression must be a string")
    if not expression.strip():
        raise ToolError("expression is empty")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ToolError(f"expression exceeds {MAX_EXPRESSION_CHARS} characters")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("expression is not valid arithmetic") from exc

    value = _evaluate(tree)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {"expression": expression, "value": value}


def text_stats(text: str) -> dict[str, Any]:
    """Deterministic text measurements used by research/review agents."""

    if not isinstance(text, str):
        raise ToolError("text must be a string")
    if not text.strip():
        raise ToolError("text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ToolError(f"text exceeds {MAX_TEXT_CHARS} characters")

    words = re.findall(r"[A-Za-z0-9']+", text)
    sentences = [segment for segment in re.split(r"[.!?]+", text) if segment.strip()]
    unique = {word.lower() for word in words}
    return {
        "characters": len(text),
        "words": len(words),
        "sentences": len(sentences),
        "unique_words": len(unique),
        "reading_time_seconds": max(1, round(len(words) / 200 * 60)),
    }


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "calculator": calculator,
    "text_stats": text_stats,
}


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute one whitelisted tool with exactly its declared arguments."""

    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise ToolError("tool arguments must be an object")

    expected = TOOL_ARGUMENTS[name]
    unexpected = sorted(set(arguments) - set(expected))
    missing = [key for key in expected if key not in arguments]
    if unexpected:
        raise ToolError(f"unexpected arguments for {name}: {', '.join(unexpected)}")
    if missing:
        raise ToolError(f"missing arguments for {name}: {', '.join(missing)}")

    return tool(**{key: arguments[key] for key in expected})


def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a model-requested tool and capture failure as an observation."""

    try:
        result = run_tool(name, arguments)
    except ToolError as exc:
        return {"tool": name, "ok": False, "error": str(exc)}
    return {"tool": name, "ok": True, "result": result}


def extract_tool_request(text: str) -> tuple[str, dict[str, Any]] | None:
    """Find a fenced JSON tool request in a model reply, if present."""

    if not text:
        return None

    for match in _FENCE.finditer(text):
        payload = _load_object(match.group(1))
        if payload is None:
            continue
        request = _as_request(payload)
        if request:
            return request

    # Also accept a single bare JSON object (no markdown fence).
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        payload = _load_object(stripped)
        if payload is not None:
            return _as_request(payload)

    return None


def _load_object(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _as_request(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    name = payload.get("tool")
    arguments = payload.get("arguments", {})
    if isinstance(name, str) and name and isinstance(arguments, dict):
        return name, arguments
    return None
