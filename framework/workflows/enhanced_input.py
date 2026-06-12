from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from typing import Optional, Iterable
import os
import subprocess

# Initialize in-memory history for session persistence
_history = InMemoryHistory()

# Key bindings for enhanced UX
_kb = KeyBindings()

@_kb.add('c-c')
def _(event):
    """Handle Ctrl+C: send empty string to signal interrupt."""
    event.app.exit(result='')

@_kb.add('c-d')
def _(event):
    """Handle Ctrl+D: exit with None to signal EOF."""
    event.app.exit(result=None)

# Style for the prompt
_style = Style.from_dict({
    'prompt': 'ansiblue bold',
    'input': 'ansigreen',
})


class SmartCompleter(Completer):
    """Intelligent completer that handles different input modes."""
    
    def __init__(self, wf_commands=None):
        self.path_completer = PathCompleter(expanduser=True)
        self.wf_commands = wf_commands or []
        self.wf_completer = WordCompleter(self.wf_commands, ignore_case=True)
    
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        
        # Handle WOLF function completion (\>)
        if text.startswith('\\>'):
            # Extract the partial command after \>
            cmd_start = 2
            partial_cmd = text[cmd_start:]
            
            # Provide WOLF command completions
            for completion in self.wf_completer.get_completions(document, complete_event):
                yield completion
        
        # Handle terminal command completion (!>)
        elif text.startswith('!>'):
            # Extract the command after !>
            cmd_text = text[2:].lstrip()
            
            # Path completion for terminal commands
            # Create a modified document that only includes the path part
            words = cmd_text.split()
            if words:
                # Complete paths for command arguments
                for completion in self.path_completer.get_completions(document, complete_event):
                    yield completion
        
        # Handle @ interlocutor completion
        elif text.startswith('@'):
            # Could add interlocutor name completion here if we have access to WF_MEMBERS
            pass
        
        # Default: filesystem-aware completion for all inputs
        else:
            # Always provide path completion
            for completion in self.path_completer.get_completions(document, complete_event):
                yield completion


def interactive_input(
    prompt_text: str = "", 
    multiline: bool = False,
    wf_commands: Optional[list] = None,
    **kwargs
) -> Optional[str]:
    """Enhanced input replacement using prompt_toolkit with smart completion.
    
    Args:
        prompt_text: Prompt string to display (e.g., "[user]> ")
        multiline: Whether to allow multi-line input (using Shift+Enter to submit)
        wf_commands: List of WOLF command names for autocompletion
        **kwargs: Additional arguments passed to `prompt()`
    
    Returns:
        User input string, or None if EOF (Ctrl+D), or '' if interrupt (Ctrl+C).
    """
    prompt_str = prompt_text if prompt_text else "input> "
    
    # Create smart completer
    completer = SmartCompleter(wf_commands=wf_commands)
    
    try:
        if multiline:
            from prompt_toolkit.buffer import Buffer
            from prompt_toolkit.layout.containers import HSplit, Window
            from prompt_toolkit.layout.controls import BufferControl
            from prompt_toolkit.layout import Layout

            buffer = Buffer(
                multiline=True,
                history=_history,
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                complete_while_typing=True,
                accept_action=None
            )
            layout = Layout(HSplit([Window(content=BufferControl(buffer=buffer))]))
            session = PromptSession(
                layout=layout,
                key_bindings=_kb,
                style=_style,
                **kwargs
            )
        else:
            session = PromptSession(
                key_bindings=_kb,
                style=_style,
                history=_history,
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                complete_while_typing=True,
                **kwargs
            )
        result = session.prompt(prompt_str)
        return result
    except (KeyboardInterrupt, EOFError):
        return ''


def interactive_input_line_wrapped(
    prompt_text: str = "",
    wf_commands: Optional[list] = None,
    **kwargs
) -> Optional[str]:
    """Wrapper that ensures line wrapping respects terminal width with smart completion.
    
    Args:
        prompt_text: Prompt string
        wf_commands: List of WOLF command names for autocompletion
        **kwargs: Additional args to `interactive_input`
    
    Returns:
        User input string, or None on interrupt/EOF.
    """
    kwargs.setdefault('wrap_lines', True)
    return interactive_input(
        prompt_text=prompt_text, 
        multiline=False,
        wf_commands=wf_commands,
        **kwargs
    )
