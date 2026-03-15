import copy
Default_vStore_params = {
        "embedding_model": "all-MiniLM-L6-v2",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "collection_name": "db",
        "persist_directory": "./vstore"
    }

# Summaries
Default_summaries_vs_params = copy.deepcopy(Default_vStore_params)
Default_summaries_vs_params["collection_name"] = "summaries"
# Traces
Default_traces_vs_params = copy.deepcopy(Default_vStore_params)
Default_traces_vs_params["collection_name"] = "traces"
