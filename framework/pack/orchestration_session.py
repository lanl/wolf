"""Per-gateway-session orchestration wrapper for WOLF Gateway.

This module wraps the existing ``framework.orchestration.AsyncWorkflowRuntime``
with a gateway-friendly API.  It now includes a GatewayTaskWorkflowAdapter that
executes tasks through profile-backed, task-local ``GatewayActionWorkflow``
clones when a gateway runtime is provided, while retaining simple echo
completion as a safe fallback/demo path.
"""

from __future__ import annotations

import base64
import asyncio
import json
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
import copy

from config.session.default.params.inputs import session_params as DEFAULT_SESSION_PARAMS
from framework.utils.config_tools import setup_cli_session
from framework.workflows.custom_workflows.gateway_action_workflow import GatewayActionWorkflow
from framework.orchestration.actions import CompleteTaskAction
from framework.orchestration.agent_pool import AgentLease
from framework.orchestration.pool_controller import AgentPoolController
from framework.orchestration.events import Event
from framework.orchestration.models import EngineConfig, ResourceBudget, TaskSpec, TaskStatus
from framework.orchestration.runtime import AsyncWorkflowRuntime
from framework.orchestration.task_infra import TaskInfrastructureFactory
from framework.pack.infrastructure_snapshot import redact_value, summarize_memory_manager, summarize_resources_from_infra


GatewayBroadcaster = Callable[[dict, str], Awaitable[None]]
ResolveActionNames = Callable[[Dict[str, Any]], List[str]]
ResolveExecutionPolicy = Callable[[Dict[str, Any]], Dict[str, Any]]
GuiCommandExtractor = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
AutoContinuePredicate = Callable[[Any], bool]

GATEWAY_ORCHESTRATION_ACTIONS = {"create_subtasks", "wait_for_tasks", "complete_task", "publish_progress", "fail_task"}



class GatewayTaskWorkflowAdapter:
    """Executes an orchestration task through GatewayActionWorkflow.

    This adapter reuses gateway policy resolution, websocket fanout, and GUI
    command bridge metadata, but leases a concrete orchestration pool worker and
    runs a task-local GatewayActionWorkflow clone for production OpenAI-compatible
    workers.  That gives each task its own chat/history/context directory while
    preserving SummaryCapsule completion through AsyncWorkflowRuntime.
    """

    def __init__(
        self,
        *,
        owner: Any,
        gateway_runtime: Dict[str, Any],
        resolve_action_names: ResolveActionNames,
        resolve_execution_policy: ResolveExecutionPolicy,
        gui_command_from_workflow_event: GuiCommandExtractor,
        should_auto_continue_gui_command: AutoContinuePredicate,
    ) -> None:
        self.owner = owner
        self.gateway_runtime = gateway_runtime
        self.resolve_action_names = resolve_action_names
        self.resolve_execution_policy = resolve_execution_policy
        self.gui_command_from_workflow_event = gui_command_from_workflow_event
        self.should_auto_continue_gui_command = should_auto_continue_gui_command
        self._worker_sessions: Dict[str, Dict[str, Any]] = {}


    def worker_sessions_snapshot(self) -> List[Dict[str, Any]]:
        """Return redacted, side-effect-free summaries of task-local worker clones."""
        rows: List[Dict[str, Any]] = []
        for key, session in sorted(self._worker_sessions.items()):
            wf = session.get('wf')
            infra = session.get('infra') or getattr(wf, 'infra', None)
            chat = getattr(infra, 'chat_manager', None)
            ctx = getattr(infra, 'context_manager', None)
            mem = getattr(infra, 'memory_manager', None)
            task_id = session.get('task_id') or (key.split(':', 1)[0] if ':' in key else key)
            agent_name = session.get('worker_agent_name') or (key.split(':', 1)[1] if ':' in key else None)
            memory_summary = summarize_memory_manager(mem)
            resources = summarize_resources_from_infra(
                infra,
                owner_type='worker',
                owner_id=key,
                locality='task_local',
                source='worker_session',
            )
            collection_names = {
                str(v.get('name') or idx): (v.get('collection_name') or v.get('text_collection_name'))
                for idx, v in enumerate(memory_summary.get('vstores') or [])
            }
            rebuild_flags = {
                str(v.get('name') or idx): v.get('rebuild_vstore')
                for idx, v in enumerate(memory_summary.get('vstores') or [])
                if 'rebuild_vstore' in v
            }
            row = {
                'id': key,
                'task_id': task_id,
                'agent_name': agent_name,
                'session_dir': session.get('session_dir'),
                'workflow_class': type(wf).__name__ if wf is not None else None,
                'infrastructure_class': type(infra).__name__ if infra is not None else None,
                'db_client_inherited': bool(session.get('db_client') is not None),
                'run_control_status': (session.get('run_control') or {}).get('status'),
                'chat_entries': len(getattr(chat, 'CHAT_HISTORY', []) or []),
                'context_entries': len(getattr(ctx, 'current_ctx', []) or []),
                'context_tokens': getattr(ctx, 'current_ctx_tokens', None),
                'max_context_tokens': getattr(ctx, 'max_ctx_tokens', None),
                'memory_path': memory_summary.get('memory_path'),
                'memory_collections': collection_names,
                'memory_rebuild_flags': rebuild_flags,
                'resource_counts': {
                    'kbs': len(getattr(infra, 'KBs', {}) or {}),
                    'tbs': len(getattr(infra, 'TBs', {}) or {}),
                    'tools': len(resources.get('tools') or []),
                    'universes': len(getattr(infra, 'UNIVs', {}) or {}),
                    'vstores': len(memory_summary.get('vstores') or []),
                },
            }
            row['infrastructure'] = {
                'session_dir': getattr(infra, 'session_dir', None),
                'log_dir': getattr(infra, 'log_dir', None),
                'memory': memory_summary,
                'resources': resources,
                'resource_counts': row['resource_counts'],
            }
            rows.append(redact_value(row))
        return rows

    async def execute_task(
        self,
        *,
        task_id: str,
        content: str,
        sender: str = "user",
        visual_context: Optional[Dict[str, Any]] = None,
        lease: Optional[AgentLease] = None,
    ) -> None:
        runtime = self.owner.runtime
        task = await runtime.repository.get(task_id)
        if task is None:
            return

        owns_lease = lease is None
        agent: Any = None
        agent_name = "gateway_worker"
        worker_session: Optional[Dict[str, Any]] = None
        control: Dict[str, Any] = {}
        adapter_registry = getattr(self.owner, "_adapter_tasks", None)
        current_adapter_task = asyncio.current_task()
        if isinstance(adapter_registry, dict) and current_adapter_task is not None:
            adapter_registry[task_id] = current_adapter_task
        try:
            if lease is None:
                lease = await runtime.agent_pool.acquire(task_id, task.spec.requirements)
            agent = await runtime.agent_pool.get(lease.agent_name)
            agent_name = lease.agent_name
            task.status = TaskStatus.RUNNING
            task.owner_agent_name = task.owner_agent_name or agent_name
            task.leased_agent_name = agent_name
            task.started_at = time.time()
            task.touch()
            await runtime.repository.put(task)
            # Runtime owns agent lease/release lifecycle events.  The gateway
            # adapter only emits workflow-specific start/progress/completion
            # events to avoid duplicate agent_leased/agent_released messages in
            # websocket consumers such as the Kanban board.
            await runtime.event_bus.publish(Event(type="task_started", task_id=task.id, actor=agent_name, payload={"workflow_type": task.spec.workflow_type, "adapter": "gateway_worker_clone"}))
            await runtime.event_bus.drain()

            if not self._supports_gateway_workflow(agent):
                await self._complete_echo_like_task(task_id=task_id, agent_name=agent_name, content=content)
                return

            worker_session = self._worker_sessions.get(task_id)
            if worker_session is None:
                worker_session = self._build_task_worker_session(agent=agent, agent_name=agent_name, task_id=task_id)
                self._worker_sessions[task_id] = worker_session
            control = worker_session.setdefault("run_control", self.owner.default_run_control())
            run_id = f"orch_run_{task_id[:8]}_{int(time.time() * 1000)}"
            control.update({
                "run_id": run_id,
                "status": "running",
                "pause_requested": False,
                "stop_requested": False,
                "reassess_requested": False,
                "pending_user_messages": [],
                "step": 0,
                "updated_at": datetime.now().isoformat(),
                "task_id": task_id,
                "source": "orchestration",
                "agent_name": agent_name,
            })
            await self.owner._broadcast({
                "type": "run_control_state",
                "run_id": run_id,
                "status": "running",
                "pause_requested": False,
                "stop_requested": False,
                "reassess_requested": False,
                "pending_user_message_count": 0,
                "step": 0,
                "content": f"Orchestration task {task_id[:8]} started on worker {agent_name}.",
                "task_id": task_id,
                "agent_name": agent_name,
                "adapter": "gateway_worker_clone",
            })

            workflow_content = self._build_workflow_content(str(content or task.spec.objective), visual_context)
            workflow_content = await self._augment_with_task_context(task_id, workflow_content)
            config = self.gateway_runtime.get("config", {}) or {}
            action_names = list(self.resolve_action_names(config) or [])
            for orch_action in GATEWAY_ORCHESTRATION_ACTIONS:
                if orch_action not in action_names:
                    action_names.append(orch_action)
            execution_policy = self.resolve_execution_policy(config)
            gui_route = self.gateway_runtime.get("gui_route") or {}
            execution_policy["gui_action_route"] = gui_route.get("route") or config.get("gui_action_route") or "direct"
            execution_policy["gui_url"] = gui_route.get("gui_url") or config.get("gui_url")
            execution_policy["gui_api_reachable"] = gui_route.get("reachable")
            mode = config.get("mode") or "single_step"
            max_steps = int(config.get("max_steps") or 1)

            await self.owner._broadcast({
                "type": "policy_resolved",
                "content": f"Gateway worker={agent_name} policy={config.get('action_policy', 'limited')} actions={action_names}",
                "action_policy": config.get("action_policy", "limited"),
                "resolved_action_names": action_names,
                "resolved_execution_policy": execution_policy,
                "visual_context_attached": bool(visual_context),
                "visual_context_schema": visual_context.get("schema_version") if isinstance(visual_context, dict) else None,
                "task_id": task_id,
                "agent_name": agent_name,
                "source": "orchestration",
                "adapter": "gateway_worker_clone",
            })

            wf = worker_session.get("wf")
            if wf is None:
                raise RuntimeError("Task worker session has no workflow instance")
            async with worker_session["lock"]:
                events = await wf.process_user_message(
                    workflow_content,
                    user_name=sender or "user",
                    action_names=action_names,
                    mode=mode,
                    max_steps=max_steps,
                    log_console=False,
                    execution_policy=execution_policy,
                    control_state=control,
                )

            events = list(events or [])
            waiting_gui_commands: List[Dict[str, Any]] = []
            for event in events:
                if not isinstance(event, dict):
                    event = {"type": "workflow_event", "content": str(event)}
                if event.get("step") is not None:
                    try:
                        control["step"] = max(int(control.get("step") or 0), int(event.get("step") or 0))
                    except Exception:
                        pass
                event.setdefault("session_id", self.owner.session_id)
                event.setdefault("task_id", task_id)
                event.setdefault("source", "orchestration")
                event.setdefault("agent_name", agent_name)
                event.setdefault("adapter", "gateway_worker_clone")
                await runtime._append_thread_entry(task_id, self._role_for_event(event), event)
                await self.owner._broadcast(event)
                gui_command = self.gui_command_from_workflow_event(event)
                if gui_command:
                    gui_command.setdefault("session_id", self.owner.session_id)
                    gui_command.setdefault("task_id", task_id)
                    gui_command.setdefault("source", "orchestration")
                    gui_command.setdefault("agent_name", agent_name)
                    pending = self.gateway_runtime.setdefault("pending_gui_commands", {})
                    command_id = gui_command.get("command_id")
                    if command_id:
                        auto_continue = self.should_auto_continue_gui_command(gui_command.get("action"))
                        pending[command_id] = {
                            "command_id": command_id,
                            "action": gui_command.get("action"),
                            "payload": gui_command.get("payload") if isinstance(gui_command.get("payload"), dict) else {},
                            "workflow_event": event,
                            "auto_continue": auto_continue,
                            "target_task_id": task_id,
                            "source": "orchestration",
                            "agent_name": agent_name,
                            "created_at": datetime.now().isoformat(),
                            "created_monotonic": time.monotonic(),
                            "timeout_seconds": getattr(self.owner, "gui_command_timeout_seconds", 60.0),
                        }
                        if auto_continue:
                            waiting_gui_commands.append({
                                "command_id": command_id,
                                "action": gui_command.get("action"),
                                "agent_name": agent_name,
                            })
                    await self.owner._broadcast(gui_command)

            failed = self._workflow_failed(events)
            if failed:
                raise RuntimeError(self._failure_summary(events))

            applied_orchestration_action = await self._apply_orchestration_action_from_events(
                task_id=task_id,
                events=events,
                agent_name=agent_name,
            )
            if applied_orchestration_action:
                control["status"] = "orchestration_action_applied"
                control["run_id"] = None
                control["pause_requested"] = False
                control["stop_requested"] = False
                control["reassess_requested"] = False
                control["updated_at"] = datetime.now().isoformat()
                await self.owner._broadcast({
                    "type": "run_control_state",
                    "run_id": None,
                    "status": "orchestration_action_applied",
                    "content": f"Orchestration task {task_id[:8]} applied {applied_orchestration_action}.",
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "adapter": "gateway_worker_clone",
                    "action": applied_orchestration_action,
                })
                return

            if waiting_gui_commands:
                task = await runtime.repository.get(task_id)
                if task is not None:
                    task.status = TaskStatus.WAITING
                    task.error = None
                    task.touch()
                    await runtime.repository.put(task)
                    await runtime._append_thread_entry(task_id, "system", {
                        "type": "waiting_for_gui_command_result",
                        "commands": waiting_gui_commands,
                    })
                    await runtime.event_bus.publish(Event(
                        type="task_waiting",
                        task_id=task_id,
                        actor=agent_name,
                        payload={
                            "reason": "waiting_for_gui_command_result",
                            "commands": waiting_gui_commands,
                            "adapter": "gateway_worker_clone",
                        },
                    ))
                control["status"] = "waiting"
                control["run_id"] = None
                control["updated_at"] = datetime.now().isoformat()
                await self.owner._broadcast({
                    "type": "run_control_state",
                    "run_id": None,
                    "status": "waiting",
                    "pause_requested": False,
                    "stop_requested": False,
                    "reassess_requested": False,
                    "pending_user_message_count": 0,
                    "step": int(control.get("step") or 0),
                    "content": f"Orchestration task {task_id[:8]} is waiting for GUI command result.",
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "adapter": "gateway_worker_clone",
                })
                return

            task = await runtime.repository.get(task_id)
            if task is None:
                return
            summary = self._completion_summary(events)
            await runtime._apply_action(
                task,
                CompleteTaskAction(
                    summary=summary,
                    artifacts={},
                    trace=events,
                    confidence=0.8,
                    important_findings=[summary[:500]] if summary else [],
                ),
                runtime.task_infras[task_id],
                runtime.policy_for(task.spec.workflow_type),
            )
            control["status"] = "completed"
            control["run_id"] = None
            control["pause_requested"] = False
            control["stop_requested"] = False
            control["reassess_requested"] = False
            control["updated_at"] = datetime.now().isoformat()
            await self.owner._broadcast({
                "type": "run_control_state",
                "run_id": None,
                "status": "completed",
                "pause_requested": False,
                "stop_requested": False,
                "reassess_requested": False,
                "pending_user_message_count": 0,
                "step": int(control.get("step") or 0),
                "content": f"Orchestration task {task_id[:8]} complete on {agent_name}.",
                "task_id": task_id,
                "agent_name": agent_name,
            })
        except Exception as exc:
            task = await runtime.repository.get(task_id)
            if task is not None:
                task.leased_agent_name = None
                if hasattr(runtime, "_record_task_failure"):
                    await runtime._record_task_failure(
                        task,
                        str(exc),
                        actor=agent_name,
                        trace=[{"adapter": "gateway_worker_clone", "error": str(exc), "exception_type": type(exc).__name__}],
                    )
                else:
                    task.status = TaskStatus.FAILED
                    task.error = str(exc)
                    task.finished_at = time.time()
                    task.touch()
                    await runtime.repository.put(task)
                    await runtime._append_thread_entry(task_id, "system", f"gateway worker task failed: {type(exc).__name__}: {exc}")
                    await runtime.event_bus.publish(Event(type="task_failed", task_id=task_id, actor=agent_name, payload={"error": str(exc), "adapter": "gateway_worker_clone"}))
            control["status"] = "failed"
            control["run_id"] = None
            control["updated_at"] = datetime.now().isoformat()
            await self.owner._broadcast({
                "type": "workflow_error",
                "status": "error",
                "content": f"Gateway worker task failed on {agent_name}: {type(exc).__name__}: {exc}",
                "error": str(exc),
                "task_id": task_id,
                "agent_name": agent_name,
                "source": "orchestration",
                "adapter": "gateway_worker_clone",
            })
        finally:
            task = await runtime.repository.get(task_id)
            if task is not None:
                task.leased_agent_name = None
                task.touch()
                await runtime.repository.put(task)
            if lease is not None and owns_lease:
                await runtime.agent_pool.release(lease)
            # Runtime _run_task publishes the authoritative agent_released event
            # for externally scheduled tasks.  Do not duplicate it here.
            if isinstance(adapter_registry, dict):
                if current_adapter_task is None or adapter_registry.get(task_id) is current_adapter_task:
                    adapter_registry.pop(task_id, None)
            await runtime.event_bus.drain()

    async def handle_gui_command_result(
        self,
        task_id: str,
        result_event: Dict[str, Any],
        *,
        continuation_prompt: Optional[str] = None,
        wake: bool = True,
    ) -> Dict[str, Any]:
        """Attach a browser/client GUI command result to the originating task.

        Gateway GUI commands are fulfilled asynchronously by a frontend client.
        For orchestration-origin commands the result must be visible in both the
        orchestration task thread and the task-local GatewayActionWorkflow clone
        so a later continuation turn can reason over the actual result/capture.
        """
        runtime = self.owner.runtime
        task = await runtime.repository.get(task_id)
        if task is None:
            raise KeyError(task_id)
        event = dict(result_event or {})
        event.setdefault("type", "gui_command_result")
        event.setdefault("task_id", task_id)
        event.setdefault("session_id", self.owner.session_id)
        event.setdefault("source", "orchestration")
        await runtime._append_thread_entry(task_id, "tool", event)
        if continuation_prompt:
            await runtime._append_thread_entry(task_id, "system_gui_continuation", continuation_prompt)

        worker_session = self._worker_sessions.get(task_id)
        appended_to_worker = False
        if worker_session is not None:
            result_text = json.dumps(event, indent=2, sort_keys=True, default=str)[:40000]
            action_label = event.get("action") or "gui_command_result"
            history_payload = {
                "action": "gui_command_result",
                "gui_action": action_label,
                "command_id": event.get("command_id"),
                "ok": event.get("ok"),
                "source": "orchestration",
                "task_id": task_id,
            }
            async with worker_session["lock"]:
                infra = worker_session.get("infra")
                wf = worker_session.get("wf")
                if infra is not None and hasattr(infra, "append_chat_history"):
                    infra.append_chat_history(
                        actor="system",
                        content=f"[GUI COMMAND RESULT] {action_label}:\n{result_text}",
                        action=history_payload,
                        log_console=False,
                    )
                    if continuation_prompt:
                        infra.append_chat_history(
                            actor="system",
                            content=f"[GUI CONTINUATION] {continuation_prompt}",
                            action={"action": "gui_command_continuation", "command_id": event.get("command_id"), "task_id": task_id},
                            log_console=False,
                        )
                    appended_to_worker = True
                elif wf is not None and hasattr(wf, "update_history"):
                    wf.update_history(
                        actor="system",
                        content=f"[GUI COMMAND RESULT] {action_label}:\n{result_text}",
                        action=history_payload,
                        log_console=False,
                    )
                    if continuation_prompt:
                        wf.update_history(
                            actor="system",
                            content=f"[GUI CONTINUATION] {continuation_prompt}",
                            action={"action": "gui_command_continuation", "command_id": event.get("command_id"), "task_id": task_id},
                            log_console=False,
                        )
                    appended_to_worker = True
                if wf is not None and hasattr(wf, "save_session_state"):
                    try:
                        wf.save_session_state()
                    except Exception:
                        pass

        woke_task = False
        if wake and task.status == TaskStatus.WAITING:
            task.status = TaskStatus.READY
            task.error = None
            task.touch()
            await runtime.repository.put(task)
            woke_task = True
            await runtime.event_bus.publish(Event(
                type="task_ready",
                task_id=task_id,
                actor="gateway",
                payload={"reason": "gui_command_result", "command_id": event.get("command_id")},
            ))

        await runtime.event_bus.publish(Event(
            type="gui_command_result_attached",
            task_id=task_id,
            actor="gateway",
            payload={
                "command_id": event.get("command_id"),
                "action": event.get("action"),
                "ok": event.get("ok"),
                "appended_to_worker": appended_to_worker,
                "continuation_prompt_attached": bool(continuation_prompt),
                "woke_task": woke_task,
            },
        ))
        return {
            "status": "gui_command_result_attached",
            "task_id": task_id,
            "appended_to_worker": appended_to_worker,
            "continuation_prompt_attached": bool(continuation_prompt),
            "woke_task": woke_task,
        }


    async def _apply_orchestration_action_from_events(self, *, task_id: str, events: List[Dict[str, Any]], agent_name: str) -> Optional[str]:
        """Apply orchestration-native actions emitted by GatewayActionWorkflow.

        GatewayActionWorkflow validates model output as normal WOLF AgentAction
        envelopes.  For orchestration-native actions, execution inside the
        workflow is intentionally a no-op; this adapter translates the emitted
        envelope back into AsyncWorkflowRuntime actions and applies it to the
        current task.  This lets model-backed gateway worker clones create
        subtasks, wait, publish progress, fail, or explicitly complete without
        being auto-wrapped as a generic CompleteTaskAction.
        """
        runtime = self.owner.runtime
        selected: Optional[Dict[str, Any]] = None
        selected_name = ""
        for event in events or []:
            if not isinstance(event, dict):
                continue
            # Only bridge action-bearing workflow events.  A later
            # workflow_result may repeat action=create_subtasks but usually lacks
            # the original payload; do not let that clobber the actionable
            # workflow_action envelope selected here.
            if event.get("type") not in {"workflow_action", "orchestration_action"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            action_name = str(event.get("action") or payload.get("action") or "")
            if action_name in GATEWAY_ORCHESTRATION_ACTIONS:
                selected = dict(payload or {})
                selected.setdefault("action", action_name)
                selected_name = action_name
                break
        if not selected or not selected_name:
            return None
        task = await runtime.repository.get(task_id)
        if task is None:
            return None
        await runtime._append_thread_entry(task_id, "system", {"type": "orchestration_action_bridge", "action": selected_name, "payload": selected})
        action = runtime._coerce_action(selected)
        await runtime._apply_action(
            task,
            action,
            runtime.task_infras[task_id],
            runtime.policy_for(task.spec.workflow_type),
        )
        await runtime.event_bus.publish(Event(
            type="orchestration_action_applied",
            task_id=task_id,
            actor=agent_name,
            payload={"action": selected_name, "adapter": "gateway_worker_clone"},
        ))
        return selected_name

    def _supports_gateway_workflow(self, agent: Any) -> bool:
        return hasattr(agent, "get_structured_output_async") or hasattr(agent, "get_chat_response") or hasattr(agent, "get_chat_response_async")

    async def _complete_echo_like_task(self, *, task_id: str, agent_name: str, content: str) -> None:
        runtime = self.owner.runtime
        task = await runtime.repository.get(task_id)
        if task is None:
            return
        summary = f"Task completed by fallback worker {agent_name}."
        if content:
            summary = f"{summary} Request: {str(content)[:500]}"
        await runtime._apply_action(
            task,
            CompleteTaskAction(summary=summary, artifacts={}, trace=[{"adapter": "echo_fallback", "agent_name": agent_name}], confidence=0.5, important_findings=[summary[:500]]),
            runtime.task_infras[task_id],
            runtime.policy_for(task.spec.workflow_type),
        )
        await self.owner._broadcast({"type": "agent_response", "content": summary, "task_id": task_id, "agent_name": agent_name, "source": "orchestration", "adapter": "echo_fallback"})

    async def _augment_with_task_context(self, task_id: str, content: str) -> str:
        try:
            detail = await self.owner.runtime.get_task_detail(task_id)
        except Exception:
            return content
        pieces: List[str] = []
        task = detail.get("task") if isinstance(detail, dict) else {}
        if isinstance(task, dict):
            spec = task.get("spec") if isinstance(task.get("spec"), dict) else {}
            if spec.get("objective") and str(spec.get("objective")) not in content:
                pieces.append(f"Task objective: {spec.get('objective')}")
            if spec.get("parent_id"):
                pieces.append(f"Parent task: {spec.get('parent_id')}")
        local_messages = detail.get("local_messages") if isinstance(detail, dict) else None
        if isinstance(local_messages, list) and local_messages:
            try:
                # Keep the most recent task-local thread entries.  This is what
                # makes GUI command results and continuation prompts visible on
                # the next scheduled pass after a task has been WAITING.
                pieces.append("Recent task-local messages: " + json.dumps(local_messages[-20:], indent=2, sort_keys=True, default=str)[:20000])
            except Exception:
                pieces.append("Recent task-local messages: " + str(local_messages[-20:])[:20000])
        for label, key in [("Parent summary", "parent_summary"), ("Child summaries", "child_summaries"), ("Dependency summaries", "dependency_summaries")]:
            value = detail.get(key) if isinstance(detail, dict) else None
            if value:
                try:
                    pieces.append(f"{label}: {json.dumps(value, indent=2, sort_keys=True)[:12000]}")
                except Exception:
                    pieces.append(f"{label}: {str(value)[:12000]}")
        if not pieces:
            return content
        return content + "\n\n[Task-local orchestration context]\n" + "\n".join(pieces)

    def _build_task_worker_session(self, *, agent: Any, agent_name: str, task_id: str) -> Dict[str, Any]:
        task_root = Path(self.owner.session_dir) / "orchestration" / "worker_sessions" / task_id / self._safe_name(agent_name)
        task_root.mkdir(parents=True, exist_ok=True)
        base_params = copy.deepcopy(self.gateway_runtime.get("session_params") or DEFAULT_SESSION_PARAMS)
        base_params["session_dir"] = str(task_root)
        base_params["verbose"] = int((self.gateway_runtime.get("config") or {}).get("verbose", base_params.get("verbose", 0)) or 0)
        base_params["banner_image_width"] = int(base_params.get("banner_image_width", 80) or 80)
        base_params["LLMs"] = {"main": self._llm_entry_from_agent(agent)}
        # Gateway/orchestration mode must not instantiate a fresh Chroma client
        # for every task-worker session.  The gateway runtime owns process-wide
        # backend clients; worker sessions inherit the existing db_client and
        # create only task/session namespaced collections through that client.
        db_client = (
            self.gateway_runtime.get("db_client")
            or (self.gateway_runtime.get("session") or {}).get("db_client")
        )
        if db_client is not None:
            # Worker sessions share the gateway-owned Chroma client.  The default
            # memory vector-store params use generic collection names (summaries,
            # traces) and may set rebuild_vstore=True.  In concurrent orchestration
            # worker sessions that lets one worker delete a collection while another
            # still holds Chroma's collection handle, causing async ingestion errors
            # such as "Collection [...] does not exist".  Give every task-worker
            # memory store a stable task-local collection name and never rebuild the
            # shared client collection from a worker clone.
            self._namespace_worker_memory_vstores(base_params, task_id)
        session = setup_cli_session(
            session_params=base_params,
            db_client=db_client,
            workflow_cls=GatewayActionWorkflow,
        )
        wf = session["wf"]
        infra = wf.infra
        return {
            "agent": session["agents"]["main"],
            "wf": wf,
            "infra": infra,
            "managers": session["managers"],
            "session_dir": session["session_dir"],
            "db_client": session.get("db_client"),
            "run_control": self.owner.default_run_control(),
            "lock": asyncio.Lock(),
            "worker_agent_name": agent_name,
            "task_id": task_id,
        }

    def _namespace_worker_memory_vstores(self, base_params: Dict[str, Any], task_id: str) -> None:
        """Make worker memory Chroma collections task-local under the shared gateway client."""

        prefix = f"orch_{self._safe_name(self.owner.session_id)[:24]}_{self._safe_name(task_id)[:24]}"

        def _copy_and_set(value: Any, collection_name: str) -> Any:
            item = copy.deepcopy(value)
            if item is None:
                return None
            if isinstance(item, dict):
                item["collection_name"] = collection_name
                item["rebuild_vstore"] = False
                return item
            # Pydantic v1/v2 compatible copy path.
            try:
                return item.model_copy(update={"collection_name": collection_name, "rebuild_vstore": False})
            except Exception:
                pass
            try:
                return item.copy(update={"collection_name": collection_name, "rebuild_vstore": False})
            except Exception:
                pass
            # Last-resort mutable object path.
            try:
                setattr(item, "collection_name", collection_name)
                setattr(item, "rebuild_vstore", False)
            except Exception:
                return item
            return item

        summaries = base_params.get("summaries_params")
        traces = base_params.get("traces_params")
        if summaries is not None:
            base_params["summaries_params"] = _copy_and_set(summaries, f"{prefix}_summaries")
        if traces is not None:
            base_params["traces_params"] = _copy_and_set(traces, f"{prefix}_traces")

    def _llm_entry_from_agent(self, agent: Any) -> Dict[str, Any]:
        return {
            "provider_type": "openai",
            "host": getattr(agent, "host_address", None) or (self.gateway_runtime.get("config") or {}).get("host_address") or "http://localhost",
            "port": getattr(agent, "host_port", None),
            "api_key": None if str(getattr(agent, "api_key", "") or "").lower() in {"", "none", "***redacted***"} else getattr(agent, "api_key", None),
            "api_key_var": getattr(agent, "api_key_var", None) or (self.gateway_runtime.get("config") or {}).get("api_key_var"),
            "api_version": getattr(agent, "api_version", None) or "",
            "verbose": int(getattr(agent, "verbose", 0) or 0),
            "model": getattr(agent, "model", None) or (self.gateway_runtime.get("config") or {}).get("model"),
            "capabilities": list(getattr(agent, "capabilities", []) or []),
            "ctx_window_length": getattr(agent, "ctx_window_length", getattr(agent, "max_ctx_tokens", None)),
        }

    def _safe_name(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(value or "worker"))[:120]

    def _build_workflow_content(self, content: str, visual_context: Optional[Dict[str, Any]]) -> str:
        workflow_content = content
        visual_context = visual_context if isinstance(visual_context, dict) else {}
        if visual_context:
            try:
                vc_text = json.dumps(visual_context, indent=2, sort_keys=True)[:60000]
            except Exception:
                vc_text = str(visual_context)[:60000]
            workflow_content = (
                f"{content}\n\n"
                "[Wolf GUI visual workspace context attached by the user. "
                "Use this context when answering questions about what is visible in the GUI. "
                "If capture_capabilities says cross-origin iframe pixels/DOM are unavailable, explain that limitation and use available metadata.]\n"
                f"{vc_text}"
            )

        pending_capture_artifacts = self.gateway_runtime.pop("pending_gui_capture_artifacts", []) or []
        if pending_capture_artifacts:
            lines = [
                "",
                "[Deferred GUI capture artifact(s) from the previous GUI command result are attached below. "
                "Use these image pixels to answer the user's question about what is visible. "
                "If your model lacks vision capability, report the artifact metadata and image path instead.]",
            ]
            for idx, artifact in enumerate(pending_capture_artifacts, start=1):
                if not isinstance(artifact, dict):
                    continue
                compact = {
                    k: artifact.get(k)
                    for k in ("capture_id", "source_url", "image_path", "metadata_path", "width", "height", "status", "ok", "error")
                    if artifact.get(k) is not None
                }
                try:
                    lines.append(f"capture_artifact_{idx}: {json.dumps(compact, sort_keys=True)}")
                except Exception:
                    lines.append(f"capture_artifact_{idx}: {compact}")
                image_path = str(artifact.get("image_path") or "").strip()
                if image_path:
                    lines.append(f"<input> {image_path} </input>")
            workflow_content = f"{workflow_content}\n" + "\n".join(lines)
        return workflow_content

    def _role_for_event(self, event: Dict[str, Any]) -> str:
        typ = str(event.get("type") or "")
        if typ in {"agent_response", "workflow_result"}:
            return "assistant"
        if typ in {"workflow_error", "error"}:
            return "system"
        if typ in {"workflow_action", "gui_command", "gui_command_result"}:
            return "tool"
        return "system"

    def _workflow_failed(self, events: List[Dict[str, Any]]) -> bool:
        if any(isinstance(e, dict) and e.get("type") == "workflow_error" for e in events):
            return True
        for event in reversed(events):
            if isinstance(event, dict) and event.get("type") == "workflow_status" and event.get("status") == "done":
                return str(event.get("stop_reason") or "").strip().lower() == "error"
        return False

    def _failure_summary(self, events: List[Dict[str, Any]]) -> str:
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if event.get("type") == "workflow_error":
                return str(event.get("content") or event.get("error") or "workflow error")
        return "workflow reported an error"

    def _completion_summary(self, events: List[Dict[str, Any]]) -> str:
        """Choose the most meaningful Gateway workflow completion summary.

        GatewayActionWorkflow emits a generic trailing workflow_status/done event
        after meaningful workflow_result or agent_response events.  Child-summary
        propagation should prefer the actual send_message/result content over
        generic text such as ``Workflow turn complete (send_message).``.
        """
        generic_fallback = ""

        def _message_from_payload(payload: Any) -> str:
            if not isinstance(payload, dict):
                return ""
            nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else None
            for source in (nested, payload):
                if not isinstance(source, dict):
                    continue
                for key in ("message", "content", "summary"):
                    value = source.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                    if value:
                        return str(value).strip()
                lines = source.get("message_lines")
                if isinstance(lines, list):
                    joined = "\n".join(str(line) for line in lines).strip()
                    if joined:
                        return joined
                encoded = source.get("message_base64")
                if isinstance(encoded, str) and encoded.strip():
                    try:
                        decoded = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8").strip()
                        if decoded:
                            return decoded
                    except Exception:
                        pass
            return ""

        # First pass: prefer meaningful model/tool result content regardless of
        # a later generic workflow_status event.
        for event in reversed(events or []):
            if not isinstance(event, dict):
                continue
            typ = event.get("type")
            if typ == "workflow_result":
                result_action = event.get("action") or (event.get("payload") or {}).get("action") or (event.get("normalized") or {}).get("action")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if result_action == "send_message":
                    message = _message_from_payload(payload) or str(event.get("content") or "").strip()
                    if message and not message.startswith("Workflow turn complete"):
                        return message
                content = str(event.get("content") or "").strip()
                if content and not content.startswith("Workflow turn complete"):
                    return content
            if typ == "agent_response" and event.get("content"):
                content = str(event.get("content") or "").strip()
                if content:
                    return content

        # Second pass: keep status/done text only as fallback when no meaningful
        # result was available.
        for event in reversed(events or []):
            if not isinstance(event, dict):
                continue
            if event.get("type") == "workflow_status" and event.get("status") == "done" and event.get("content"):
                generic_fallback = str(event.get("content") or "")
                break
        return generic_fallback or "Task completed through GatewayActionWorkflow."


class GatewayOrchestrationSession:
    """Owns a per-session orchestration runtime and gateway event bridge."""

    def __init__(
        self,
        *,
        session_id: str,
        account_id: str,
        config: Dict[str, Any],
        session_dir: str,
        broadcaster: GatewayBroadcaster,
        gateway_runtime: Optional[Dict[str, Any]] = None,
        resolve_action_names: Optional[ResolveActionNames] = None,
        resolve_execution_policy: Optional[ResolveExecutionPolicy] = None,
        gui_command_from_workflow_event: Optional[GuiCommandExtractor] = None,
        should_auto_continue_gui_command: Optional[AutoContinuePredicate] = None,
    ) -> None:
        self.session_id = session_id
        self.account_id = account_id
        self.config = dict(config or {})
        self.session_dir = str(session_dir)
        self._broadcaster = broadcaster
        self._stop_event = asyncio.Event()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._gui_command_timeout_task: Optional[asyncio.Task] = None
        self.gui_command_timeout_seconds = max(1.0, float(self.config.get("gui_command_timeout_seconds") or 60.0))
        self._started = False
        self._lock = asyncio.Lock()
        self.root_task_ids: List[str] = []
        self.last_task_id: Optional[str] = None
        self.created_at = datetime.now().isoformat()
        self._adapter_tasks: Dict[str, asyncio.Task] = {}
        self._forwarded_event_ids: set[str] = set()
        self._use_gateway_adapter = bool(
            gateway_runtime is not None
            and resolve_action_names is not None
            and resolve_execution_policy is not None
            and gui_command_from_workflow_event is not None
            and should_auto_continue_gui_command is not None
        )
        self.gateway_adapter: Optional[GatewayTaskWorkflowAdapter] = None
        if self._use_gateway_adapter:
            self.gateway_adapter = GatewayTaskWorkflowAdapter(
                owner=self,
                gateway_runtime=gateway_runtime or {},
                resolve_action_names=resolve_action_names,  # type: ignore[arg-type]
                resolve_execution_policy=resolve_execution_policy,  # type: ignore[arg-type]
                gui_command_from_workflow_event=gui_command_from_workflow_event,  # type: ignore[arg-type]
                should_auto_continue_gui_command=should_auto_continue_gui_command,  # type: ignore[arg-type]
            )

        worker_count = max(1, int(self.config.get("orchestration_worker_count") or 1))
        self.pool_controller: Optional[AgentPoolController] = None
        # AgentPoolController.create is async; build the initial pool synchronously
        # through a local event-loop-safe helper to keep __init__ non-async.
        from framework.orchestration.profiles import AgentFactory, normalize_mix, normalize_profiles
        from framework.orchestration.agent_pool import AgentPool
        profiles = normalize_profiles(self.config.get("agent_profiles"), self.config)
        mix = normalize_mix(self.config.get("agent_pool_mix"), profiles, worker_count)
        pool = AgentPool([])
        controller = AgentPoolController(pool=pool, profiles=profiles, mix=mix, factory=AgentFactory(self.config), session_id=session_id)
        # Populate initial target mix without awaiting public locks; __init__ is single-threaded.
        for profile in profiles:
            target = int(mix.get(profile.profile_id, 0) or 0)
            if profile.max_instances is not None:
                target = min(target, max(0, int(profile.max_instances)))
            for idx in range(1, target + 1):
                agent = controller.factory.build(profile, instance_index=idx, session_id=session_id)
                desc_meta = {"profile": profile.to_dict(redact=True)}
                # Direct insert is safe before runtime starts.
                from framework.orchestration.agent_pool import AgentDescriptor
                desc = AgentDescriptor.from_agent(agent)
                desc.profile_id = profile.profile_id
                desc.agent_kind = profile.agent_kind
                desc.metadata = desc_meta
                pool._agents[desc.name] = desc
                controller._counters[profile.profile_id] = idx
        self.pool_controller = controller

        budgets = ResourceBudget(
            max_total_tasks=max(1, int(self.config.get("orchestration_max_total_tasks") or 128)),
            max_active_tasks=max(1, int(self.config.get("orchestration_max_active_tasks") or max(1, sum(mix.values())))),
        )
        engine_config = EngineConfig(budgets=budgets)
        session_root = Path(self.session_dir) / "orchestration"
        infra_factory = TaskInfrastructureFactory(session_root=str(session_root))
        self.runtime = AsyncWorkflowRuntime(
            agent_pool=pool,
            infra_factory=infra_factory,
            config=engine_config,
            enable_streaming=False,
            enable_websocket=False,
        )
        if self.gateway_adapter is not None:
            self.runtime.external_task_executor = self.gateway_adapter.execute_task
        self.runtime.event_bus.subscribe(self._on_event)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("orchestration_enabled"))

    def default_run_control(self) -> Dict[str, Any]:
        return {
            "run_id": None,
            "status": "idle",
            "pause_requested": False,
            "stop_requested": False,
            "reassess_requested": False,
            "pending_user_messages": [],
            "step": 0,
            "updated_at": datetime.now().isoformat(),
        }

    async def _event_pump(self, stop_event: asyncio.Event) -> None:
        if not self.runtime._started:
            await self.runtime.start()
        while not stop_event.is_set():
            await self.runtime.event_bus.drain()
            await asyncio.sleep(self.runtime.config.scheduler_poll_interval)

    async def _gui_command_timeout_monitor(self, stop_event: asyncio.Event) -> None:
        """Fail orchestration GUI commands that were never answered by the browser.

        Orchestration tasks are moved to WAITING after an auto-continuable GUI
        command is emitted. If the browser client never returns a
        gui_command_result, the task would otherwise remain blocked forever.
        This monitor converts stale pending commands into synthetic failed GUI
        results and wakes the originating task so the worker can recover, retry,
        or report the unavailable browser action.
        """
        poll_interval = min(5.0, max(0.25, self.gui_command_timeout_seconds / 4.0))
        while not stop_event.is_set():
            try:
                await asyncio.sleep(poll_interval)
                if stop_event.is_set() or self.gateway_adapter is None:
                    continue
                runtime = self.gateway_adapter.gateway_runtime
                pending = runtime.setdefault("pending_gui_commands", {}) if isinstance(runtime, dict) else {}
                if not isinstance(pending, dict) or not pending:
                    continue
                now = time.monotonic()
                expired: List[Dict[str, Any]] = []
                for command_id, item in list(pending.items()):
                    if not isinstance(item, dict):
                        continue
                    if item.get("source") != "orchestration" or not item.get("target_task_id"):
                        continue
                    timeout_seconds = max(1.0, float(item.get("timeout_seconds") or self.gui_command_timeout_seconds))
                    created = item.get("created_monotonic")
                    if created is None:
                        try:
                            created_at = datetime.fromisoformat(str(item.get("created_at") or ""))
                            created = now - max(0.0, (datetime.now() - created_at).total_seconds())
                        except Exception:
                            created = now
                        item["created_monotonic"] = created
                    if now - float(created) >= timeout_seconds:
                        removed = pending.pop(command_id, None)
                        if isinstance(removed, dict):
                            expired.append(removed)
                for item in expired:
                    command_id = item.get("command_id")
                    task_id = item.get("target_task_id")
                    action = item.get("action") or "gui_command"
                    timeout_seconds = max(1.0, float(item.get("timeout_seconds") or self.gui_command_timeout_seconds))
                    result_event = {
                        "type": "gui_command_result",
                        "command_id": command_id,
                        "action": action,
                        "ok": False,
                        "content": f"GUI command timed out after {timeout_seconds:g} seconds without a browser/client result.",
                        "result": {"ok": False, "status": "timeout", "timeout_seconds": timeout_seconds},
                        "error": "gui_command_timeout",
                        "timestamp": datetime.now().isoformat(),
                        "session_id": self.session_id,
                        "task_id": task_id,
                        "source": "orchestration",
                    }
                    continuation_prompt = (
                        f"The browser/client did not return a result for GUI command {command_id or '<unknown>'} "
                        f"({action}) within {timeout_seconds:g} seconds. Treat this as a failed tool action; "
                        "continue by explaining the timeout, choosing a safe fallback, or asking the user to retry."
                    )
                    try:
                        attach = await self.handle_gui_command_result(
                            str(task_id),
                            result_event,
                            continuation_prompt=continuation_prompt,
                            wake=True,
                        )
                        result_event["orchestration_attach"] = attach
                    except Exception as exc:
                        result_event["history_bridge_error"] = f"{type(exc).__name__}: {exc}"
                    await self._broadcast(result_event)
                    await self._broadcast({
                        "type": "workflow_status",
                        "status": "gui_command_timeout",
                        "content": result_event["content"],
                        "command_id": command_id,
                        "action": action,
                        "task_id": task_id,
                        "timestamp": datetime.now().isoformat(),
                        "session_id": self.session_id,
                    })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._broadcast({
                    "type": "workflow_error",
                    "status": "error",
                    "content": f"GUI command timeout monitor error: {type(exc).__name__}: {exc}",
                    "error": str(exc),
                    "timestamp": datetime.now().isoformat(),
                    "session_id": self.session_id,
                })

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._stop_event = asyncio.Event()
            await self.runtime.start()
            self._scheduler_task = asyncio.create_task(
                self.runtime.run_forever(self._stop_event),
                name=f"gateway-orchestration-{self.session_id[:8]}",
            )
            if self.gateway_adapter is not None:
                self._gui_command_timeout_task = asyncio.create_task(
                    self._gui_command_timeout_monitor(self._stop_event),
                    name=f"gateway-orchestration-gui-timeouts-{self.session_id[:8]}",
                )
            self._started = True
        await self._broadcast(
            {
                "type": "workflow_status",
                "status": "orchestration_started",
                "content": "Orchestration runtime started for this session.",
            }
        )

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._stop_event.set()
            task = self._scheduler_task
            gui_timeout_task = self._gui_command_timeout_task
            self._scheduler_task = None
            self._gui_command_timeout_task = None
            self._started = False
        if gui_timeout_task is not None:
            try:
                await asyncio.wait_for(gui_timeout_task, timeout=2.0)
            except asyncio.TimeoutError:
                gui_timeout_task.cancel()
            except Exception:
                pass
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
            except Exception:
                pass
        for adapter_task in list(self._adapter_tasks.values()):
            if adapter_task is not None and not adapter_task.done():
                adapter_task.cancel()
        self._adapter_tasks.clear()
        await self.runtime.stop()
        await self._broadcast(
            {
                "type": "workflow_status",
                "status": "orchestration_stopped",
                "content": "Orchestration runtime stopped for this session.",
            }
        )

    async def submit_user_message(
        self,
        content: str,
        *,
        sender: str = "user",
        visual_context: Optional[Dict[str, Any]] = None,
        target_task_id: Optional[str] = None,
        force_new_root: bool = False,
    ) -> Dict[str, Any]:
        """Route a gateway chat message into orchestration.

        If target_task_id is supplied, the message is injected into that task's
        local thread. Otherwise a new root task is submitted.  A persistent root
        coordinator can be layered on later without changing the gateway surface.
        """
        await self.start()
        content = str(content or "")
        visual_context = visual_context if isinstance(visual_context, dict) else {}
        await self._broadcast(
            {
                "type": "user_echo",
                "content": content,
                "sender": sender,
            }
        )

        if target_task_id and not force_new_root:
            payload: Any = content
            if visual_context:
                payload = {"content": content, "visual_context": visual_context}
            return await self.inject_user_message(target_task_id, payload, role=sender or "user")

        objective = content
        inputs: Dict[str, Any] = {"content": content, "sender": sender}
        if visual_context:
            inputs["visual_context"] = visual_context
            objective = f"{content}\n\n[User attached GUI visual context; inspect task inputs.visual_context.]"
        spec = TaskSpec(
            name=f"User request {len(self.root_task_ids) + 1}",
            objective=objective,
            workflow_type="chat",
            inputs=inputs,
            priority=0,
            session_id=self.session_id,
        )
        task_id = await self.runtime.submit_root_task(spec)
        await self.runtime.inject_user_message(task_id, content, role=sender or "user", wake=True)
        self.root_task_ids.append(task_id)
        self.last_task_id = task_id
        await self._broadcast(
            {
                "type": "workflow_status",
                "status": "root_task_submitted",
                "content": f"Submitted orchestration root task {task_id[:8]}.",
                "task_id": task_id,
            }
        )
        return {"status": "root_task_submitted", "task_id": task_id}

    async def inject_user_message(self, task_id: str, content: Any, *, role: str = "user") -> Dict[str, Any]:
        await self.start()
        await self.runtime.inject_user_message(task_id, content, role=role, wake=True)
        self.last_task_id = task_id
        return {"status": "message_injected", "task_id": task_id}

    async def handle_gui_command_result(
        self,
        task_id: str,
        result_event: Dict[str, Any],
        *,
        continuation_prompt: Optional[str] = None,
        wake: bool = True,
    ) -> Dict[str, Any]:
        if self.gateway_adapter is None:
            await self.runtime._append_thread_entry(task_id, "tool", result_event)
            if continuation_prompt:
                await self.runtime._append_thread_entry(task_id, "system_gui_continuation", continuation_prompt)
            task = await self.runtime.repository.get(task_id)
            woke_task = False
            if wake and task is not None and task.status == TaskStatus.WAITING:
                task.status = TaskStatus.READY
                task.error = None
                task.touch()
                await self.runtime.repository.put(task)
                woke_task = True
                await self.runtime.event_bus.publish(Event(type="task_ready", task_id=task_id, actor="gateway", payload={"reason": "gui_command_result"}))
            return {"status": "gui_command_result_attached", "task_id": task_id, "appended_to_worker": False, "continuation_prompt_attached": bool(continuation_prompt), "woke_task": woke_task}
        return await self.gateway_adapter.handle_gui_command_result(task_id, result_event, continuation_prompt=continuation_prompt, wake=wake)

    async def pause_task(self, task_id: str, reason: str = "paused by user") -> Dict[str, Any]:
        await self.runtime.pause_task(task_id, reason=reason)
        return {"status": "paused", "task_id": task_id}

    async def resume_task(self, task_id: str, reason: str = "resumed by user") -> Dict[str, Any]:
        await self.start()
        await self.runtime.resume_task(task_id, reason=reason)
        return {"status": "resumed", "task_id": task_id}

    async def cancel_task(self, task_id: str, reason: str = "cancelled by user") -> Dict[str, Any]:
        await self.runtime.cancel_task(task_id, reason=reason)
        return {"status": "cancelled", "task_id": task_id}

    async def retry_task(self, task_id: str, reason: str = "retried by user") -> Dict[str, Any]:
        await self.start()
        await self.runtime.retry_task(task_id, reason=reason)
        return {"status": "retried", "task_id": task_id}

    async def cancel_subtree(self, task_id: str, reason: str = "cancelled subtree by user", include_root: bool = True) -> Dict[str, Any]:
        return await self.runtime.cancel_subtree(task_id, reason=reason, include_root=include_root)

    async def retry_subtree(self, task_id: str, reason: str = "retried subtree by user", include_root: bool = True, include_completed: bool = False) -> Dict[str, Any]:
        await self.start()
        return await self.runtime.retry_subtree(task_id, reason=reason, include_root=include_root, include_completed=include_completed)

    async def request_replan(self, task_id: str, reason: str = "replan requested by user", prompt: Optional[str] = None) -> Dict[str, Any]:
        await self.start()
        return await self.runtime.request_replan(task_id, reason=reason, prompt=prompt)

    async def get_task_detail(self, task_id: str) -> Dict[str, Any]:
        return await self.runtime.get_task_detail(task_id)

    async def agent_pool_snapshot(self) -> Dict[str, Any]:
        if self.pool_controller is None:
            return {"type": "agent_pool_snapshot", "session_id": self.session_id, "agent_profiles": [], "agent_pool_mix": {}, "profiles": [], "agents": []}
        return await self.pool_controller.snapshot()

    async def update_agent_pool_mix(self, body: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(body or {})
        profiles = body.get("agent_profiles") if "agent_profiles" in body else body.get("profiles")
        mix = body.get("agent_pool_mix") if "agent_pool_mix" in body else body.get("mix", {})
        if self.pool_controller is None:
            raise RuntimeError("orchestration agent pool is not initialized")
        snap = await self.pool_controller.update_mix(mix or {}, profiles if isinstance(profiles, list) else None, base_config=self.config)
        self.config["agent_profiles"] = snap.get("agent_profiles", [])
        self.config["agent_pool_mix"] = snap.get("agent_pool_mix", {})
        self.config["orchestration_worker_count"] = int(sum((snap.get("agent_pool_mix") or {}).values()) or 1)
        await self._broadcast({"type": "agent_pool_update", "content": "Orchestration agent pool mix updated.", "agent_pool": snap})
        return snap

    def worker_sessions_snapshot(self) -> List[Dict[str, Any]]:
        if self.gateway_adapter is None:
            return []
        return self.gateway_adapter.worker_sessions_snapshot()

    async def snapshot(self) -> Dict[str, Any]:
        snap = await self.runtime.snapshot()
        pool_snap = await self.agent_pool_snapshot()
        snap.update(
            {
                "type": "orchestration_snapshot",
                "session_id": self.session_id,
                "enabled": self.enabled,
                "started": self._started,
                "root_task_ids": list(self.root_task_ids),
                "last_task_id": self.last_task_id,
                "adapter": "gateway_workflow" if self._use_gateway_adapter else "echo_scheduler",
                "adapter_task_ids": list(self._adapter_tasks.keys()),
                "agent_profiles": pool_snap.get("agent_profiles", []),
                "agent_pool_mix": pool_snap.get("agent_pool_mix", {}),
                "agent_pool_summary": pool_snap,
                "created_at": self.created_at,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return snap

    async def _on_event(self, event: Event) -> None:
        # EventBus delivery should normally be once-only, but gateway websocket
        # forwarding is user-visible and must be robust against accidental
        # double-drain/re-subscription/replay of the same Event object.
        # Deduplicate by stable Event.id before broadcasting any compatibility
        # fanout messages.
        event_id = str(getattr(event, "id", "") or "")
        if event_id and event_id in self._forwarded_event_ids:
            return
        if event_id:
            self._forwarded_event_ids.add(event_id)
            # Bound memory in long-lived sessions.  Exact eviction order is not
            # semantically important; this is only a replay guard.
            if len(self._forwarded_event_ids) > 10000:
                self._forwarded_event_ids = set(tuple(self._forwarded_event_ids)[-5000:])

        payload = {
            "type": "orchestration_event",
            "event_type": event.type,
            "task_id": event.task_id,
            "actor": event.actor,
            "payload": self._jsonable(event.payload),
            "event_id": event.id,
            "event_ts": event.ts,
            "content": self._event_content(event),
        }
        await self._broadcast(payload)

        # Compatibility fanout for the existing GUI chat renderer.
        if event.type == "assistant_message":
            message = ""
            if isinstance(event.payload, dict):
                message = str(event.payload.get("message") or "")
            if message:
                await self._broadcast(
                    {
                        "type": "agent_response",
                        "content": message,
                        "task_id": event.task_id,
                        "actor": event.actor,
                        "source": "orchestration",
                    }
                )
        elif event.type == "task_completed":
            summary = ""
            if isinstance(event.payload, dict):
                summary = str(event.payload.get("summary") or "")
            if summary:
                await self._broadcast(
                    {
                        "type": "agent_response",
                        "content": summary,
                        "task_id": event.task_id,
                        "actor": event.actor,
                        "source": "orchestration",
                    }
                )
        elif event.type in {"task_failed", "user_input_requested"}:
            await self._broadcast(
                {
                    "type": "workflow_status" if event.type == "user_input_requested" else "workflow_error",
                    "status": event.type,
                    "content": self._event_content(event),
                    "task_id": event.task_id,
                    "actor": event.actor,
                    "source": "orchestration",
                }
            )

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        message.setdefault("session_id", self.session_id)
        message.setdefault("timestamp", datetime.now().isoformat())
        await self._broadcaster(message, self.session_id)

    def _event_content(self, event: Event) -> str:
        data = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "task_registered":
            return f"Task registered: {data.get('name') or event.task_id[:8]}"
        if event.type == "task_started":
            return f"Task {event.task_id[:8]} started on {event.actor}."
        if event.type == "task_completed":
            return str(data.get("summary") or f"Task {event.task_id[:8]} completed.")
        if event.type == "task_failed":
            return str(data.get("error") or f"Task {event.task_id[:8]} failed.")
        if event.type == "task_progress":
            return str(data.get("message") or f"Task {event.task_id[:8]} progress update.")
        if event.type == "assistant_message":
            return str(data.get("message") or "Assistant message.")
        if event.type == "user_input_requested":
            return str(data.get("question") or "User input requested.")
        return f"Orchestration event: {event.type}"

    def _jsonable(self, value: Any) -> Any:
        if is_dataclass(value):
            return self._jsonable(asdict(value))
        if isinstance(value, dict):
            return {str(k): self._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._jsonable(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
