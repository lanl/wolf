import os, copy, logging, pickle, json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Dict
from framework.infrastructure.chat_data_models import ChatEntry
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

        # Ensure persistence directories exist before add_chat_entries() writes
        # initial chat history/header pickle files.
        os.makedirs(self.session_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

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
            self.log.error(msg)
        elif "[warn]" in MSG:
            self.log.warning(msg)
        elif "[debug]" in MSG:
            self.log.debug(msg)
        elif "[critk]" in MSG:
            self.log.critical(msg)
        else:
            self.log.info(msg)

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

    # ------ Snapshot and Restore methods ------
    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of the current chat manager state.
        
        Returns:
            Dict containing all state information needed to restore the instance.
        """
        snapshot_data = {
            "CHAT_HISTORY": [ entry if isinstance(entry, dict) else entry.model_dump() for entry in self.CHAT_HISTORY],
            "CHAT_ENTRY": {k: (v if isinstance(v, dict) else v.model_dump()) for k, v in self.CHAT_ENTRY.items()},
            "CHAT_HEAD_IDX": self.CHAT_HEAD_IDX,
            "LAST_CHAT_ENTRY_IDX": self.LAST_CHAT_ENTRY_IDX,
            "LAST_COUNTED_ENTRY_IDX": self.LAST_COUNTED_ENTRY_IDX,
            "CHAT_HISTORY_TOKEN_COUNT": self.CHAT_HISTORY_TOKEN_COUNT,
            "timestamp": self.get_timestamp(),
        }
        return snapshot_data

    def restore(self, snapshot_data: Dict[str, Any], verbose=0) -> None:
        """Restore the chat manager state from a snapshot.
        
        Args:
            snapshot_data: Dictionary containing state information from a previous snapshot.
        """
        # Restore CHAT_HISTORY.  Preserve dict metadata such as action,
        # history_index, and entry_id; converting to ChatEntry is lossy because
        # ChatEntry only models sender/timestamp/content.
        self.CHAT_HISTORY = []
        for pos, entry in enumerate(snapshot_data.get("CHAT_HISTORY", [])):
            if isinstance(entry, dict):
                restored = copy.deepcopy(entry)
                restored.setdefault("sender", "system")
                restored.setdefault("content", "")
                restored.setdefault("timestamp", self.get_timestamp())
                if not isinstance(restored.get("history_index"), int):
                    restored["history_index"] = pos
                self.CHAT_HISTORY.append(restored)
            elif isinstance(entry, ChatEntry):
                restored = entry.model_dump()
                restored["history_index"] = pos
                self.CHAT_HISTORY.append(restored)
            else:
                restored = {"sender": "system", "content": str(entry), "timestamp": self.get_timestamp(), "history_index": pos}
                self.CHAT_HISTORY.append(restored)

        # Restore CHAT_ENTRY with the same metadata-preserving behavior.
        self.CHAT_ENTRY = {}
        for raw_key, raw_value in snapshot_data.get("CHAT_ENTRY", {}).items():
            try:
                key = int(raw_key)
            except Exception:
                key = raw_key
            if isinstance(raw_value, dict):
                restored = copy.deepcopy(raw_value)
                restored.setdefault("sender", "system")
                restored.setdefault("content", "")
                restored.setdefault("timestamp", self.get_timestamp())
                if isinstance(key, int) and not isinstance(restored.get("history_index"), int):
                    restored["history_index"] = key
                self.CHAT_ENTRY[key] = restored
            elif isinstance(raw_value, ChatEntry):
                restored = raw_value.model_dump()
                if isinstance(key, int):
                    restored["history_index"] = key
                self.CHAT_ENTRY[key] = restored
            else:
                self.CHAT_ENTRY[key] = raw_value
        
        # Restore indexes and counts.  Ensure append paths continue after the
        # restored history rather than reusing stale/null index values.
        self.CHAT_HEAD_IDX = snapshot_data.get("CHAT_HEAD_IDX", 0)
        self.LAST_CHAT_ENTRY_IDX = max(int(snapshot_data.get("LAST_CHAT_ENTRY_IDX", 0) or 0), len(self.CHAT_HISTORY))
        self.LAST_COUNTED_ENTRY_IDX = max(int(snapshot_data.get("LAST_COUNTED_ENTRY_IDX", 0) or 0), len(self.CHAT_HISTORY))
        self.CHAT_HISTORY_TOKEN_COUNT = snapshot_data.get("CHAT_HISTORY_TOKEN_COUNT", 0)
        
        # Save restored state to disk for persistence
        save_pickle_file(f"{self.session_dir}/{self.chat_history_fname}", self.CHAT_HISTORY)
        save_pickle_file(f"{self.session_dir}/{self.chat_entries_fname}", self.CHAT_ENTRY)

    def save_snapshot(self, file_path: str) -> None:
        """Save a snapshot to disk.
        
        Args:
            file_path: Path where the snapshot should be saved.
        """
        snapshot_data = self.snapshot()
        save_pickle_file(file_path, snapshot_data)
        self.console_log(f"[INFO] Snapshot saved to {file_path}")

    def load_snapshot(self, file_path: str) -> None:
        """Load and restore from a snapshot file.
        
        Args:
            file_path: Path to the snapshot file to load.
        """
        snapshot_data = load_pickle_file(file_path)
        if snapshot_data is not None:
            self.restore(snapshot_data)
            self.console_log(f"[INFO] Snapshot loaded from {file_path}")
        else:
            self.console_log(f"[ERROR] Failed to load snapshot from {file_path}")
