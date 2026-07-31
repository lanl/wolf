from __future__ import annotations

from pathlib import Path

from .tool_models import ArtifactKind, ArtifactRef, LocalityKind, LocalityRef


def local_file_ref(path: str, media_type: str | None = None, permissions: str = 'read_only') -> ArtifactRef:
    p = Path(path)
    size = p.stat().st_size if p.exists() and p.is_file() else None
    return ArtifactRef(
        uri=str(p),
        kind=ArtifactKind.FILE,
        media_type=media_type,
        locality=LocalityRef(kind=LocalityKind.LOCAL, id='local', path=str(p.parent)),
        size_bytes=size,
        permissions=permissions,
    )


def inline_ref(value: str, media_type: str = 'text/plain') -> ArtifactRef:
    return ArtifactRef(
        uri='inline://value',
        kind=ArtifactKind.INLINE,
        media_type=media_type,
        locality=LocalityRef(kind=LocalityKind.INLINE, id='inline'),
        permissions='read_only',
        metadata={'value': value},
    )
