"""Example usage patterns for async orchestration with coordination patterns.

Demonstrates:
- Basic task submission and monitoring
- Map-reduce pattern
- Pipeline pattern
- Fan-out/fan-in pattern
- Scatter-gather pattern
- Real-time streaming with progress monitoring
- Interactive tasks with user input injection
"""

import asyncio
from typing import List

from .runtime import AsyncWorkflowRuntime
from .models import TaskSpec, AgentRequirements, EngineConfig
from .agent_pool import AgentPool
from .task_infra import TaskInfrastructureFactory
from .coordination import CoordinationPatterns
from .streaming import stream_task_progress, wait_for_task_completion


# Example 1: Basic task submission with streaming progress monitoring
async def example_basic_task_with_streaming():
    """Submit a task and monitor progress in real-time."""
    print("\n=== Example 1: Basic Task with Streaming ===")
    
    # Setup (assume agent_pool and infra_factory are available)
    agent_pool = AgentPool([])  # Add your agents here
    infra_factory = TaskInfrastructureFactory()
    
    # Create runtime with streaming enabled
    runtime = AsyncWorkflowRuntime(
        agent_pool=agent_pool,
        infra_factory=infra_factory,
        enable_streaming=True,
        enable_websocket=False  # Can enable for WebSocket support
    )
    
    await runtime.start()
    
    # Submit a task
    spec = TaskSpec(
        name="data_analysis",
        objective="Analyze the provided dataset and generate insights",
        workflow_type="generic",
        inputs={"dataset_path": "/path/to/data.csv"}
    )
    
    task_id = await runtime.submit_root_task(spec)
    print(f"Task submitted: {task_id}")
    
    # Stream progress updates
    async for event in stream_task_progress(runtime.streaming, task_id):
        print(f"[{event.type}] {event.payload}")
        
        if event.type == "task_progress":
            print(f"  Progress: {event.payload.get('message', 'Working...')}")
        elif event.type == "assistant_message":
            print(f"  Agent: {event.payload.get('message', '')}")
        elif event.type in {"task_completed", "task_failed"}:
            print(f"  Task finished with status: {event.type}")
            break
    
    await runtime.stop()


# Example 2: Map-reduce pattern for parallel processing
async def example_map_reduce():
    """Process multiple items in parallel and aggregate results."""
    print("\n=== Example 2: Map-Reduce Pattern ===")
    
    agent_pool = AgentPool([])  # Add your agents
    infra_factory = TaskInfrastructureFactory()
    runtime = AsyncWorkflowRuntime(agent_pool, infra_factory, enable_streaming=True)
    await runtime.start()
    
    # Create coordination patterns helper
    coordinator = CoordinationPatterns(runtime)
    
    # Items to process in parallel
    data_files = [
        "/data/file1.csv",
        "/data/file2.csv",
        "/data/file3.csv",
        "/data/file4.csv"
    ]
    
    # Execute map-reduce
    result = await coordinator.map_reduce.execute(
        name="parallel_data_processing",
        map_items=data_files,
        map_objective="Process data file: {item}",
        reduce_objective="Aggregate results from all processed files",
        workflow_type="generic"
    )
    
    if result.success:
        print(f"Map-reduce completed successfully!")
        print(f"Total tasks: {len(result.task_ids)}")
        print(f"Final result: {result.results[-1]}")
    else:
        print(f"Map-reduce failed. Failed tasks: {result.failed_tasks}")
    
    await runtime.stop()


# Example 3: Pipeline pattern for sequential processing
async def example_pipeline():
    """Execute tasks sequentially with data flow between stages."""
    print("\n=== Example 3: Pipeline Pattern ===")
    
    agent_pool = AgentPool([])  # Add your agents
    infra_factory = TaskInfrastructureFactory()
    runtime = AsyncWorkflowRuntime(agent_pool, infra_factory, enable_streaming=True)
    await runtime.start()
    
    coordinator = CoordinationPatterns(runtime)
    
    # Define pipeline stages
    stages = [
        {"name": "collect", "objective": "Collect raw data from API"},
        {"name": "clean", "objective": "Clean and validate the collected data"},
        {"name": "analyze", "objective": "Analyze the cleaned data"},
        {"name": "visualize", "objective": "Create visualizations from analysis results"}
    ]
    
    # Execute pipeline
    result = await coordinator.pipeline.execute(
        name="data_pipeline",
        stages=stages,
        initial_input={"api_endpoint": "https://api.example.com/data"},
        workflow_type="generic"
    )
    
    if result.success:
        print(f"Pipeline completed successfully!")
        print(f"Stages completed: {result.metadata['stages_completed']}")
        for i, stage_result in enumerate(result.results):
            print(f"  Stage {i}: {stage_result}")
    else:
        failed_stage = result.metadata.get('failed_stage', 'unknown')
        print(f"Pipeline failed at stage {failed_stage}")
    
    await runtime.stop()


# Example 4: Fan-out/fan-in pattern
async def example_fan_out_fan_in():
    """Execute multiple independent tasks in parallel."""
    print("\n=== Example 4: Fan-Out/Fan-In Pattern ===")
    
    agent_pool = AgentPool([])  # Add your agents
    infra_factory = TaskInfrastructureFactory()
    runtime = AsyncWorkflowRuntime(agent_pool, infra_factory, enable_streaming=True)
    await runtime.start()
    
    coordinator = CoordinationPatterns(runtime)
    
    # Define parallel tasks
    tasks = [
        {"name": "sentiment", "objective": "Perform sentiment analysis on customer reviews"},
        {"name": "keywords", "objective": "Extract key phrases from customer reviews"},
        {"name": "classify", "objective": "Classify customer reviews into categories"},
        {"name": "summarize", "objective": "Generate summary of customer feedback"}
    ]
    
    # Execute fan-out/fan-in (wait for all)
    result = await coordinator.fan_out_fan_in.execute(
        name="review_analysis",
        tasks=tasks,
        wait_policy="all",  # Wait for all tasks
        workflow_type="generic"
    )
    
    if result.success:
        print(f"All {len(tasks)} tasks completed successfully!")
        for i, task_result in enumerate(result.results):
            print(f"  {tasks[i]['name']}: {task_result}")
    else:
        print(f"Some tasks failed: {result.failed_tasks}")
    
    await runtime.stop()


# Example 5: Interactive task with user input injection
async def example_interactive_task():
    """Submit a task that can request user input during execution."""
    print("\n=== Example 5: Interactive Task ===")
    
    agent_pool = AgentPool([])  # Add your agents
    infra_factory = TaskInfrastructureFactory()
    runtime = AsyncWorkflowRuntime(agent_pool, infra_factory, enable_streaming=True)
    await runtime.start()
    
    # Submit an interactive task
    spec = TaskSpec(
        name="interactive_config",
        objective="Configure system settings based on user preferences",
        workflow_type="chat",  # Use chat workflow for interactive tasks
    )
    
    task_id = await runtime.submit_root_task(spec)
    print(f"Task submitted: {task_id}")
    
    # Monitor for user input requests
    async for event in stream_task_progress(runtime.streaming, task_id):
        print(f"[{event.type}] {event.payload}")
        
        if event.type == "user_input_requested":
            question = event.payload.get("question", "Please provide input:")
            print(f"\n>>> Agent asks: {question}")
            
            # Simulate user response (in real app, get from user interface)
            user_response = "Yes, proceed with default settings"
            print(f"<<< User responds: {user_response}")
            
            # Inject user response
            await runtime.inject_user_message(task_id, user_response, wake=True)
        
        elif event.type in {"task_completed", "task_failed"}:
            print(f"\nTask finished: {event.type}")
            break
    
    await runtime.stop()


# Example 6: Task control (pause, resume, cancel)
async def example_task_control():
    """Demonstrate pausing, resuming, and canceling tasks."""
    print("\n=== Example 6: Task Control ===")
    
    agent_pool = AgentPool([])  # Add your agents
    infra_factory = TaskInfrastructureFactory()
    runtime = AsyncWorkflowRuntime(agent_pool, infra_factory, enable_streaming=True)
    await runtime.start()
    
    # Submit a long-running task
    spec = TaskSpec(
        name="long_computation",
        objective="Perform a lengthy computation that may need to be paused",
        workflow_type="generic"
    )
    
    task_id = await runtime.submit_root_task(spec)
    print(f"Task submitted: {task_id}")
    
    # Wait a bit then pause
    await asyncio.sleep(2)
    print("\nPausing task...")
    await runtime.pause_task(task_id, reason="User requested pause")
    
    # Wait then resume
    await asyncio.sleep(2)
    print("\nResuming task...")
    await runtime.resume_task(task_id, reason="User resumed task")
    
    # Or cancel the task
    # await runtime.cancel_task(task_id, reason="User cancelled task")
    
    # Wait for completion
    completion_event = await wait_for_task_completion(
        runtime.streaming, task_id, timeout=30.0
    )
    
    if completion_event:
        print(f"\nTask finished: {completion_event.type}")
    else:
        print("\nTask timed out")
    
    await runtime.stop()


# Example 7: WebSocket client (JavaScript)
WEBSOCKET_CLIENT_JS = '''
// Example WebSocket client for browser
const ws = new WebSocket('ws://localhost:8765');

ws.onopen = () => {
    console.log('Connected to workflow runtime');
    
    // Subscribe to specific task
    ws.send(JSON.stringify({
        type: 'subscribe',
        task_ids: ['task-id-here'],
        event_types: ['task_progress', 'assistant_message', 'task_completed']
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'event') {
        const evt = data.event;
        console.log(`[${evt.type}] ${JSON.stringify(evt.payload)}`);
        
        // Handle different event types
        if (evt.type === 'user_input_requested') {
            const userResponse = prompt(evt.payload.question);
            
            // Send user response
            ws.send(JSON.stringify({
                type: 'inject_message',
                task_id: evt.task_id,
                content: userResponse,
                wake: true
            }));
        }
    }
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};

ws.onclose = () => {
    console.log('Disconnected from workflow runtime');
};

// Pause a task
function pauseTask(taskId) {
    ws.send(JSON.stringify({
        type: 'pause_task',
        task_id: taskId,
        reason: 'User paused via UI'
    }));
}

// Resume a task
function resumeTask(taskId) {
    ws.send(JSON.stringify({
        type: 'resume_task',
        task_id: taskId,
        reason: 'User resumed via UI'
    }));
}

// Cancel a task
function cancelTask(taskId) {
    ws.send(JSON.stringify({
        type: 'cancel_task',
        task_id: taskId,
        reason: 'User cancelled via UI'
    }));
}
'''


if __name__ == "__main__":
    # Run examples
    print("Async Orchestration Examples")
    print("=============================")
    
    # Uncomment to run specific examples:
    # asyncio.run(example_basic_task_with_streaming())
    # asyncio.run(example_map_reduce())
    # asyncio.run(example_pipeline())
    # asyncio.run(example_fan_out_fan_in())
    # asyncio.run(example_interactive_task())
    # asyncio.run(example_task_control())
    
    print("\nTo run examples, uncomment the desired example in __main__")
    print("\nWebSocket client example (JavaScript) available in WEBSOCKET_CLIENT_JS")
