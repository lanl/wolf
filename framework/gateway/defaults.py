from __future__ import annotations

from typing import Any, List

from framework.orchestration.default_agents import ChattyAgent, EchoAgent


def build_default_agents(count: int = 4) -> List[Any]:
    """Best-effort default agent construction.

    Tries to import the project's real agent module first. Falls back to simple echo agents
    so the gateway remains runnable in isolation.
    """
    try:
        from framework.agentic.agents import OpenAIAgent  # type: ignore
        # We do not know the caller's preferred model parameters here, so we only use this
        # path when a zero-config constructor is supported. Otherwise fall back gracefully.
        try:
            return [OpenAIAgent(name=f"agent-{i}") for i in range(count)]
        except Exception:
            pass
    except Exception:
        pass
    return [ChattyAgent(name=f"agent-{i}") for i in range(count)]
