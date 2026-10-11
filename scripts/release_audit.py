"""Read-only public inventory, documentation and live evidence checks."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import runpy
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
        if any(part in {'.nexo', '.git', '.env', '__pycache__'} for part in path.relative_to(root).parts):
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
    evidence = root / 'docs/evidence/codex-smoke.json'
    source = root / 'docs/evidence/normalize.py'
    if evidence.exists() and source.exists():
        record = json.loads(evidence.read_text(encoding='utf-8'))
        if record.get('source_sha256') != hashlib.sha256(source.read_bytes()).hexdigest():
            failures.append('Live artifact hash mismatch')
        normalize = runpy.run_path(str(source))['normalize']
        cases = [([], []), ([' A ', 'b', 'a', '', ' B '], ['a', 'b']),
                 (['Straße', 'STRASSE', '  É  '], ['strasse', 'é']),
                 (iter([' Z ', 'z', 'Y']), ['z', 'y'])]
        if any(normalize(values) != expected for values, expected in cases):
            failures.append('Published live artifact fails acceptance')
        if not record.get('complete') or record.get('cases') != 4:
            failures.append('Live evidence does not report four passed cases')
    else:
        failures.append('Missing public live evidence/artifact')
    if failures:
        raise ValueError('\n'.join(failures))
    return {'public_files': len(files), 'links': 'passed', 'live_artifact': '4 cases passed',
            'limits': 'Inventory checks are not an exhaustive secret scanner or proof of provider authenticity.'}


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
