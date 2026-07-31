#from framework.utils.io_tools import load_env_vars
import json
import copy
import ast
import re

from framework.utils.io_tools import copy, USER_ENV_VARs
from framework.utils.multimodal_input import normalize_capabilities
from framework.utils.config_tools import set_llm_api_key
import os

# 1. Default Local Engine
Provider_params = { 'provider_type': "openai",                    # the type of inference engine i.e both 'openai' and 'open ai compatible server' = 'openai'
                  'host'           : "https://shirty.sandia.gov", # URL of the node hosting the engine
                  'port'           : None,                        # Port on whitch it is listening i.e 8000 for vllm
                  'api_key_var'    : "LOCAL_API_KEY",             # The variable holding your API key in your .env file
                  'api_version'    : "api/v1",                    # The version of the API
                  'verbose'        : 2                            # Inference verbosity
                }
Model_params    = { 'model'        : None,                        # Name of the model as served by the inference engine i.e 'google/gema4'
                  "capabilities"   : []                           # The model capabilities i.e ['vision', 'tool',....]
}

_INDEXED_LLM_RE = re.compile(r"^LLM_(\d+)_(.+)$")
_INDEXED_FIELD_MAP = {
    "PROVIDER_TYPE": "provider_type",
    "INFERENCE_ENGINE_TYPE": "provider_type",
    "HOST": "host",
    "HOST_ADDRESS": "host",
    "INFERENCE_HOST_ADDRESS": "host",
    "PORT": "port",
    "HOST_PORT": "port",
    "INFERENCE_HOST_PORT": "port",
    "API_KEY_VAR": "api_key_var",
    "LOCAL_API_KEY_VAR": "api_key_var",
    "API_VERSION": "api_version",
    "VERBOSE": "verbose",
    "MODEL": "model",
    "LLM_MODEL": "model",
    "CAPABILITIES": "capabilities",
    "LLM_CAPABILITIES": "capabilities",
    "CTX_WINDOW_LENGTH": "ctx_window_length",
}


def _has_value(value):
    """Return True when an environment value is present and non-empty."""
    return value is not None and str(value).strip() != ""


def _coerce_int(value):
    """Best-effort integer coercion for numeric env values, preserving blanks."""
    if not _has_value(value):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _coerce_verbose(value):
    coerced = _coerce_int(value)
    return coerced


def _normalize_llm_entry(entry):
    """Normalize one LLM config entry to the shape consumed by build_list_agents()."""
    normalized = copy.deepcopy(Provider_params)
    normalized.update(copy.deepcopy(Model_params))
    normalized.update(copy.deepcopy(entry or {}))

    if "capabilities" in normalized:
        normalized["capabilities"] = list(normalize_capabilities(normalized.get("capabilities", [])))
    if "verbose" in normalized:
        normalized["verbose"] = _coerce_verbose(normalized["verbose"])
    if "ctx_window_length" in normalized:
        normalized["ctx_window_length"] = _coerce_int(normalized["ctx_window_length"])
    return normalized


def _entry_name(default_name, entry):
    """Choose a stable dictionary key for one LLM entry."""
    name = entry.get("name") or entry.get("id") or entry.get("alias")
    if _has_value(name):
        return str(name)
    model = entry.get("model")
    if _has_value(model):
        return str(model)
    return default_name


def build_single_llm_config(env_vars=None):
    """Build the legacy single-LLM config from the original .env variables."""
    env_vars = copy.deepcopy(USER_ENV_VARs if env_vars is None else env_vars)
    env_keys = env_vars.keys()

    provider = copy.deepcopy(Provider_params)
    model = copy.deepcopy(Model_params)

    # 2.1 Engine Params from .env
    if "INFERENCE_ENGINE_TYPE" in env_keys: provider['provider_type'] = env_vars['INFERENCE_ENGINE_TYPE']
    if "INFERENCE_HOST_ADDRESS" in env_keys: provider['host'] = env_vars['INFERENCE_HOST_ADDRESS']
    if "INFERENCE_HOST_PORT"    in env_keys: provider['port'] = env_vars['INFERENCE_HOST_PORT']
    if "LOCAL_API_KEY_VAR"      in env_keys: provider['api_key_var'] = env_vars['LOCAL_API_KEY_VAR']
    if "API_VERSION"            in env_keys: provider['api_version'] = env_vars['API_VERSION']
    # 2.2.Model Params from .env
    if "LLM_MODEL"              in env_keys: model['model'] = env_vars['LLM_MODEL']
    if 'LLM_CAPABILITIES'       in env_keys: model['capabilities'] = list(normalize_capabilities(env_vars['LLM_CAPABILITIES']))
    if 'LLM_CTX_WINDOW_LENGTH'  in env_keys and _has_value(env_vars['LLM_CTX_WINDOW_LENGTH']):
        model['ctx_window_length'] = _coerce_int(env_vars['LLM_CTX_WINDOW_LENGTH'])

    model_name = "llm0"
    if _has_value(model.get('model')):
        model_name = model['model']

    llm = {model_name: copy.deepcopy(provider)}
    for k in model.keys():
        llm[model_name][k] = model[k]
    return llm


def _discover_indexed_llm_ids(env_vars):
    ids = set()
    for key in env_vars.keys():
        match = _INDEXED_LLM_RE.match(key)
        if match:
            ids.add(int(match.group(1)))
    if _has_value(env_vars.get("LLM_COUNT")):
        try:
            ids.update(range(1, int(env_vars["LLM_COUNT"]) + 1))
        except ValueError:
            pass
    return sorted(ids)


def build_indexed_llm_config(env_vars=None):
    """Build multi-LLM config from indexed .env variables.

    Supported pattern:
        LLM_1_NAME=main
        LLM_1_MODEL=...
        LLM_1_HOST_ADDRESS=...
        LLM_1_LOCAL_API_KEY_VAR=...
        LLM_2_NAME=worker
        LLM_2_MODEL=...

    Missing per-LLM fields inherit the legacy/default provider settings.
    """
    env_vars = copy.deepcopy(USER_ENV_VARs if env_vars is None else env_vars)
    legacy_default = next(iter(build_single_llm_config(env_vars).values()))
    llms = {}

    for idx in _discover_indexed_llm_ids(env_vars):
        prefix = f"LLM_{idx}_"
        entry = copy.deepcopy(legacy_default)
        entry_name = None
        saw_config_value = False

        for key, value in env_vars.items():
            if not key.startswith(prefix):
                continue
            field = key[len(prefix):]
            if field == "NAME":
                if _has_value(value):
                    entry_name = str(value)
                continue
            target = _INDEXED_FIELD_MAP.get(field)
            if target is None:
                continue
            if not _has_value(value):
                continue
            saw_config_value = True
            if target == "capabilities":
                entry[target] = list(normalize_capabilities(value))
            elif target in {"verbose", "ctx_window_length"}:
                entry[target] = _coerce_int(value)
            else:
                entry[target] = value

        if not saw_config_value and entry_name is None:
            continue
        if entry_name:
            entry["name"] = entry_name
        name = _entry_name(f"llm{idx}", entry)
        llms[name] = _normalize_llm_entry(entry)
        llms[name].pop("name", None)
        llms[name].pop("id", None)
        llms[name].pop("alias", None)

    return llms


def read_llm_config_from_file(env_vars=None):
    """Read multi-LLM config from LLMS_JSON_FILE.
    LLMS_JSON_FILE should point to a proper json file containing LLM config
    """
    env_vars = copy.deepcopy(USER_ENV_VARs if env_vars is None else env_vars)

    llm_json_file = env_vars.get("LLMS_JSON_FILE")
    if _has_value(llm_json_file):
        try:
            with open(llm_json_file, 'r') as f:
                llm_conf = json.load(f)
        except Exception as json_read_err:
            print(f"[!][ERROR] unable to read {llm_json_file} as a json: {json_read_err}")
            print(f"[?][TRY] Trying to read it as a python dictionary...")
            try:
                with open(llm_json_file, 'r') as f:
                    # ast.literal_eval is safer than eval() for parsing python dict strings
                    llm_conf = ast.literal_eval(f.read())
            except Exception as dict_read_err:
                print(f"[!][ERROR] unable to read {llm_json_file} as a python dictionary neither: {dict_read_err}")
                print(f"[-] Giving up///")
                return {}
        #for k in llm_conf.keys():
        #    llm = llm_conf[k]
        #    set_llm_api_key(llm, env_vars=USER_ENV_VARs) 
 
        # Normalization Logic:
        # Ensure the output is always a dictionary mapping names -> config
        if isinstance(llm_conf, list):
            normalized_conf = {}
            for idx, item in enumerate(llm_conf):
                if not isinstance(item, dict):
                    continue
                # Try to find a key to use as the identifier
                name = item.get("name") or item.get("id") or item.get("alias") or f"llm_{idx}"
                normalized_conf[name] = item
            #llm_conf = normalized_conf
            #for k in llm_conf.keys():
            #    llm = llm_conf[k]
            #    set_llm_api_key(llm, env_vars=USER_ENV_VARs)
            return normalized_conf
        
        elif isinstance(llm_conf, dict):
            return llm_conf
        
        else:
            print(f"[!][ERROR] Unexpected JSON structure: expected list or dict, got {type(llm_conf)}")
            return {}

    return {}


def build_json_llm_config(env_vars=None):
    """Build multi-LLM config from LLMS_JSON.
    LLMS_JSON_FILE should point to a proper json file containing LLM config
    LLMS_JSON may be either:
      - a JSON object mapping names to config dictionaries, or
      - a JSON list of config dictionaries, each optionally containing name/id/alias.
    """
    env_vars = copy.deepcopy(USER_ENV_VARs if env_vars is None else env_vars)
    if os.environ.get("WOLF_DEBUG_ENV_DUMP", "").lower() in {"1", "true", "yes"}:
        redacted = {}
        for key, value in env_vars.items():
            key_l = str(key).lower()
            if any(marker in key_l for marker in ("key", "token", "secret", "password")) and value:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = value
        print(f"[++] JSON VARS = {redacted}")

    # Precedence to LLMS_JSON_FILE 
    llm_json_file = env_vars.get("LLMS_JSON_FILE")
    if _has_value(llm_json_file):
        return read_llm_config_from_file(env_vars=env_vars)

    # Then to LLMS_JSON
    raw = env_vars.get("LLMS_JSON")
    if not _has_value(raw):
        return {}
    data = json.loads(raw)
    llms = {}

    if isinstance(data, dict):
        iterable = data.items()
        for name, entry in iterable:
            if not isinstance(entry, dict):
                raise ValueError("LLMS_JSON object values must be LLM config objects")
            llms[str(name)] = _normalize_llm_entry(entry)
    elif isinstance(data, list):
        for idx, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                raise ValueError("LLMS_JSON list values must be LLM config objects")
            name = _entry_name(f"llm{idx}", entry)
            llms[name] = _normalize_llm_entry(entry)
            llms[name].pop("name", None)
            llms[name].pop("id", None)
            llms[name].pop("alias", None)
    else:
        raise ValueError("LLMS_JSON must be a JSON object or list")

    return llms


def build_llm_config(env_vars=None):
    """Build LLM mapping from environment variables.

    Precedence:
      1. LLMS_JSON_FILE, when provided.
      2. LLMS_JSON, when provided.
      3. Indexed LLM_N_* variables, when provided.
      4. Legacy single-LLM variables.
    """
    env_vars = copy.deepcopy(USER_ENV_VARs if env_vars is None else env_vars)

    json_llms = build_json_llm_config(env_vars)
    if json_llms:
        return json_llms

    indexed_llms = build_indexed_llm_config(env_vars)
    if indexed_llms:
        return indexed_llms

    return build_single_llm_config(env_vars)


ENV_VARs = copy.deepcopy(USER_ENV_VARs)
ENV_VAR_KEYs = ENV_VARs.keys()

# Preserve the historical single-LLM derived params for callers that import them.
_LEGACY_LLM = build_single_llm_config(ENV_VARs)
_LEGACY_MODEL_NAME = next(iter(_LEGACY_LLM.keys()))
LLM_params = {'provider': {k: _LEGACY_LLM[_LEGACY_MODEL_NAME][k] for k in Provider_params.keys()},
              'llm': {k: _LEGACY_LLM[_LEGACY_MODEL_NAME][k] for k in Model_params.keys() if k in _LEGACY_LLM[_LEGACY_MODEL_NAME]}}

# Build one or more LLMs from .env.
LLM = build_llm_config(ENV_VARs)
