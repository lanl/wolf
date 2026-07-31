"""Test script for WOLF TUI Client.

This script demonstrates how to connect and test the TUI client.
"""

import sys
import os

import asyncio
from framework.ui.tui_client import WolfTUIClient


async def main():
    """Run the TUI client test."""
    print("[*] Starting WOLF TUI Client...")
    print("[*] Connecting to gateway at http://127.0.0.1:8000")
    print("[*] Make sure the gateway is running first!\n")
    
    client = WolfTUIClient(
        gateway_url="http://127.0.0.1:8000"
    )
    
    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Exiting...")
