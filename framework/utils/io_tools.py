import os
from dotenv import load_dotenv
import re, copy, subprocess, pickle, ast, json
from PIL import Image

import base64
from io import BytesIO

from pathlib import Path
from typing import List, Dict, Any, Union, Tuple, Optional
from PIL import Image
from searxng_wrapper import SearxngWrapper as searxng
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.console import Console
console = Console()

CONTAINER_HANDLES = ["```json", "```python", "```js", "```html", "```java",]

# ------------------------------------------------------------------
# Helper I/O functions – replace these with your real implementations
# ------------------------------------------------------------------

def load_env_vars(env_path=".env"):
    env_file = Path(env_path)
    if not env_file.exists():
        print(f".env file not found at {env_path}")
        return {}
    load_dotenv(dotenv_path=env_file)
    return {key: os.getenv(key) for key in os.environ}

def _resolve_path(file_path: Union[str, Path]) -> Path:
    """
    Resolve a file path to an absolute Path object.

    If *file_path* is relative, it is resolved relative to the current working
    directory.  Leading ``~`` is expanded to the user's home directory.
    """
    return Path(file_path).expanduser().resolve()

def read_file(file_path: Union[str, Path],
                   *,
                   encoding: str = "utf-8",
                   binary: bool = False,
                   chunk_size: int = 8192,
                   encoding_errors: str = "strict"):
    """
    Read the contents of *file_path*.

    Parameters
    ----------
    file_path
        Path to the file, can be relative or absolute.
    encoding
        Encoding used for text mode. Ignored when *binary* is True.
    binary
        If True, the file is opened in binary mode and the raw bytes are
        returned.  Otherwise a :class:`str` is returned.
    chunk_size
        When reading text mode, the file is read in chunks of this size to
        avoid loading very large files into memory all at once.
    encoding_errors
        Keyword argument passed to :meth:`str.decode` / ``open(..., errors=…)``.
    """
    path = _resolve_path(file_path)
    mode = "rb" if binary else "r"
    try:
        with path.open(mode, encoding=encoding if not binary else None, errors=encoding_errors) as f:
            if binary:
                return f.read()
            else:
                content = []
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    content.append(chunk)
                return "".join(content)
    except OSError as exc:
        raise OSError(f"Failed to read from {path!s}: {exc}") from exc

def write_file(file_path: Union[str, Path],
                    content: Union[str, bytes],
                    *,
                    encoding: str = "utf-8",
                    append: bool = False,
                    binary: bool = False,
                    encoding_errors: str = "strict"):
    """
    Write *content* to *file_path*.

    Parameters
    ----------
    file_path
        Target path.  If the parent directories do not exist they are
        created automatically.
    content
        String or bytes.  If *binary* is True, *content* must be :class:`bytes`.
    encoding
        Encoding to use when writing text. Ignored if *binary* is True.
    append
        If True, the file is opened in append mode; otherwise it is truncated.
    binary
        When True the file is opened in binary mode.  In this mode *content*
        must be ``bytes``.  When False, *content* must be ``str``.
    encoding_errors
        Keyword argument passed to ``open(..., errors=…)``.
    """
    path = _resolve_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ab" if binary and append else ("wb" if binary else ("a" if append else "w"))
    try:
        with path.open(mode, encoding=encoding if not binary else None, errors=encoding_errors) as f:
            if binary:
                if not isinstance(content, (bytes, bytearray)):
                    raise TypeError("content must be bytes-like for binary mode")
                f.write(content)
            else:
                if not isinstance(content, str):
                    raise TypeError("content must be str for text mode")
                f.write(content)
    except OSError as exc:
        raise OSError(f"Failed to write to {path!s}: {exc}") from exc


def run_syscall(command: Union[str, List[str]], timeout: int = 30, shell: bool = False) -> Tuple[str, str, int]:
    """
    Run a system command and return its stdout, stderr, and exit code.

    Args:
        command (str | list[str]): Command to execute.
        timeout (int): Timeout in seconds.
        shell (bool): Whether to run through the shell.

    Returns:
        tuple[str, str, int]: (stdout, stderr, returncode)
    """
    print(f"[run_syscall()]: command={command,}")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=shell,
        check=False,)
    return {"stdout":result.stdout, "stderr":result.stderr, "returncode":result.returncode}


def run_syscall(command: Union[str, List[str]], timeout: int = 30, shell: bool = False) -> Tuple[str, str, int]:
    """
    Run a system command and return its stdout, stderr, and exit code.
    If the command fails due to formatting (e.g., string without shell=True),
    it will retry automatically.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            check=False,
        )
        return {"stdout":result.stdout, "stderr":result.stderr, "returncode":result.returncode}

    except FileNotFoundError as e:
        # If command is a string and wasn't run in a shell, retry with shell=True
        if isinstance(command, str) and not shell:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=True,   # <-- fallback
                    check=False,
                )
                return {"stdout":result.stdout, "stderr":result.stderr, "returncode":result.returncode}
            except Exception as inner_e:
                return "", f"Retry with shell=True failed: {inner_e}", 127
        return "", str(e), 127

    except subprocess.TimeoutExpired as e:
        return e.stdout or "", f"TimeoutExpired: {e}", -1

    except Exception as e:
        return "", f"Unexpected error: {e}", -1

def extract_functions_from_file(file_path: str) -> List[Dict[str, Any]]:
    """
    Extracts Python functions from a file and returns them as a list of dictionaries.
    
    Each dictionary contains:
    - 'name_func': function name
    - 'description': docstring or first comment line
    - 'args': list of argument names
    - 'body': function body as string
    - 'return': return statement or None
    
    Args:
        file_path (str): Path to the Python file
        
    Returns: 
        List[Dict[str, Any]]: List of dictionaries containing function information
    """

    # Read the entire file content
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # Regular expression pattern to match function definitions
    # This pattern looks for def statements and captures the whole function
    func_pattern = re.compile(
        r'def\s+(\w+)\s*\((.*?)\):(?:\s+"""(.*?)""")?\s*(.*?)(?=\ndef|\Z)',
        re.DOTALL | re.MULTILINE
    )
    
    functions = []

    # Find all function definitions
    for match in func_pattern.finditer(content):
        name_func = match.group(1)
        args_str = match.group(2).strip() if match.group(2) else ""
        description = match.group(3).strip() if match.group(3) else ""
        body = match.group(4).strip()
        
        # Parse arguments
        args = parse_arguments(args_str)
        
        # Extract return statement from body
        return_stmt = extract_return_statement(body)

        functions.append({
            'name_func': name_func,
            'description': description,
            'args': args,
            'body': body,
            'return': return_stmt
        })
    
    return functions

def parse_arguments(args_str: str) -> List[str]:
    """
    Parse argument string into a list of argument names.
    
    Args:
        args_str (str): String containing function arguments
        
    Returns:
        List[str]: List of argument names
    """
    if not args_str.strip():
        return []
    
    # Split by comma and clean up whitespace
    args = [arg.strip().split('=')[0].strip() for arg in args_str.split(',')]
    # Remove empty strings
    return [arg for arg in args if arg]

def extract_return_statement(body: str) -> str:
    """ 
    Extract the return statement from function body.
    
    Args:
        body (str): Function body as string
        
    Returns:
        str: Return statement or None if no return found
    """
    # Split body into lines and look for return statements
    lines = body.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('return '):
            return line[7:]  # Remove 'return ' prefix
    
    return None


# Alternative implementation that handles nested functions better:
def extract_functions_from_file_advanced(file_path: str) -> List[Dict[str, Any]]:
    """
    Advanced version that handles more complex function definitions.
    
    Args:
        file_path (str): Path to the Python file
        
    Returns:
        List[Dict[str, Any]]: List of dictionaries containing function information
    """
    
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # For a more robust solution, we'll use a simple parsing approach
    functions = []
    
    # Split content by function definitions (def statements)
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for function definition
        if line.startswith('def ') and ':' in line:
            # Extract function name
            func_def = line[4:].split('(')
            name_func = func_def[0]

            # Find the beginning of the function body
            j = i + 1

            # Skip empty lines that might come after def line
            while j < len(lines) and not lines[j].strip() and not lines[j].startswith('def '):
                j += 1

            # Collect function body until next function definition or end of file
            func_lines = []

            # Keep track of indentation to determine when function ends
            current_indentation = None

            while j < len(lines):
                current_line = lines[j]
                stripped_line = current_line.lstrip()

                # Check if it's a new function definition at same or higher indentation level
                if (stripped_line.startswith('def ') and
                    (current_indentation is None or
                     len(current_line) - len(stripped_line) <= current_indentation)):
                    break

                func_lines.append(current_line)
                j += 1

                # Set base indentation from first non-empty line
                if current_indentation is None and stripped_line:
                    current_indentation = len(current_line) - len(stripped_line)

            # Join lines to form body
            body_text = '\n'.join(func_lines).strip()

            # Extract docstring (if exists)
            description = ""
            body_lines = func_lines.copy()

            # Look for first string literal in body
            for idx, line in enumerate(body_lines):
                stripped_line = line.strip()
                if stripped_line.startswith('"""') or stripped_line.startswith("'''"):
                    # Extract docstring
                    description = stripped_line[3:-3].strip() if stripped_line.startswith('"""') else stripped_line[3:-3].strip()
                    break

            # Extract args from def line
            args_match = re.search(r'def\s+\w+\s*\((.*?)\)', line)
            args_str = args_match.group(1) if args_match and args_match.group(1) else ""
            args = parse_arguments(args_str)

            # Try to extract return statement
            return_stmt = extract_return_statement(body_text)

            functions.append({
                'name_func': name_func,
                'description': description,
                'args': args,
                'body': body_text,
                'return': return_stmt
            })

        i += 1

    return functions

# Convenience function for better usage:
def extract_functions(file_path: str) -> List[Dict[str, Any]]:
    """
    Extracts Python functions from a file.
    
    Args:
        file_path (str): Path to the Python file
        
    Returns:
        List[Dict[str, Any]]: List of dictionaries containing function information
    """
    return extract_functions_from_file_advanced(file_path)



def clone_dict(template_dict:dict, replace: dict| None = None) -> dict:
    clone = copy.deepcopy(template_dict)
    if replace is not None:
        ks = replace.keys()
        for k in ks: clone[k] = replace[k]
    return clone

def clean_str_container(msg: str, head_handle:str, tail_handle="```"):
    new_msg = None
    if msg.startswith(head_handle):
        new_msg = msg.lstrip(head_handle).rstrip(tail_handle)
    if new_msg is None:
        return msg
    else:
        return new_msg

def expand_dict(dct: dict, dept: int = 0, stride: int = 2) -> str:
    nblanks = dept * stride
    tab = " " * nblanks
    message = ""
    for k in dct.keys():
        if isinstance(dct[k], dict):
            message += f"{tab}{k}:\n{expand_dict(dct[k], dept + 1, stride)}"
        else:
            message += f"{tab}{k}: {dct[k]}\n"
    return message

def save_pickle_file(fname, fcontent):
    with open(fname, 'wb') as f:
        pickle.dump(fcontent, f)

def load_pickle_file(fname):
    with open(fname, 'rb') as f:
        return pickle.load(f)


def save_json_file(fname, fcontent, indent=4):
    with open(fname, 'w') as f:
         json.dump(fcontent, f, indent=indent)

def load_json_file(fname):
    with open(fname, 'r') as f:
        return json.load(f)

def jsonfy(text, verbose=0):
    err_msg, err_msg2, err_msg3 = None, None, None
    try: 
        js_txt = json.loads(text)
    except Exception as js_load_err:
        if isinstance(text, str):
            for hdl in CONTAINER_HANDLES:
                if text.startswith(hdl):
                    js_txt = clean_str_container(msg=text, head_handle=hdl)
                    return js_txt
        err_msg = f"[!][jsonify][JSON FORMAT][ERROR]: {js_load_err}\n text={text}\n"
        if verbose > 0: console.print(err_msg)
        try:
            js_txt = ast.literal_eval(text)
        except Exception as ast_eval_err:
            err_msg2 = f"[!][jsonify][AST FORMAT][ERROR]: {ast_eval_err}\n text={text}\n"
            if verbose > 0: console.print(err_msg2)
            try:
                js_txt = ast.literal_eval(text.replace("'", '""'))
            except Exception as ast_eval_err:
                err_msg3 = f"[!][jsonify][AST FORMAT][ERROR]: {ast_eval_err}\n text={text}\n"
                if verbose > 0: console.print(err_msg3)
                #raise Exception(f"[!][jsonify][FORMAT][ERROR]: UNABLE to jsonify {text}:\n    None of the jsonifying tricks worked.")
                return {"jsonify_error":f"[!][jsonify][FORMAT][ERROR]: UNABLE to jsonify {text}:\n    None of the jsonifying tricks worked."}
    return js_txt



class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    def disable(self):
        self.HEADER = ''
        self.OKBLUE = ''
        self.OKGREEN = ''
        self.WARNING = ''
        self.FAIL = ''
        self.ENDC = ''



def amber(msg):
    return bcolors.WARNING + str(msg) + bcolors.ENDC
def blue(msg):
    return bcolors.OKBLUE  + str(msg) + bcolors.ENDC
def green(msg):
    return bcolors.OKGREEN + str(msg) + bcolors.ENDC
def purple(msg):
    return bcolors.HEADER + str(msg) + bcolors.ENDC
def red(msg):
    return bcolors.FAIL + str(msg) + bcolors.ENDC
def nocolor(msg):
    return str(msg)


def display_image(pil_image, scale=1.0, max_width=None, max_height=1200):
  print(displayable_image(pil_image, scale, max_width, max_height))

def resize_image(pil_image, scale=1.0, max_width=None, max_height=1200):
    if scale != 1.0 or max_width or max_height:
        width, height = pil_image.size

        if scale != 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
        else:
            new_width, new_height = width, height
    
        if max_width and new_width > max_width:
            new_width = max_width
            new_height = int(height * (max_width / width))

        if max_height and new_height > max_height:
            new_height = max_height
            new_width = int(width * (max_height / height))
    
        pil_image = pil_image.resize((new_width, new_height), Image.LANCZOS)
    return pil_image 
    
        
def displayable_image(pil_image, scale=1.0, max_width=None, max_height=1200):
    pil_image = resize_image(pil_image, scale, max_width, max_height)
            
    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    encoded_string = base64.b64encode(image_bytes).decode()

    return f'\x1b]1337;File=inline=1:{encoded_string}\a'


def view(item):
  c = Console()
  with c.pager(styles=True):
    c.print(item)


def as_table(rows):
  table = Table(show_header=True)
  table.add_column("Path")
  table.add_column("Image")

  for row in rows:
    table.add_row(
      row.image_uri,
      imgcat(row.image)
    )
  return table

# Usage examples:
# display_pil_image(rs[0].image)  # Original size
# display_pil_image(rs[0].image, scale=0.5)  # Half size
# display_pil_image(rs[0].image, max_width=300)  # Limit width to 300 pixels
# display_pil_image(rs[0].image, max_height=200)  # Limit height to 200 pixels

def image_to_ascii(path, width=80, flag='america'):
    """
    Convert an image to ASCII art and print it to the console.

    Parameters:
    - path: str, file path to the input image
    - width: int, desired character width of the ASCII art
    """
    FLAGS = {'blue':[blue], 'red':[red], 'green':[green], 'purple':[purple], 'amber':[amber], 'nocolor':[nocolor],
             'america':[purple, nocolor, red], 'rainbow':[blue, purple, red, amber, green]}
    KNOW_FLAGS = list(FLAGS.keys())
    FLAG = flag.lower()
    if FLAG not in KNOW_FLAGS:
        print(f"[!][WARN] FLAG '{flasg}' is not RECOGNISED: -setting flasg to nocolor")
        FLAG = 'nocolor'
    # Load the image
    img = Image.open(path)

    # Compute new height to maintain aspect ratio (approximate correction for character aspect)
    aspect_ratio = img.height / img.width
    new_height = int(aspect_ratio * width * 0.5)

    # Resize and convert to grayscale
    img = img.resize((width, new_height))
    img = img.convert('L')

    # ASCII characters from dark to light
    chars = "@%#*+=-:. "

    # Map each pixel to an ASCII char
    pixels = img.getdata()
    ascii_str = ''.join(chars[pixel * len(chars) // 256] for pixel in pixels)

    # Split the string into lines
    lines = [ascii_str[index:index + width] for index in range(0, len(ascii_str), width)]

    # Print the ASCII art

    for i, line in enumerate(lines):
        color = FLAGS[FLAG][i % len(FLAGS[FLAG])]
        print(color(line))
