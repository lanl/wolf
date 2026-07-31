"""Flask web application for async orchestration monitoring and control.

Provides a comprehensive web UI for:
- Real-time event streaming
- Task tree visualization
- Chat interface for task interaction
- Agent pool monitoring
- Session/workflow tracking
- System metrics and diagnostics
"""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import asyncio
import json
import logging
from typing import Optional
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from framework.orchestration.runtime import AsyncWorkflowRuntime
from framework.orchestration.agent_pool import AgentPool
from framework.orchestration.task_infra import TaskInfrastructureFactory
from framework.orchestration.models import TaskSpec, AgentRequirements
from framework.orchestration.streaming import StreamingInterface

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'orchestration-ui-secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global runtime instance
runtime: Optional[AsyncWorkflowRuntime] = None
event_subscription_id: Optional[str] = None


@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('index.html')


@app.route('/api/runtime/snapshot', methods=['GET'])
async def get_runtime_snapshot():
    """Get current runtime state snapshot."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    snapshot = await runtime.snapshot()
    return jsonify(snapshot)


@app.route('/api/tasks', methods=['GET'])
async def list_tasks():
    """List all tasks."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    tasks = await runtime.repository.list()
    return jsonify([
        {
            'id': t.id,
            'name': t.spec.name,
            'status': t.status.value,
            'workflow_type': t.spec.workflow_type,
            'depth': t.depth,
            'parent_id': t.spec.parent_id,
            'created_at': t.created_at,
            'updated_at': t.updated_at
        }
        for t in tasks
    ])


@app.route('/api/tasks/<task_id>', methods=['GET'])
async def get_task_detail(task_id: str):
    """Get detailed task information."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    try:
        detail = await runtime.get_task_detail(task_id)
        return jsonify(detail)
    except KeyError:
        return jsonify({'error': f'Task {task_id} not found'}), 404


@app.route('/api/tasks', methods=['POST'])
async def create_task():
    """Create a new root task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    spec = TaskSpec(
        name=data['name'],
        objective=data['objective'],
        workflow_type=data.get('workflow_type', 'generic'),
        inputs=data.get('inputs', {}),
        session_id=data.get('session_id')
    )
    
    task_id = await runtime.submit_root_task(spec)
    return jsonify({'task_id': task_id})


@app.route('/api/tasks/<task_id>/inject_message', methods=['POST'])
async def inject_message(task_id: str):
    """Inject a user message into a task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    await runtime.inject_user_message(
        task_id,
        data['content'],
        role=data.get('role', 'user'),
        wake=data.get('wake', True)
    )
    return jsonify({'status': 'ok'})


@app.route('/api/tasks/<task_id>/pause', methods=['POST'])
async def pause_task(task_id: str):
    """Pause a task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    await runtime.pause_task(task_id, reason=data.get('reason', 'paused via UI'))
    return jsonify({'status': 'ok'})


@app.route('/api/tasks/<task_id>/resume', methods=['POST'])
async def resume_task(task_id: str):
    """Resume a task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    await runtime.resume_task(task_id, reason=data.get('reason', 'resumed via UI'))
    return jsonify({'status': 'ok'})


@app.route('/api/tasks/<task_id>/cancel', methods=['POST'])
async def cancel_task(task_id: str):
    """Cancel a task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    await runtime.cancel_task(task_id, reason=data.get('reason', 'cancelled via UI'))
    return jsonify({'status': 'ok'})


@app.route('/api/tasks/<task_id>/retry', methods=['POST'])
async def retry_task(task_id: str):
    """Retry a failed task."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    data = request.json
    await runtime.retry_task(task_id, reason=data.get('reason', 'retried via UI'))
    return jsonify({'status': 'ok'})


@app.route('/api/agent_pool/stats', methods=['GET'])
async def get_agent_pool_stats():
    """Get agent pool statistics."""
    if not runtime:
        return jsonify({'error': 'Runtime not initialized'}), 503
    
    stats = await runtime.agent_pool.stats()
    return jsonify(stats)


@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    logger.info(f"Client connected: {request.sid}")
    emit('connection', {'status': 'connected', 'client_id': request.sid})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection."""
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on('subscribe')
def handle_subscribe(data):
    """Subscribe to event stream."""
    logger.info(f"Client {request.sid} subscribing with filters: {data}")
    # Start streaming events to this client
    if runtime and runtime.streaming:
        asyncio.run(stream_events_to_client(request.sid, data))


async def stream_events_to_client(client_id: str, filters: dict):
    """Stream events to a specific client."""
    if not runtime or not runtime.streaming:
        return
    
    subscription_id = runtime.streaming.subscribe(
        task_ids=set(filters.get('task_ids', [])) or None,
        event_types=set(filters.get('event_types', [])) or None
    )
    
    try:
        while True:
            event = await runtime.streaming.get_events(subscription_id, timeout=1.0)
            if event:
                socketio.emit('event', {
                    'type': event.type,
                    'task_id': event.task_id,
                    'actor': event.actor,
                    'payload': event.payload,
                    'timestamp': event.ts,
                    'id': event.id
                }, room=client_id)
    except Exception as e:
        logger.error(f"Error streaming to client {client_id}: {e}")
    finally:
        await runtime.streaming.unsubscribe(subscription_id)


def init_runtime(agent_pool: AgentPool, infra_factory: TaskInfrastructureFactory):
    """Initialize the runtime for the web UI."""
    global runtime
    runtime = AsyncWorkflowRuntime(
        agent_pool=agent_pool,
        infra_factory=infra_factory,
        enable_streaming=True,
        enable_websocket=False  # We're using Flask-SocketIO instead
    )
    asyncio.run(runtime.start())
    logger.info("Runtime initialized for web UI")


if __name__ == '__main__':
    # This is for testing - in production, use init_runtime() from outside
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
