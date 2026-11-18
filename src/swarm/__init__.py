"""CloudGuard Multi-Agent Local LLM Swarm.

A small, dependency-light multi-agent orchestrator that drives a local
inference server (Ollama or vLLM, both OpenAI-compatible) through declared
personas, workflows and whitelisted tools.
"""

__all__ = ["VERSION"]

VERSION = "0.1.0"
