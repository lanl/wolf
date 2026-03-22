from __future__ import annotations

import asyncio

from framework.gateway import GatewayServer
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
            return CompleteTaskAction(summary=f'root synthesized by {self.name}', important_findings=['Merged child summaries'])
        if 'Task Name: child-1' in user_prompt and 'grandchild-1a' in user_prompt and 'grandchild-1b' in user_prompt:
            return CompleteTaskAction(summary=f'child-1 synthesized by {self.name}', important_findings=['Merged grandchild summaries'])
        return CompleteTaskAction(summary=f'leaf completed by {self.name}', important_findings=['Leaf node complete'])


def test_gateway_smoke():
    async def _run():
        gateway = GatewayServer(agents=[DemoAgent(f'agent-{i}') for i in range(4)], db_path=':memory:')
        session_id = gateway.create_session({'test': True})
        root_id = await gateway.submit_task(session_id, 'Demo orchestration tree', name='root-demo')
        root = await gateway.run_until_complete(root_id)
        snapshot = await gateway.get_snapshot(session_id)
        assert root.status.value == 'completed'
        assert 'root synthesized' in (root.result.summary if root.result else '')
        assert len(snapshot['tasks']) == 5
        event_types = {ev.type for ev in snapshot['events']}
        assert 'subtasks_created' in event_types
        assert 'summary_generated' in event_types
        assert 'summary_propagated' in event_types
        assert all(not row['busy'] for row in snapshot['agent_pool'])
        assert sum(len(v) for v in snapshot['artifacts'].values()) >= 5
        db_rows = gateway.store.list_artifacts(session_id=session_id)
        assert len(db_rows) >= 5
    asyncio.run(_run())



def test_gateway_message_resume():
    async def _run():
        from framework.orchestration import RequestUserInputAction

        class InteractiveAgent:
            def __init__(self, name: str):
                self.name = name
                self.capabilities = ['structured_output', 'planning']
                self.model = 'demo-model'
                self.max_ctx_tokens = 32000

            async def get_json_structured_output_async(self, user_prompt, output_format=None):
                if 'Task Name: root-demo' in user_prompt and 'Child Summaries:\n<none>' in user_prompt:
                    return CreateSubtasksAction(subtasks=[TaskSpec(name='child-1', objective='Need guidance')], wait_policy=WaitPolicy.ALL, summary='delegated')
                if 'Task Name: child-1' in user_prompt and 'user:' not in user_prompt.lower():
                    return RequestUserInputAction(question='Need guidance', reason='interactive')
                if 'Task Name: child-1' in user_prompt and 'user:' in user_prompt.lower():
                    return CompleteTaskAction(summary=f'child done by {self.name}', important_findings=['guided'])
                return CompleteTaskAction(summary=f'root done by {self.name}', important_findings=['merged'])

        gateway = GatewayServer(agents=[InteractiveAgent(f'agent-{i}') for i in range(2)], db_path=':memory:')
        session_id = gateway.create_session({'test': 'interactive'})
        root_id = await gateway.submit_task(session_id, 'Interactive orchestration tree', name='root-demo')

        async def drive_user():
            child_id = None
            async for ev in gateway.subscribe(session_id):
                if ev.type == 'user_input_requested':
                    child_id = ev.task_id
                    await gateway.send_message(session_id, 'Here is guidance', target_task_id=child_id)
                    break
            return child_id

        child_id = await asyncio.gather(gateway.run_until_complete(root_id), drive_user())
        root = child_id[0]
        guided_child = child_id[1]
        detail = await gateway.get_task_detail(guided_child)
        assert root.status.value == 'completed'
        assert any(m['role'] == 'user' for m in detail['local_messages'])
        event_types = {ev.type for ev in (await gateway.get_snapshot(session_id))['events']}
        assert 'user_message' in event_types
        assert 'task_ready' in event_types

    asyncio.run(_run())



def test_gateway_chat_thread_reaction():
    async def _run():
        from framework.orchestration import TaskThreadMessageAction

        class ChatAgent:
            def __init__(self, name: str):
                self.name = name
                self.capabilities = ['structured_output', 'planning']
                self.model = 'demo-model'
                self.max_ctx_tokens = 32000

            async def get_json_structured_output_async(self, user_prompt, output_format=None):
                if 'Recent Local Messages:' in user_prompt and 'user: hello there' in user_prompt.lower():
                    return {'action': 'send_message', 'message': f'Roger that from {self.name}'}
                return {'action': 'request_user_input', 'question': 'say hello', 'reason': 'awaiting input'}

        gateway = GatewayServer(agents=[ChatAgent('agent-0')], db_path=':memory:')
        session_id = gateway.create_session({'test': 'chat'})
        root_id = await gateway.submit_task(session_id, 'Chat with the user', name='chat-root', workflow_type='chat')
        # Wait until the agent asks for input.
        for _ in range(200):
            detail = await gateway.get_task_detail(root_id)
            if any('say hello' in str(m.get('content','')).lower() for m in detail['local_messages']):
                break
            await asyncio.sleep(0.02)
        await gateway.send_message(session_id, 'hello there', target_task_id=root_id)
        for _ in range(300):
            detail = await gateway.get_task_detail(root_id)
            if any('roger that' in str(m.get('content','')).lower() for m in detail['local_messages']):
                break
            await asyncio.sleep(0.02)
        detail = await gateway.get_task_detail(root_id)
        assert any(m['role'] == 'assistant' and 'Roger that' in str(m['content']) for m in detail['local_messages'])
        await gateway.shutdown()

    asyncio.run(_run())
