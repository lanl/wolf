elif agent_output['action'] in ["run_playbook"]:
            deployment = agent_output['deployment']
            async with cl.Step(name=move, type="run") as step:
                deployment_type = deployment["payload"]["type"].lower()
                if deployment_type in ['id', 'by_id', 'by id']:
                    playbook_id =  deployment["payload"]["playbook"]
                    playbook = WF.get_playbook_by_id(playbook_id)
                    if playbook is None:
                        MESSAGE = f"[ERROR]: Playbook with ID {playbook_id} was not found:\n     Playbook search result = {playbook}"
                        WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                        step.output = MESSAGE
                    else:
                        MESSAGE = f"[+] PlayBook['{playbook_id}'] Found OK"
                        WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                elif deployment_type in ['raw_text', 'text', 'content', 'raw', 'playbook_content', 'content_playbook', 'playbook content', 'content playbook']:
                    playbook = deployment["payload"]["playbook"]
                    MESSAGE = f"[+] RAW Playbook Copied OK"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                elif deployment_type in ['file', 'playbook file', 'playbook_file']:
                    with open(deployment["payload"]["playbook"], 'r') as pb_file:
                        playbook = pb_file.read()
                        MESSAGE = f"[+] PlayBook Read from {deployment['payload']['playbook']}OK"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                else:
                    MESSAGE = f"[ERROR]: The Playbook deployment of type {deployment_type} is not supported"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                deployment_scenario  = deployment["context"]
                deployment_variables = deployment["var"]
                # Generate a random UUID
                deployment_id = str(uuid.uuid4())[:8]
                if WF.DEPLOYMENTS is None: 
                    WF.DEPLOYMENTS={deployment_id:{"state":"created", "scenario":deployment_scenario, "variables":deployment_variables}}
                else:
                    WF.DEPLOYMENTS[deployment_id]={"state":"created", "scenario":deployment_scenario, "variables":deployment_variables}
                MESSAGE=f"=================[ BEGINING PLAYBOOK DEPLOYMENT '{deployment['name']}' HISTORY ]=================\n"
                MESSAGE += f"""[SYSTEM]:\n
                  **Deployment ID** = {deployment_id}\n
                  <Deployment Scenario/Context>\n
                    {deployment_scenario} \n
                  </Deployment Scenario/Context>\n
                  <Playbook Content>\n
                    {playbook}\n
                  </Playbook Content>\n
                  <Playbook Variables>\n
                    {deployment_variables}\n
                  </Playbook Variables> \n"""
                MESSAGE += f"Build a workplan for the scenario/context provided above using the provided playbook\n"
                MESSAGE += f"Show the workplan to user for approval or feedback\n"
                WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                step.output = MESSAGE

        elif agent_output['action'] in ["validate_workplan"]:
            deployment_id=agent_output["deployment_id"]
            try:
                move = f"Validation of workplan for Deployment[{agent_output['deployment_id']}]: {agent_output['purpose']}"
            except:
                move = f"PlayBook Deploymen workplan validation"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                if deployment_id not in list(WF.DEPLOYMENTS.keys()):
                    MESSAGE = f"[ERROR] Unable to find Deployment[{deployment_id}]. Has the deployment been created (as a result od playbook deployment)?"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                #WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[deployment_id]["validated"]
                except:
                    WP_VALIDATED = False
                if WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] Has already been Approved/Validated"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                validationMessage = f"[TENTATIVE WORKPLAN]:\n {agent_output['workplan']}"
                FULL_MESSAGE = f"""{validationMessage}\n 
                Above is a tentative workplan for review; Approve if satisfied, or provide feedback otherwise"""
                WF.update_chat_history(FULL_MESSAGE, role='assistant', verbose_out=show_WF_steps)
                user_raw_input = await cl.AskUserMessage(content=FULL_MESSAGE, author="assistant",timeout=300).send()
                user_input=user_raw_input['output']
                WF.update_chat_history(f"{user_input}", role='user', verbose_out=show_WF_steps)
                step.output = f"WORKPLAN: {agent_output['workplan']} | User Approval: {user_input}"
                AGENT_PROMPT = f"A user was asked to approve/validate a workflow, or to suggest furter comments/feedback/improvements."
                AGENT_PROMPT += f" Your role is to tell whether the user input is an approval/validation.\n user input = {user_input}\n"
                RESPONSE_FORMAT = "{'user_approval': 'yes/no/comment'}"
                INVALID_FORMAT = True
                FORMAT_ERROR_MESSAGE = ""
                CORRECTION_RESPONSE_FORMAT = RESPONSE_FORMAT
                format_correction_trial = 0
                while ( INVALID_FORMAT and (format_correction_trial < WF.max_format_correction_trials) ):
                        agent_output2 = await cl.make_async(WF.agent.get_chat_response)(FORMAT_ERROR_MESSAGE + AGENT_PROMPT + CORRECTION_RESPONSE_FORMAT,
                                                                  image_prompt=None,
                                                                  llm_sampling_settings=WF.agent.settings
                                                                                        )
                        try:
                            formatted_agent_output = WF.jsonify(agent_output2)
                            INVALID_FORMAT = False
                            agent_output2 = formatted_agent_output
                        except Exception as format_error:
                            INVALID_FORMAT = True
                            FORMAT_ERROR_MESSAGE = f"[ASSISTANT]: {agent_output2}"
                            FORMAT_ERROR_MESSAGE += f"[SYSTEM][FORMAT ERROR]: {format_error}"
                            CORRECTION_RESPONSE_FORMAT = f"['SYSTEM']: Fix previous formatting issues: Your  response MUST STRICTLY follow the following format:\n {RESPONSE_FORMAT}"
                        format_correction_trial +=1
                if not INVALID_FORMAT: 
                    validation = agent_output2['user_approval']
                    if validation in ['yes', 'approved', 'OK', 'validated', True]:
                        WF.DEPLOYMENTS[deployment_id]["validated"]=True
                        WF.DEPLOYMENTS[deployment_id]["state"]="approved"
                    else:
                        WF.DEPLOYMENTS[deployment_id]["validated"]=False
                    step.output = f"WorkPlan validation: {validation}"
                else:
                    MESSAGE = FORMAT_ERROR_MESSAGE 
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE


        elif agent_output['action'] in ["itemize_workplan"]:
            workplan = agent_output['workplan']
            deployment_id=agent_output["deployment_id"]
            max_format_correction_trials=WF.max_format_correction_trials
            max_audit_trial=WF.max_audit_trial
            try:
                move = f"Deployment {deployment_id} workplan itemization: {agent_output['purpose']}"
            except:
                move = f"PlayBook Deployment {deployment_id} workplan itemization"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                if deployment_id not in list(WF.DEPLOYMENTS.keys()):
                    MESSAGE = f"[ERROR] Unable to find Deployment[{deployment_id}]. Has the deployment been created (as a result od playbook deployment)?"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                #WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[deployment_id]["validated"]
                except:
                    WP_VALIDATED = False
                    pass
                if not WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{deployment_id}] has not been Approved/Validated\n Please check and obtain user approval first"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                INVALID_RESPONSE = True
                while INVALID_RESPONSE:
                    MESSAGE = WF.interactive_chat_history + f"Make sure workplan has been approved by user before itemizing the approved workplan\n"
                    MESSAGE += f"Itemize the workplan by providing an ordered list of steps/tasks forming the workplan. If possible state taks execution order and dependencies"
                    RESPONSE_FORMAT = "If workplan was not approved by user, get user approval by responding with:"
                    RESPONSE_FORMAT += """{"action":"validate_workplan",
                                           "workplan":"Worplan approved for deployment by user",
                                           "purpose": "Short description of the intent of the action, or reason why you are taking the action"
                                           }
                                        """
                    RESPONSE_FORMAT += "Otherwise response with:"
                    RESPONSE_FORMAT += """{"action":"provide_itemized_task_list",
                                           "workplan":[{"tId": [int] task1 id,
                                                        "description": [str] "description of task1", 
                                                        "dependencies": [list] [list of tIds of tasks that need to complete befor taks1]},
                                                       {"tId": [int] task2 id,
                                                        "description": [str] "description of task2", 
                                                        "dependencies": [list] [list of tIds of tasks that need to complete befor taks2]},
                                                        ...,
                                                       {"tId": [int] id of last task,
                                                        "description": [str] "description of last task", 
                                                        "dependencies": [list] [list of tIds of tasks that need to complete befor this taks]},
                                                      ],
                                           "purpose": "Short description of the intent of the action, or reason why you are taking the action"
                                           }
                                        """
                    #WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    IMAGES = None
                    AGENT_PROMPT  = MESSAGE + WF.AGENT_BEHAVIOUR
                    AGENT_PROMPT += WF.WF_RULES
                    # Get Well formatted agent output
                    INVALID_FORMAT = True
                    FORMAT_ERROR_MESSAGE = ""
                    CORRECTION_RESPONSE_FORMAT = RESPONSE_FORMAT
                    format_correction_trial = 0
                    while ( INVALID_FORMAT and (format_correction_trial < max_format_correction_trials) ):
                        agent_output2 = await cl.make_async(WF.agent.get_chat_response)(FORMAT_ERROR_MESSAGE + AGENT_PROMPT + CORRECTION_RESPONSE_FORMAT,
                                                                  image_prompt=IMAGES,
                                                                  llm_sampling_settings=WF.agent.settings
                                                                                        )
                        try:
                            formatted_agent_output = WF.jsonify(agent_output2)
                            INVALID_FORMAT = False
                            agent_output2 = formatted_agent_output
                        except Exception as format_error:
                            INVALID_FORMAT = True
                            FORMAT_ERROR_MESSAGE = f"[ASSISTANT]: {agent_output2}"
                            FORMAT_ERROR_MESSAGE += f"[SYSTEM][FORMAT ERROR]: {format_error}"
                            CORRECTION_RESPONSE_FORMAT = f"['SYSTEM']: Fix previous formatting issues: Your  response MUST STRICTLY follow the following format:\n {RESPONSE_FORMAT}"
                        format_correction_trial +=1
                    # Choose how to proceed further.
                    if agent_output2["action"] in ["validate_workplan"]:
                        INVALID_RESPONSE = False
                        MESSAGE = f"[Workplan approval check failure]: Worplan was not approved: Agent/Assistant needs to resquest workplan approval from user "
                        WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                        step.output = MESSAGE
                    elif agent_output2["action"] in ["provide_itemized_task_list"]:
                        workplan = agent_output2["workplan"]
                        MESSAGE = f"Deployment[{deployment_id}] worplan task list received: \n ##########################\n {workplan} \n ##########################\n Proceed to tasks execution"
                        WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                        step.output = MESSAGE
                        INVALID_RESPONSE = False
                        ## Create the TaskList
                        task_list = cl.TaskList()
                        task_list.status = f"Deployment[{deployment_id}] worplan task list"
                        WF.DEPLOYMENTS[deployment_id]["task_list"] = task_list
                        # Update the task list in the interface
                        # Create tasks and put then in the running state
                        message = await cl.Message(content="Processing task list").send()
                        for ts in workplan:
                            tID = ts["tId"]
                            WF.DEPLOYMENTS[deployment_id][tID] = {"task": cl.Task(title=f"{ts['description']}", status=cl.TaskStatus.READY)}
                            WF.DEPLOYMENTS[deployment_id][tID]['task'].forId = message.id
                            try:
                                if 'dependencies' in ts.keys():
                                    WF.DEPLOYMENTS[deployment_id][tID]['dependencies'] = ts["tId"]["dependencies"]
                                else:
                                    WF.DEPLOYMENTS[deployment_id][tID]['dependencies'] = []
                            except:
                                WF.DEPLOYMENTS[deployment_id][tID]['dependencies'] = []
                            await WF.DEPLOYMENTS[deployment_id]["task_list"].add_task(WF.DEPLOYMENTS[deployment_id][tID]['task'])
                        #Update the task list in the interface
                        WF.DEPLOYMENTS[deployment_id]["state"]="ready"
                        await WF.DEPLOYMENTS[deployment_id]["task_list"].send()

                        ## Perform some action on your end
                        #await cl.sleep(1)
                        ## Update the task statuses
                        #task1.status = cl.TaskStatus.DONE
                        #task2.status = cl.TaskStatus.FAILED
                        #task_list.status = "Failed"
                        #await task_list.send()
                    else:
                        INVALID_RESPONSE = True
                        MESSAGE = f" {agent_output2['action']} is invalid: Action needs to be 'validate_workplan' or 'provide_itemized_task_list'\n conform to formatting guidelines!"
                        WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                        step.output = MESSAGE

        elif agent_output['action'] in ["modify_task"]:
            dId     = agent_output['deployment_id']
            tID     = agent_output['tId']
            modifications = agent_output['modifications']
            try:
                move = f" Modifying Task<{tID}> of Deployment[{dId}]: {agent_output['purpose']}"
            except:
                move = f"Modifying Task<{tID}> of Deployment[{dId}]"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                if dId not in list(WF.DEPLOYMENTS.keys()):
                    MESSAGE = f"[ERROR] Unable to find Deployment[{dId}]. Has the deployment been created (as a result od playbook deployment)?"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[dId]["validated"]
                except:
                    pass
                if not WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{dId}] has not been Approved/Validated\n Please check and obtain user approval first"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Verify that tID and dId match
                WP = list(WF.DEPLOYMENTS[dId].keys())
                for wp_key in ['state', 'scenario', 'variables', 'validated', 'task_list']: WP.remove(wp_key)
                if tID not in WP:
                    MESSAGE = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{dId}] WorkPlan task list: {WP}. Verify Task and Deployment IDs"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                if WF.DEPLOYMENTS[dId]["state"] not in ["running"]:
                    MESSAGE = f"[WARN] Changing Deployment[{dId}] state 'ready' -> 'running'"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    WF.DEPLOYMENTS[dId]["state"]="running"
                # Modify task
                #message = await cl.Message(content="Processing task list").send()
                modifications_keys = list(modifications.keys())
                if "title" in modifications_keys: WF.DEPLOYMENTS[dId][tID]["task"].title = modifications["title"]
                if "status" in modifications_keys:
                    ctx_status = modifications["status"].lower().strip()
                    if ctx_status in ['ready', 'changed', 'modified', 'updated']:
                        WF.DEPLOYMENTS[dId][tID]["task"].status = cl.TaskStatus.READY
                    elif ctx_status in ['done', 'completed', 'finished', 'success']:
                        WF.DEPLOYMENTS[dId][tID]["task"].status = cl.TaskStatus.DONE
                    elif ctx_status in ['failed', 'terminated', 'unfinished', 'error', 'fail']:
                        WF.DEPLOYMENTS[dId][tID]["task"].status = cl.TaskStatus.FAILED
                    else:
                        MESSAGE = f"[ERROR] Task status {modifications['status']} in not supported. Options are 'ready', 'done', 'failed'"
                        WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                        step.output = MESSAGE
                        continue
                if "dependencies" in modifications_keys: WF.DEPLOYMENTS[dId][tID]['dependencies'] = modifications['dependencies']
                msg_id = WF.DEPLOYMENTS[dId][tID]['task'].forId 
                #Update the task list in the interface
                await WF.DEPLOYMENTS[dId]["task_list"].send()

        elif agent_output['action'] in ["run_task"]:
            dId     = agent_output['deployment_id']
            tID     = agent_output['tId']
            context = agent_output['context'] 
            try:
                move = f"Running Task<{tID}> of Deployment[{dId}]: {agent_output['purpose']}"
            except:
                move = f"Running Task<{tID}> of Deployment[{dId}]"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                if dId not in list(WF.DEPLOYMENTS.keys()):
                    MESSAGE = f"[ERROR] Unable to find Deployment[{dId}]. Has the deployment been created (as a result od playbook deployment)?"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[dId]["validated"]
                except:
                    pass
                if not WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{dId}] has not been Approved/Validated\n Please check and obtain user approval first"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Verify that tID and dId match
                WP = list(WF.DEPLOYMENTS[dId].keys())
                for wp_key in ['state', 'scenario', 'variables', 'validated', 'task_list']: 
                    try:
                        WP.remove(wp_key)
                    except:
                        print(f"[!] {wp_key} not in ['state', 'scenario', 'variables', 'validated', 'task_list']")

                if tID not in WP:
                    MESSAGE = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{dId}] WorkPlan task list: {WP}. Verify Task and Deployment IDs"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                if WF.DEPLOYMENTS[dId]["state"] not in ["running"]:
                    MESSAGE = f"[WARN] Changing Deployment[{dId}] state 'ready' -> 'running'"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    WF.DEPLOYMENTS[dId]["state"]="running"
                # Verify that the task before tID was ended correctly
                task_position = WP.index(tID)
                precedent_task_position = task_position - 1
                precedent_task_id = WP[precedent_task_position]
                if task_position != 0: # This check is only for the 2nd and later tasks
                    status_precedent_task = WF.DEPLOYMENTS[dId][precedent_task_id]["task"].status
                    if status_precedent_task not in [cl.TaskStatus.DONE, cl.TaskStatus.FAILED]:
                        MESSAGE = f"[ERROR] You need to end the precedent Task<{precedent_task_id}> before trying to run Task<{tID}>"
                        WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                        step.output = MESSAGE
                        continue
                    else:
                        if status_precedent_task in [cl.TaskStatus.FAILED]: # We cannot continue with the deployment with a failed task
                            MESSAGE = f"[ERROR] The precedent Task<{precedent_task_id}> failed. "
                            MESSAGE += f"We cannot continue the deployment with a failed task, so You need to either fix Task<{precedent_task_id}> and rerunning it, or end the deployment {dId}"
                            WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                            step.output = MESSAGE
                            continue
                # Check Task dependencies
                UNMET_DEPs = []
                for depID in WF.DEPLOYMENTS[dId][tID]['dependencies']:
                    if WF.DEPLOYMENTS[dId][depID]["task"].status not in [cl.TaskStatus.DONE, cl.TaskStatus.FAILED]: UNMET_DEPs.append(depID)
                if len(UNMET_DEPs)>0:
                    MESSAGE = f"[ERROR] Task<{tID}> seems to have unresolved dependencies:"
                    for depID in UNMET_DEPs: MESSAGE += f"\n   - Task<{depID}> status: {WF.DEPLOYMENTS[dId][depID]['task'].status}"
                    MESSAGE += f"\n Make sure those tasks have as status {cl.TaskStatus.DONE} or {cl.TaskStatus.FAILED}"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                ####
                MESSAGE = f"========================== Starting {dId} | task: {tID} Execution ==========================\n"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                try:
                    DEPLOYMENT = WF.DEPLOYMENTS[dId]
                except Exception as Deployment_not_found_err:
                    MESSAGE += f"[ERROR] Unable to find deployment[{dId}]: \n   {Deployment_not_found_err}"
                    MESSAGE += f"Did you start Deployment[{dId}]?"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                try:
                    TASK = DEPLOYMENT[tID]
                except Exception as Task_not_found_err:
                    MESSAGE += f"[ERROR] Unable to find Task<{tID}> in the deployment[{dId}] workplan: \n   {Task_not_found_err}"
                    if not WF.DEPLOYMENTS[dId]["validate"]:  MESSAGE += f"Deployment[{dId}] does not seem to have been validated. Did you start and validated the deployment?"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                if  WF.DEPLOYMENTS[dId][tID]["task"].status is not cl.TaskStatus.READY:
                    MESSAGE += f"[ERROR] Deployment[{dId}] | Task<{tID}> is in a {WF.DEPLOYMENTS[dId][tID]['task'].status} state: \n Only taks in {cl.TaskStatus.READY} state can be started"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                MESSAGE += "You are a helpful Assistant, and your role is to perform the task below:"
                MESSAGE += f"    <Task {tID}>"
                MESSAGE += f"        {TASK}"
                MESSAGE += f"    </Task {tID}>"
                if context is not None:
                    MESSAGE += "The Background info below is provided as context for the task execution:"
                    MESSAGE += f"    <Context/background info>"
                    MESSAGE += f"        {context}"
                    MESSAGE += f"    </Context/background info>"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                WF.DEPLOYMENTS[dId][tID]["task"].status = cl.TaskStatus.RUNNING
                await WF.DEPLOYMENTS[dId]["task_list"].send()
                step.output = f"{dId} | task: {tID} is RUNNING"


        elif agent_output['action'] in ["end_task_run"]:
            dId      = agent_output['deployment_id']
            tID      = agent_output['tId']
            run_info = agent_output['run_info']
            STATUS = run_info['status']
            task_states = {"success":cl.TaskStatus.DONE, "failed":cl.TaskStatus.FAILED, "interrupted":cl.TaskStatus.READY}
            VALID_STATUS = list(task_states.keys())
            if STATUS.lower() not in VALID_STATUS:
                MESSAGE += f"[end_task_run][Input Format ERROR]: run_info['status']={STATUS} is not a valid status. Valid status = {VALID_STATUS}"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                step.output = MESSAGE
                continue
            try:
                move = f"Ending Task<{tID}> of Deployment[{dId}]: {agent_output['purpose']}"
            except:
                move = f"Ending Task<{tID}> of Deployment[{dId}]"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                Deployments =  list(WF.DEPLOYMENTS.keys())
                if dId not in Deployments:
                    MESSAGE = f"[ERROR] Unable to find Deployment[{dId}]. Has the deployment been created (as a result od playbook deployment)?\n Deployments={WF.DEPLOYMENTS}"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[dId]["validated"]
                except:
                    pass
                if not WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{dId}] has not been Approved/Validated\n Please check and obtain user approval first"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Verify that tID and dId match
                WP = list(WF.DEPLOYMENTS[dId].keys())
                if tID not in WP:
                    MESSAGE = f"[ERROR] Unable to find Task<{tID}> in the Deployment[{dId}] WorkPlan task list: {WP}. Verify Task and Deployment IDs"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue

                MESSAGE = f"========================== Ending {dId} | task: {tID} execution ==========================\n"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                try:
                    DEPLOYMENT = WF.DEPLOYMENTS[dId]
                except Exception as Deployment_not_found_err:
                    MESSAGE += f"[ERROR] Unable to find deployment[{dId}]: \n   {Deployment_not_found_err}"
                    MESSAGE += f"Did you start Deployment[{dId}]?"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                try:
                    TASK = DEPLOYMENT[tID]
                except Exception as Task_not_found_err:
                    MESSAGE += f"[ERROR] Unable to find Task<{tID}> in the deployment[{dId}] workplan: \n   {Task_not_found_err}"
                    if not WF.DEPLOYMENTS[dId]["validate"]:  MESSAGE += f"Deployment[{dId}] does not seem to have been validated. Did you start and validated the deployment?"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                if  WF.DEPLOYMENTS[dId][tID]["task"].status is not cl.TaskStatus.RUNNING:
                    MESSAGE += f"[ERROR] {dId} | Task<{tID}> in a {WF.DEPLOYMENTS[dId][tID]['task'].status} state: \n Only taks in {cl.TaskStatus.RUNNING} state can be ended"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                MESSAGE =  f"Execution of task[{tID}] of deployment[{dId}] has completed"
                MESSAGE += f"Task Execution Info: {run_info}"
                MESSAGE += f"========================== End of Deployment: {dId} | task: {tID} Execution =========================="
                WF.DEPLOYMENTS[dId][tID]["task"].status = task_states[STATUS.lower()]
                await WF.DEPLOYMENTS[dId]["task_list"].send()
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                step.output = f"{dId} | task: {tID} is COMPLETED"
                step.output = MESSAGE


        elif agent_output['action'] in ["conclude_workplan_deployment"]:
            dName    = agent_output['deployment']['name']
            dId      = agent_output['deployment']['deployment_id']
            run_info = agent_output['deployment']['run_info']
            STATUS = run_info['status']
            Deployment_states = {"success":cl.TaskStatus.DONE, "failed":cl.TaskStatus.FAILED, "interrupted":cl.TaskStatus.READY}
            VALID_STATUS = list(Deployment_states.keys())
            if STATUS.lower() not in VALID_STATUS:
                MESSAGE += f"[conclude_workplan_deployment][Input Format ERROR]: run_info['status']={STATUS} is not a valid status. Valid status = {VALID_STATUS}"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                step.output = MESSAGE
                continue
            try:
                move = f"Completing Deployment[{dId}]: {agent_output['purpose']}"
            except:
                move = f"Ending Deployment[{dId}]"
            async with cl.Step(name=move, type="run") as step:
                # Check whether deployment has been created
                if dId not in list(WF.DEPLOYMENTS.keys()):
                    MESSAGE = f"[ERROR] Unable to find Deployment[{dId}]. Has the deployment been created (as a result od playbook deployment)?"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Check whether deployment has already been valitated
                WP_VALIDATED = False
                try:
                    WP_VALIDATED = WF.DEPLOYMENTS[dId]["validated"]
                except:
                    pass
                if not WP_VALIDATED:
                    MESSAGE = f"[ERROR] WorkPlan for the Deployment[{dId}] has not been Approved/Validated\n Please check and obtain user approval first"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                # Chck whether all WorkPlan tasks are done
                WP = [k for k in WF.DEPLOYMENTS[dId].keys() if isinstance(k, int)]
                INCOMPLETE_WP = False
                for tID in WP:
                    if INCOMPLETE_WP: break
                    if WF.DEPLOYMENTS[dId][tID]["task"].status not in [cl.TaskStatus.DONE, cl.TaskStatus.FAILED]:
                        INCOMPLETE_WP = True
                        break
                if INCOMPLETE_WP:
                    MESSAGE = f"[WARN] Completing Deployment[{dId}] Prematurely: WorkPlan was not fully executed:\n WorkPlan Tasks={WF.DEPLOYMENTS[dId]}"
                    WF.update_chat_history(MESSAGE, role='system',verbose_out=show_WF_steps)
                    step.output = MESSAGE
                MESSAGE = f"========================== Completing Deployment[{dId}] ==========================\n"
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                try:
                    DEPLOYMENT = WF.DEPLOYMENTS[dId]
                except Exception as Deployment_not_found_err:
                    MESSAGE += f"[ERROR] Unable to find deployment[{dId}]: \n   {Deployment_not_found_err}"
                    MESSAGE += f"Did you start Deployment[{dId}]?"
                    WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                    step.output = MESSAGE
                    continue
                MESSAGE =  f"Deployment {dName} wirj ID={dId} is completed"
                MESSAGE += f"Deplyment Info: {run_info}"
                MESSAGE += f"========================== End of Deployment {dName}: ID={dId}=========================="
                #WF.DEPLOYMENTS[dId][tID]["task"].status = Deployment_states[STATUS.lower()]
                #await WF.DEPLOYMENTS[dId]["task_list"].send()
                WF.update_chat_history(MESSAGE, role='system', verbose_out=show_WF_steps)
                WF.DEPLOYMENTS[dId]["state"]="completed"
                step.output = f"Deployment[{dId}] is COMPLETED"



