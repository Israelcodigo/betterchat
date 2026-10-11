"""Implicit-routing probe: synthetic assignments in a real Codex client.

Checks use native commandExecution results and skills/list. Fresh fixtures
also verify the final output independently from the host; historical protected
fixtures are never reopened or repaired by this probe.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.codex_integration_probe import Client, CORE_PERSONAL_SKILLS
from scripts.native_fixture import create_native_fixture
from nexo_kit.actions import initialize_project


VALIDATOR = '''import csv,json,sys
from pathlib import Path
src,dst=map(Path,sys.argv[1:])
with src.open(encoding="utf-8-sig",newline="") as f:
    expected=[{"id":int(r["id"]),"label":r["label"],"amount":float(r["amount"])} for r in csv.DictReader(f)]
value=json.loads(dst.read_text(encoding="utf-8"))
assert value==expected,(value,expected)
assert all(type(r["id"]) is int and type(r["amount"]) in (int,float) for r in value)
print("CSV_CONVERSION_VERIFIED")
'''

REUSE_INPUT = 'incoming.csv'
REUSE_OUTPUT = 'converted.json'
REUSE_CSV = 'id,label,amount\n73,"mañana, 東京",-8.75\n2,"línea\nsegunda",0.125\n'


def successful_commands(events, thread):
    seen = set()
    for event in events:
        params = event.get('params', {})
        item = params.get('item') or {}
        if (event.get('method') == 'item/completed' and params.get('threadId') == thread
                and item.get('type') == 'commandExecution' and item.get('status') == 'completed'
                and item.get('exitCode') == 0 and item.get('id') not in seen):
            seen.add(item.get('id'))
            yield item


def simple_native_invocations(command):
    """Parse only simple PowerShell invocations; fail closed on dynamic scripts.

    Native traces wrap PowerShell commands in pwsh -Command. Do not count an
    embedded path in Write-Output, comments, Python -c, or an unrelated command
    as an actual invocation. Complex shells remain inspectable in the raw trace
    but cannot satisfy this automated proof.
    """
    # Codex renders command argv with shell escaping even on Windows. Decode
    # that display representation; this does not execute or interpolate text.
    try:
        rendered = shlex.split(command)
    except ValueError:
        return
    script = (rendered[2] if len(rendered) == 3 and rendered[1].lower() == '-command'
              and ntpath.basename(rendered[0]).lower() in {'pwsh.exe', 'powershell.exe'} else command)
    token_pattern = re.compile(r"'(?:[^']|'')*'|\"[^\"`$]*\"|[^\s'\";|&`$#{}()]+")
    invocations = []
    for line in script.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r'if\s*\(\s*\$LASTEXITCODE\s+-ne\s+0\s*\)\s*\{\s*exit\s+\$LASTEXITCODE\s*\}', line, re.I):
            if not invocations or not python_arguments(invocations[-1]):
                return
            # This only propagates failure of the preceding Python command;
            # an event with exit 0 and the following validator's marker proves
            # that it did not skip the remaining calls.
            continue
        if line.startswith('& '):
            line = line[2:].lstrip()
        parts, end = [], 0
        for match in token_pattern.finditer(line):
            if line[end:match.start()].strip():
                return
            token = match.group()
            parts.append(token[1:-1].replace("''", "'") if token.startswith(("'", '"')) else token)
            end = match.end()
        if parts and not line[end:].strip():
            invocations.append(parts)
        else:
            return
    for tokens in invocations:
        if tokens[0].lower() in {'get-content', 'cat', 'type', 'gc'}:
            continue
        arguments = python_arguments(tokens)
        if not arguments or not arguments[0].lower().endswith('.py'):
            return
    yield from invocations


def python_arguments(tokens):
    if not re.fullmatch(r'(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?', ntpath.basename(tokens[0]), re.I):
        return []
    arguments = tokens[1:]
    while arguments and arguments[0] in {'-B', '-u', '-3', '-I', '-E', '-s', '-S'}:
        arguments = arguments[1:]
    return arguments


def installed_path(path, skill, *, suffix=None):
    """Accept this project's installed path or its relative spelling only."""
    candidate = ntpath.normcase(ntpath.normpath(path))
    roots = [ntpath.normcase(ntpath.normpath(skill['path'])),
             ntpath.normcase(ntpath.join('.agents', 'skills', skill['name']))]
    if suffix:
        return any(candidate == ntpath.join(root, suffix) for root in roots)
    return any(candidate.startswith(ntpath.join(root, 'scripts') + '\\')
               and candidate.endswith('.py') for root in roots)


def reuse_evidence(events, thread, installed):
    """Require native reading AND execution in the new conversation, not a claim."""
    result = {'skill_reads': [], 'installed_script_executions': [], 'independent_native_checks': []}
    for item in successful_commands(events, thread):
        command = item.get('command') or ''
        output = item.get('aggregatedOutput') or ''
        invocations = list(simple_native_invocations(command))
        if (any(python_arguments(tokens) == ['verify_conversion.py', REUSE_INPUT, REUSE_OUTPUT]
                for tokens in invocations) and output.strip() == 'CSV_CONVERSION_VERIFIED'):
            result['independent_native_checks'].append({'execution_id': item['id'], 'command': command})
        for skill in installed:
            read = any(tokens[0].lower() in {'get-content', 'cat', 'type', 'gc'}
                and any(installed_path(token, skill, suffix='skill.md') for token in tokens[1:])
                for tokens in invocations)
            if read and re.search(
                    r'^name:\s*[\'\"]?' + re.escape(skill['name']) + r'[\'\"]?\s*$', output, re.M):
                result['skill_reads'].append({'name': skill['name'], 'execution_id': item['id'], 'command': command})
            # The fixture's learned converter is Python. Require its installed
            # script path, interpreter invocation and the new input/output;
            # merely listing or reading files is insufficient.
            executions = []
            for tokens in invocations:
                arguments = python_arguments(tokens)
                if arguments and installed_path(arguments[0], skill) and REUSE_INPUT in arguments and REUSE_OUTPUT in arguments:
                    executions.append(arguments)
            if executions:
                result['installed_script_executions'].append(
                    {'name': skill['name'], 'execution_id': item['id'], 'command': command})
    return result


def json_objects(output):
    """Extract complete JSON objects with optional other stdout."""
    decoder, offset = json.JSONDecoder(), 0
    while offset < len(output):
        start = output.find('{', offset)
        if start < 0:
            return
        try:
            value, length = decoder.raw_decode(output[start:])
        except ValueError:
            offset = start + 1
            continue
        offset = start + length
        if isinstance(value, dict):
            yield value


def native_evidence(events, thread):
    """Use successful native executions, never assistant success prose."""
    result = {'conversions': [], 'observations': [], 'stages': [], 'validations': [],
              'installations': [], 'observed_skill_reads': []}
    for item in successful_commands(events, thread):
        command, output = item.get('command') or '', item.get('aggregatedOutput') or ''
        if 'SKILL.md' in command:
            result['observed_skill_reads'].append({'execution_id': item['id'], 'command': command})
        if output.strip() == 'CSV_CONVERSION_VERIFIED' and 'verify_conversion.py' in command:
            for index in range(1, 4):
                if f'input-{index}.csv' in command and f'output-{index}.json' in command:
                    result['conversions'].append({'request': f'csv-request-{index}',
                        'execution_id': item['id'], 'native_exit_code': 0,
                        'native_output': 'CSV_CONVERSION_VERIFIED'})
        if 'nexo.py' not in command or 'learn' not in command:
            continue
        for value in json_objects(output):
            receipt = {**value, 'native_execution_id': item['id']}
            if 'request_id' in value and 'pattern' in value and 'duplicate' in value:
                receipt.setdefault('key', value['pattern'].get('key'))
                receipt['full_receipt_emitted'] = 'evidence' in value and 'exit_code' in value
                result['observations'].append(receipt)
            elif value.get('status') == 'staged' and 'generation' in value and 'signature' in value:
                result['stages'].append(receipt)
            elif 'case_id' in value and 'skill_sha256' in value and 'generation' in value:
                result['validations'].append(receipt)
            elif value.get('status') == 'installed' and 'name' in value and 'path' in value:
                result['installations'].append(receipt)
    return result


def read_learning_ledger(project):
    """Read receipts from the new inherited-ACL fixture, never mutate state."""
    database = (Path(project) / '.nexo/learning.sqlite3').resolve()
    with contextlib.closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        return {'mode': 'ro', 'path': str(database),
                'attempts': [json.loads(row[0]) for row in connection.execute('SELECT receipt FROM attempts ORDER BY id')],
                'validations': [json.loads(row[0]) for row in connection.execute('SELECT receipt FROM validations ORDER BY id')]}


def evaluate(report, events, local_skills):
    evidence = native_evidence(events, report['thread_id'])
    report.update(evidence)
    requests = [f'csv-request-{index}' for index in range(1, 4)]
    converted = {row['request'] for row in evidence['conversions']}
    ledger = report.get('ledger_read_only', {})
    corroborated = []
    for native in evidence['observations']:
        persisted = next((row for row in ledger.get('attempts', [])
                          if row.get('key') == native.get('key') and row.get('request_id') == native.get('request_id')), {})
        if persisted and any(native[key] != value for key, value in persisted.items() if key in native):
            continue
        corroborated.append({**persisted, **native, 'persisted_receipt_corroborated': bool(persisted)})
    success = [row for row in corroborated
               if row.get('status') == 'passed' and row.get('exit_code') == 0 and row.get('duplicate') is False]
    report['turns'] = [{'request': request, 'output_verified_by_native_client': request in converted,
                       'observations': [row for row in success if row.get('request_id') == request]}
                      for request in requests if request in converted]
    selected = [next((row for row in success if row.get('request_id') == request), {}) for request in requests]
    keys = {row.get('key') for row in selected if row}
    stages = [row for row in evidence['stages'] if row.get('key') in keys]
    validations = [row for row in evidence['validations']
                   if row.get('status') == 'passed' and row.get('exit_code') == 0
                   and row.get('case_id') not in requests and row.get('key') in keys
                   and re.fullmatch(r'[a-f0-9]{64}', str(row.get('skill_sha256', '')))
                   and any(stage.get('generation') == row.get('generation')
                           and stage.get('signature') == row.get('skill_sha256') for stage in stages)]
    installs = [row for row in evidence['installations'] if row.get('scope') == 'project'
                and any(stage.get('name') == row.get('name') for stage in stages)]
    original_hash = report['validator_original_sha256']
    report['checks'] = {
        'three_native_outputs_verified': converted == set(requests),
        'three_distinct_synthetic_requests_recorded': len(success) == 3
            and {row.get('request_id') for row in success} == set(requests),
        'three_native_receipts_corroborated_by_read_only_ledger': ledger.get('mode') == 'ro'
            and len(success) == 3 and all(row['persisted_receipt_corroborated'] for row in success),
        'one_pattern': len(keys) == 1 and all(selected),
        'counts_progressed_one_two_three': all(row.get('pattern', {}).get('observations') == index
            and row.get('pattern', {}).get('consecutive_successes') == index
            for index, row in enumerate(selected, 1)),
        'validator_hash_preserved_in_emitted_receipts_and_independent_host_check': bool(validations)
            and report.get('host_validator_original_preserved') is True and all(
            any(item.get('path') == 'verify_conversion.py' and item.get('sha256') == original_hash
                for item in row.get('evidence', [])) for row in selected + validations),
        'new_case_validated_for_staged_skill_hash': bool(validations),
        'native_install_receipt': bool(installs),
        'learned_skill_discovered': bool(installs) and not report.get('skills_errors') and all(
            any(skill.get('name') == row.get('name') and skill.get('enabled') is True
                and Path(skill.get('path', '')) == Path(row['path']) / 'SKILL.md'
                for skill in local_skills) for row in installs),
        'implicit_learning_skill_read': any('nexo-learn' in row['command']
            for row in evidence['observed_skill_reads']),
        'no_explicit_skill_inputs': report['explicit_skill_inputs'] == [],
        'all_nine_core_skills_discovered': CORE_PERSONAL_SKILLS <= {
            skill.get('name') for skill in report.get('core_skills', []) if skill.get('enabled') is True},
    }
    reuse = reuse_evidence(events, report.get('reuse_thread_id'), installs)
    report['automatic_reuse'] = reuse
    report['checks'].update({
        'reuse_in_new_thread_without_history': bool(report.get('reuse_thread_id'))
            and report['reuse_thread_id'] != report['thread_id'] and report.get('reuse_thread_started_fresh') is True,
        'learned_skill_read_implicitly_in_new_thread': bool(reuse['skill_reads']),
        'installed_script_executed_in_new_thread': bool(reuse['installed_script_executions']),
        'new_result_verified_by_native_client': bool(reuse['independent_native_checks']),
        'new_result_independently_verified_by_host': report.get('host_reuse_result_verified') is True,
        'reuse_prompt_has_no_skill_hint': report.get('reuse_prompt_has_no_skill_hint') is True,
    })
    report['passed'] = all(report['checks'].values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-model', action='store_true')
    parser.add_argument('--resume-prior', type=Path)
    args = parser.parse_args()
    if not args.run_model or not args.codex:
        parser.error('--run-model and an available Codex executable are required')
    if args.resume_prior and args.resume_prior.resolve() == args.output.resolve():
        parser.error('Resume output must differ from the historical report directory')
    args.output.mkdir(parents=True, exist_ok=True)
    prior_events = []
    validator_hash = hashlib.sha256(VALIDATOR.replace('\n', os.linesep).encode('utf-8')).hexdigest()
    if args.resume_prior:
        previous = json.loads((args.resume_prior / 'report.json').read_text(encoding='utf-8'))
        project, prior_thread = Path(previous['project']), previous['thread_id']
        prior_events = [json.loads(line) for line in
                        (args.resume_prior / 'native-events.jsonl').read_text(encoding='utf-8').splitlines() if line]
        prior = native_evidence(prior_events, prior_thread)
        if ({row['request'] for row in prior['conversions']} != {'csv-request-1'}
                or not any(row.get('request_id') == 'csv-request-1' and row.get('status') == 'passed'
                           and row.get('pattern', {}).get('observations') == 1 for row in prior['observations'])):
            raise ValueError('Resume requires native verification and first observation only; refusing to repeat requests')
    else:
        previous, prior_thread = None, None
        project = create_native_fixture('nexo-auto-learning-')
        initialize_project(project)
        (project / 'AGENTS.md').write_text(
            '# Alcance del ensayo\n\nTodos los cambios y artefactos reutilizables de este ensayo '
            'se guardan solo en este proyecto. No modificar el perfil personal. '
            'No editar verify_conversion.py. Las peticiones son independientes; '
            'sus referencias permanecen estables en caso de reintento.\n', encoding='utf-8')
        for index in range(1, 5):
            (project / f'input-{index}.csv').write_text(
                'id,label,amount\n' + f'{index},café,{index}.50\n' + f'{index+10},東京,{index+1}.25\n',
                encoding='utf-8')
        (project / 'verify_conversion.py').write_text(VALIDATOR, encoding='utf-8')
    report = {'project': str(project), 'explicit_skill_inputs': [], 'passed': False, 'turns': [],
              'validator_original_sha256': validator_hash,
              'verification_method': 'Native commandExecution results, skills/list, read-only persisted receipts and independent host validation in the new inherited-ACL fixture.',
              'request_provenance': 'Three separate synthetic assignments in a real Codex client, not prior human requests.',
              'host_artifact_reopen_performed': False}
    if previous:
        report.update(prior_report=str(args.resume_prior.resolve()), prior_error=previous.get('error'),
                      resumed_thread_id=prior_thread)
    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    save()
    client = Client(args.codex, project, args.output)
    try:
        parameters = {'cwd': str(project), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'}
        if prior_thread:
            parameters['threadId'] = prior_thread
        started = client.request('thread/resume' if prior_thread else 'thread/start', parameters)
        thread = started['thread']['id']
        if prior_thread and thread != prior_thread:
            raise RuntimeError('Native resume returned a different thread')
        report['thread_id'] = thread
        client.allowed_nexo_threads[thread] = project.resolve()
        print('AUTOLEARN THREAD', thread, 'resumed', bool(prior_thread), flush=True)
        for index in ((2, 3) if prior_thread else (1, 2, 3)):
            prompt = (f'Referencia del encargo: csv-request-{index}. Convierte input-{index}.csv en output-{index}.json. '
                      'Quiero una lista de objetos con id entero, label texto Unicode y amount numérico; conserva el orden. '
                      f'Comprueba el resultado con {sys.executable!r} -B verify_conversion.py input-{index}.csv output-{index}.json. '
                      'Resuelve los detalles necesarios y deja una respuesta breve.')
            client.turn(thread, prompt, skills=())
            evaluate(report, prior_events + client.events, [])
            save()
        info = client.request('thread/read', {'threadId': thread, 'includeTurns': False})
        report['rollout_path'] = info['thread'].get('path')
        try:
            discovered = client.request('skills/list', {'cwds': [str(project)], 'forceReload': True})
            report['skills_errors'] = [error for row in discovered['data'] for error in row.get('errors', [])]
            report['core_skill_names'] = sorted({skill['name'] for row in discovered['data']
                                                for skill in row['skills'] if skill['name'].startswith('nexo-')})
            report['core_skills'] = [skill for row in discovered['data'] for skill in row['skills']
                                    if skill['name'] in CORE_PERSONAL_SKILLS]
            local_skills = [skill for row in discovered['data'] for skill in row['skills']
                            if Path(skill['path']).is_relative_to(project)]
        except Exception as error:
            report['skills_errors'], local_skills = [str(error)], []
        report['local_skills'] = local_skills
        if not prior_thread:
            report['ledger_read_only'] = read_learning_ledger(project)
        evaluate(report, prior_events + client.events, local_skills)
        if (local_skills and report['checks']['learned_skill_discovered'] and not prior_thread):
            # A fourth independent request in a new native thread proves that
            # discovery is usable without inherited conversation instructions.
            (project / REUSE_INPUT).write_text(REUSE_CSV, encoding='utf-8')
            reuse_started = client.request('thread/start', {
                'cwd': str(project), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})
            reuse_thread = reuse_started['thread']['id']
            report['reuse_thread_id'] = reuse_thread
            report['reuse_thread_started_fresh'] = True
            client.allowed_nexo_threads[reuse_thread] = project.resolve()
            prompt = (f'Convierte {REUSE_INPUT} en {REUSE_OUTPUT}. Quiero una lista de objetos '
                      'con id entero, label texto Unicode y amount numérico; conserva el orden. '
                      f'Comprueba el resultado con {sys.executable!r} -B verify_conversion.py '
                      f'{REUSE_INPUT} {REUSE_OUTPUT}. Resuelve los detalles necesarios y deja una respuesta breve.')
            report['reuse_prompt'] = prompt
            report['reuse_prompt_has_no_skill_hint'] = ('skill' not in prompt.lower() and '$' not in prompt
                and all(skill['name'] not in prompt and skill['path'] not in prompt for skill in local_skills))
            save()
            print('AUTOLEARN REUSE THREAD', reuse_thread, flush=True)
            client.turn(reuse_thread, prompt, skills=())
            # This is a new fixture created with inherited ACLs, not an attempt
            # to reopen the old protected reproduction or change its permissions.
            report['host_artifact_reopen_performed'] = True
            assert (project / 'verify_conversion.py').read_bytes() == VALIDATOR.replace('\n', os.linesep).encode('utf-8')
            report['host_validator_original_preserved'] = True
            assert (project / REUSE_INPUT).read_text(encoding='utf-8') == REUSE_CSV
            with (project / REUSE_INPUT).open(encoding='utf-8-sig', newline='') as source:
                expected = [{'id': int(row['id']), 'label': row['label'], 'amount': float(row['amount'])}
                            for row in csv.DictReader(source)]
            actual = json.loads((project / REUSE_OUTPUT).read_text(encoding='utf-8'))
            assert actual == expected and all(type(row['id']) is int
                and type(row['amount']) in (int, float) for row in actual)
            report['host_reuse_result_verified'] = True
            report['reuse_output_sha256'] = hashlib.sha256((project / REUSE_OUTPUT).read_bytes()).hexdigest()
            evaluate(report, prior_events + client.events, local_skills)
        print('AUTOLEARN PROBE', report['passed'], 'local_skills', [s['name'] for s in local_skills], flush=True)
        if not report['passed']:
            raise RuntimeError('Native autolearning checks failed: ' + ', '.join(
                name for name, passed in report['checks'].items() if not passed))
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        save()
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    main()
