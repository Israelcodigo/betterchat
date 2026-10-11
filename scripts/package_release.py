"""Build a deterministic source ZIP from an explicit public file inventory."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nexo_kit import __version__

ROOT_FILES = ('README.md', 'README.es.md', 'CHANGELOG.md', 'LICENSE', 'pyproject.toml', 'MANIFEST.in', 'nexo.py',
              '.gitignore', '.gitattributes', 'CONTRIBUTING.md', 'SECURITY.md')
DIRECTORIES = ('nexo_kit', 'tests', 'docs', 'examples', 'scripts', '.github')
EXTENSIONS = {'.py', '.md', '.json', '.toml', '.html', '.css', '.js', '.cjs', '.yml', '.yaml', '.svg'}


def public_files(root=ROOT):
    files = [root / name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        files.extend(p for p in (root / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in EXTENSIONS)
    return sorted(set(files))


def main():
    from release_audit import audit
    audit(ROOT)
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    archive = destination / f'nexo-kit-{__version__}.zip'
    files = public_files()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in files:
            info = zipfile.ZipInfo('nexo-kit/' + path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, path.read_bytes())
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None or len(check.namelist()) != len(files):
            raise RuntimeError('Archive verification failed')
    artifacts = [p for p in destination.iterdir() if p.suffix in {'.zip', '.whl', '.gz'}]
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(artifacts)}
    (destination / 'SHA256SUMS.txt').write_text(''.join(f'{value}  {name}\n' for name, value in checksums.items()), encoding='utf-8')
    print(json.dumps({'archive': archive.name, 'files': len(files), 'bytes': archive.stat().st_size,
                      'sha256': checksums[archive.name]}, indent=2))


if __name__ == '__main__':
    main()
