from pathlib import Path
from typing import Union, Optional

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
