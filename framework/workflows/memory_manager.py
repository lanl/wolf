import os, copy, gc, json, asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from framework.utils.io_tools import console


class MemoryManager:
    """Manages structured and vector‑enhanced memory for a BaseWorkflow instance.

    Supports:
    - In‑memory key‑value storage (facts, preferences, task state)
    - Summarization and indexing of chat history
    - Traces vector store for raw chat entries (searchable by semantics)
    - Persistent storage (JSON) and optional main vector store (summaries)
    """

    def __init__(
        self,
        memory_path: Optional[str] = None,
        session_dir: Optional[str] = None,
        max_summary_tokens: int = 2000,
        max_ctx_tokens: int = 16000,
        memory_fragment_types: List[str] = ["user_prefs", "warnings", "strategies", "decisions", "conclusions", "solutions"],
        traces_vector_store: Any = None,
        summaries_vector_store: Any = None,
    ):
        # Support session_dir to derive memory_path for session isolation
        if session_dir:
            self.session_dir = session_dir
        else:
            self.session_dir = "./"
        if memory_path is None:
            self.memory_path = os.path.join(session_dir, "memory.json")
        else:
            self.memory_path = memory_path
        self.max_summary_tokens = max_summary_tokens
        self.max_ctx_tokens = max_ctx_tokens
        #self.facts: Dict[str, Any] = {}
        self.memory_fragment_types = memory_fragment_types
        self.memory_fragments : Dict[str, Any] = {}
        for  mem_frag_type in self.memory_fragment_types: 
            self.memory_fragments[mem_frag_type]= [] #Dict[str, Any] = {}
        #self.user_prefs: Dict[str, Any] = {}
        #self.task_state: Dict[str, Any] = {}
        #self.summaries: List[str] = []
        self._traces_vector_store = traces_vector_store
        self._summaries_vector_store = summaries_vector_store
        self._last_indexed_entry_idx = 0
        self._load()

    # ---------------------------------------------------------------------
    # Helper / Public API
    # ---------------------------------------------------------------------
    def set_traces_vector_store(self, traces_vs, verbose: int = 0):
        """Attach or update the traces vector store."""
        self._traces_vector_store = traces_vs
        if verbose > 0:
            console.print("[MEMORY] Traces vector store attached.")

    def set_summaries_vector_store(self, summaries_vs, verbose: int = 0):
        """Attach or update the summaries vector store."""
        self._summaries_vector_store = summaries_vs
        if verbose > 0:
            console.print("[MEMORY] Summaries vector store attached.")

    # ---------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------
    def _load(self, verbose: int = 0):
        if self.memory_path and os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.memory_fragment_types = data.get("memory_fragment_types", [])
                self.memory_fragmens       = data.get("memory_fragments", {})
                #self.facts.update(data.get("facts", {}))
                #self.user_prefs.update(data.get("user_prefs", {}))
                #self.task_state.update(data.get("task_state", {}))
                #self.summaries.extend(data.get("summaries", []))
                self._last_indexed_entry_idx = data.get("_last_indexed_entry_idx", 0)
                if verbose > 0:
                    console.print(f"[MEMORY] Loaded memory from {self.memory_path}")
            except Exception as e:
                console.print(f"[MEMORY] Failed to load memory: {e}")

    def _save(self, verbose: int = 0):
        """Write the in‑memory structures to *self.memory_path* safely.
        This replaces the previous ad‑hoc file write with an explicit UTF‑8
        encoding and atomic write via *Path.write_text*.
        """
        if not self.memory_path:
            return
        try:
            Path(self.memory_path).parent.mkdir(parents=True, exist_ok=True)
            data = {
                "memory_fragment_types": self.memory_fragment_types,
                "memory_fragments": self.memory_fragment_types,
                "_last_indexed_entry_idx": self._last_indexed_entry_idx,
            }
            # Use json.dumps to create a string and then write atomically.
            json_str = json.dumps(data, indent=2, ensure_ascii=False)
            Path(self.memory_path).write_text(json_str, encoding="utf-8")
        except Exception as e:
            console.print(f"[MEMORY] Failed to save memory: {e}")

    # ---------------------------------------------------------------------
    # Basic KV operations
    # ---------------------------------------------------------------------
    def remember(self, key: str, value: Any, category: str = "facts"):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types: self.memory_fragment_types.append(cat)
        self.memory_fragments[cat] = copy.deepcopy(value)
        self._save()
    def generate_memory_fragments(self, 
                                  chat_histrory, 
                                  agent,
                                  max_summary_workd_count = 100,
                                  #summarization_format = """```<FRAGMENTS> [{'type of memory fragmentt': "sumary"},....] <FRAGMENTS/>``` or ```[]```""",
                                  summarization_format = """```json [{'type of memory fragmentt': "sumary"},....] ```"""
                                  ):
        Agent_prompt = f""" You are a helpful assistant, and below is a snipet from a chat histrory: \n

        *** CHAT HISTORY START*** \n
          {chat_histrory} \n\n
        *** CHAT HISTORY END*** \n\n
        Your role is to help compact the chat history by generating memory fragments (summaries, facts, notes,...) from the provided snipet of chat history: 
        The following are the types of memory fragments already recorded about the full chat history:
        *** TYPES of MEMORY FRAGMENTS START ***\n
          {self.memory_fragment_types}\n
        *** TYPES of MEMORY FRAGMENTS END ***\n
        1. The benefit of memory fragments is to provide lossles sumaries (compressions) that can substitute the provided snipet (or parts of it) in the full chat hists, therefore,
        make sure to generate fragments ONLY when entries from the provided chat hist amount to a self-contained history/note/insight/remark/event...,
        unless you want to capture warnings, user preferences, subtle facts, something small, but REALY IMPORTANT to REMEMBER.\n 
        2. Avoid providing redundent memory fragments, and keep the fragment up to {max_summary_workd_count} words/fragment.\n
        3. If breaking down a summary into smaller, but related fragments, can help improving  the quality of compressions and satisfy the imposed word limit per fragment, do so.\n  
        Your response MUST STRICKTLY match the following format: {summarization_format}
        """
        # Obtain a response (structured or free‑form)
        if "structured_output" in getattr(agent, "capabilities", []):
            response = agent.get_structured_output(user_prompt=Agent_prompt, output_format=summarization_format)
            print(f"[!!!!] MEM GEN RESPONSE = {response}")
        else:
            bad, response, raw, result = agent.format_agent_response(Agent_prompt, summarization_format)
            if bad:
                # fallback to no sumaries
                #print(f"[ERROR][MEMORY][generate_memory_fragments]: Problem formatting Agent[{agent.name}]'s response:\n  {raw} ")
                response = []
        print(f"[MEMORY][Fragment Gen]:\n  {response}")
        self.format_fragment_response(response)

    def format_fragment_response(self, response):
        if isinstance(response, list):
            for fragment in response:
                self.format_fragment_response(fragment)
        elif isinstance(response, dict):
            Ks = response.keys()
            for fk in Ks:
                _fk = fk.strip().lower()
                if _fk not in self.memory_fragment_types:
                    self.memory_fragment_types.append(_fk)
                    self.memory_fragments[_fk] = []
                self.memory_fragments[_fk].append( copy.deepcopy(response[fk]) )
        else:
            raise Exception(f"[ERROR][MEMORY][generate_memory_fragments] Unable to format memory fragment{response}")

    def get_category(self, category: str) -> Any:
        """Return a deep‑copied view of the requested top‑level category.
        Supported categories: "facts", "user_prefs", "task_state", "summaries".
        """
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
        else:
            return copy.deepcopy(self.memory_fragments[cat])

    def recall(self, key: Optional[str] = None, category: str = "facts") -> Any:
        """Retrieve stored data.
        * If *key* is provided, return the value for that key within the given *category*.
        * If *key* is **None** and a *category* is supplied, return only that category's dict/list.
        * If both are omitted, return a compact snapshot containing all categories.
        """
        cat = category.strip().lower()
        if category is not None:
            cat = category.strip().lower()
            if cat in self.memory_fragment_types:
                if key is not None:
                    return self.memory_fragments[cat].get(key)
                else:
                    return self.memory_fragments[cat]
            else:
                raise ValueError(f"Unknown memory category: {category}")
        else:
            return self.memory_fragments

    def forget(self, key: str, category: str = "facts"):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
            #return
        else:
            if key in self.memory_fragments[cat]: 
                self.memory_fragments[cat].remove(key)
                gc.collect()
                self._save()
            else:
                raise ValueError(f"{key} not in Mem category: {category}")

    def clear(self, category: Optional[str] = None):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise ValueError(f"Unknown memory category: {category}")
        del self.memory_fragments[cat]
        gc.collect()
        self._save()

    # ----------------------------------------------------------
    # Chat‑history indexing and summarization
    # ----------------------------------------------------------
    def process_new_entries(self, new_entries: List[Dict[str, Any]], verbose: int = 0) -> None:
        """Index freshly added chat entries into the traces vector store.
        The method also updates the internal pointer used for incremental indexing.
        """
        if not new_entries:
            return

        if self._traces_vector_store:
            entries_text = [e.get("content", "") for e in new_entries]
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._traces_vector_store.add_documents(entries_text, pbar=None))
                else:
                    loop.run_until_complete(self._traces_vector_store.add_documents(entries_text, pbar=None))
                if verbose > 0:
                    console.print(f"[MEMORY] Indexed {len(new_entries)} chat entries to traces.")
            except RuntimeError:
                asyncio.run(self._traces_vector_store.add_documents(entries_text, pbar=None))
                if verbose > 0:
                    console.print(f"[MEMORY] Indexed {len(new_entries)} chat entries to traces (run).")
            self._last_indexed_entry_idx += len(new_entries)
            self._save()

    def summarize_recent_chat(self, lines: List[str], from_idx: int, to_idx: int, summarize_fn, verbose: int = 0):
        segment = "\n".join(lines[from_idx:to_idx])
        try:
            summary = summarize_fn(segment)
        except Exception as e:
            console.print(f"[MEMORY] Summarization failed: {e}")
            summary = "[Summary unavailable]"
        self.summaries.append(summary)
        self._save()
        if self._summaries_vector_store:
            self._index_summary_to_store(summary, verbose)

    def _index_summary_to_store(self, summary: str, verbose: int = 0):
        try:
            idx = len(self.summaries) - 1
            doc_id = f"summary_{idx}"
            source = f"workflow_memory/summary_{idx}"
            # The vector store API expects a list of documents.
            self._summaries_vector_store.add_documents([summary], pbar=None)
            if verbose > 0:
                console.print(f"[MEMORY] Indexed summary #{idx} to vstore.")
        except Exception as e:
            console.print(f"[MEMORY] Failed to index summary: {e}")

    # ----------------------------------------------------------
    # Semantic recall
    # ----------------------------------------------------------
    def semantic_recall(
        self,
        query: str,
        category: Optional[str] = None,
        n_results: int = 3,
        source: str = "traces",
        verbose: int = 0,
    ) -> List[Dict[str, Any]]:
        """Recall memory semantically via the specified vector store (traces or summaries)."""
        vs = self._traces_vector_store if source == "traces" else self._summaries_vector_store
        if vs is None:
            if verbose > 0:
                console.print(f"[MEMORY] No {source} vector store attached. Falling back to keyword recall.")
            return []
        full_query = query
        if category:
            full_query += f" {category}"
        try:
            results = vs.query(query=full_query, n_results=n_results)
            return results
        except Exception as e:
            console.print(f"[MEMORY] Semantic recall ({source}) failed: {e}")
            return []

    # ----------------------------------------------------------
    # Prompt contextualisation helper
    # ----------------------------------------------------------
    def contextualize(self, prompt: str) -> str:
        """Inject memory context into *prompt*.
        The method builds a human‑readable block containing facts, preferences,
        task state and any stored summaries.
        """
        memory_context = []
        if self.facts:
            memory_context.append("--- Facts ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.facts.items()])
        if self.user_prefs:
            memory_context.append("--- User Preferences ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.user_prefs.items()])
        if self.task_state:
            memory_context.append("--- Task State ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.task_state.items()])
        if self.summaries:
            memory_context.append("--- History Summaries ---")
            memory_context.extend(self.summaries)
        if memory_context:
            return prompt + "\n\n[MEMORY CONTEXT]\n" + "\n".join(memory_context)
        return prompt
    
