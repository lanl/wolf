from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from prompt_toolkit.document import Document
from typing import Optional, Iterable, Tuple
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
    """Intelligent completer that handles commands and paths anywhere.

    ``PathCompleter`` works well when the whole prompt buffer is a path, but
    passing the full sentence means mid-field paths such as ``read ./REA`` are
    not interpreted as path fragments.  This completer extracts only the token
    immediately before the cursor and runs ``PathCompleter`` against that small
    document, while preserving prompt_toolkit's replacement offsets.
    """

    _PATH_PREFIXES = ("./", "../", "/", "~/", ".", "~")

    def __init__(self, wf_commands=None):
        self.path_completer = PathCompleter(expanduser=True)
        self.wf_commands = wf_commands or []
        self.wf_completer = WordCompleter(self.wf_commands, ignore_case=True)

    @staticmethod
    def _token_before_cursor(text: str) -> Tuple[str, int]:
        """Return the shell-ish token ending at the cursor and its start index."""
        if not text:
            return "", 0

        quote = None
        start = len(text)
        for i in range(len(text) - 1, -1, -1):
            ch = text[i]
            if quote:
                if ch == quote:
                    start = i + 1
                    break
                continue
            if ch in ('"', "'"):
                quote = ch
                continue
            if ch.isspace():
                start = i + 1
                break
            start = i
        return text[start:], start

    @classmethod
    def _looks_like_path_token(cls, token: str) -> bool:
        if not token:
            return False
        if token.startswith(cls._PATH_PREFIXES):
            return True
        # Complete nested relative paths once the user has typed a slash, e.g.
        # ``config/pre`` or ``framework/workflows/en``.
        return "/" in token or "\\" in token

    def _yield_path_completions_for_token(self, token: str, complete_event):
        token_doc = Document(token, cursor_position=len(token))
        yield from self.path_completer.get_completions(token_doc, complete_event)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        token, token_start = self._token_before_cursor(text)

        # Handle WOLF function completion (\>) at the beginning of a command.
        if text.startswith('\\>'):
            cmd_text = text[2:]
            cmd_token, _ = self._token_before_cursor(cmd_text)
            # Command word completion before the first argument.
            if len(cmd_text.strip().split()) <= 1 and not cmd_text.endswith(' '):
                cmd_doc = Document(cmd_token, cursor_position=len(cmd_token))
                yield from self.wf_completer.get_completions(cmd_doc, complete_event)
                return
            # Path completion for file=..., prompt files, or other args.
            if "=" in token:
                key, value = token.split("=", 1)
                if self._looks_like_path_token(value):
                    for c in self._yield_path_completions_for_token(value, complete_event):
                        yield Completion(f"{key}={c.text}", start_position=-(len(key) + 1 + len(value)), display=c.display, display_meta=c.display_meta)
                    return
            if self._looks_like_path_token(token):
                yield from self._yield_path_completions_for_token(token, complete_event)
            return

        # Handle terminal command completion (!>) and regular text: complete
        # the current token if it looks like a path, regardless of field index.
        if text.startswith('!>'):
            if self._looks_like_path_token(token):
                yield from self._yield_path_completions_for_token(token, complete_event)
            return

        # Handle @ interlocutor completion.  The current implementation does not
        # receive WF_MEMBERS, but keep this branch explicit for future extension.
        if text.startswith('@') and len(text.split()) <= 1:
            return

        if self._looks_like_path_token(token):
            yield from self._yield_path_completions_for_token(token, complete_event)


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
