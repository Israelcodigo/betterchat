"""Read-only public inventory, documentation checks."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_release import public_files


def audit(root=ROOT):
    files = public_files(root)
    failures = []
    for path in files:
        if not path.is_file():
            failures.append(f'Missing {path.name}')
            continue
        if path.is_symlink():
            failures.append(f'Symlink is not a public source file: {path.name}')
        text = path.read_text(encoding='utf-8')
        relative = path.relative_to(root).as_posix()
        if relative.startswith('docs/evidence/') or any(part in {'.nexo', '.git', '.env', '__pycache__'} for part in path.relative_to(root).parts):
            failures.append(f'Private path: {relative}')
        if re.search(r'C:[\\/]+Users[\\/]+(?!Public\b|example\b|test\b|tu-usuario\b)[A-Za-z0-9_-]+', text):
            failures.append(f'Personal Windows path: {relative}')
        if path.suffix == '.md':
            for link in re.findall(r'\]\(([^)]+)\)', text):
                link = link.split('#')[0]
                if not link or re.match(r'[a-z]+:', link) or '{' in link:
                    continue
                if not (path.parent / unquote(link)).exists():
                    failures.append(f'Broken link in {relative}: {link}')
    if failures:
        raise ValueError('\n'.join(failures))
    return {'public_files': len(files), 'links': 'passed',
            'limits': 'Inventory checks are not an exhaustive secret scanner.'}


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
