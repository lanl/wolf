#!/usr/bin/env python3
"""
Script to read variables from a .env file.
Uses python-dotenv to load environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


def load_env_vars(env_path: str):
    env_file = Path(env_path)
    if not env_file.exists():
        print(f".env file not found at {env_path}")
        return {}
    load_dotenv(dotenv_path=env_file)
    return {key: os.getenv(key) for key in os.environ}

if __name__ == "__main__":
    env_path = ".env"
    vars = load_env_vars(env_path)
    for k, v in vars.items():
        print(f"{k}={v}")