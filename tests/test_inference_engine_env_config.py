from config.defaults.inference_engine import build_llm_config


def test_legacy_single_llm_env_config_is_preserved():
    llms = build_llm_config(
        {
            "INFERENCE_ENGINE_TYPE": "openai",
            "INFERENCE_HOST_ADDRESS": "http://localhost",
            "INFERENCE_HOST_PORT": "8000",
            "LOCAL_API_KEY_VAR": "LOCAL_API_KEY",
            "API_VERSION": "v1",
            "LLM_MODEL": "legacy-model",
            "LLM_CAPABILITIES": "['text','tool']",
        }
    )

    assert list(llms) == ["legacy-model"]
    assert llms["legacy-model"]["host"] == "http://localhost"
    assert llms["legacy-model"]["port"] == "8000"
    assert llms["legacy-model"]["api_key_var"] == "LOCAL_API_KEY"
    assert llms["legacy-model"]["api_version"] == "v1"
    assert llms["legacy-model"]["model"] == "legacy-model"
    assert set(llms["legacy-model"]["capabilities"]) == {"text", "tool"}


def test_indexed_multi_llm_env_config_builds_multiple_entries_with_inheritance():
    llms = build_llm_config(
        {
            "INFERENCE_HOST_ADDRESS": "http://default-host",
            "LOCAL_API_KEY_VAR": "DEFAULT_KEY",
            "API_VERSION": "v1",
            "LLM_1_NAME": "main",
            "LLM_1_MODEL": "model-a",
            "LLM_1_CAPABILITIES": "['text','tool']",
            "LLM_2_NAME": "worker",
            "LLM_2_MODEL": "model-b",
            "LLM_2_HOST_ADDRESS": "http://worker-host",
            "LLM_2_LOCAL_API_KEY_VAR": "WORKER_KEY",
            "LLM_2_VERBOSE": "3",
            "LLM_2_CTX_WINDOW_LENGTH": "12345",
        }
    )

    assert list(llms) == ["main", "worker"]
    assert llms["main"]["model"] == "model-a"
    assert llms["main"]["host"] == "http://default-host"
    assert llms["main"]["api_key_var"] == "DEFAULT_KEY"
    assert set(llms["main"]["capabilities"]) == {"text", "tool"}

    assert llms["worker"]["model"] == "model-b"
    assert llms["worker"]["host"] == "http://worker-host"
    assert llms["worker"]["api_key_var"] == "WORKER_KEY"
    assert llms["worker"]["verbose"] == 3
    assert llms["worker"]["ctx_window_length"] == 12345


def test_llms_json_takes_precedence_over_indexed_entries():
    llms = build_llm_config(
        {
            "LLMS_JSON": '{"json-main": {"model": "json-model", "host": "http://json-host", "api_key_var": "JSON_KEY", "api_version": "v1", "capabilities": ["text"]}}',
            "LLM_1_NAME": "indexed-main",
            "LLM_1_MODEL": "indexed-model",
        }
    )

    assert list(llms) == ["json-main"]
    assert llms["json-main"]["model"] == "json-model"
    assert llms["json-main"]["host"] == "http://json-host"
    assert llms["json-main"]["api_key_var"] == "JSON_KEY"
    assert llms["json-main"]["capabilities"] == ["text"]
