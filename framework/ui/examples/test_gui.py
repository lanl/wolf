"""Test script for WOLF GUI Client.

This script demonstrates how to connect and test the GUI client.
"""

import sys
import os

# Add parent directory to path
#sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))

from framework.ui.gui_client import WolfGUIClient


def main():
    """Run the GUI client test."""
    print("[*] Starting WOLF GUI Client...")
    print("[*] Connecting to gateway at ws://127.0.0.1:8000")
    print("[*] Make sure the gateway is running first!\n")
    
    client = WolfGUIClient(
        gateway_url="ws://127.0.0.1:8000"
    )
    
    client.run()


if __name__ == "__main__":
    main()
