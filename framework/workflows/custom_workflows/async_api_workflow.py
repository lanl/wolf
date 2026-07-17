import asyncio
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from framework.workflows.base_workflow import BaseWorkflow
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.sessions_data_models import BaseSession

class AsyncAPIWorkflow(BaseWorkflow):
    """
    Workflow that exposes the agentic loop via a FastAPI interface.
    Allows for asynchronous interaction, telemetry endpoints, and 
    non-blocking execution.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.WF_TAG = "AsyncAPIWorkflow"
        self.is_running = False

    async def run_async(self, user_name: str = "user"):
        """
        Asynchronous entry point.
        """
        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"
        self.is_running = True

    async def _handle_actor_turn_async(self, actor, name: str):
        """
        Async version of _handle_actor_turn to prevent blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        # We wrap the blocking logic in a thread pool executor
        await loop.run_in_executor(None, self._handle_actor_turn, actor, name)

    def get_telemetry(self) -> Dict[str, Any]:
        """
        Gathers diagnostics from infrastructure managers.
        """
        ctx_diag = self.infra.context_manager.get_context_diagnostics()
        return {
            "session_id": self.session.infra.chat_manager.session_dir,
            "context_utilization": ctx_diag,
            "history_length": len(self.infra.chat_history),
            "active_workers": list(self.infra.workers.keys()),
            "workflow_turn": self.WORKFLOW_TURN
        }

# --- FastAPI Application Wrapper ---

app = FastAPI(title="Cerberus Async API")

sessions: Dict[str, AsyncAPIWorkflow] = {}

class SessionConfig(BaseModel):
    session_id: str
    user_name: str = "user"
    resume_from: Optional[str] = None

@app.post("/session/start")
async def start_session(config: SessionConfig):
    if config.session_id in sessions:
        return {"status": "already_running", "session_id": config.session_id}
    
    try:
        # Integration with BaseWorkflow session loading
        wf = AsyncAPIWorkflow(session=config.resume_from, wf_user=config.user_name)
        await wf.run_async(user_name=config.user_name)
        sessions[config.session_id] = wf
        return {"status": "started", "session_id": config.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session/{session_id}/telemetry")
async def get_session_telemetry(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id].get_telemetry()

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    if session_id not in sessions:
        await websocket.send_text("Error: Session not found.")
        await websocket.close()
        return

    wf = sessions[session_id]
    try:
        while True:
            data = await websocket.receive_text()
            
            # 1. Update history
            wf.update_history(actor=wf.WF_USER, content=data, action={"action": "user_input"})
            wf.WORKFLOW_TURN = "system"

            # 2. Async Process turn
            await wf._handle_actor_turn_async(wf.agent, wf.agent.name)

            # 3. Push response
            last_entry = wf.infra.chat_history[-1]
            await websocket.send_json({
                "actor": last_entry.get("role", "unknown"),
                "content": last_entry.get("content", "")
            })

    except WebSocketDisconnect:
        print(f"Client disconnected from session {session_id}")
