from __future__ import annotations

import asyncio

from framework.gateway import GatewayServer, GatewayTUI, build_default_agents
from framework.orchestration import CompleteTaskAction, CreateSubtasksAction, RequestUserInputAction, TaskSpec, WaitPolicy


class DemoInteractiveAgent:
    def __init__(self, name: str):
        self.name = name
        self.capabilities = ['structured_output', 'planning']
        self.model = 'demo-model'
        self.max_ctx_tokens = 32000

    async def get_json_structured_output_async(self, user_prompt, output_format=None):
        if 'Task Name: root-demo' in user_prompt and 'Child Summaries:\n<none>' in user_prompt:
            return CreateSubtasksAction(
                subtasks=[TaskSpec(name='child-1', objective='Investigate branch 1'), TaskSpec(name='child-2', objective='Investigate branch 2')],
                wait_policy=WaitPolicy.ALL,
                summary=f'{self.name} delegated root task',
            )
        if 'Task Name: child-1' in user_prompt and 'Child Summaries:\n<none>' in user_prompt:
            return RequestUserInputAction(question='Provide guidance for child-1', reason='Need user steering before proceeding')
        if 'Task Name: child-1' in user_prompt and 'user:' in user_prompt.lower():
            return CompleteTaskAction(summary=f'child-1 completed with user guidance by {self.name}', important_findings=['Used interactive user guidance'])
        if 'Task Name: root-demo' in user_prompt and 'child-1 completed with user guidance' in user_prompt and 'child-2' in user_prompt:
            return CompleteTaskAction(summary=f'root synthesized by {self.name}', important_findings=['Merged child summaries'])
        return CompleteTaskAction(summary=f'leaf completed by {self.name}', important_findings=['Leaf node complete'])


async def main() -> None:
    gateway = GatewayServer(agents=build_default_agents(4))
    session_id = gateway.create_session({'name': 'interactive-demo'})
    await gateway.submit_task(session_id, 'Demo orchestration tree', name='root-demo')
    tui = GatewayTUI(gateway)
    await tui.run(session_id)


if __name__ == '__main__':
    asyncio.run(main())
