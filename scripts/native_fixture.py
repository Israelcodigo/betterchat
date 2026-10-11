"""Disposable, non-secret projects shared by a host and its native sandbox.

On Windows CPython 3.13's tempfile.mkdtemp uses mode 0o700, which creates a
protected OWNER RIGHTS DACL. Files subsequently owned by a sandbox identity
can become unreadable by the host. Native integration fixtures instead inherit
their parent's normal project ACL; this helper never edits an existing ACL.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import uuid


def create_native_fixture(prefix: str, parent: Path | None = None) -> Path:
    """Create a unique directory without replacing any existing filesystem entry.

    Not a private storage helper: use tempfile for secrets or single-identity
    unit-test data. The unpredictable name and exclusive mkdir prevent races
    from silently reusing somebody else's directory.
    """
    if not prefix or any(character in prefix for character in '/\\:') or prefix in {'.', '..'}:
        raise ValueError('Fixture prefix must be a simple nonempty filename prefix')
    root = Path(tempfile.gettempdir()) if parent is None else Path(parent)
    root = root.resolve(strict=True)
    for _ in range(8):
        path = root / (prefix + uuid.uuid4().hex)
        try:
            path.mkdir(mode=0o777, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise FileExistsError('Could not create an exclusive native fixture after 8 collisions')
