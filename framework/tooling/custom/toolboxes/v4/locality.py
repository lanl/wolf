from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .tool_models import (
    ArtifactKind,
    ArtifactRef,
    LocalityKind,
    LocalityRef,
    MountSpec,
    TransferSpec,
)


class LocalityResolver:
    """Classify artifact URIs and propose mount/transfer strategies.

    This is an MVP resolver. It is intentionally conservative: it classifies
    common URI/path patterns and produces inspectable plans rather than moving
    data or creating mounts itself.
    """

    def classify_uri(self, uri: str, kind: ArtifactKind | str = ArtifactKind.UNKNOWN, media_type: Optional[str] = None) -> ArtifactRef:
        if uri.startswith('universe://'):
            rest = uri[len('universe://'):]
            univ_id = rest.split('/', 1)[0] if rest else 'unknown'
            locality = LocalityRef(kind=LocalityKind.UNIVERSE, id=univ_id, uri=f'universe://{univ_id}')
        elif uri.startswith('actionbox://'):
            rest = uri[len('actionbox://'):]
            ab_id = rest.split('/', 1)[0] if rest else 'unknown'
            locality = LocalityRef(kind=LocalityKind.ACTIONBOX, id=ab_id, uri=f'actionbox://{ab_id}')
        elif uri.startswith('container://'):
            rest = uri[len('container://'):]
            cid = rest.split('/', 1)[0] if rest else 'unknown'
            locality = LocalityRef(kind=LocalityKind.CONTAINER, id=cid, uri=f'container://{cid}')
        elif uri.startswith('volume://'):
            rest = uri[len('volume://'):]
            vid = rest.split('/', 1)[0] if rest else 'unknown'
            locality = LocalityRef(kind=LocalityKind.VOLUME, id=vid, uri=f'volume://{vid}')
            kind = ArtifactKind.VOLUME
        elif uri.startswith(('s3://', 'gs://', 'az://', 'object://')):
            locality = LocalityRef(kind=LocalityKind.OBJECT_STORE, id=uri.split('://', 1)[0], uri=uri)
        elif uri.startswith('inline://'):
            locality = LocalityRef(kind=LocalityKind.INLINE, id='inline', uri='inline://')
            kind = ArtifactKind.INLINE
        else:
            p = Path(uri).expanduser()
            locality = LocalityRef(kind=LocalityKind.LOCAL, id='local', path=str(p.parent), uri=str(p.parent))
            if kind == ArtifactKind.UNKNOWN:
                if p.exists() and p.is_dir():
                    kind = ArtifactKind.DIRECTORY
                elif p.exists() and p.is_file():
                    kind = ArtifactKind.FILE
        size = None
        try:
            p = Path(uri).expanduser()
            if p.exists() and p.is_file():
                size = p.stat().st_size
        except Exception:
            size = None
        return ArtifactRef(uri=uri, kind=kind, media_type=media_type, locality=locality, size_bytes=size)

    def plan_mount_or_transfer(
        self,
        artifact: ArtifactRef,
        target_locality: LocalityRef,
        allow_mounts: bool = True,
        allow_data_movement: bool = False,
        target_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        if artifact.locality.id == target_locality.id and artifact.locality.kind == target_locality.kind:
            return {'strategy': 'already_local', 'mount': None, 'transfer': None, 'warning': None}

        reason = (
            f'Artifact {artifact.uri} lives at {artifact.locality.kind}:{artifact.locality.id}; '
            f'target execution locality is {target_locality.kind}:{target_locality.id}.'
        )

        if allow_mounts and artifact.locality.kind in {LocalityKind.LOCAL, LocalityKind.VOLUME}:
            mount = MountSpec(
                type='volume' if artifact.locality.kind == LocalityKind.VOLUME else 'bind',
                source=artifact.uri,
                target=target_path or '/mnt/wolf/input',
                mode='ro',
                reason=reason,
            )
            return {'strategy': 'mount', 'mount': mount, 'transfer': None, 'warning': None}

        if allow_data_movement:
            transfer = TransferSpec(
                source_uri=artifact.uri,
                target_uri=target_path,
                source_locality=artifact.locality,
                target_locality=target_locality,
                strategy='copy_placeholder',
                reason=reason,
            )
            return {'strategy': 'transfer', 'mount': None, 'transfer': transfer, 'warning': None}

        return {
            'strategy': 'blocked',
            'mount': None,
            'transfer': None,
            'warning': reason + ' Mounts and data movement are not allowed by policy.',
        }
