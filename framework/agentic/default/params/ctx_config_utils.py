from framework.agentic.default.params.known_llm_ctx_win_len import know_ctx_win_length

def set_ctx_win_len(llms, llm_max_ctx=know_ctx_win_length):
    llms_with_known_ctx_len = list(llm_max_ctx.keys())
    for llm in list(llms.keys()):
        model = llms[llm]["model"]
        if model in llms_with_known_ctx_len:
            llms[llm] ["ctx_window_length"] = llm_max_ctx[model]
