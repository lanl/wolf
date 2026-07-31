// WOLF Orchestration Dashboard - Main Application

class OrchestrationDashboard {
    constructor() {
        this.socket = null;
        this.selectedTaskId = null;
        this.tasks = new Map();
        this.eventBuffer = [];
        this.maxEvents = 100;
        
        this.init();
    }

    init() {
        this.initWebSocket();
        this.initEventHandlers();
        this.loadInitialData();
    }

    // ==================== WebSocket ====================
    initWebSocket() {
        this.socket = io({
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 10
        });

        this.socket.on('connect', () => {
            console.log('WebSocket connected');
            this.updateConnectionStatus(true);
            this.subscribe();
        });

        this.socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus(false);
        });

        this.socket.on('event', (data) => {
            this.handleEvent(data);
        });
    }

    subscribe() {
        this.socket.emit('subscribe', {
            task_ids: [],
            event_types: []
        });
    }

    updateConnectionStatus(connected) {
        const statusEl = document.getElementById('connection-status');
        const textEl = document.getElementById('connection-text');
        
        if (connected) {
            statusEl.classList.remove('disconnected');
            statusEl.classList.add('connected');
            textEl.textContent = 'Connected';
        } else {
            statusEl.classList.remove('connected');
            statusEl.classList.add('disconnected');
            textEl.textContent = 'Disconnected';
        }
    }

    // ==================== Event Handlers ====================
    initEventHandlers() {
        // Chat input
        document.getElementById('send-message').addEventListener('click', () => this.sendMessage());
        document.getElementById('chat-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
                this.sendMessage();
            }
        });

        // Task controls
        document.getElementById('pause-task').addEventListener('click', () => this.pauseTask());
        document.getElementById('resume-task').addEventListener('click', () => this.resumeTask());
        document.getElementById('cancel-task').addEventListener('click', () => this.cancelTask());
        document.getElementById('retry-task').addEventListener('click', () => this.retryTask());

        // Task selector
        document.getElementById('selected-task').addEventListener('change', (e) => {
            this.selectTask(e.target.value);
        });

        // Refresh buttons
        document.getElementById('refresh-tree').addEventListener('click', () => this.refreshTaskTree());
        document.getElementById('refresh-agents').addEventListener('click', () => this.refreshAgentPool());
        document.getElementById('refresh-metrics').addEventListener('click', () => this.refreshMetrics());
        document.getElementById('clear-events').addEventListener('click', () => this.clearEvents());

        // FAB and modal
        document.getElementById('fab-create-task').addEventListener('click', () => this.openCreateTaskModal());
        document.querySelectorAll('.modal-close, .modal-cancel').forEach(el => {
            el.addEventListener('click', () => this.closeCreateTaskModal());
        });
        document.getElementById('create-task-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.createTask();
        });
    }

    // ==================== API Calls ====================
    async apiCall(endpoint, method = 'GET', body = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (body) {
            options.body = JSON.stringify(body);
        }

        try {
            const response = await fetch(endpoint, options);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return await response.json();
        } catch (error) {
            console.error('API call failed:', error);
            this.showNotification('API Error: ' + error.message, 'error');
            return null;
        }
    }

    async loadInitialData() {
        await this.refreshTaskTree();
        await this.refreshAgentPool();
        await this.refreshMetrics();
    }

    async refreshTaskTree() {
        const tasks = await this.apiCall('/api/tasks');
        if (tasks) {
            this.tasks.clear();
            tasks.forEach(task => this.tasks.set(task.id, task));
            this.renderTaskTree();
            this.updateTaskSelector();
        }
    }

    async refreshAgentPool() {
        const stats = await this.apiCall('/api/agent_pool/stats');
        if (stats) {
            this.renderAgentPoolStats(stats);
        }
    }

    async refreshMetrics() {
        const snapshot = await this.apiCall('/api/runtime/snapshot');
        if (snapshot) {
            this.renderSystemMetrics(snapshot);
        }
    }

    async selectTask(taskId) {
        if (!taskId) {
            this.selectedTaskId = null;
            this.clearTaskDetail();
            return;
        }

        this.selectedTaskId = taskId;
        const detail = await this.apiCall(`/api/tasks/${taskId}`);
        if (detail) {
            this.renderTaskDetail(detail);
            this.renderChatMessages(detail.local_messages);
        }
    }

    // ==================== Task Control ====================
    async sendMessage() {
        if (!this.selectedTaskId) {
            this.showNotification('Please select a task first', 'warning');
            return;
        }

        const input = document.getElementById('chat-input');
        const content = input.value.trim();
        if (!content) return;

        const wake = document.getElementById('wake-task').checked;

        await this.apiCall(`/api/tasks/${this.selectedTaskId}/inject_message`, 'POST', {
            content,
            role: 'user',
            wake
        });

        input.value = '';
    }

    async pauseTask() {
        if (!this.selectedTaskId) return;
        await this.apiCall(`/api/tasks/${this.selectedTaskId}/pause`, 'POST', {
            reason: 'paused via UI'
        });
    }

    async resumeTask() {
        if (!this.selectedTaskId) return;
        await this.apiCall(`/api/tasks/${this.selectedTaskId}/resume`, 'POST', {
            reason: 'resumed via UI'
        });
    }

    async cancelTask() {
        if (!this.selectedTaskId) return;
        if (!confirm('Are you sure you want to cancel this task?')) return;
        await this.apiCall(`/api/tasks/${this.selectedTaskId}/cancel`, 'POST', {
            reason: 'cancelled via UI'
        });
    }

    async retryTask() {
        if (!this.selectedTaskId) return;
        await this.apiCall(`/api/tasks/${this.selectedTaskId}/retry`, 'POST', {
            reason: 'retried via UI'
        });
    }

    async createTask() {
        const name = document.getElementById('task-name').value;
        const objective = document.getElementById('task-objective').value;
        const workflowType = document.getElementById('task-workflow-type').value;
        const sessionId = document.getElementById('task-session-id').value || undefined;

        const result = await this.apiCall('/api/tasks', 'POST', {
            name,
            objective,
            workflow_type: workflowType,
            session_id: sessionId
        });

        if (result) {
            this.closeCreateTaskModal();
            this.showNotification(`Task created: ${result.task_id.substring(0, 8)}`, 'success');
            await this.refreshTaskTree();
        }
    }

    // ==================== Event Handling ====================
    handleEvent(data) {
        const event = data.event;
        this.eventBuffer.push(event);
        if (this.eventBuffer.length > this.maxEvents) {
            this.eventBuffer.shift();
        }

        this.renderEvent(event);

        // Update task if it's in our list
        if (event.task_id && this.tasks.has(event.task_id)) {
            this.refreshTaskTree();
        }

        // Update task detail if it's the selected task
        if (event.task_id === this.selectedTaskId) {
            this.selectTask(this.selectedTaskId);
        }
    }

    // ==================== Rendering ====================
    renderTaskTree() {
        const container = document.getElementById('task-tree-container');
        container.innerHTML = '';

        const roots = Array.from(this.tasks.values()).filter(t => !t.parent_id);
        roots.sort((a, b) => b.created_at - a.created_at);

        roots.forEach(root => this.renderTaskNode(root, container, 0));
    }

    renderTaskNode(task, container, depth) {
        const node = document.createElement('div');
        node.className = `task-node ${task.status}`;
        if (task.id === this.selectedTaskId) {
            node.classList.add('selected');
        }

        const indent = '  '.repeat(depth);
        const icon = this.getStatusIcon(task.status);
        node.textContent = `${indent}${icon} ${task.name} (${task.id.substring(0, 8)})`;

        node.addEventListener('click', () => this.selectTask(task.id));
        container.appendChild(node);

        // Render children
        const children = Array.from(this.tasks.values())
            .filter(t => t.parent_id === task.id)
            .sort((a, b) => a.created_at - b.created_at);
        
        children.forEach(child => this.renderTaskNode(child, container, depth + 1));
    }

    getStatusIcon(status) {
        const icons = {
            'pending': '⏸',
            'ready': '▶',
            'running': '⚙',
            'waiting': '⏳',
            'paused': '⏸',
            'blocked': '🚫',
            'completed': '✓',
            'failed': '✗',
            'cancelled': '⊗'
        };
        return icons[status] || '•';
    }

    updateTaskSelector() {
        const selector = document.getElementById('selected-task');
        const currentValue = selector.value;
        
        selector.innerHTML = '<option value="">Select a task...</option>';
        
        Array.from(this.tasks.values())
            .sort((a, b) => b.updated_at - a.updated_at)
            .forEach(task => {
                const option = document.createElement('option');
                option.value = task.id;
                option.textContent = `${task.name} (${task.id.substring(0, 8)}) - ${task.status}`;
                selector.appendChild(option);
            });
        
        if (currentValue) {
            selector.value = currentValue;
        }
    }

    renderTaskDetail(detail) {
        const container = document.getElementById('task-detail-content');
        const task = detail.task;

        container.innerHTML = `
            <div class="detail-section">
                <div class="detail-label">Task ID</div>
                <div class="detail-value">${task.id}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Status</div>
                <div class="detail-value">${this.getStatusIcon(task.status)} ${task.status}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Objective</div>
                <div class="detail-value">${task.spec.objective}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Workflow Type</div>
                <div class="detail-value">${task.spec.workflow_type}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Owner Agent</div>
                <div class="detail-value">${task.owner_agent_name || 'none'}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Depth</div>
                <div class="detail-value">${task.depth}</div>
            </div>
            <div class="detail-section">
                <div class="detail-label">Created</div>
                <div class="detail-value">${new Date(task.created_at * 1000).toLocaleString()}</div>
            </div>
            ${task.error ? `
            <div class="detail-section">
                <div class="detail-label">Error</div>
                <div class="detail-value" style="color: var(--danger-color);">${task.error}</div>
            </div>
            ` : ''}
        `;
    }

    renderChatMessages(messages) {
        const container = document.getElementById('chat-messages');
        container.innerHTML = '';

        messages.forEach(msg => {
            const div = document.createElement('div');
            div.className = `chat-message ${msg.role}`;
            div.textContent = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content, null, 2);
            container.appendChild(div);
        });

        container.scrollTop = container.scrollHeight;
    }

    clearTaskDetail() {
        document.getElementById('task-detail-content').innerHTML = '<p class="placeholder">Select a task to view details</p>';
        document.getElementById('chat-messages').innerHTML = '';
    }

    renderAgentPoolStats(stats) {
        const container = document.getElementById('agent-pool-stats');
        container.innerHTML = `
            <div class="stat-item">
                <span class="stat-label">Total Agents</span>
                <span class="stat-value">${stats.total_agents || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Available</span>
                <span class="stat-value">${stats.available || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">In Use</span>
                <span class="stat-value">${stats.in_use || 0}</span>
            </div>
        `;
    }

    renderSystemMetrics(snapshot) {
        const container = document.getElementById('system-metrics');
        const tasks = snapshot.tasks || [];
        
        const statusCounts = {};
        tasks.forEach(t => {
            statusCounts[t.status] = (statusCounts[t.status] || 0) + 1;
        });

        let html = `
            <div class="stat-item">
                <span class="stat-label">Total Tasks</span>
                <span class="stat-value">${tasks.length}</span>
            </div>
        `;

        Object.entries(statusCounts).forEach(([status, count]) => {
            html += `
            <div class="stat-item">
                <span class="stat-label">${status}</span>
                <span class="stat-value">${count}</span>
            </div>
            `;
        });

        container.innerHTML = html;
    }

    renderEvent(event) {
        const container = document.getElementById('event-stream');
        const div = document.createElement('div');
        div.className = 'event-item';
        
        const time = new Date(event.timestamp * 1000).toLocaleTimeString();
        div.innerHTML = `
            <div class="event-type">${event.type}</div>
            <div class="event-time">${time} - ${event.task_id?.substring(0, 8) || 'system'}</div>
        `;

        container.insertBefore(div, container.firstChild);

        // Keep only recent events in DOM
        while (container.children.length > 50) {
            container.removeChild(container.lastChild);
        }
    }

    clearEvents() {
        document.getElementById('event-stream').innerHTML = '';
        this.eventBuffer = [];
    }

    // ==================== Modal ====================
    openCreateTaskModal() {
        document.getElementById('create-task-modal').classList.remove('hidden');
    }

    closeCreateTaskModal() {
        document.getElementById('create-task-modal').classList.add('hidden');
        document.getElementById('create-task-form').reset();
    }

    // ==================== Notifications ====================
    showNotification(message, type = 'info') {
        console.log(`[${type.toUpperCase()}] ${message}`);
        // Could be enhanced with a toast notification system
    }
}

// Initialize dashboard when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.dashboard = new OrchestrationDashboard();
    });
} else {
    window.dashboard = new OrchestrationDashboard();
}
