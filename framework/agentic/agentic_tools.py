import funkybob, tiktoken

class NameGenerator():
    def __init__(self, generator='unique_random'):
        gen = generator.lower()
        if gen in ['unique', 'unique_random', 'unique_random_name', 'unique_random_name_generator', 'uniquerandomnamegenerator']:
            self.name_generator = funkybob.UniqueRandomNameGenerator()
        elif gen in ['simple', 'simple_name', 'simple_name_generator','simplenamegenerator']:
            self.name_generator = funkybob.SimpleNameGenerator()
        elif geb in ['random_name', 'random_name_generator','randomnamegenerator']:
            self.name_generator = funkybob.RandomNameGenerator()
        else:
            raise Exception(f"[!][NameGenerator]: Generator {generator} is not supported: try SimpleNameGenerator, RandomNameGenerator, or UniqueRandomNameGenerator")
        self.name_iterator = iter(self.name_generator)
    def get_name(self):
        return next(self.name_iterator)

def num_tokens_from_string(string: str, encoding_name="o200k_base") -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens
#{"role": "system", "actor":"system", "content": f"Agent {self.agent.name}, is the main agent and in charge of managing this workflow", "timestamp":timestamp, "action":None}

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
                raise NotImplementedError( f"""num_tokens_chat_entry() is not implemented for {type(chat_entry[k])} types of chat entries.""")
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

def format_agent_response(prompt, schema, agent):
    while True:
        raw = agent.get_chat_response(user_prompt=prompt + f"\n{schema}")
        result = robust_jsonfy(raw)
        if "parsed" in result:
            return False, result["parsed"], raw, result
        raw = agent.get_chat_response(
            user_prompt=f"Please fix the JSON format of the following response: {result}\n{schema}"
        )
        result = robust_jsonfy(raw)
        if "parsed" in result:
            return False, result["parsed"], raw, result
        return True, None, raw, result

