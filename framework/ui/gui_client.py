"""Graphical User Interface (GUI) Client for WOLF Agent Gateway.

A tkinter-based GUI client that connects to the WOLF gateway via WebSocket
for interactive agent communication.
"""

import asyncio
import uuid
import threading
import json
from datetime import datetime
from typing import Optional
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import sys

try:
    import websockets
except ImportError:
    print("Error: websockets package not installed. Install with: pip install websockets")
    sys.exit(1)


class WolfGUIClient:
    """Graphical User Interface client for WOLF agent interaction."""
    
    def __init__(
        self,
        gateway_url: str = "ws://127.0.0.1:8000",
        session_id: Optional[str] = None
    ):
        self.gateway_url = gateway_url
        self.session_id = session_id or str(uuid.uuid4())
        self.websocket = None
        self.connected = False
        self.agent_name = "WOLF Agent"
        self.streaming_mode = False
        
        # Setup GUI
        self.root = tk.Tk()
        self.root.title("WOLF Agent Interface")
        self.root.geometry("800x600")
        
        self._setup_ui()
        self._setup_styles()
        
        # Async loop for WebSocket
        self.loop = None
        self.ws_thread = None
        
    def _setup_styles(self):
        """Configure UI styles."""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Configure colors
        style.configure('Connected.TLabel', foreground='green')
        style.configure('Disconnected.TLabel', foreground='red')
        style.configure('Send.TButton', background='#4CAF50', foreground='white')
        
    def _setup_ui(self):
        """Setup the GUI components."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # Header frame
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        header_frame.columnconfigure(1, weight=1)
        
        # Title
        title_label = ttk.Label(
            header_frame,
            text="WOLF Agent Terminal",
            font=('Helvetica', 16, 'bold')
        )
        title_label.grid(row=0, column=0, sticky=tk.W)
        
        # Connection status
        self.status_label = ttk.Label(
            header_frame,
            text="● Disconnected",
            style='Disconnected.TLabel'
        )
        self.status_label.grid(row=0, column=1, sticky=tk.E)
        
        # Session info
        session_label = ttk.Label(
            header_frame,
            text=f"Session: {self.session_id[:8]}...",
            font=('Helvetica', 9)
        )
        session_label.grid(row=1, column=0, columnspan=2, sticky=tk.W)
        
        # Chat display area
        chat_frame = ttk.LabelFrame(main_frame, text="Conversation", padding="5")
        chat_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        chat_frame.columnconfigure(0, weight=1)
        chat_frame.rowconfigure(0, weight=1)
        
        self.chat_display = scrolledtext.ScrolledText(
            chat_frame,
            wrap=tk.WORD,
            width=80,
            height=20,
            font=('Courier', 10),
            state=tk.DISABLED
        )
        self.chat_display.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure text tags for styling
        self.chat_display.tag_configure('system', foreground='#2196F3', font=('Courier', 10, 'italic'))
        self.chat_display.tag_configure('user', foreground='#4CAF50', font=('Courier', 10, 'bold'))
        self.chat_display.tag_configure('agent', foreground='#FF9800', font=('Courier', 10))
        self.chat_display.tag_configure('error', foreground='#F44336', font=('Courier', 10, 'bold'))
        self.chat_display.tag_configure('timestamp', foreground='#9E9E9E', font=('Courier', 8))
        
        # Input frame
        input_frame = ttk.Frame(main_frame)
        input_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))
        input_frame.columnconfigure(0, weight=1)
        
        # Input field
        self.input_field = ttk.Entry(input_frame, font=('Helvetica', 11))
        self.input_field.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        self.input_field.bind('<Return>', lambda e: self.send_message())
        
        # Send button
        self.send_button = ttk.Button(
            input_frame,
            text="Send",
            command=self.send_message,
            style='Send.TButton'
        )
        self.send_button.grid(row=0, column=1)
        
        # Control frame
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        
        # Connect button
        self.connect_button = ttk.Button(
            control_frame,
            text="Connect",
            command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=0, padx=(0, 5))
        
        # Reset button
        reset_button = ttk.Button(
            control_frame,
            text="Reset Context",
            command=self.reset_context
        )
        reset_button.grid(row=0, column=1, padx=(0, 5))
        
        # Stream mode checkbox
        self.stream_var = tk.BooleanVar()
        stream_check = ttk.Checkbutton(
            control_frame,
            text="Streaming Mode",
            variable=self.stream_var,
            command=self.toggle_streaming
        )
        stream_check.grid(row=0, column=2, padx=(0, 5))
        
        # Clear button
        clear_button = ttk.Button(
            control_frame,
            text="Clear Chat",
            command=self.clear_chat
        )
        clear_button.grid(row=0, column=3)
        
    def append_message(self, message: str, tag: str = 'system', show_timestamp: bool = True):
        """Append a message to the chat display."""
        self.chat_display.config(state=tk.NORMAL)
        
        if show_timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.chat_display.insert(tk.END, f"[{timestamp}] ", 'timestamp')
        
        self.chat_display.insert(tk.END, message + "\n\n", tag)
        self.chat_display.see(tk.END)
        self.chat_display.config(state=tk.DISABLED)
        
    def clear_chat(self):
        """Clear the chat display."""
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete(1.0, tk.END)
        self.chat_display.config(state=tk.DISABLED)
        
    def toggle_streaming(self):
        """Toggle streaming mode."""
        self.streaming_mode = self.stream_var.get()
        mode = "enabled" if self.streaming_mode else "disabled"
        self.append_message(f"Streaming mode {mode}", 'system')
        
    def reset_context(self):
        """Reset the agent context."""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to the gateway first.")
            return
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_message("", "reset"),
                self.loop
            )
        
    def toggle_connection(self):
        """Toggle connection to the gateway."""
        if self.connected:
            self.disconnect()
        else:
            self.connect()
        
    def connect(self):
        """Establish connection to the gateway."""
        self.ws_thread = threading.Thread(target=self._run_websocket_loop, daemon=True)
        self.ws_thread.start()
        
    def disconnect(self):
        """Disconnect from the gateway."""
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
        
    def _run_websocket_loop(self):
        """Run the WebSocket event loop in a separate thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connect_and_listen())
        
    async def _connect_and_listen(self):
        """Connect to gateway and listen for messages."""
        try:
            ws_url = f"{self.gateway_url}/ws/{self.session_id}?client_type=gui"
            async with websockets.connect(ws_url) as websocket:
                self.websocket = websocket
                self.connected = True
                
                # Update UI
                self.root.after(0, self._update_connection_status, True)
                
                # Listen for messages
                async for message in websocket:
                    data = json.loads(message)
                    self.root.after(0, self._handle_message, data)
                    
        except Exception as e:
            self.root.after(0, self.append_message, f"Connection error: {e}", 'error')
        finally:
            self.connected = False
            self.root.after(0, self._update_connection_status, False)
            
    async def _disconnect(self):
        """Close WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            
    def _update_connection_status(self, connected: bool):
        """Update connection status in UI."""
        if connected:
            self.status_label.config(text="● Connected", style='Connected.TLabel')
            self.connect_button.config(text="Disconnect")
            self.append_message("Connected to WOLF Gateway", 'system')
        else:
            self.status_label.config(text="● Disconnected", style='Disconnected.TLabel')
            self.connect_button.config(text="Connect")
            self.append_message("Disconnected from gateway", 'system')
            
    def _handle_message(self, data: dict):
        """Handle incoming messages from the gateway."""
        msg_type = data.get("type", "unknown")
        content = data.get("content", "")
        
        if msg_type == "system":
            self.append_message(f"[SYSTEM] {content}", 'system')
            if "Connected to WOLF agent:" in content:
                self.agent_name = content.split(":")[-1].strip()
                
        elif msg_type == "user_echo":
            self.append_message(f"You: {content}", 'user')
            
        elif msg_type == "agent_response":
            agent_name = data.get("agent_name", self.agent_name)
            self.append_message(f"{agent_name}: {content}", 'agent')
            
        elif msg_type == "stream_start":
            self.append_message(f"{self.agent_name} is typing...", 'system')
            
        elif msg_type == "stream_complete":
            agent_name = data.get("agent_name", self.agent_name)
            self.append_message(f"{agent_name}: {content}", 'agent')
            
        elif msg_type == "error":
            self.append_message(f"[ERROR] {content}", 'error')
            
    def send_message(self):
        """Send a message to the gateway."""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to the gateway first.")
            return
        
        message = self.input_field.get().strip()
        if not message:
            return
        
        self.input_field.delete(0, tk.END)
        
        message_type = "stream" if self.streaming_mode else "chat"
        
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_message(message, message_type),
                self.loop
            )
            
    async def _send_message(self, content: str, message_type: str = "chat"):
        """Send message via WebSocket."""
        if not self.websocket:
            return
        
        message = {
            "type": message_type,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id
        }
        
        await self.websocket.send(json.dumps(message))
        
    def run(self):
        """Start the GUI application."""
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()
        
    def on_closing(self):
        """Handle window closing."""
        if self.connected:
            self.disconnect()
        self.root.destroy()


def main():
    """Entry point for the GUI client."""
    import argparse
    
    parser = argparse.ArgumentParser(description="WOLF Agent GUI Client")
    parser.add_argument(
        "--gateway",
        default="ws://127.0.0.1:8000",
        help="Gateway WebSocket URL (default: ws://127.0.0.1:8000)"
    )
    parser.add_argument(
        "--session-id",
        help="Session ID (auto-generated if not provided)"
    )
    
    args = parser.parse_args()
    
    client = WolfGUIClient(
        gateway_url=args.gateway,
        session_id=args.session_id
    )
    
    client.run()


if __name__ == "__main__":
    main()
