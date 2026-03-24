from typing import Literal, Dict, Optional 
from pydantic import BaseModel, Field
from framework.workflows.base_agent_action import AgentAction
from framework.universes.universe_tools import build_params_from_info, get_base_universe_params


# ---------------------------
# PlayBook Deployment Metadata
# ---------------------------
class PlaybookDeploymentInfo(BaseModel):
    type: str = Field(description="Type of playbook deployment: 'by_id', 'raw_text', 'file")
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

class RunPlayBook(AgentAction):
    """Starts the deployment of a playbook.
    """
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
                        ctx_msg = (f"[ERROR]: Parent playbook deployemnt with ID {playbook_id}'s state={infra.PLAYBOOK_DEPLOYMENTS[parent_id]['state']}:"
                                   f"    Meaning is inactive; check and provide correct parent ID")
                        VALID_PARENT = False
                    else:
                        VALID_PARENT = True
            else:
                ctx_msg = (f"[ERROR]: Parent playbook deployment with ID {playbook_id} was not found:"
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
                ctx_msg = (f"[ERROR]:Reading Playbook from file: {plbk_file_path}:\n"
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
            WF.DEPLOYMENTS[deployment_id]={"state":"created", "scenario":deployment_scenario, "variables":deployment_variables, "parent_id":parent_id}

        ctx_msg = (f"=================[ BEGINING PLAYBOOK DEPLOYMENT '{deployment['name']}' HISTORY ]=================\n"
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

