from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from typing import Optional

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

def interactive_input(
    prompt_text: str = "", 
    multiline: bool = False, 
    **kwargs
) -> Optional[str]:
    """
    Replacement for `input()` using prompt_toolkit.
    
    Args:
        prompt_text: Prompt string to display (e.g., "[user]> ")
        multiline: Whether to allow multi-line input (using Shift+Enter to submit)
        **kwargs: Additional arguments passed to `prompt()`
    
    Returns:
        User input string, or None if EOF (Ctrl+D), or '' if interrupt (Ctrl+C).
    """
    prompt_str = prompt_text if prompt_text else "input> "
    
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
                accept_action=None
            )
            layout = Layout(HSplit([Window(content=BufferControl(buffer=buffer))]))
            session = PromptSession(
                layout=layout,
                key_bindings=_kb,
                style=_style,
                complete_while_typing=False,
                **kwargs
            )
        else:
            session = PromptSession(
                key_bindings=_kb,
                style=_style,
                history=_history,
                auto_suggest=AutoSuggestFromHistory(),
                complete_while_typing=False,
                **kwargs
            )
        result = session.prompt(prompt_str)
        return result
    except (KeyboardInterrupt, EOFError):
        return ''

def interactive_input_line_wrapped(
    prompt_text: str = "",
    **kwargs
) -> Optional[str]:
    """
    Wrapper that ensures line wrapping respects terminal width.
    
    Args:
        prompt_text: Prompt string
        **kwargs: Additional args to `interactive_input`
    
    Returns:
        User input string, or None on interrupt/EOF.
    """
    kwargs.setdefault('wrap_lines', True)
    return interactive_input(prompt_text=prompt_text, multiline=False, **kwargs)
