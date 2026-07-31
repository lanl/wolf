from collections import Counter, deque
from typing import Any, List, Optional
import json
import time

from framework.utils.io_tools import console, jsonfy, save_pickle_file, load_pickle_file
from framework.utils.json_parsing import robust_jsonfy
from framework.utils.tokenomics import num_tokens_from_string
# Import the updated workflow models that provide action-subset capability
from framework.workflows.workflow_models import (
    Actions as FullActions,
    SCHEMA_STRING as FULL_SCHEMA_STRING,
    AGENT_ROLE_PROMPT as FULL_AGENT_ROLE_PROMPT,
    get_actions_subset,
    ACTION_SPACE_PROMPT, ACTIONS, ACTION_NAMES,
)
from framework.workflows.sessions_data_models import BaseSession
from framework.infrastructure.base_infrastructure import BaseInfrastructure
from framework.workflows.enhanced_input import interactive_input_line_wrapped
from framework.workflows.base_workflow import BaseWorkflow
from framework.utils.multimodal_input import combine_prompt_with_user_content


class FastTurnBasedWorkflow(BaseWorkflow):
    """Hybrid low-latency workflow loop.

    The prompt shows full payload examples only for a small adaptive hot-action
    buffer.  All remaining active actions are shown by name/description and a
    short alias only.

    Agent behavior supported by this workflow:
    - Complete valid JSON action object: validate and execute in one model call.
    - Name-only action, alias, or object with no payload: trigger one targeted
      second prompt containing only that action's schema/example.
    - Adaptive scoring promotes reliable, recent, frequently successful actions
      into the hot buffer while demoting failure-prone or stale actions.
    """

    DEFAULT_HOT_ACTIONS = [
        "send_message",
        "read_file",
        "write_file",
        "run_syscall",
        "check_context_utilization",
        "list_memory_categories",
        "recall_memory",
        "create_memory_fragment",
        "semantic_recall",
        "get_list_known_universes",
        "universe_info",
        "universe_health",
    ]
    ALWAYS_HOT_ACTIONS = ["send_message", "read_file", "check_context_utilization"]
    RISKY_ACTIONS = {"write_file", "run_syscall", "create_universe"}
    DESTRUCTIVE_ACTIONS = {
        "terminate_deployment",
        "universe_kb_purge",
        "forget_memory",
        "clear_memory_category",
        "batch_forget_memory",
        "truncate_context_window",
    }

    HOT_ACTION_BUFFER_MAX = 20
    MIN_COUNT_TO_PROMOTE = 1
    RECENT_WINDOW_SIZE = 80
    PROMPT_SCHEMA_TOKEN_BUDGET = 3500
    SECOND_STEP_CONTEXT_ENTRIES = 4

    # Scoring knobs.  Exposed through configure_fast_workflow(...).
    DEFAULT_BOOST = 2.0
    ALWAYS_HOT_BOOST = 10.0
    SUCCESS_WEIGHT = 2.0
    FAILURE_PENALTY = 3.0
    RECENCY_WEIGHT = 1.0
    RISK_PENALTY = 1.0
    DESTRUCTIVE_PENALTY = 100.0

    def __init__(self,
                 session: BaseSession | str | None = None,
                 infra: BaseInfrastructure | None = None,
                 actions_union: Any = FullActions,
                 wf_rules_file: str | None = "config/preferences/rules/workflow/basewf.md",
                 wf_agent_behaviour_file: str | None = "config/preferences/behaviour/workflow/basewf.md",
                 wf_agent_sys_prompt_file: str | None = "config/preferences/prompts/workflow/basewf_default_assistant_sys_prompt.md",
                 wf_user: str = "user",
                 wf_turn: str | None = None,
                 WF_TAG: str | None = "FastTurnBasedWF",
                 WF_VERBOSE: int = 0):
        super().__init__(session,
                         infra,
                         actions_union,
                         wf_rules_file,
                         wf_agent_behaviour_file,
                         wf_agent_sys_prompt_file,
                         wf_user,
                         wf_turn,
                         WF_TAG,
                         WF_VERBOSE)
        self.action_usage_counts = Counter()
        self.action_success_counts = Counter()
        self.action_failure_counts = Counter()
        self.recent_actions = deque(maxlen=self.RECENT_WINDOW_SIZE)
        self.action_validation_errors: list[dict] = []
        self.last_agent_prompt_size: dict[str, Any] = {}
        self.last_hot_actions: list[str] = []
        self.last_cold_aliases: dict[str, str] = {}
        self.last_fast_path: str | None = None
        self._initialize_action_stats_from_history()
        pending_state = getattr(self, "_pending_workflow_state", None)
        if pending_state:
            self.restore_workflow_state(pending_state)
            self._pending_workflow_state = None

    # -----------------------------------------------------------------
    # Snapshot hooks used by BaseWorkflow when available
    # -----------------------------------------------------------------
    def get_workflow_state(self) -> dict:
        """Return JSON-serializable FastTurnBasedWorkflow adaptive state."""
        return {
            "action_usage_counts": dict(self.action_usage_counts),
            "action_success_counts": dict(self.action_success_counts),
            "action_failure_counts": dict(self.action_failure_counts),
            "recent_actions": list(self.recent_actions),
            "action_validation_errors": self.action_validation_errors[-100:],
            "last_agent_prompt_size": self.last_agent_prompt_size,
            "last_hot_actions": self.last_hot_actions,
            "last_cold_aliases": self.last_cold_aliases,
            "last_fast_path": self.last_fast_path,
            "config": self.get_fast_workflow_config(),
        }

    def restore_workflow_state(self, state: dict | None) -> None:
        """Restore adaptive state from a workflow snapshot."""
        if not isinstance(state, dict):
            return
        self.action_usage_counts = Counter(state.get("action_usage_counts", {}))
        self.action_success_counts = Counter(state.get("action_success_counts", {}))
        self.action_failure_counts = Counter(state.get("action_failure_counts", {}))
        self.recent_actions = deque(state.get("recent_actions", []), maxlen=self.RECENT_WINDOW_SIZE)
        self.action_validation_errors = list(state.get("action_validation_errors", []))[-100:]
        self.last_agent_prompt_size = dict(state.get("last_agent_prompt_size", {}))
        self.last_hot_actions = list(state.get("last_hot_actions", []))
        self.last_cold_aliases = dict(state.get("last_cold_aliases", {}))
        self.last_fast_path = state.get("last_fast_path")
        config = state.get("config", {})
        if isinstance(config, dict):
            self.configure_fast_workflow(**config)

    # -----------------------------------------------------------------
    # Configuration / observability
    # -----------------------------------------------------------------
    def get_fast_workflow_config(self) -> dict:
        return {
            "hot_action_buffer_max": self.HOT_ACTION_BUFFER_MAX,
            "default_hot_actions": list(self.DEFAULT_HOT_ACTIONS),
            "always_hot_actions": list(self.ALWAYS_HOT_ACTIONS),
            "min_count_to_promote": self.MIN_COUNT_TO_PROMOTE,
            "recent_window_size": self.RECENT_WINDOW_SIZE,
            "prompt_schema_token_budget": self.PROMPT_SCHEMA_TOKEN_BUDGET,
            "second_step_context_entries": self.SECOND_STEP_CONTEXT_ENTRIES,
            "default_boost": self.DEFAULT_BOOST,
            "always_hot_boost": self.ALWAYS_HOT_BOOST,
            "success_weight": self.SUCCESS_WEIGHT,
            "failure_penalty": self.FAILURE_PENALTY,
            "recency_weight": self.RECENCY_WEIGHT,
            "risk_penalty": self.RISK_PENALTY,
            "destructive_penalty": self.DESTRUCTIVE_PENALTY,
        }

    def configure_fast_workflow(self, **updates: Any) -> dict:
        """Update runtime tuning knobs from CLI/session config."""
        aliases = {
            "fast_hot_action_buffer_max": "hot_action_buffer_max",
            "fast_default_hot_actions": "default_hot_actions",
            "fast_always_hot_actions": "always_hot_actions",
            "fast_min_count_to_promote": "min_count_to_promote",
            "fast_recent_window_size": "recent_window_size",
            "fast_prompt_schema_token_budget": "prompt_schema_token_budget",
            "fast_second_step_context_entries": "second_step_context_entries",
        }
        attr_map = {
            "hot_action_buffer_max": "HOT_ACTION_BUFFER_MAX",
            "default_hot_actions": "DEFAULT_HOT_ACTIONS",
            "always_hot_actions": "ALWAYS_HOT_ACTIONS",
            "min_count_to_promote": "MIN_COUNT_TO_PROMOTE",
            "recent_window_size": "RECENT_WINDOW_SIZE",
            "prompt_schema_token_budget": "PROMPT_SCHEMA_TOKEN_BUDGET",
            "second_step_context_entries": "SECOND_STEP_CONTEXT_ENTRIES",
            "default_boost": "DEFAULT_BOOST",
            "always_hot_boost": "ALWAYS_HOT_BOOST",
            "success_weight": "SUCCESS_WEIGHT",
            "failure_penalty": "FAILURE_PENALTY",
            "recency_weight": "RECENCY_WEIGHT",
            "risk_penalty": "RISK_PENALTY",
            "destructive_penalty": "DESTRUCTIVE_PENALTY",
        }
        changed = {}
        for key, value in updates.items():
            canonical = aliases.get(key, key)
            if canonical not in attr_map:
                continue
            attr = attr_map[canonical]
            if canonical in {"default_hot_actions", "always_hot_actions"}:
                if isinstance(value, str):
                    value = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
                value = [v for v in value if v in ACTIONS]
            elif canonical in {"hot_action_buffer_max", "min_count_to_promote", "recent_window_size", "prompt_schema_token_budget", "second_step_context_entries"}:
                value = int(value)
            else:
                value = float(value)
            setattr(self, attr, value)
            changed[canonical] = value
        if "recent_window_size" in changed:
            self.recent_actions = deque(list(self.recent_actions)[-self.RECENT_WINDOW_SIZE:], maxlen=self.RECENT_WINDOW_SIZE)
        return changed

    def get_fast_workflow_observability(self) -> dict:
        return {
            "type": type(self).__name__,
            "hot_actions": self._hot_action_names(),
            "cold_action_count": len(self._cold_action_names()),
            "cold_aliases": self.last_cold_aliases,
            "usage_counts": dict(self.action_usage_counts),
            "success_counts": dict(self.action_success_counts),
            "failure_counts": dict(self.action_failure_counts),
            "recent_actions": list(self.recent_actions),
            "scores": {name: self._action_score(name) for name in self._active_action_names()},
            "last_agent_prompt_size": self.last_agent_prompt_size,
            "last_fast_path": self.last_fast_path,
            "recent_validation_errors": self.action_validation_errors[-10:],
            "config": self.get_fast_workflow_config(),
        }

    # -----------------------------------------------------------------
    # Action inventory / schema rendering helpers
    # -----------------------------------------------------------------
    def _initialize_action_stats_from_history(self) -> None:
        """Rebuild approximate action counts from durable chat history."""
        try:
            history = getattr(self.chat_manager, "CHAT_HISTORY", []) or []
            for entry in history:
                action = entry.get("action") if isinstance(entry, dict) else None
                action_name = None
                if isinstance(action, str):
                    action_name = action
                elif isinstance(action, dict):
                    action_name = action.get("action")
                if action_name in ACTIONS:
                    self._record_action_use(action_name, success=True, from_history=True)
        except Exception:
            self.action_usage_counts = Counter()
            self.action_success_counts = Counter()
            self.action_failure_counts = Counter()
            self.recent_actions = deque(maxlen=self.RECENT_WINDOW_SIZE)

    def _record_action_use(self, action_name: str, success: bool = True, from_history: bool = False) -> None:
        if action_name not in ACTIONS:
            return
        self.action_usage_counts[action_name] += 1
        if success:
            self.action_success_counts[action_name] += 1
        else:
            self.action_failure_counts[action_name] += 1
        if not from_history:
            self.recent_actions.append(action_name)

    def _record_validation_error(self, action_name: str | None, err_msg: str, phase: str) -> None:
        if action_name in ACTIONS:
            self._record_action_use(action_name, success=False)
        self.action_validation_errors.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action_name,
            "phase": phase,
            "error": str(err_msg)[:1200],
        })
        self.action_validation_errors = self.action_validation_errors[-100:]

    def _active_action_names(self) -> List[str]:
        if self.action_names_to_use:
            return [name for name in self.action_names_to_use if name in ACTIONS]
        return list(ACTION_NAMES)

    def _active_actions_map(self) -> dict:
        active_names = set(self._active_action_names())
        return {name: cls for name, cls in ACTIONS.items() if name in active_names}

    def _action_score(self, name: str) -> float:
        default_boost = self.DEFAULT_BOOST if name in self.DEFAULT_HOT_ACTIONS else 0.0
        always_boost = self.ALWAYS_HOT_BOOST if name in self.ALWAYS_HOT_ACTIONS else 0.0
        success = self.action_success_counts.get(name, 0) * self.SUCCESS_WEIGHT
        failure = self.action_failure_counts.get(name, 0) * self.FAILURE_PENALTY
        recent = sum(1 for item in self.recent_actions if item == name) * self.RECENCY_WEIGHT
        risk = self.RISK_PENALTY if name in self.RISKY_ACTIONS else 0.0
        destructive = self.DESTRUCTIVE_PENALTY if name in self.DESTRUCTIVE_ACTIONS else 0.0
        return default_boost + always_boost + success + recent - failure - risk - destructive

    def _hot_action_names(self) -> List[str]:
        """Return adaptive hot-action schema-buffer names under token budget."""
        active = set(self._active_action_names())
        always = [name for name in self.ALWAYS_HOT_ACTIONS if name in active]
        candidates = set(name for name in self.DEFAULT_HOT_ACTIONS if name in active)
        candidates.update(
            name for name, count in self.action_success_counts.items()
            if count >= self.MIN_COUNT_TO_PROMOTE and name in active
        )
        # Do not auto-promote destructive actions into full-schema hot view.
        candidates = {name for name in candidates if name not in self.DESTRUCTIVE_ACTIONS}
        candidates.update(always)

        ranked = sorted(candidates, key=lambda n: (-self._action_score(n), n))
        hot = []
        budget = max(500, int(self.PROMPT_SCHEMA_TOKEN_BUDGET))
        spent = 0
        for name in ranked:
            if len(hot) >= max(1, int(self.HOT_ACTION_BUFFER_MAX)):
                break
            action_cls = ACTIONS.get(name)
            if action_cls is None:
                continue
            block = self._hot_action_block(name, action_cls)
            block_tokens = num_tokens_from_string(block)
            # Always-hot actions are allowed to exceed budget slightly so the
            # assistant can always speak / read / check context.
            if name not in always and spent + block_tokens > budget:
                continue
            hot.append(name)
            spent += block_tokens
        self.last_hot_actions = hot
        return hot

    def _cold_action_names(self) -> List[str]:
        hot = set(self._hot_action_names())
        return [name for name in self._active_action_names() if name not in hot]

    def _cold_alias_map(self) -> dict[str, str]:
        aliases = {f"C{i:02d}": name for i, name in enumerate(self._cold_action_names(), start=1)}
        self.last_cold_aliases = aliases
        return aliases

    @staticmethod
    def _field_default(cls: type, field_name: str, default: str = "") -> str:
        field = cls.model_fields.get(field_name)
        value = getattr(field, "default", default) if field is not None else default
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _resolve_ref(schema: dict, ref: str) -> dict:
        if not ref.startswith("#/"):
            return {}
        cur = schema
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return cur if isinstance(cur, dict) else {}

    @classmethod
    def _deref(cls, prop: dict, root: dict) -> dict:
        seen = set()
        while isinstance(prop, dict) and "$ref" in prop and prop["$ref"] not in seen:
            seen.add(prop["$ref"])
            resolved = cls._resolve_ref(root, prop["$ref"])
            merged = {k: v for k, v in prop.items() if k != "$ref"}
            prop = {**resolved, **merged}
        return prop if isinstance(prop, dict) else {}

    @classmethod
    def _example_for_schema(cls, prop: dict, root: dict, depth: int = 0):
        if depth > 4:
            return "..."
        prop = cls._deref(prop, root)
        for union_key in ("anyOf", "oneOf", "allOf"):
            branches = prop.get(union_key)
            if isinstance(branches, list) and branches:
                branch = next((b for b in branches if isinstance(b, dict) and b.get("type") != "null"), branches[0])
                return cls._example_for_schema(branch, root, depth + 1)
        if "const" in prop:
            return prop["const"]
        if "enum" in prop and prop["enum"]:
            return prop["enum"][0]
        typ = prop.get("type")
        if typ == "object" or "properties" in prop:
            props = prop.get("properties", {})
            return {name: cls._example_for_schema(child, root, depth + 1) for name, child in props.items()} if isinstance(props, dict) else {}
        if typ == "array":
            return [cls._example_for_schema(prop.get("items", {}), root, depth + 1)]
        if typ == "integer":
            return 0
        if typ == "number":
            return 0.0
        if typ == "boolean":
            return False
        if typ == "string":
            return "string"
        return "value"

    @classmethod
    def _payload_example(cls, action_cls: type) -> dict:
        schema = action_cls.model_json_schema()
        payload_schema = schema.get("properties", {}).get("payload", {})
        example = cls._example_for_schema(payload_schema, schema)
        return example if isinstance(example, dict) else {}

    @classmethod
    def _payload_json_schema(cls, action_cls: type) -> str:
        payload_schema_text = cls._field_default(action_cls, "payload_schema", "").strip()
        if payload_schema_text:
            return payload_schema_text
        try:
            return json.dumps(cls._payload_example(action_cls), indent=2, sort_keys=True)
        except Exception:
            return "{}"

    @classmethod
    def _hot_action_block(cls, action_name: str, action_cls: type) -> str:
        desc = cls._field_default(action_cls, "description")
        payload = cls._payload_example(action_cls)
        return (
            f"- {action_name}: {desc}\n"
            f"  payload example: {json.dumps(payload, sort_keys=True)}"
        )

    def _hybrid_action_prompt(self) -> str:
        active_actions = self._active_actions_map()
        hot_names = self._hot_action_names()
        aliases = self._cold_alias_map()
        lines = [
            "Action response protocol:",
            "- Prefer emitting one complete JSON action object when the needed action is in HOT ACTIONS.",
            "- If you need a COLD ACTION schema, emit only the exact action name or alias (e.g. C03). The workflow will ask one targeted follow-up.",
            "- Do not add surrounding prose or Markdown.",
            "- Complete action top-level fields: action, payload, purpose, expectations, yield_motion_to.",
            "",
            f"HOT ACTIONS WITH PAYLOAD EXAMPLES (adaptive, max={self.HOT_ACTION_BUFFER_MAX}, token_budget={self.PROMPT_SCHEMA_TOKEN_BUDGET}):",
            "*** HOT ACTIONS START ***",
        ]
        for name in hot_names:
            cls = active_actions[name]
            risk_note = " [RISKY: use only when appropriate/approved]" if name in self.RISKY_ACTIONS else ""
            lines.append(self._hot_action_block(name, cls) + risk_note)
        lines.extend([
            "*** HOT ACTIONS END ***",
            "",
            "COLD ACTIONS (name/description only; use name or alias to request exact schema):",
            "*** COLD ACTIONS START ***",
        ])
        for alias, name in aliases.items():
            cls = active_actions[name]
            desc = self._field_default(cls, "description")
            risk_note = " [DESTRUCTIVE]" if name in self.DESTRUCTIVE_ACTIONS else (" [RISKY]" if name in self.RISKY_ACTIONS else "")
            lines.append(f"{alias} = '{name}': {desc}{risk_note}")
        lines.extend([
            "*** COLD ACTIONS END ***",
            f"Allowed action names: {self._active_action_names()}",
        ])
        prompt = "\n".join(lines)
        self.last_agent_prompt_size["hybrid_action_prompt_tokens"] = num_tokens_from_string(prompt)
        return prompt

    def _minimal_recent_context(self) -> str:
        """Return a small context slice for second-step targeted prompts."""
        entries = getattr(self.chat_manager, "CHAT_HISTORY", []) or []
        recent = entries[-max(1, int(self.SECOND_STEP_CONTEXT_ENTRIES)):]
        rendered = []
        for entry in recent:
            sender = entry.get("sender", "unknown") if isinstance(entry, dict) else "unknown"
            content = entry.get("content", entry) if isinstance(entry, dict) else entry
            rendered.append(f"[{sender}] {content}")
        return "\n".join(rendered)

    def _targeted_action_prompt(self, action_name: str, validation_error: str | None = None) -> str:
        action_cls = ACTIONS[action_name]
        payload_schema = self._payload_json_schema(action_cls)
        error_block = f"\nPrevious validation error to fix:\n{validation_error}\n" if validation_error else ""
        prompt = (
            f"{self.agent_role_prompt}\n\n"
            "You are in a targeted second-step action formatting turn.\n"
            f"Selected action: {action_name}\n"
            f"Description: {self._field_default(action_cls, 'description')}\n"
            f"{error_block}"
            "Recent task context:\n"
            "*** Recent Context Start ***\n"
            f"{self._minimal_recent_context()}\n"
            "*** Recent Context End ***\n\n"
            "Emit exactly one valid JSON action object and no surrounding prose or Markdown.\n"
            "Top-level fields must be: action, payload, purpose, expectations, yield_motion_to.\n"
            f"The top-level action field must be {action_name!r}.\n"
            "Payload schema/example:\n"
            f"{payload_schema}\n"
        )
        self.last_agent_prompt_size["targeted_prompt_tokens"] = num_tokens_from_string(prompt)
        return prompt

    @staticmethod
    def _parse_possible_json_response(response: Any) -> Any:
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            parsed = robust_jsonfy(response)
            if isinstance(parsed, dict) and "parsed" in parsed:
                return parsed["parsed"]
        return response

    def _clean_action_name(self, response: Any, active_names: List[str]) -> str:
        aliases = self.last_cold_aliases or self._cold_alias_map()
        inverse_aliases = {alias.lower(): name for alias, name in aliases.items()}
        if isinstance(response, str):
            candidate = response.strip().strip('`').strip()
            parsed = robust_jsonfy(candidate)
            if isinstance(parsed, dict) and "parsed" in parsed:
                parsed_value = parsed["parsed"]
                if isinstance(parsed_value, str):
                    candidate = parsed_value.strip()
                elif isinstance(parsed_value, dict) and isinstance(parsed_value.get("action"), str):
                    candidate = parsed_value["action"].strip()
            candidate = candidate.strip().strip('"').strip("'")
        elif isinstance(response, dict) and isinstance(response.get("action"), str):
            candidate = response["action"].strip()
        else:
            candidate = str(response).strip()

        if candidate in active_names:
            return candidate
        if candidate.lower() in inverse_aliases:
            return inverse_aliases[candidate.lower()]
        matches = [name for name in active_names if name in candidate]
        if len(matches) == 1:
            return matches[0]
        return candidate

    def _attempt_action_execution(self, action_args: dict, actor, phase: str) -> bool:
        """Validate/execute a complete action object. Return True on handled success."""
        proposed_action = action_args.get("action") if isinstance(action_args, dict) else None
        bad_format, err_msg, action_obj, normalized = self.normalize_and_validate_agent_response(action_args, actor)
        if bad_format:
            self._record_validation_error(proposed_action, err_msg, phase)
            return False

        self.update_history(actor=actor.name, content=normalized, action=normalized["action"], log_console=True)
        result = action_obj.execute(infra=self.infra)
        self._record_action_use(normalized["action"], success=True)
        self.last_fast_path = phase

        if getattr(action_obj, "yield_motion_to", None):
            self.WORKFLOW_TURN = action_obj.yield_motion_to
        elif getattr(action_obj, "receiver", None):
            self.WORKFLOW_TURN = action_obj.receiver
        else:
            self.WORKFLOW_TURN = "system"
        return True

    # -----------------------------------------------------------------
    # Unified actor-turn handling (used for both the main agent and workers)
    # -----------------------------------------------------------------
    def _handle_actor_turn(self, actor, name: str, verbose=0):
        self.infra.show_updated_history()

        context_str = self.context_manager.get_compacted_context()
        diagnostics = self.context_manager.get_context_diagnostics()
        active_names = self._active_action_names()

        AGENT_PROMPT = (
            f"{self.agent_role_prompt}\n\n"
            "Below is the context formed from the current chat history:\n"
            "*** Context Start ***\n"
            f"{context_str}\n"
            "*** Context End ***\n\n"
            "*** Description of the infrastructure Start *** \n"
            f"{self.infra.INFRA_DESCRIPTION}\n"
            "*** Description of the infrastructure End *** \n"
            f"*** Best Practices Start *** \n"
            f"{self.AGENT_BEHAVIOUR}\n"
            f"*** Best Practices End *** \n"
            f"*** WORKFLOW RULES Start *** \n"
            f"{self.WF_RULES}\n"
            "*** WORKFLOW RULES End *** \n\n"
            f"{self._hybrid_action_prompt()}\n"
        )
        self.last_agent_prompt_size["first_prompt_tokens"] = num_tokens_from_string(AGENT_PROMPT)
        self.last_agent_prompt_size["context_tokens"] = num_tokens_from_string(context_str)
        if verbose > 0:
            console.print(f"[++] AGENT_PROMPT = {AGENT_PROMPT}")

        pending_agent_content = self.infra.consume_pending_agent_content()
        AGENT_INPUT = combine_prompt_with_user_content(AGENT_PROMPT, pending_agent_content)
        response = actor.get_chat_response(AGENT_INPUT)
        if verbose > 0:
            console.print(f"[!][!] AGENT RESPONSE = {response} | type = {type(response)}")

        parsed_response = self._parse_possible_json_response(response)
        next_action_name = None
        validation_error = None

        # One-call path: complete JSON action object.
        if isinstance(parsed_response, dict) and isinstance(parsed_response.get("action"), str):
            proposed_action = parsed_response.get("action")
            if proposed_action in active_names and "payload" in parsed_response:
                if self._attempt_action_execution(parsed_response, actor, phase="one_call_complete_json"):
                    return
                validation_error = self.action_validation_errors[-1]["error"] if self.action_validation_errors else None
                next_action_name = proposed_action
            elif proposed_action in active_names:
                next_action_name = proposed_action

        # Name/alias path.
        if next_action_name is None:
            next_action_name = self._clean_action_name(parsed_response, active_names)

        nTrial, nMAX = 0, 5
        while next_action_name not in active_names and nTrial < nMAX:
            fix_prompt = (
                f"[FORMAT ERROR] Response {response!r} was neither a complete valid JSON action nor an allowed action name/alias.\n"
                f"Allowed action names: {active_names}\n"
                f"Allowed aliases: {self.last_cold_aliases}\n"
                "Respond with either a complete JSON action object or the exact action name/alias."
            )
            response = actor.get_chat_response(f"{AGENT_PROMPT}\n{fix_prompt}")
            parsed_response = self._parse_possible_json_response(response)
            if isinstance(parsed_response, dict) and isinstance(parsed_response.get("action"), str):
                proposed_action = parsed_response["action"]
                if proposed_action in active_names and "payload" in parsed_response:
                    if self._attempt_action_execution(parsed_response, actor, phase="one_call_complete_json_after_selection_retry"):
                        return
                    validation_error = self.action_validation_errors[-1]["error"] if self.action_validation_errors else None
                next_action_name = proposed_action
            else:
                next_action_name = self._clean_action_name(parsed_response, active_names)
            nTrial += 1

        if next_action_name not in active_names:
            self.WORKFLOW_TURN = "user"
            self.update_history(
                actor="system",
                content=f"[ERROR] {actor.name} could not select a valid action after {nMAX} attempts. Last response: {response}",
                action={"action": "system_error"},
                log_console=True,
            )
            return

        # Targeted second-step prompt. This is intentionally much smaller than
        # the first prompt: no full context, no hot buffer, no cold inventory.
        action_prompt = self._targeted_action_prompt(next_action_name, validation_error=validation_error)
        payload_schema = self._payload_json_schema(ACTIONS[next_action_name])

        if "structured_output" in getattr(actor, "capabilities", []):
            response = actor.get_structured_output(user_prompt=action_prompt, output_format=self.Actions)
        else:
            bad_format, response, raw_response, result = actor.format_agent_response(action_prompt, payload_schema)
            if bad_format:
                self._record_validation_error(next_action_name, raw_response, phase="targeted_formatting")
                self.WORKFLOW_TURN = "user"
                self.update_history(
                    actor="system",
                    content=f"{actor.name} could not produce a valid response:\n {raw_response}",
                    action={"action": "system_info"},
                    log_console=True,
                )
                return

        payload = self._parse_possible_json_response(response)
        if not isinstance(payload, dict):
            self._record_validation_error(next_action_name, f"Non-dict payload: {payload}", phase="targeted_parse")
            self.WORKFLOW_TURN = "user"
            self.update_history(
                actor="system",
                content=f"[ERROR] Invalid action payload for {next_action_name}: {payload}",
                action={"action": "system_error"},
                log_console=True,
            )
            return

        if "payload" not in payload:
            action_args = {"action": next_action_name, "payload": payload}
        else:
            action_args = payload
        action_args["action"] = next_action_name

        if self._attempt_action_execution(action_args, actor, phase="targeted_second_step"):
            return

        err_msg = self.action_validation_errors[-1]["error"] if self.action_validation_errors else "validation failed"
        self.WORKFLOW_TURN = "user"
        self.update_history(actor="system", content=err_msg, action={"action": "system_info"}, log_console=True)

    # -----------------------------------------------------------------
    # Core workflow loop
    # -----------------------------------------------------------------
    def run(self, user_name: str = "user",
            action_names: Optional[List[str]] = None,
            wolf_commands=['help', 'show', 'set', 'reload', 'actions', 'clear', 'quit', 'exit', 'bye', 'cls'],
            wf_first_turn="user",
            log_console: bool = True,
            verbose: int = 0):
        self.set_wf_action_space(action_names)
        self.infra.cli_workflow = self
        self.WF_USER = user_name
        self.infra.ROLEs[user_name] = "user"
        self.WORKFLOW_TURN = wf_first_turn

        while True:
            turn = self.WORKFLOW_TURN.strip().lower()
            worker_names = [w.strip().lower() for w in self.workers]

            if turn in ["user", self.WF_USER.lower()]:
                self.infra.show_updated_history()
                raw_input = interactive_input_line_wrapped(
                    prompt_text=f"[{self.WF_USER}]> ",
                    wf_commands=wolf_commands
                )
                if raw_input is None:
                    break
                user_prompt = raw_input.strip()
                BREAK, IS_CMD, ERROR, INTERLOCUTOR, WF_PROMPT = self.infra.process_user_input(user_prompt)
                if IS_CMD:
                    if WF_PROMPT:
                        console.print(WF_PROMPT)
                        self.console_log(WF_PROMPT)
                    if BREAK:
                        break
                else:
                    if ERROR and WF_PROMPT:
                        console.print(WF_PROMPT)
                        self.console_log(WF_PROMPT)
                    else:
                        target_actor = self.workers.get(INTERLOCUTOR, self.agent)
                        input_bundle = self.infra.prepare_user_input_for_agent(WF_PROMPT, agent=target_actor)
                        self.update_history(
                            actor=self.WF_USER,
                            content=input_bundle.history_text,
                            action={"action": "user_input"},
                            log_console=log_console,
                        )
                        self.WORKFLOW_TURN = INTERLOCUTOR
                continue

            if turn in ["system", "assistant", "agent", self.agent.name.strip().lower()]:
                self._handle_actor_turn(self.agent, self.agent.name, verbose=verbose)
                continue

            if turn in worker_names:
                worker = self.workers[self.WORKFLOW_TURN]
                self._handle_actor_turn(worker, worker.name, verbose=verbose)
                continue

            self.WORKFLOW_TURN = "user"
