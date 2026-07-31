"""Robust JSON parsing utilities for agentic workflows.

This module provides resilient JSON extraction and parsing strategies to handle
imperfect LLM outputs in structured action payloads.
"""

import json
import re
from typing import Any, Dict, Optional
from framework.utils.io_tools import console, jsonfy

# Try to import json5 for lenient parsing
try:
    import json5 as json5
    JSON5_AVAILABLE = True
except ImportError:
    JSON5_AVAILABLE = False
    #console.print("[WARNING] json5 not available; falling back to strict json")


def extract_json_block(text: str) -> Optional[str]:
    """Extract JSON block from text using regex.

    Supports:
    - ```json ... ```
    - { ... } (nested, greedy from first { to last })
    """
    # Try to extract from code fence first
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Try to find a balanced { ... } block
    # This uses a counter-based approach
    start_idx = text.find('{')
    if start_idx == -1:
        return None

    depth = 0
    for i, char in enumerate(text[start_idx:], start_idx):
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start_idx:i+1].strip()

    return None


def robust_jsonfy(raw_text: str, max_retries: int = 3) -> Dict[str, Any]:
    """Parse raw text into JSON, using multiple fallback strategies.

    Strategy:
    1. Extract JSON block using extract_json_block()
    2. Try json.loads()
    3. If fails and json5 available, try json5.loads()
    4. If still fails, retry with simplified structure extraction (e.g., extract keys)

    Returns a dict with either 'parsed' or 'jsonify_error' key.
    """
    if not isinstance(raw_text, str):
        return jsonfy(raw_text)
        #return {"jsonify_error": "Input is not a string", "raw": raw_text}

    # Clean input
    raw = raw_text.strip()

    # Try extract_json_block first
    json_str = extract_json_block(raw)
    if json_str:
        raw = json_str

    for attempt in range(max_retries):
        try:
            # Try standard json first
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {"parsed": parsed}
            else:
                #print(f"[+++] BAD INPUT = {raw}")
                #return {"jsonify_error": f"Parsed JSON is not a dict, got {type(parsed).__name__}", "raw": raw}
                return jsonfy(raw)

        except json.JSONDecodeError as e:
            # Fallback to json5 if available
            if JSON5_AVAILABLE:
                try:
                    parsed = json5.loads(raw)
                    if isinstance(parsed, dict):
                        return {"parsed": parsed}
                except Exception:
                    pass

            # Fallback: try to reconstruct likely structure
            # E.g., look for key-value-like patterns
            if attempt == 0:
                # Simplify quotes and whitespace
                simplified = re.sub(r"\s+", " ", raw)
                simplified = re.sub(r'"([^"\{\}:,]*?)"', r"'\1'", simplified)
                simplified = re.sub(r"'([a-zA-Z0-9_]*?)'", r'"\1"', simplified)
                simplified = simplified.replace("'", '"')
                simplified = simplified.replace("True", "true").replace("False", "false").replace("None", "null")
                raw = simplified
            else:
                return {"jsonify_error": f"Failed to parse after {attempt} retries: {str(e)}", "raw": raw}

    return {"jsonify_error": "Failed to parse JSON after multiple strategies", "raw": raw}
