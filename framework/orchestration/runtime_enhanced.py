# Enhanced runtime with full infrastructure bridge
# This file contains updates to runtime.py to properly pass infrastructure to actions

# Key changes needed in AsyncWorkflowRuntime._execute_task:

# 1. Get the full infrastructure from task_infras
infra = self.task_infras[task.id]
compat_infra = getattr(infra, 'compat_infra', None)

# 2. Build context similar to old workflow
context = await self.context_builder.build_task_context(task)
policy = self.policy_for(task.spec.workflow_type)
bundle = self.action_adapter.build(policy.allowed_action_names(task))

# 3. Build agent prompt with full infrastructure description
AGENT_PROMPT = (
    f"{policy.agent_role_prompt}\n\n"
    "Below is the context formed from the current task state:\n"
    "*** Context Start ***\n"
    f"{context}\n"
    "*** Context End ***\n\n"
    "The following are the actions you can take:\n"
    "*** List of allowed Actions Start ***\n"
    f"{bundle.schema_text}\n"
    "*** List of allowed Actions End ***\n"
)

# 4. If compat_infra exists, add infrastructure description
if compat_infra and hasattr(compat_infra, 'INFRA_DESCRIPTION'):
    AGENT_PROMPT += (
        "*** Description of the infrastructure Start ***\n"
        f"{compat_infra.INFRA_DESCRIPTION}\n"
        "*** Description of the infrastructure End ***\n"
    )

# 5. Execute action with full infrastructure access
if hasattr(action, 'execute'):
    # Pass compat_infra if available, otherwise task_infra
    infra_to_use = compat_infra if compat_infra else infra
    result = action.execute(infra=infra_to_use)
