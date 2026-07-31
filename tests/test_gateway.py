"""Test script for WOLF Gateway.

This script demonstrates how to start and test the WOLF gateway.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0,
                os.path.abspath(os.path.join(os.path.dirname(__file__), 
                                '../../..')
                               )
               )

from framework.pack.gateway import WolfGateway


def main():
    """Start the WOLF gateway."""
    
    print("[*] Starting WOLF Gateway...")
    print("[*] Gateway will start on http://127.0.0.1:8000")
    print("[*] WebSocket endpoint: ws://127.0.0.1:8000/ws/{session_id}")
    print("[*] Web UI will be available at: http://127.0.0.1:8000")
    print("\n[*] Agent configuration will be provided by clients upon connection")
    print("[*] Press Ctrl+C to stop\n")
    
    gateway = WolfGateway(
        host="0.0.0.0",
        port=8000
    )
    
    gateway.run()


if __name__ == "__main__":
    main()
