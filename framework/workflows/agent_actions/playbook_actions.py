from typing import Literal, Dict, Optional 
from pydantic import BaseModel, Field
from framework.workflows.base_agent_action import AgentAction
from framework.universes.universe_tools import build_params_from_info, get_base_universe_params

# ---------------------------
# PlayBook Deployment Metadata
# ---------------------------
class PlaybookDeploymentInfo(BaseModel):
    type: str = Field(description="Type of playbook deployment: 'by_id', 'raw_text', 'file'")
    playbook: str = Field(description="""Reference of playbook: type='by_id' -> playbook='id of playbook',
                                                                type='raw_text' -> playbook='literal content of playbook',
                                                                type='file' ->  playbook='path to file containing the playbook' 
                                                                """)

class PlaybookDeploymentArg(BaseModel):
    name: str = Field(description="Chose a name for the deployment")
    id: str = Field(description="Chose a unique ID for the deployment")
    parent_id: Optional[str] = Field(default=None, description="ID of parent playbook deployment (happens with nested playbook deployments)")
    context: str = Field(description="Scenario to which the playbook is being applied to, or background context/information about the scenario the playbook is being applied to")
    var: Dict = Field(description="Dictionaty with any playbook variables and respective values as key-value pair")
    info: PlaybookDeploymentInfo = Field(description="Information about the playbook to deploy")


import uuid

# Helper constants
METADATA_KEYS = {'state', 'scenario', 'variables', 'validated', 'task_list'}

# Helper functions
def _get_deployment(infra, deployment_id):
    """Get deployment dict if exists, else log error and return None."""
    if deployment_id not in infra.PLAYBOOK_DEPLOYMENTS:
        ctx_msg = f"[ERROR] Unable to find Deployment[{deployment_id}]. Has the deployment been created (as a result of playbook deployment)?"
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
        return None
    return infra.PLAYBOOK_DEPLOYMENTS[deployment_id]


def _get_task_list(deployment):
    """Get task list from deployment, falling back to integer keys excluding metadata."""
    task_list = deployment.get("task_list")
    if task_list is None:
        task_list = [k for k in deployment.keys() if isinstance(k, int) and k not in METADATA_KEYS]
    return task_list


def _simulate_validation(infra, deployment_id, workplan):
    """Simulate validation by assuming user says 'yes'. Updates deployment and returns validation result."""
    # Output the tentative workplan for review
    validationMessage = f"[TENTATIVE WORKPLAN]:\n {workplan}"
    FULL_MESSAGE = f"""{validationMessage}\n\nAbove is a tentative workplan for review; Approve if satisfied, or provide feedback otherwise"""
    infra.append_chat_history(
        actor="assistant",
        content=FULL_MESSAGE,
        action={"action": "system_info"},
        log_console=True,
    )

    # Simulate user input: we assume the user says 'yes'
    user_input = 'yes'   # Placeholder for actual user input
    infra.append_chat_history(
        actor="user",
        content=user_input,
        action={"action": "system_info"},
        log_console=True,
    )

    # Process user input
    positive_responses = ['yes', 'approved', 'OK', 'validated', True]
    if isinstance(user_input, bool):
        validation = user_input
    else:
        validation = user_input.lower().strip() in [str(x).lower().strip() for x in positive_responses]

    if validation:
        infra.PLAYBOOK_DEPLOYMENTS[deployment_id]["validated"] = True
        infra.PLAYBOOK_DEPLOYMENTS[deployment_id]["state"] = "approved"
        result_msg = f"WorkPlan validation: {user_input} (approved)"
    else:
        infra.PLAYBOOK_DEPLOYMENTS[deployment_id]["validated"] = False
        result_msg = f"WorkPlan validation: {user_input} (not approved)"

    infra.append_chat_history(
        actor="system",
        content=result_msg,
        action={"action": "system_info"},
        log_console=True,
    )
    return validation


class ValidateWorkplanArg(BaseModel):
    deployment_id: str = Field(description="The ID of the deployment to validate")
    workplan: str = Field(description="The workplan to validate")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class ValidateWorkplan(AgentAction):
    action: Literal["validate_workplan"] = "validate_workplan"
    description: Literal["Validate the workplan for a deployment"] = "Validate the workplan for a deployment"
    payload: ValidateWorkplanArg
    payload_schema: str = """
    {"deployment_id": <string>: "The ID of the deployment to validate",
     "workplan": <string>: "The workplan to validate",
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment_id = self.payload.deployment_id
        workplan = self.payload.workplan
        # Check deployment exists
        deployment = _get_deployment(infra, deployment_id)
        if deployment is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = deployment["validated"]
        except KeyError:
            WP_VALIDATED = False

        if WP_VALIDATED:
            ctx_msg = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] Has already been Approved/Validated"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Simulate validation
        validation = _simulate_validation(infra, deployment_id, workplan)
        # Note: _simulate_validation already updates the deployment and logs messages
        # No further action needed


class ItemizeWorkplanArg(BaseModel):
    deployment_id: str = Field(description="The ID of the deployment to itemize")
    workplan: str = Field(description="The workplan to itemize")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class ItemizeWorkplan(AgentAction):
    action: Literal["itemize_workplan"] = "itemize_workplan"
    description: Literal["Itemize the workplan for a deployment"] = "Itemize the workplan for a deployment"
    payload: ItemizeWorkplanArg
    payload_schema: str = """
    {"deployment_id": <string>: "The ID of the deployment to itemize",
     "workplan": <string>: "The workplan to itemize",
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment_id = self.payload.deployment_id
        workplan = self.payload.workplan
        # Check deployment exists
        deployment = _get_deployment(infra, deployment_id)
        if deployment is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = deployment["validated"]
        except KeyError:
            WP_VALIDATED = False

        if not WP_VALIDATED:
            # Simulate validation (assuming user says yes)
            validation = _simulate_validation(infra, deployment_id, workplan)
            if not validation:
                # If not approved, we cannot proceed
                return

        # Now we have a validated workplan, we need to itemize it.
        # For simplicity, we will split the workplan by lines and create a task for each non-empty line.
        lines = [line.strip() for line in workplan.splitlines() if line.strip()]
        task_list = []
        for idx, line in enumerate(lines, start=1):
            tID = idx
            task_list.append(tID)
            deployment[tID] = {
                "task": {
                    "title": line,
                    "status": "READY"
                },
                "dependencies": []  # No dependencies for simplicity
            }
        # Store the task list
        deployment["task_list"] = task_list
        deployment["state"] = "ready"
        # Notify
        ctx_msg = f"Deployment[{deployment_id}] workplan itemized into {len(task_list)} tasks."
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )


class ModifyTaskArg(BaseModel):
    deployment_id: str = Field(description="The ID of the deployment")
    tId: int = Field(description="The ID of the task to modify")
    modifications: Dict = Field(description="Dictionary of modifications to apply (e.g., {'title': 'new title', 'status': 'done', 'dependencies': [1,2]})")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class ModifyTask(AgentAction):
    action: Literal["modify_task"] = "modify_task"
    description: Literal["Modify a task in a deployment"] = "Modify a task in a deployment"
    payload: ModifyTaskArg
    payload_schema: str = """
    {"deployment_id": <string>: "The ID of the deployment",
     "tId": <int>: "The ID of the task to modify",
     "modifications": <Dict>: "Dictionary of modifications to apply (e.g., {'title': 'new title', 'status': 'done', 'dependencies': [1,2]})",
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment_id = self.payload.deployment_id
        tID = self.payload.tId
        modifications = self.payload.modifications
        # Check deployment exists
        deployment = _get_deployment(infra, deployment_id)
        if deployment is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = deployment["validated"]
        except KeyError:
            WP_VALIDATED = False
        if not WP_VALIDATED:
            ctx_msg = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] has not been Approved/Validated\n Please check and obtain user approval first"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Verify that tID exists in the deployment (excluding metadata keys)
        WP = [k for k in deployment.keys() if k not in METADATA_KEYS]
        if tID not in WP:
            ctx_msg = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{deployment_id}] WorkPlan task list: {WP}. Verify Task and Deployment IDs"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Ensure deployment is running; if not, set to running
        current_state = deployment.get("state", "created")
        if current_state != "running":
            ctx_msg = f"[WARN] Changing Deployment[{deployment_id}] state '{current_state}' -> 'running'"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            deployment["state"] = "running"

        # Get the task
        task_info = deployment.get(tID)
        if not task_info:
            ctx_msg = f"[ERROR] Task<{tID}> not found in deployment[{deployment_id}]"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Apply modifications
        if "title" in modifications:
            task_info["task"]["title"] = modifications["title"]
        if "status" in modifications:
            new_status = modifications["status"].lower().strip()
            status_map = {
                'ready': 'READY',
                'changed': 'READY',
                'modified': 'READY',
                'updated': 'READY',
                'done': 'DONE',
                'completed': 'DONE',
                'finished': 'DONE',
                'success': 'DONE',
                'failed': 'FAILED',
                'terminated': 'FAILED',
                'unfinished': 'FAILED',
                'error': 'FAILED',
                'fail': 'FAILED'
            }
            if new_status in status_map:
                task_info["task"]["status"] = status_map[new_status]
            else:
                ctx_msg = f"[ERROR] Task status {modifications['status']} not supported. Options are 'ready', 'done', 'failed'"
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
        if "dependencies" in modifications:
            # Ensure it's a list
            deps = modifications["dependencies"]
            if isinstance(deps, list):
                task_info["dependencies"] = deps
            else:
                ctx_msg = f"[ERROR] Dependencies must be a list of task IDs"
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return

        # Notify
        ctx_msg = f"Task<{tID}> in Deployment[{deployment_id}] modified."
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )


class RunTaskArg(BaseModel):
    deployment_id: str = Field(description="The ID of the deployment")
    tId: int = Field(description="The ID of the task to run")
    context: Optional[str] = Field(default=None, description="Context/background info for the task execution")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class RunTask(AgentAction):
    action: Literal["run_task"] = "run_task"
    description: Literal["Run a task in a deployment"] = "Run a task in a deployment"
    payload: RunTaskArg
    payload_schema: str = """
    {"deployment_id": <string>: "The ID of the deployment",
     "tId": <int>: "The ID of the task to run",
     "context": <Optional<string>>: "Context/background info for the task execution",
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment_id = self.payload.deployment_id
        tID = self.payload.tId
        context = self.payload.context
        # Check deployment exists
        deployment = _get_deployment(infra, deployment_id)
        if deployment is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = deployment["validated"]
        except KeyError:
            WP_VALIDATED = False
        if not WP_VALIDATED:
            ctx_msg = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] has not been Approved/Validated\n Please check and obtain user approval first"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Get task list (order) and metadata keys
        task_order = _get_task_list(deployment)
        # Verify that tID is in task_order
        if tID not in task_order:
            ctx_msg = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{deployment_id}] WorkPlan task list: {task_order}. Verify Task and Deployment IDs"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Ensure deployment is running; if not, set to running
        current_state = deployment.get("state", "created")
        if current_state != "running":
            ctx_msg = f"[WARN] Changing Deployment[{deployment_id}] state '{current_state}' -> 'running'"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            deployment["state"] = "running"

        # Determine task position in order
        try:
            task_position = task_order.index(tID)
        except ValueError:
            ctx_msg = f"[ERROR] Task<{tID}> not found in task order"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Check precedent task (if not first)
        if task_position != 0:
            precedent_task_id = task_order[task_position - 1]
            precedent_task_info = deployment.get(precedent_task_id)
            if not precedent_task_info:
                ctx_msg = f"[ERROR] Precedent Task<{precedent_task_id}> not found"
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
            precedent_status = precedent_task_info["task"]["status"]
            if precedent_status not in ["DONE", "FAILED"]:
                ctx_msg = f"[ERROR] You need to end the precedent Task<{precedent_task_id}> before trying to run Task<{tID}>"
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return
            if precedent_status == "FAILED":
                ctx_msg = f"[ERROR] The precedent Task<{precedent_task_id}> failed. "
                ctx_msg += f"We cannot continue the deployment with a failed task, so you need to either fix Task<{precedent_task_id}> and rerun it, or end the deployment {deployment_id}"
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return

        # Check dependencies
        task_info = deployment.get(tID)
        if not task_info:
            ctx_msg = f"[ERROR] Task<{tID}> not found in deployment[{deployment_id}]"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return
        deps = task_info.get("dependencies", [])
        unmet_deps = []
        for depID in deps:
            dep_info = deployment.get(depID)
            if not dep_info:
                unmet_deps.append(depID)
                continue
            dep_status = dep_info["task"]["status"]
            if dep_status not in ["DONE", "FAILED"]:
                unmet_deps.append(depID)
        if unmet_deps:
            ctx_msg = f"[ERROR] Task<{tID}> seems to have unresolved dependencies:"
            for depID in unmet_deps:
                dep_info = deployment.get(depID)
                dep_status = dep_info["task"]["status"] if dep_info else "unknown"
                ctx_msg += f"\n   - Task<{depID}> status: {dep_status}"
            ctx_msg += f"\n Make sure those tasks have status DONE or FAILED"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Check that task is READY
        task_status = task_info["task"]["status"]
        if task_status != "READY":
            ctx_msg = f"[ERROR] Deployment[{deployment_id}] | Task<{tID}> is in a {task_status} state: \n Only tasks in READY state can be started"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Build message
        header = f"========================== Starting {deployment_id} | task: {tID} Execution ==========================\n"
        task_desc = task_info["task"]["title"]
        msg = header + f"You are a helpful Assistant, and your role is to perform the task below:\n    <Task {tID}>\n        {task_desc}\n    </Task {tID}>"
        if context is not None:
            msg += f"\nThe Background info below is provided as context for the task execution:\n    <Context/background info>\n        {context}\n    </Context/background info>"
        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "system_info"},
            log_console=True,
        )

        # Update task status to RUNNING
        task_info["task"]["status"] = "RUNNING"
        infra.append_chat_history(
            actor="system",
            content=f"{deployment_id} | task: {tID} is RUNNING",
            action={"action": "system_info"},
            log_console=True,
        )


class EndTaskRunArg(BaseModel):
    deployment_id: str = Field(description="The ID of the deployment")
    tId: int = Field(description="The ID of the task to end")
    run_info: Dict = Field(description="Information about the task execution, must include a 'status' key with value 'success', 'failed', or 'interrupted'")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class EndTaskRun(AgentAction):
    action: Literal["end_task_run"] = "end_task_run"
    description: Literal["End the execution of a task in a deployment"] = "End the execution of a task in a deployment"
    payload: EndTaskRunArg
    payload_schema: str = """
    {"deployment_id": <string>: "The ID of the deployment",
     "tId": <int>: "The ID of the task to end",
     "run_info": <Dict>: "Information about the task execution, must include a 'status' key with value 'success', 'failed', or 'interrupted'",
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment_id = self.payload.deployment_id
        tID = self.payload.tId
        run_info = self.payload.run_info
        # Check deployment exists
        deployment = _get_deployment(infra, deployment_id)
        if deployment is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = deployment["validated"]
        except KeyError:
            WP_VALIDATED = False
        if not WP_VALIDATED:
            ctx_msg = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] has not been Approved/Validated\n Please check and obtain user approval first"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Get task list and metadata keys
        task_order = _get_task_list(deployment)
        if tID not in task_order:
            ctx_msg = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{deployment_id}] WorkPlan task list: {task_order}. Verify Task and Deployment IDs"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Validate run_info status
        status = run_info.get("status")
        if not status:
            ctx_msg = f"[end_task_run][Input Format ERROR]: run_info missing 'status' key"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return
        status_lower = status.lower()
        valid_statuses = ["success", "failed", "interrupted"]
        if status_lower not in valid_statuses:
            ctx_msg = f"[end_task_run][Input Format ERROR]: run_info['status']={status} is not a valid status. Valid status = {valid_statuses}"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Map status to our task status
        status_map = {
            "success": "DONE",
            "failed": "FAILED",
            "interrupted": "READY"
        }
        new_status = status_map[status_lower]

        # Get task info
        task_info = deployment.get(tID)
        if not task_info:
            ctx_msg = f"[ERROR] Task<{tID}> not found in deployment[{deployment_id}]"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Check that task is RUNNING
        current_status = task_info["task"]["status"]
        if current_status != "RUNNING":
            ctx_msg = f"[ERROR] {deployment_id} | Task<{tID}> in a {current_status} state: \n Only tasks in RUNNING state can be ended"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Build message
        header = f"========================== Ending {deployment_id} | task: {tID} execution ==========================\n"
        msg = header + f"Execution of task[{tID}] of deployment[{deployment_id}] has completed\n"
        msg += f"Task Execution Info: {run_info}\n"
        msg += f"========================== End of Deployment: {deployment_id} | task: {tID} Execution =========================="
        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "system_info"},
            log_console=True,
        )

        # Update task status
        task_info["task"]["status"] = new_status
        infra.append_chat_history(
            actor="system",
            content=f"{deployment_id} | task: {tID} is {new_status}",
            action={"action": "system_info"},
            log_console=True,
        )


class ConcludeWorkplanDeploymentArg(BaseModel):
    deployment: Dict = Field(description="Deployment dictionary containing at least 'name', 'deployment_id', and 'run_info'")
    purpose: Optional[str] = Field(default=None, description="Short description of the intent of the action")


class ConcludeWorkplanDeployment(AgentAction):
    action: Literal["conclude_workplan_deployment"] = "conclude_workplan_deployment"
    description: Literal["Conclude a workplan deployment"] = "Conclude a workplan deployment"
    payload: ConcludeWorkplanDeploymentArg
    payload_schema: str = """
    {"deployment": <Dict>: {"name": <string>: "Name of the deployment",
                           "deployment_id": <string>: "Unique ID of the deployment",
                           "run_info": <Dict>: {"status": <string>: "Status of the deployment (success, failed, interrupted)"}} ,
     "purpose": <Optional<string>>: "Short description of the intent of the action"
    }
    """

    def execute(self, infra) -> None:
        deployment = self.payload.deployment
        dName = deployment.get("name")
        dId = deployment.get("deployment_id")
        run_info = deployment.get("run_info")
        # Check deployment exists
        dep = _get_deployment(infra, dId)
        if dep is None:
            return

        # Check whether deployment has already been validated
        try:
            WP_VALIDATED = dep["validated"]
        except KeyError:
            WP_VALIDATED = False
        if not WP_VALIDATED:
            ctx_msg = f"[ERROR] WorkPlan for the Deployment[{dId}] has not been Approved/Validated\n Please check and obtain user approval first"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )
            return

        # Check that all tasks in task_list are DONE or FAILED
        task_list = _get_task_list(dep)
        incomplete = False
        for tID in task_list:
            task_info = dep.get(tID)
            if not task_info:
                incomplete = True
                break
            task_status = task_info["task"]["status"]
            if task_status not in ["DONE", "FAILED"]:
                incomplete = True
                break
        if incomplete:
            ctx_msg = f"[WARN] Completing Deployment[{dId}] Prematurely: WorkPlan was not fully executed:\n WorkPlan Tasks={task_list}"
            infra.append_chat_history(
                actor="system",
                content=ctx_msg,
                action={"action": "system_info"},
                log_console=True,
            )

        # Build message
        header = f"========================== Completing Deployment[{dId}] ==========================\n"
        msg = header + f"Deployment {dName} with ID={dId} is completed\n"
        msg += f"Deployment Info: {run_info}\n"
        msg += f"========================== End of Deployment {dName}: ID={dId}=========================="
        infra.append_chat_history(
            actor="system",
            content=msg,
            action={"action": "system_info"},
            log_console=True,
        )

        # Update deployment state to completed
        dep["state"] = "completed"


class RunPlayBook(AgentAction):
    action: Literal["run_playbook"] = "run_playbook"
    description: Literal["Start the deployment of a playbook"] = "Start the deployment of a playbook"
    payload: PlaybookDeploymentArg
    payload_schema: str = """
    {"name": <string>: "Chose a name for the deployment",
     "id": <string>: "Chose a unique ID for the deployment",
     "parent_id": <Optional[str]>: ID of parent playbook deployment (happens with nested playbook deployments),
     "context": <string>: "Scenario to which the playbook is being applied to, or background context/information about the scenario the playbook is being applied to",
     "var": <Dict>: "Dictionaty with any playbook variables and respective values as key-value pair",
     "info": <Dict>: {"type":"by_id", "playbook":"ID of the playbook"} or
                     {"type":"raw_text", "playbook":"Content of playbook"} or
                     {"type":"file", "playbook":"/path/to playbook/file"} 
    }
    """

    def execute(self, infra) -> None:
        # Get parent ID
        try:
            parent_id = self.payload.parent_id
        except:
            parent_id = None
        # We need to verify whether parent_id is legit
        if ( (parent_id is not None) and (len(deployment_id)>1) ):
            VALID_PARENT = False
            if infra.PLAYBOOK_DEPLOYMENTS is not None:
                if parent_id not in infra.PLAYBOOK_DEPLOYMENTS.keys(): 
                    VALID_PARENT = False
                else: #We now need to check whether the the parent deployment is active (not concluded)
                    if infra.PLAYBOOK_DEPLOYMENTS[parent_id]['state'].lower() in ['completed','canceled','terminated']:
                        ctx_msg = (f"[ERROR]: Parent playbook deployment with ID {parent_id}'s state={infra.PLAYBOOK_DEPLOYMENTS[parent_id]['state']}:"
                                   f"    Meaning is inactive; check and provide correct parent ID")
                        VALID_PARENT = False
                    else:
                        VALID_PARENT = True
            else:
                ctx_msg = (f"[ERROR]: Parent playbook deployment with ID {parent_id} was not found:"
                           f"    In fact no precedent playbook has been deployed")
                VALID_PARENT = False

            if not VALID_PARENT:
                infra.append_chat_history(
                    actor="system",
                    content=ctx_msg,
                    action={"action": "system_info"},
                    log_console=True,
                )
                return

        # Generate a random UUID if ID not provided
        try:
            deployment_id = self.payload.id
        except:
            deployment_id = None
        if ( (deployment_id is None) or (len(deployment_id)<1) ):
            deployment_id = str(uuid.uuid4())[:8]

        # Obtain Playbook
        VALID_PLAYBOOK = False
        deployment_type = self.payload.info['type'].strip().lower()
        if deployment_type in ['id', 'by_id', 'by id']:
            playbook_id = self.payload.info['playbook'].strip()
            playbook = infra.get_playbook_by_id(playbook_id)
            if playbook is None:
                ctx_msg = (f"[ERROR]: Playbook with ID {playbook_id} was not found:"
                           f"    Playbook search result = {playbook}")
                VALID_PLAYBOOK = False
            else:
                ctx_msg = f"[+]  PlayBook['{playbook_id}'] Found"
                VALID_PLAYBOOK = True
        elif deployment_type in ['raw_text', 'text', 'content', 'raw', 'playbook_content', 'content_playbook', 'playbook content', 'content playbook']:
            playbook = self.payload.info['playbook'].strip()
            ctx_msg = f"[+] RAW Playbook Copied OK"
            VALID_PLAYBOOK = True
        elif deployment_type in  ['file', 'playbook file', 'playbook_file', 'path', 'file_path', 'playbook path', 'playbook_path']:
            plbk_file_path = self.payload.info['playbook'].strip()
            try:
                with open(plbk_file_path, 'r') as pb_file:
                    playbook = pb_file.read()
                ctx_msg = f"[+] PlayBook Read from {plbk_file_path}"
                VALID_PLAYBOOK = True
            except Exception as plbk_read_err:
                ctx_msg = (f"[ERROR]:Reading Playbook from file: {plbk_file_path}:"
                           f"  exception message: {plbk_read_err}")
                VALID_PLAYBOOK = False
        else:
            ctx_msg = f"[ERROR]: Unsupported Playbook deployment of type {deployment_type}"
            VALID_PLAYBOOK = False
        # Message out progress
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
        if not VALID_PLAYBOOK: return

        deployment_scenario  = self.payload.context
        deployment_variables = self.payload.var
        if infra.PLAYBOOK_DEPLOYMENTS is None:
            infra.PLAYBOOK_DEPLOYMENTS={deployment_id:{"state":"created", "scenario":deployment_scenario, "variables":deployment_variables, "parent_id":parent_id}}
        else:
            infra.PLAYBOOK_DEPLOYMENTS[deployment_id]={"state":"created", "scenario":deployment_scenario, "variables":deployment_variables, "parent_id":parent_id}

        ctx_msg = (f"=================[ BEGINING PLAYBOOK DEPLOYMENT '{self.payload.name}' HISTORY ]=================\n"
                   f"[SYSTEM]:\n"
                   f"**Deployment ID** = {deployment_id}\n" 
                   f"<Deployment Scenario/Context>\n"
                   f"{deployment_scenario} \n"
                   f"</Deployment Scenario/Context>\n"
                   f"<Playbook Content>\n"
                   f"{playbook}\n"
                   f"</Playbook Content>\n"
                   f"<Playbook Variables>\n"
                   f"{deployment_variables}\n"
                   f"</Playbook Variables> \n"
                   f"Build a workplan for the scenario/context provided above using the provided playbook\n"
                   f"Show the workplan to user for approval or feedback\n"
                   )
        infra.append_chat_history(
            actor="system",
            content=ctx_msg,
            action={"action": "system_info"},
            log_console=True,
        )
