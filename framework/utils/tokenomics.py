import tiktoken

def num_tokens_from_string(string: str, encoding_name="o200k_base") -> int:
    if not isinstance(string, str):
        string = str(string) if string is not None else ""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def num_tokens_chat_entry(chat_entry,
                          encoding_name="o200k_base",
                          valid_keys=["role","actor","content","timestamp","action"]):
    nTokens = 0
    for k in chat_entry.keys():
        if ( (chat_entry[k] is not None) and (k in valid_keys) ):
            if isinstance(chat_entry[k], dict):
                msg = f"{chat_entry[k]}"
            elif isinstance(chat_entry[k], str):
                msg = chat_entry[k]
            else:
                raise NotImplementedError(f"""num_tokens_chat_entry() is not implemented for {type(chat_entry[k])} types of chat entries.""")
            nTokens += num_tokens_from_string(f"{k}: {msg}")
    return nTokens

def num_tokens_from_messages(messages, model="gpt-4o"):
    """Return the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        print("Warning: model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    if model in {
        "gpt-3.5-turbo-0613",
        "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
        }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif "gpt-3.5-turbo" in model:
        print("Warning: gpt-3.5-turbo may update over time. Returning num tokens assuming gpt-3.5-turbo-0613.")
        return num_tokens_from_messages(messages, model="gpt-3.5-turbo-0613")
    elif "gpt-4" in model:
        print("Warning: gpt-4 may update over time. Returning num tokens assuming gpt-4-0613.")
        return num_tokens_from_messages(messages, model="gpt-4-0613")
    else:
        raise NotImplementedError(
            f"""num_tokens_from_messages() is not implemented for model {model}. See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens."""
        )
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
        num_tokens += 3
    return num_tokens