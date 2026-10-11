"""Remove only native Codex trust entries for the exact completed probe fixtures.

No model, session, project directory, rollout, unrelated setting, or hook trust is
removed. Use --execute only after the native clean probe has completed. Config
values stay in memory; the output contains paths/counts and preservation checks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import tempfile
import tomllib

from codex_integration_probe import Client
from nexo_kit.util import canonical


class CleanupError(RuntimeError):
    """Only fixed, non-sensitive diagnostics are emitted by this script."""


class MetadataClient(Client):
    """Reuse the native Client without logging response/error configuration."""
    def pump(self, timeout=1):
        try:
            message = self.queue.get(timeout=timeout)
        except queue.Empty:
            return
        if message.get('probe_eof'):
            raise CleanupError('Native app-server ended unexpectedly')
        if 'id' in message and 'method' not in message:
            self.responses[message['id']] = message
            return
        if 'id' in message:
            self.send({'id': message['id'], 'error': {
                'code': -32601, 'message': 'Cleanup does not authorize interactive requests'}})
            raise CleanupError('Unexpected native interactive request')
        # No raw event/config content is retained. Cleanup has no active thread.
        self.events.append({'method': str(message.get('method', ''))})


def normalized(path: Path | str) -> str:
    return os.path.normcase(str(canonical(Path(path))))


def fixture_paths(initial_report: Path, native_report: Path, temp_root: Path) -> set[str]:
    initial = json.loads(initial_report.read_text(encoding='utf-8'))
    native = json.loads(native_report.read_text(encoding='utf-8'))
    if native.get('passed') is not True:
        raise CleanupError('Native clean probe has not completed successfully; cleanup is not permitted')
    initial_fixtures = initial.get('fixtures')
    if not isinstance(initial_fixtures, list) or not initial_fixtures:
        raise CleanupError('Initial report has no explicit fixture list')
    values = [*initial_fixtures, native.get('fixture')]
    validated = set()
    temp = canonical(temp_root)
    for value in values:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise CleanupError('Fixture must be an explicit absolute path')
        candidate = canonical(Path(value))
        if (normalized(candidate.parent.parent) != normalized(temp)
                or not re.fullmatch(r'nexo-real-codex-[A-Za-z0-9_-]+', candidate.parent.name)
                or not candidate.name or candidate.name in {'.', '..'}):
            raise CleanupError('Fixture is not a direct child of a guarded Nexo probe directory under TEMP')
        validated.add(normalized(candidate))
    if not validated:
        raise CleanupError('No validated fixture paths')
    return validated


def user_layer(client: Client, config_path: Path) -> dict:
    response = client.request('config/read', {'includeLayers': True})
    layers = response.get('layers')
    if not isinstance(layers, list):
        raise CleanupError('Native config/read did not return layers')
    selected = [layer for layer in layers if isinstance(layer, dict)
                and isinstance(layer.get('name'), dict)
                and layer['name'].get('type') == 'user'
                and not layer['name'].get('profile')
                and isinstance(layer['name'].get('file'), str)
                and normalized(layer['name']['file']) == normalized(config_path)]
    if len(selected) != 1 or not isinstance(selected[0].get('config'), dict) or not selected[0].get('version'):
        raise CleanupError('Exactly one versioned base user layer for the expected config file is required')
    if selected[0].get('disabledReason'):
        raise CleanupError('The expected user config layer is disabled')
    return selected[0]


def file_snapshot(config_path: Path) -> tuple[str, dict]:
    raw = config_path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), tomllib.loads(raw.decode('utf-8-sig'))


def cleanup(client: Client, config_path: Path, fixtures: set[str], execute: bool = False) -> dict:
    before = user_layer(client, config_path)
    original = before['config']
    file_hash, disk_original = file_snapshot(config_path)
    if disk_original != original:
        raise CleanupError('User layer and current config file differ; possible concurrent change')
    projects = original.get('projects', {})
    if not isinstance(projects, dict):
        raise CleanupError('User projects setting is not an object')
    removed = []
    retained = copy.deepcopy(projects)
    for key in projects:
        if not isinstance(key, str) or not Path(key).is_absolute():
            continue
        if normalized(key) in fixtures:
            removed.append(key)
            del retained[key]
    report = {'executed': False, 'removed_fixture_keys': sorted(removed),
              'removed_count': len(removed), 'other_project_count': len(retained),
              'settings_preserved': True, 'other_projects_preserved': True}
    if not execute or not removed:
        report['dry_run'] = not execute
        return report
    # Re-read directly before the guarded write, then rely on the native CAS.
    immediate = user_layer(client, config_path)
    current_hash, current_disk = file_snapshot(config_path)
    if (immediate['version'] != before['version'] or immediate['config'] != original
            or current_hash != file_hash or current_disk != original):
        raise CleanupError('Configuration changed during cleanup preparation; nothing was written')
    expected = copy.deepcopy(original)
    expected['projects'] = retained
    written = client.request('config/batchWrite', {
        'filePath': str(config_path), 'expectedVersion': before['version'],
        'edits': [{'keyPath': 'projects', 'value': retained, 'mergeStrategy': 'replace'}],
        'reloadUserConfig': True})
    if (not isinstance(written, dict) or written.get('status') not in {'ok', 'okOverridden'}
            or not written.get('version') or not isinstance(written.get('filePath'), str)
            or normalized(written['filePath']) != normalized(config_path)):
        raise CleanupError('Native write response could not be verified; inspect metadata before retrying')
    after = user_layer(client, config_path)
    after_hash, disk_after = file_snapshot(config_path)
    if after['version'] != written['version'] or after['config'] != expected or disk_after != expected:
        raise CleanupError('Post-write preservation check failed or another writer changed settings; no rollback attempted')
    stable = user_layer(client, config_path)
    stable_hash, stable_disk = file_snapshot(config_path)
    if (stable['version'] != after['version'] or stable['config'] != expected
            or stable_hash != after_hash or stable_disk != expected):
        raise CleanupError('Configuration changed during final verification; no rollback attempted')
    report.update({'executed': True, 'dry_run': False})
    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--initial-report', type=Path, default=root / '.nexo/integration-verification/report.json')
    parser.add_argument('--native-report', type=Path, default=root / '.nexo/integration-native-clean/report.json')
    parser.add_argument('--config', type=Path, default=Path.home() / '.codex/config.toml')
    parser.add_argument('--output', type=Path, default=root / '.nexo/integration-cleanup')
    parser.add_argument('--report', type=Path, default=root / 'docs/verification/codex-profile-cleanup.json')
    parser.add_argument('--execute', action='store_true', help='Explicitly perform the guarded native config update')
    args = parser.parse_args()
    if not args.codex:
        parser.error('An available Codex executable is required')
    client = None
    try:
        fixtures = fixture_paths(args.initial_report, args.native_report, Path(tempfile.gettempdir()))
        config_path = canonical(args.config)
        args.output.mkdir(parents=True, exist_ok=True)
        client = MetadataClient(args.codex, root, args.output)
        report = cleanup(client, config_path, fixtures, args.execute)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.execute:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as error:
        # Native error envelopes may contain effective configuration. Emit only
        # our fixed diagnostics and a type for all other failures, never values.
        diagnostic = str(error) if isinstance(error, CleanupError) else 'Native cleanup failed; configuration values withheld'
        print(json.dumps({'write_outcome': 'not_confirmed', 'verified': False, 'error_type': type(error).__name__,
                          'error': diagnostic}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
