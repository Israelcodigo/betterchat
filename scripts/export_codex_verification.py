"""Export compact evidence from actual Codex traces without full conversation logs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexo_kit.store import Store
from nexo_kit.evidence import validate_evidence
from nexo_kit.mcp import tool_list


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--initial', type=Path, required=True)
    p.add_argument('--followup', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repair', type=Path, action='append', default=[])
    p.add_argument('--hooks-recheck', type=Path)
    p.add_argument('--native-clean', type=Path)
    args = p.parse_args()
    initial = json.loads((args.initial / 'report.json').read_text(encoding='utf-8'))
    final = json.loads((args.followup / 'report.json').read_text(encoding='utf-8'))
    clean = (json.loads((args.native_clean / 'report.json').read_text(encoding='utf-8'))
             if args.native_clean else None)
    if clean:
        assert clean.get('passed'), 'Clean native orchestration did not pass'
        assert all(final['checks'].get(name) for name in ('mcp_workflow', 'four_native_roles', 'beta_no_tasks'))
    else:
        assert final.get('passed'), 'Follow-up probe did not pass'
    folders = [args.initial, args.followup] + args.repair + ([args.native_clean] if clean else [])
    traces = [[json.loads(line) for line in (folder / 'native-events.jsonl').read_text(encoding='utf-8').splitlines()] for folder in folders]
    events = [event for trace in traces for event in trace]
    names = {tool['name'] for tool in tool_list()}
    def mcp_calls(trace):
        return [e['params']['item'] for e in trace if e['method'] == 'item/completed' and
                e['params']['item']['type'] == 'mcpToolCall' and e['params']['item'].get('server') == 'nexo'
                and e['params']['item'].get('tool') in names]
    def call_ok(call):
        return call['status'] == 'completed' and not (call.get('result') or {}).get('isError')
    def call_error(call):
        return call.get('error') or (call.get('result') if not call_ok(call) else None)
    calls = mcp_calls(events)
    hooks = [e['params'] for e in events if e['method'] == 'hook/completed']
    output = {'date': '2026-10-10', 'codex': subprocess.check_output([initial['codex'], '--version'], text=True).strip(),
              'python': sys.version.split()[0], 'initial_thread': initial['thread_alpha'],
              'followup_thread': final['thread_alpha'], 'beta_thread': final['thread_beta'],
              'scope': 'personal', 'fixtures': initial['fixtures'], 'checks': final['checks'],
              'orchestration_mode': final.get('orchestration_mode'),
              'orchestration_repair_thread': final.get('orchestration_repair_thread'),
              'followup_passed': final.get('passed', False),
              'followup_error': final.get('error'),
              'native_clean': ({key: clean.get(key) for key in (
                  'fixture', 'thread_id', 'passed', 'checks', 'task_id', 'native_roles', 'error')}
                               if clean else None),
              'skills': [{'cwd': row['cwd'], 'errors': row['errors'], 'skills': [
                  {k: s.get(k) for k in ('name', 'path', 'scope', 'enabled')} for s in row['skills']]} for row in initial['skills']],
              'native_roles': final['child_roles'],
              'hooks': dict(Counter(h['run']['eventName'] for h in hooks if h['run']['status'] == 'completed')),
              'hook_failures': [h for h in hooks if h['run']['status'] != 'completed'],
              'mcp_successes': dict(Counter(c['tool'] for c in calls if call_ok(c))),
              'initial_mcp_denials': [{'tool': c['tool'], 'error': call_error(c)} for c in mcp_calls(traces[0]) if not call_ok(c)],
              'followup_mcp_failures': [{'tool': c['tool'], 'error': call_error(c)} for c in mcp_calls([e for t in traces[1:] for e in t]) if not call_ok(c)],
              'one_time_mcp_approvals': dict(Counter(e['params']['tool'] for e in events if e['method'] == 'probe/approvalOnce')),
              'compact_items_completed': sum(e['method'] == 'item/completed' and e['params']['item']['type'] == 'contextCompaction' for e in events),
              'initial_probe_error': initial.get('error'), 'projects': []}
    fixtures = initial['fixtures'] + ([clean['fixture']] if clean else [])
    for index, fixture in enumerate(fixtures):
        project = Path(fixture)
        with Store(project) as store:
            tasks = store.list_tasks()
            memories = store.list_memories()
            hook_events = [event for event in store.events(1000) if event['kind'] == 'hook']
        root_commit = subprocess.check_output(['git', '-C', str(project), 'rev-list', '--max-parents=0', 'HEAD'], text=True).strip()
        assert '\n' not in root_commit, 'Fixture must have a single initial commit'
        baseline = subprocess.check_output(['git', '-C', str(project), 'show', root_commit + ':test_arithmetic.py'])
        tests_same = baseline.replace(b'\r\n', b'\n') == (project / 'test_arithmetic.py').read_bytes().replace(b'\r\n', b'\n')
        task_rows = []
        for task in tasks:
            evidence = task.get('evidence') or {}
            # The first task's receipt intentionally predates the later docstring
            # change. Validate the newest receipt against the final source only.
            valid_final = False
            if task['status'] == 'done' and evidence.get('verdict') == 'passed':
                try:
                    validate_evidence(project, evidence, project)
                    valid_final = True
                except (ValueError, OSError):
                    pass
            task_rows.append({'id': task['id'], 'title': task['title'], 'status': task['status'],
                              'attempts': task['attempts'], 'evidence_id': evidence.get('id'),
                              'verdict': evidence.get('verdict'), 'valid_for_final_source': valid_final,
                              'checks': [{k: check.get(k) for k in ('name', 'status', 'exit_code', 'command', 'excerpt', 'log_sha256')}
                                         for check in evidence.get('checks', [])]})
        output['projects'].append({'project': fixture, 'tasks': task_rows,
                                   'active_memories': [m['text'] for m in memories if m['status'] == 'active'],
                                   'candidate_count': sum(m['status'] == 'candidate' for m in memories),
                                   'sqlite_hook_events': hook_events, 'tests_content_unchanged': tests_same,
                                   'source_sha256': hashlib.sha256((project / 'arithmetic.py').read_bytes()).hexdigest()})
    # Validate isolation from actual MCP return values, not the model's summary.
    beta_items = [e['params']['item'] for e in traces[1] if e['method'] == 'item/completed' and
                  e['params'].get('threadId') == final['thread_beta'] and e['params']['item'].get('type') == 'mcpToolCall']
    beta_values = {c['tool']: json.loads(c['result']['content'][0]['text']) for c in beta_items if c['status'] == 'completed'}
    output['beta_mcp_values'] = beta_values
    output['isolation_verified'] = (beta_values['nexo_status']['project'] == initial['fixtures'][1] and
                                    beta_values['nexo_status']['tasks'] == [] and
                                    'NEXO_BETA' in json.dumps(beta_values['nexo_memory_search']) and
                                    'NEXO_ALPHA' not in json.dumps(beta_values['nexo_memory_search']))
    if args.hooks_recheck:
        output['final_hook_recheck'] = json.loads((args.hooks_recheck / 'report.json').read_text(encoding='utf-8'))
        assert output['final_hook_recheck']['passed']
    else:
        assert not output['hook_failures']
    assert output['isolation_verified']
    assert len(output['mcp_successes']) == 13
    assert all(p['tests_content_unchanged'] for p in output['projects'])
    final_project = output['projects'][-1] if clean else output['projects'][0]
    assert any(t['valid_for_final_source'] for t in final_project['tasks'])
    output['verified_requirements'] = {
        'enabled_skills_per_project': [len(row['skills']) for row in output['skills']],
        'native_roles_invoked': len(output['native_roles']),
        'final_four_hook_events_passed': bool(output.get('final_hook_recheck', {}).get('passed')),
        'mcp_tools_exercised_successfully': len(output['mcp_successes']),
        'native_orchestration_complete': bool(clean and clean['passed']),
        'project_isolation': output['isolation_verified'],
    }
    output['documented_limitations'] = [
        'Nested Codex CLI could not write to its profile inside the Windows sandbox.',
        'The later mixed-mode alpha task remains in review: its earlier CLI workspace metadata was inaccessible.',
        'The separate clean native workflow passed; this does not turn either earlier attempt into a success.',
    ] if clean else []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'skills': len(output['skills'][0]['skills']),
                      'roles': len(output['native_roles']), 'hooks': output['hooks'],
                      'mcp_tools_exercised': len(output['mcp_successes']), 'isolation': True}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
