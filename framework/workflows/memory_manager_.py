import os, copy, gc, json, asyncio
from pathlib import path
from typing import any, dict, list, optional
from framework.utils.io_tools import console


class memorymanager:
    """manages structured and vector‑enhanced memory for a baseworkflow instance.

    supports:
    - in‑memory key‑value storage (facts, preferences, task state)
    - summarization and indexing of chat history
    - traces vector store for raw chat entries (searchable by semantics)
    - persistent storage (json) and optional main vector store (summaries)
    """

    def __init__(
        self,
        memory_path: optional[str] = none,
        session_dir: optional[str] = none,
        max_summary_tokens: int = 2000,
        max_ctx_tokens: int = 16000,
        memory_fragment_types: list[str] = ["user_prefs", "warnings", "strategies", "decisions", "conclusions", "solutions"],
        traces_vector_store: any = none,
        summaries_vector_store: any = none,
    ):
        # support session_dir to derive memory_path for session isolation
        if session_dir:
            self.session_dir = session_dir
        else:
            self.session_dir = "./"
        if memory_path is none:
            self.memory_path = os.path.join(session_dir, "memory.json")
        else:
            self.memory_path = memory_path
        self.max_summary_tokens = max_summary_tokens
        self.max_ctx_tokens = max_ctx_tokens
        #self.facts: dict[str, any] = {}
        self.memory_fragment_types = memory_fragment_types
        self.memory_fragments : dict[str, any] = {}
        for  mem_frag_type in self.memory_fragment_types:
            self.memory_fragments[mem_frag_type]= [] #dict[str, any] = {}
        #self.user_prefs: dict[str, any] = {}
        #self.task_state: dict[str, any] = {}
        #self.summaries: list[str] = []
        self._traces_vector_store = traces_vector_store
        self._summaries_vector_store = summaries_vector_store
        self._last_indexed_entry_idx = 0
        self._load()

    # ---------------------------------------------------------------------
    # helper / public api
    # ---------------------------------------------------------------------
    def set_traces_vector_store(self, traces_vs, verbose: int = 0):
        """attach or update the traces vector store."""
        self._traces_vector_store = traces_vs
        if verbose > 0:
            console.print("[memory] traces vector store attached.")

    def set_summaries_vector_store(self, summaries_vs, verbose: int = 0):
        """attach or update the summaries vector store."""
        self._summaries_vector_store = summaries_vs
        if verbose > 0:
            console.print("[memory] summaries vector store attached.")

    # ---------------------------------------------------------------------
    # persistence
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
                    console.print(f"[memory] loaded memory from {self.memory_path}")
            except exception as e:
                console.print(f"[memory] failed to load memory: {e}")

    def _save(self, verbose: int = 0):
        """write the in‑memory structures to *self.memory_path* safely.
        this replaces the previous ad‑hoc file write with an explicit utf‑8
        encoding and atomic write via *path.write_text*.
        """
        if not self.memory_path:
            return
        try:
            path(self.memory_path).parent.mkdir(parents=true, exist_ok=true)
            data = {
                "memory_fragment_types": self.memory_fragment_types,
                "memory_fragments": self.memory_fragment_types,
                "_last_indexed_entry_idx": self._last_indexed_entry_idx,
            }
            # use json.dumps to create a string and then write atomically.
            json_str = json.dumps(data, indent=2, ensure_ascii=false)
            path(self.memory_path).write_text(json_str, encoding="utf-8")
        except exception as e:
            console.print(f"[memory] failed to save memory: {e}")

    # ---------------------------------------------------------------------
    # basic kv operations
    # ---------------------------------------------------------------------
    def remember(self, key: str, value: any, category: str = "facts"):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types: self.memory_fragment_types.append(cat)
        self.memory_fragments[cat] = copy.deepcopy(value)
        self._save()
    def generate_memory_fragments(self,
                                  chat_histrory,
                                  agent,
                                  max_summary_workd_count = 100,
                                  #summarization_format = """```<fragments> [{'type of memory fragmentt': "sumary"},....] <fragments/>``` or ```[]```""",
                                  summarization_format = """```json [{'type of memory fragmentt': "sumary"},....] ```"""
                                  ):
        agent_prompt = f""" you are a helpful assistant, and below is a snipet from a chat histrory: \n

        *** chat history start*** \n
          {chat_histrory} \n\n
        *** chat history end*** \n\n
        your role is to help compact the chat history by generating memory fragments (summaries, facts, notes,...) from the provided snipet of chat history:
        the following are the types of memory fragments already recorded about the full chat history:
        *** types of memory fragments start ***\n
          {self.memory_fragment_types}\n
        *** types of memory fragments end ***\n
        1. the benefit of memory fragments is to provide lossles sumaries (compressions) that can substitute the provided snipet (or parts of it) in the full chat hists, therefore,
        make sure to generate fragments only when entries from the provided chat hist amount to a self-contained history/note/insight/remark/event...,
        unless you want to capture warnings, user preferences, subtle facts, something small, but realy important to remember.\n
        2. avoid providing redundent memory fragments, and keep the fragment up to {max_summary_workd_count} words/fragment.\n
        3. if breaking down a summary into smaller, but related fragments, can help improving  the quality of compressions and satisfy the imposed word limit per fragment, do so.\n
        your response must stricktly match the following format: {summarization_format}
        """
        # obtain a response (structured or free‑form)
        if "structured_output" in getattr(agent, "capabilities", []):
            response = agent.get_structured_output(user_prompt=agent_prompt, output_format=summarization_format)
            print(f"[!!!!] mem gen response = {response}")
        else:
            bad, response, raw, result = agent.format_agent_response(agent_prompt, summarization_format)
            if bad:
                # fallback to no sumaries
                #print(f"[error][memory][generate_memory_fragments]: problem formatting agent[{agent.name}]'s response:\n  {raw} ")
                response = []
        print(f"[memory][fragment gen]:\n  {response}")
        self.format_fragment_response(response)

    def format_fragment_response(self, response):
        if isinstance(response, list):
            for fragment in response:
                self.format_fragment_response(fragment)
        elif isinstance(response, dict):
            ks = response.keys()
            for fk in ks:
                _fk = fk.strip().lower()
                if _fk not in self.memory_fragment_types:
                    self.memory_fragment_types.append(_fk)
                    self.memory_fragments[_fk] = []
                self.memory_fragments[_fk].append( copy.deepcopy(response[fk]) )
        else:
            raise exception(f"[error][memory][generate_memory_fragments] unable to format memory fragment{response}")

    def get_category(self, category: str) -> any:
        """return a deep‑copied view of the requested top‑level category.
        supported categories: "facts", "user_prefs", "task_state", "summaries".
        """
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise valueerror(f"unknown memory category: {category}")
        else:
            return copy.deepcopy(self.memory_fragments[cat])

    def recall(self, key: optional[str] = none, category: str = "facts") -> any:
        """retrieve stored data.
        * if *key* is provided, return the value for that key within the given *category*.
        * if *key* is **none** and a *category* is supplied, return only that category's dict/list.
        * if both are omitted, return a compact snapshot containing all categories.
        """
        cat = category.strip().lower()
        if category is not none:
            cat = category.strip().lower()
            if cat in self.memory_fragment_types:
                if key is not none:
                    return self.memory_fragments[cat].get(key)
                else:
                    return self.memory_fragments[cat]
            else:
                raise valueerror(f"unknown memory category: {category}")
        else:
            return self.memory_fragments

    def forget(self, key: str, category: str = "facts"):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise valueerror(f"unknown memory category: {category}")
            #return
        else:
            if key in self.memory_fragments[cat]:
                self.memory_fragments[cat].remove(key)
                gc.collect()
                self._save()
            else:
                raise valueerror(f"{key} not in mem category: {category}")

    def clear(self, category: optional[str] = none):
        cat = category.strip().lower()
        if cat not in self.memory_fragment_types:
            raise valueerror(f"unknown memory category: {category}")
        del self.memory_fragments[cat]
        gc.collect()
        self._save()

    # ----------------------------------------------------------
    # chat‑history indexing and summarization
    # ----------------------------------------------------------
    def process_new_entries(self, new_entries: list[dict[str, any]], verbose: int = 0) -> none:
        """index freshly added chat entries into the traces vector store.
        the method also updates the internal pointer used for incremental indexing.
        """
        if not new_entries:
            return

        if self._traces_vector_store:
            entries_text = [e.get("content", "") for e in new_entries]
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._traces_vector_store.add_documents(entries_text, pbar=none))
                else:
                    loop.run_until_complete(self._traces_vector_store.add_documents(entries_text, pbar=none))
                if verbose > 0:
                    console.print(f"[memory] indexed {len(new_entries)} chat entries to traces.")
            except runtimeerror:
                asyncio.run(self._traces_vector_store.add_documents(entries_text, pbar=none))
                if verbose > 0:
                    console.print(f"[memory] indexed {len(new_entries)} chat entries to traces (run).")
            self._last_indexed_entry_idx += len(new_entries)
            self._save()

    def summarize_recent_chat(self, lines: list[str], from_idx: int, to_idx: int, summarize_fn, verbose: int = 0):
        segment = "\n".join(lines[from_idx:to_idx])
        try:
            summary = summarize_fn(segment)
        except exception as e:
            console.print(f"[memory] summarization failed: {e}")
            summary = "[summary unavailable]"
        self.summaries.append(summary)
        self._save()
        if self._summaries_vector_store:
            self._index_summary_to_store(summary, verbose)

    def _index_summary_to_store(self, summary: str, verbose: int = 0):
        try:
            idx = len(self.summaries) - 1
            doc_id = f"summary_{idx}"
            source = f"workflow_memory/summary_{idx}"
            # the vector store api expects a list of documents.
            self._summaries_vector_store.add_documents([summary], pbar=none)
            if verbose > 0:
                console.print(f"[memory] indexed summary #{idx} to vstore.")
        except exception as e:
            console.print(f"[memory] failed to index summary: {e}")

    # ----------------------------------------------------------
    # semantic recall
    # ----------------------------------------------------------
    def semantic_recall(
        self,
        query: str,
        category: optional[str] = none,
        n_results: int = 3,
        source: str = "traces",
        verbose: int = 0,
    ) -> list[dict[str, any]]:
        """recall memory semantically via the specified vector store (traces or summaries)."""
        vs = self._traces_vector_store if source == "traces" else self._summaries_vector_store
        if vs is none:
            if verbose > 0:
                console.print(f"[memory] no {source} vector store attached. falling back to keyword recall.")
            return []
        full_query = query
        if category:
            full_query += f" {category}"
        try:
            results = vs.query(query=full_query, n_results=n_results)
            return results
        except exception as e:
            console.print(f"[memory] semantic recall ({source}) failed: {e}")
            return []

    # ----------------------------------------------------------
    # prompt contextualisation helper
    # ----------------------------------------------------------
    def contextualize(self, prompt: str) -> str:
        """inject memory context into *prompt*.
        the method builds a human‑readable block containing facts, preferences,
        task state and any stored summaries.
        """
        memory_context = []
        if self.facts:
            memory_context.append("--- facts ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.facts.items()])
        if self.user_prefs:
            memory_context.append("--- user preferences ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.user_prefs.items()])
        if self.task_state:
            memory_context.append("--- task state ---")
            memory_context.extend([f"{k}: {v}" for k, v in self.task_state.items()])
        if self.summaries:
            memory_context.append("--- history summaries ---")
            memory_context.extend(self.summaries)
        if memory_context:
            return prompt + "\n\n[memory context]\n" + "\n".join(memory_context)
        return prompt


