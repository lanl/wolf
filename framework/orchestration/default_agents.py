from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .actions import CompleteTaskAction, CreateSubtasksAction
from .models import TaskSpec, WaitPolicy


@dataclass
class EchoAgent:
    name: str = 'echo'
    capabilities: List[str] = None
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ['structured_output']
    async def get_json_structured_output_async(self, user_prompt, output_format=None):
        return CompleteTaskAction(summary=f'Completed by {self.name}', important_findings=[user_prompt[:120]])


@dataclass
class PlannerAgent:
    name: str = 'planner'
    capabilities: List[str] = None
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ['structured_output', 'planning']
    async def get_json_structured_output_async(self, user_prompt, output_format=None):
        if 'Child Summaries:' in user_prompt and '<none>' not in user_prompt:
            return CompleteTaskAction(summary=f'Synthesis complete by {self.name}')
        return CreateSubtasksAction(
            subtasks=[TaskSpec(name='analyze-subtask-1', objective='Analyze part 1'), TaskSpec(name='analyze-subtask-2', objective='Analyze part 2')],
            wait_policy=WaitPolicy.ALL,
            summary='Planned two child tasks',
        )


@dataclass
class ChattyAgent:
    name: str = 'chatty'
    capabilities: List[str] = None
    model: str = 'demo-chat-model'
    max_ctx_tokens: int = 32000
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ['structured_output', 'planning']
    async def get_json_structured_output_async(self, user_prompt, output_format=None):
        low = user_prompt.lower()
        if 'recent local messages:' in low and 'user:' in low:
            # respond to the latest user line
            last = ''
            for line in reversed(user_prompt.splitlines()):
                if line.strip().lower().startswith('- user:'):
                    last = line.split(':', 1)[1].strip()
                    break
            return {'action': 'send_message', 'message': f'{self.name} received: {last or "acknowledged"}'}
        return {'action': 'request_user_input', 'question': 'How would you like me to proceed?', 'reason': 'chat bootstrap'}
