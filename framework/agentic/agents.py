# agents.py
from __future__ import annotations
import asyncio
import base64
from typing import Any, Dict, List, Union, Optional
from pydantic import BaseModel
from urllib.parse import urlparse
# OpenAI
import openai
from openai import OpenAI, AsyncOpenAI
# Instructor
try:
    import instructor
except ImportError:
    instructor = None
# Ollama (optional backend)
try:
    import ollama
except ImportError:
    ollama = None

# Local tools
from framework.agentic.agentic_tools import NameGenerator
from framework.utils.io_tools import console, jsonfy
from framework.utils.json_parsing import robust_jsonfy

Message = Dict[str, str]  # alias for chat message dict

class OpenAIAgent:
    """
    A unified OpenAI-compatible agent wrapper.
    Supports sync/async chat, streaming, structured outputs, vision, and audio.
    """
    def __init__(
        self, 
        model: str,
        host_address: str = "http://localhost",
        host_port: Optional[int] = None,
        api_key: str|None = None,
        api_version: Optional[str] = None,
        sys_prompt: str = "You are a helpful assistant",
        agent_name: Optional[str] = None,
        cache_history: bool = True,
        verbose: int = 0,
        capabilities=[],
        ctx_window_length=None, ) -> None:

        # Name assignment
        if agent_name is None:
            name_generator = NameGenerator()
            agent_name = name_generator.get_name()
        self.name = agent_name
        if api_key is None: api_key="none"
        self.model = model
        self.host_address = host_address
        self.host_port = host_port
        self.api_version = api_version
        self.api_key = api_key
        self.verbose = verbose
        self.sys_prompt = sys_prompt
        self.cache_history = cache_history
        self.console = console if console is not None else print
        self.capabilities=capabilities
        self.ctx_window_length = ctx_window_length
        
        parsed = urlparse(host_address)
        scheme = parsed.scheme if parsed.scheme else "http"
        netloc = parsed.netloc or "localhost"
        path = parsed.path
        if host_port:
            netloc = f"{netloc}:{host_port}"
        
        # Construct base_url: include the path from host_address if present
        self.base_url = f"{scheme}://{netloc}{path}"
        
        if api_version:
            # Ensure we don't double slash
            self.base_url = self.base_url.rstrip('/')
            self.base_url += f"/{api_version}"
            
        if api_key:
            self.llm = OpenAI(api_key=api_key, base_url=self.base_url)
            self.async_llm = AsyncOpenAI(api_key=api_key, base_url=self.base_url)
        else:
            self.llm = OpenAI(base_url=self.base_url)
            self.async_llm = AsyncOpenAI(base_url=self.base_url)
            
        if instructor:
            self.instructor_client = instructor.from_openai(
                OpenAI(base_url=self.base_url, api_key=self.api_key),
                mode=instructor.Mode.JSON,
            )
            self.instructor_async_client = instructor.from_openai(
                AsyncOpenAI(base_url=self.base_url, api_key=self.api_key),
                mode=instructor.Mode.JSON,
            )
        else:
            self.instructor_client = None
            self.instructor_async_client = None
            
        self.CTX: List[Message] = [
            {"role": "system", "content": f"You name is {self.name}"},
            {"role": "system", "content": self.sys_prompt},
        ]
        if self.verbose > 0:
            self.console_log(
                f"""
                [*OpenAI*] Agent('{self.name}'):
                  Sys Prompt: {self.sys_prompt}
                  LLM Model: {self.model}
                  Base URL: {self.base_url}
                  Max CTX: {self.ctx_window_length}
                  Instructor Support: {self.instructor_client is not None}
                """
            )

    # ---------------------------
    # Public Methods
    # ---------------------------

    def get_chat_response(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        model: Optional[str] = None,
        resp_choice_idx: Union[int, List[int]] = 0,) -> Union[str, List[str]]:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        raw_response = self.llm.chat.completions.create(model=model, messages=CTX)
        return self._extract_response(raw_response, resp_choice_idx)

    async def get_chat_response_async(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        model: Optional[str] = None,
        resp_choice_idx: Union[int, List[int]] = 0,) -> Union[str, List[str]]:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        raw_response = await self.async_llm.chat.completions.create(
            model=model, messages=CTX
        )
        return self._extract_response(raw_response, resp_choice_idx)

    def stream_chat_response(self, user_prompt: Union[str, Message, List[Message], 
                             List[Dict[str, Any]]], model: Optional[str] = None) -> str:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        stream = self.llm.chat.completions.create(model=model, messages=CTX, stream=True)
        response = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            self.console_log(delta, end="")
            response += delta
        return response

    async def stream_chat_response_async(self, user_prompt: Union[str, Message, List[Message], 
                                         List[Dict[str, Any]]], model: Optional[str] = None) -> str:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        response = ""
        async with await self.async_llm.chat.completions.create(
            model=model, messages=CTX, stream=True
        ) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                self.console_log(delta, end="")
                response += delta
        return response

    def get_json_structured_output(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        output_format: Any,
        temperature: float = 0,
        model: Optional[str] = None,
        resp_choice_idx: Union[int, List[int]] = 0,) -> Any:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        if "structured_output" in self.capabilities:
            try:
                completion = self.llm.beta.chat.completions.parse(
                    temperature=temperature,
                    model=model,
                    messages=CTX,
                    response_format=output_format,
                )
                return self._extract_response(completion, resp_choice_idx, structured=True)
            except Exception as e:
                return f"[0][!][FORMAT ERROR]: Problem with Assistant output: {e}"
        else:
            try:
                completion = self.llm.beta.chat.completions.parse(
                    temperature=temperature,
                    model=model,
                    messages=CTX,
                )
                agent_output = self._extract_response(completion, resp_choice_idx, structured=False)
            except Exception as e:
                return f"[1][!][FORMAT ERROR]: Problem with Assistant output: {e}"
            if isinstance(agent_output, list):
                try:
                    return [ jsonfy(output) for output in agent_output ]
                except Exception as e:
                    return f"[2][!][get_json_structured_output][FORMAT ERROR]: Problem with Assistant output: {e}"
            elif isinstance(agent_output, str):
                    try:
                        return jsonfy(agent_output)
                    except Exception as e1:
                        return f"[3][!][get_json_structured_output][FORMAT ERROR]: Problem with Assistant output: {e1}"
            else:
                return f"[4][!][get_json_structured_output][FORMAT ERROR]: Problem with Assistant output"

    async def get_json_structured_output_async(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        output_format: Any,
        temperature: float = 0,
        model: Optional[str] = None,
        resp_choice_idx: Union[int, List[int]] = 0,) -> Any:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        if "structured_output" in self.capabilities:
            try:
                completion = await self.async_llm.beta.chat.completions.parse(
                    temperature=temperature,
                    model=model,
                    messages=CTX,
                    response_format=output_format,
                )
                return self._extract_response(completion, resp_choice_idx, structured=True)
            except Exception as e:
                self.console_log(f"[5][!][get_json_structured_output_async][FORMAT WARN]: Problem with Assistant output: {e}")
                if isinstance(user_prompt, str):
                    try:
                        return jsonfy(user_prompt)
                    except Exception as e1:
                        return f"[6][!][FORMAT ERROR]: Problem with Assistant output: {e1}"
        else:
            try:
                completion = await self.async_llm.beta.chat.completions.parse(temperature=temperature, 
                                                                          model=model, messages=CTX,)
                agent_output = self._extract_response(completion, resp_choice_idx, structured=False)
            except Exception as e:
                return f"[7][!][FORMAT ERROR]: Problem with Assistant output: {e}"
            if isinstance(agent_output, list):
                try:
                    return [ jsonfy(output) for output in agent_output ]
                except Exception as e:
                    return f"[8][!][get_json_structured_output_async][FORMAT ERROR]: Problem with Assistant output: {e}"
            elif isinstance(agent_output, str):
                    try:
                        return jsonfy(agent_output)
                    except Exception as e1:
                        return f"[9][!][get_json_structured_output_async][FORMAT ERROR]: Problem with Assistant output: {e1}"
            else:
                return f"[10][!][get_json_structured_output_async][FORMAT ERROR]: Problem with Assistant output"

    def get_structured_output(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        output_format: BaseModel,
        temperature: float = 0,
        model: Optional[str] = None,) -> Any:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        if self.instructor_client:
            if "structured_output" in self.capabilities:
                return self.instructor_client.chat.completions.create(
                    model=model, messages=CTX, response_model=output_format
                )
            else:
                return self.get_json_structured_output(user_prompt, output_format, temperature, model)
        else:
            return self.get_json_structured_output(user_prompt, output_format, temperature, model)

    async def get_structured_output_async(
        self, 
        user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]],
        output_format: BaseModel,
        temperature: float = 0,
        model: Optional[str] = None,) -> Any:

        model = model or self.model
        CTX = self._make_ctx(user_prompt)
        if self.instructor_async_client:
            if "structured_output" in self.capabilities:
                return await self.instructor_async_client.chat.completions.create(
                    model=model, messages=CTX, response_model=output_format
                )
            else:
                return await self.get_json_structured_output_async( user_prompt, output_format, temperature, model)
        else:
            return await self.get_json_structured_output_async(user_prompt, output_format, temperature, model)

    # -------------------- AUDIO MODALITIES --------------------
    def get_audio_transcription(self, file_path: str, model: str = "whisper-1") -> str:
        """Transcribe audio file to text."""
        with open(file_path, "rb") as audio_file:
            transcript = self.llm.audio.transcriptions.create(model=model, file=audio_file)
            return transcript.text

    async def get_audio_transcription_async(self, file_path: str, model: str = "whisper-1") -> str:
        """Transcribe audio file to text asynchronously."""
        with open(file_path, "rb") as audio_file:
            transcript = await self.async_llm.audio.transcriptions.create(model=model, file=audio_file)
            return transcript.text

    def get_speech_synthesis(self, text: str, output_path: str, model: str = "tts-1", voice: str = "alloy") -> None:
        """Convert text to speech and save to file."""
        response = self.llm.audio.speech.create(model=model, voice=voice, input=text)
        response.stream_to_file(output_path)

    async def get_speech_synthesis_async(self, text: str, output_path: str, model: str = "tts-1", voice: str = "alloy") -> None:
        """Convert text to speech and save to file asynchronously."""
        response = await self.async_llm.audio.speech.create(model=model, voice=voice, input=text)
        await response.stream_to_file(output_path)

    # -------------------- CTX OPERATIONS --------------------
    def reset_ctx(self, system_prompt: Optional[str] = None) -> None:
        system_prompt = system_prompt or self.sys_prompt
        self.CTX = [
            {"role": "system", "content": f"You name is {self.name}"},
            {"role": "system", "content": system_prompt},
        ]

    # ---------------------------
    # Private Helpers
    # ---------------------------

    def _make_ctx(self, user_prompt: Union[str, Message, List[Message], List[Dict[str, Any]]]) -> List[Message]:

        if isinstance(user_prompt, list):
            # Check if the list is already formatted as multimodal content
            if len(user_prompt) > 0 and isinstance(user_prompt[0], dict) and "role" in user_prompt[0]:
                return self.CTX + user_prompt
            
            # Otherwise, treat it as content blocks for a single user message
            content = []
            for item in user_prompt:
                if isinstance(item, str):
                    content.append({"type": "text", "text": item})
                elif isinstance(item, dict):
                    if "image_url" in item:
                        content.append({"type": "image_url", "image_url": item})
                    elif "type" in item:
                        content.append(item)
                    else:
                        content.append({"type": "text", "text": str(item)})
            return self.CTX + [{"role": "user", "content": content}]

        if isinstance(user_prompt, dict):
            if "role" in user_prompt:
                return self.CTX + [user_prompt]
            return self.CTX + [{"role": "user", "content": user_prompt}]
            
        if isinstance(user_prompt, str):
            return self.CTX + [{"role": "user", "content": user_prompt}]
            
        raise TypeError(f"[FORMAT ERROR]: {type(user_prompt)} not supported")

    def _extract_response(self, completion: Any, resp_choice_idx: Union[int, List[int]], structured: bool = False) -> Union[str, List[str], Any]:
        if isinstance(resp_choice_idx, list):
            return [
                self._extract_single(completion, idx, structured)
                for idx in resp_choice_idx
            ]
        return self._extract_single(completion, resp_choice_idx, structured)

    def _extract_single(self, completion: Any, idx: int, structured: bool) -> Any:
        if structured:
            return completion.choices[idx].message
        return completion.choices[idx].message.content

    def console_log(self, msg: str, end: str = "\n") -> None:
        if self.verbose > 0:
            if callable(self.console):
                self.console(msg, end=end)
            else:
                self.console.print(msg, end=end)

    def format_agent_response(self, prompt, schema, n_max_trials=5):
        n_trial = 0
        while n_trial < n_max_trials:
            raw = self.get_chat_response(user_prompt=prompt + f"\n{schema}")
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result
            raw = self.get_chat_response(
                user_prompt=f"Please fix the JSON format of the following response: {result}\n{schema}"
            )
            result = robust_jsonfy(raw)
            if "parsed" in result:
                return False, result["parsed"], raw, result
            n_trial += 1
        return True, None, raw, result
