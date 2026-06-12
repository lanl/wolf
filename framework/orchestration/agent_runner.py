from __future__ import annotations

from typing import Any

from .action_adapter import DynamicActionAdapter


class AgentRunner:
    def __init__(self, action_adapter: DynamicActionAdapter) -> None:
        self.action_adapter = action_adapter

    async def get_action(self, agent: Any, prompt: str, validator: Any) -> Any:
        if hasattr(agent, 'get_json_structured_output_async'):
            raw = await agent.get_json_structured_output_async(user_prompt=prompt, output_format=validator)
            return self.action_adapter.parse_json(raw)
        if hasattr(agent, 'get_structured_output'):
            raw = agent.get_structured_output(user_prompt=prompt, output_format=validator)
            return self.action_adapter.parse_json(raw)
        if hasattr(agent, 'get_chat_response_async'):
            raw = await agent.get_chat_response_async(user_prompt=prompt)
            return self.action_adapter.parse_json(raw)
        if hasattr(agent, 'get_chat_response'):
            raw = agent.get_chat_response(user_prompt=prompt)
            return self.action_adapter.parse_json(raw)
        if hasattr(agent, 'act'):
            return await agent.act(prompt)
        raise TypeError(f'Unsupported agent interface for {agent}')
