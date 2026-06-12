import copy
Default_vStore_params = {
        "embedding_model": "all-MiniLM-L6-v2",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "collection_name": "db",
        "persist_directory": "./vstore"
    }

Default_Nomic_params = {
        "embedding_model": "ollama/nomic-embed-text:latest",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "collection_name": "default_nomics",
        "persist_directory": "./embedding_db/default_nomic",
        "embedding_function_params": {
            "type": "openai_compatible",
            "model": "ollama/nomic-embed-text:latest",
            "base_url": "http://localhost:4444/v1",
            "api_key_var":"LOCAL_API_KEY",
            "drop_unsupported_params": True
        }
    }

# Summaries
Default_summaries_vs_params = copy.deepcopy(Default_vStore_params)
#Default_summaries_vs_params = copy.deepcopy(Default_Nomic_params)
Default_summaries_vs_params["collection_name"] = "summaries"
# Traces
Default_traces_vs_params = copy.deepcopy(Default_vStore_params)
#Default_traces_vs_params = copy.deepcopy(Default_Nomic_params)
Default_traces_vs_params["collection_name"] = "traces"
