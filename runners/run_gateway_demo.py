from __future__ import annotations

import asyncio

from framework.gateway import GatewayServer, GatewayTUI, build_default_agents
from framework.orchestration import CompleteTaskAction, CreateSubtasksAction, TaskSpec, WaitPolicy


class DemoAgent:
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
            return CreateSubtasksAction(
                subtasks=[TaskSpec(name='grandchild-1a', objective='Analyze part A'), TaskSpec(name='grandchild-1b', objective='Analyze part B')],
                wait_policy=WaitPolicy.ALL,
                summary=f'{self.name} delegated child-1',
            )
        if 'Task Name: root-demo' in user_prompt and 'child-1' in user_prompt and 'child-2' in user_prompt:
            return CompleteTaskAction(summary=f'root synthesized by {self.name}', important_findings=['Merged child summaries', 'Prepared root result'])
        if 'Task Name: child-1' in user_prompt and 'grandchild-1a' in user_prompt and 'grandchild-1b' in user_prompt:
            return CompleteTaskAction(summary=f'child-1 synthesized by {self.name}', important_findings=['Merged grandchild summaries'])
        return CompleteTaskAction(summary=f'leaf completed by {self.name}', important_findings=['Leaf node complete'])


async def main() -> None:
    gateway = GatewayServer(agents=build_default_agents(4))
    session_id = gateway.create_session({'name': 'demo'})
    root_id = await gateway.submit_task(session_id, 'Demo orchestration tree', name='root-demo')
    tui = GatewayTUI(gateway)
    task = asyncio.create_task(gateway.run_until_complete(root_id))
    await tui.run(session_id, duration=2.5)
    root = await task
    snap = await gateway.get_snapshot(session_id)
    print('\n=== FINAL RESULT ===')
    print(f'root_id={root.id}')
    print(f'status={root.status.value}')
    print(f'summary={root.result.summary if root.result else None}')
    print(f"tasks={len(snap['tasks'])}")
    print(f"artifact_rows={sum(len(v) for v in snap['artifacts'].values())}")
    print(f"event_types={sorted({ev.type for ev in snap['events']})}")


if __name__ == '__main__':
    asyncio.run(main())
