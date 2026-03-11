import os, copy, logging, pickle
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Dict
from framework.workflows.chat_data_models import ChatEntry
from framework.utils.io_tools import console, jsonfy, save_pickle_file, load_pickle_file
from framework.utils.tokenomics import (
    num_tokens_from_string,
    num_tokens_chat_entry,
    num_tokens_from_messages,
)


class BaseChatManager:
    """Handles chat history storage, retrieval, and timestamping.
    Does NOT handle trimming, pruning, summarization, or context-window management.
    Those responsibilities belong to a separate context manager.
    """

    def __init__(
        self,
        wf_log_dir: str = "wf_logs", #Optional[str] = None,
        session_dir: str = "./", #Optional[str] = None,
        chat_history_fname = "chat_history.pkl",
        chat_entries_fname = "chat_entries.pkl",
        chat_block_divider: str = "/" * 120,
        time_stamp_format: str = "%Y%m%d_%H%M%S",
        chat_header: Optional[List[Dict[str, Any]]] = None,
    ):
        # Support session_dir as primary, fall back to wf_log_dir for backwards compatibility
        self.session_dir = session_dir.strip().rstrip("/")
        self.log_dir = f"{self.session_dir}/{wf_log_dir.strip().rstrip('/')}"
        self.chat_history_fname = chat_history_fname.strip()
        self.chat_entries_fname = chat_entries_fname.strip()

        self.chat_block_divider = chat_block_divider
        self.time_stamp_format = time_stamp_format

        # Initialize chat containers
        self.CHAT_HISTORY: List[ChatEntry] = []
        self.CHAT_ENTRY = {}
        self.CHAT_HEAD_IDX = 0
        self.LAST_CHAT_ENTRY_IDX = 0
        self.LAST_COUNTED_ENTRY_IDX = 0
        self.CHAT_HISTORY_TOKEN_COUNT = 0

        # Setup default header if not provided
        if chat_header is None:
            chat_header = [
                {"sender": "system", "content": "<FILL CHAT_BLOCK_DIV />", "timestamp": "+"},
                {"sender": "system", "content": "--- Begining of Worflow (WF) Chat history ----", "timestamp": "<FILL TIME_STAMP />"}
            ]
        self.add_chat_entries(chat_header)

        # Logging setup
        os.makedirs(self.log_dir, exist_ok=True)
        timestamp = datetime.now().strftime(self.time_stamp_format)
        self.log = logging.getLogger(f"workflow_logger_{timestamp}")
        self.log.setLevel(logging.DEBUG)
        log_filename = os.path.join(self.log_dir, f"workflow_{timestamp}.log")
        fh = logging.FileHandler(log_filename)
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s', datefmt=self.time_stamp_format)
        fh.setFormatter(formatter)
        self.log.addHandler(fh)

    # ------ Helper / utility methods ------
    def get_timestamp(self, time_stamp_format: Optional[str] = None) -> str:
        """Get current timestamp formatted according to provided or stored format."""
        fmt = time_stamp_format if time_stamp_format is not None else self.time_stamp_format
        return datetime.now().strftime(fmt)

    def console_log(self, msg: str) -> None:
        """Log a message with appropriate level based on keywords."""
        MSG = msg.lower()
        if "[error]" in MSG:
            self.log.error(MSG)
        elif "[warn]" in MSG:
            self.log.warning(MSG)
        elif "[debug]" in MSG:
            self.log.debug(MSG)
        elif "[critk]" in MSG:
            self.log.critical(MSG)
        else:
            self.log.info(MSG)

    def replace_fillers(self, msg: str, filler_head: str = "<FILL", filler_tail: str = "/>") -> str:
        """Replace placeholder fillers with their actual values."""
        clean_entry = msg.strip()
        if not (clean_entry.startswith(filler_head) and clean_entry.endswith(filler_tail)):
            return clean_entry
        
        filler = clean_entry[len(filler_head):len(clean_entry) - len(filler_tail)].strip().lower()

        if filler in ["chat_block_div", "chat_block", "block_div"]:
            return self.chat_block_divider
        elif filler in ["time", "time_stamp", "timestamp"]:
            return self.get_timestamp()
        else:
            # Return original if no match
            return msg

    def add_chat_entries(self, entries: List[Dict[str, Any]] = None) -> None:
        """Add one or more chat entries, normalizing to ChatEntry objects."""
        if entries is None:
            return

        for raw_entry in entries:
            if isinstance(raw_entry, dict):
                entry = raw_entry
            elif isinstance(raw_entry, ChatEntry):
                entry = raw_entry.model_dump()
            else:
                raise NotImplementedError(
                    f"[!] add_chat_entries() is not implemented for type: {type(raw_entry)}"
                )

            # Normalize fields
            normalized_entry: Dict[str, Any] = {}
            for k, v in entry.items():
                if isinstance(v, str):
                    normalized_entry[k] = self.replace_fillers(v)
                else:
                    normalized_entry[k] = v

            # Ensure required fields exist with defaults
            sender = normalized_entry.get("sender", "system")
            content = normalized_entry.get("content", "")
            timestamp = normalized_entry.get("timestamp", self.get_timestamp())

            chat_entry = ChatEntry(
                sender=sender,
                content=content,
                timestamp=timestamp
            )

            self.CHAT_HISTORY.append(chat_entry)
            save_pickle_file(f"{self.session_dir}/{self.chat_history_fname}", self.CHAT_HISTORY)
            self.CHAT_ENTRY[self.LAST_CHAT_ENTRY_IDX] = copy.deepcopy(chat_entry)
            save_pickle_file(f"{self.session_dir}/{self.chat_entries_fname}", self.CHAT_ENTRY)
            self.CHAT_HISTORY_TOKEN_COUNT += num_tokens_chat_entry(chat_entry.model_dump())
            self.LAST_COUNTED_ENTRY_IDX += 1
            self.LAST_CHAT_ENTRY_IDX += 1
