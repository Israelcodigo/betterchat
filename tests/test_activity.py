"""Metadata-only telemetry: conservative evidence, isolation and hook resilience."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nexo_kit.activity import observe_hook, read_activity, record_usage, register_project, registered_projects
from nexo_kit.hooks import handle_hook


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-activity-')
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'profile'
        self.project = self.root / 'project'
        self.project.mkdir()
        self.environment = patch.dict(os.environ, {'CODEX_HOME': str(self.home), 'CODEX_THREAD_ID': '',
                                                    'CODEX_SESSION_ID': ''})
        self.environment.start()
        self.skill = self.home / 'skills/nexo-example'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('Skill instructions', encoding='utf-8')
        (self.skill / 'scripts').mkdir()
        (self.skill / 'scripts/check.py').write_text('print("ok")', encoding='utf-8')
        self.role = self.home / 'agents/nexo-reviewer.toml'
        self.role.parent.mkdir(parents=True)
        self.role.write_text('name = "nexo-reviewer"\ndeveloper_instructions = "Review evidence."\n', encoding='utf-8')

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def tool(self, command=None, response=None, name='functions.exec_command', **arguments):
        if command is not None:
            arguments['command'] = command
        payload = {'session_id': 'session-1', 'tool_name': name, 'tool_input': arguments,
                   'tool_response': response, 'prompt': 'private-prompt-marker',
                   'transcript_path': 'private-transcript-marker'}
        self.assertTrue(observe_hook(self.project, 'PostToolUse', payload))
        return read_activity()['events']

    def skill_event(self, **arguments):
        events = self.tool(**arguments)
        return next(event for event in events if event['kind'] == 'skill')

    def test_readers_do_not_create_profile_or_project_state(self):
        empty_home = self.root / 'does-not-exist'
        self.assertEqual(registered_projects(empty_home), [])
        self.assertEqual(read_activity(empty_home), {'events': [], 'sessions': [], 'latest_reports': []})
        self.assertFalse(empty_home.exists())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_registration_respects_profile_and_does_not_initialize_project(self):
        value = register_project(self.project)
        self.assertEqual(value['path'], str(self.project))
        self.assertEqual(registered_projects()[0]['name'], 'project')
        self.assertTrue((self.home / 'nexo-kit/activity.sqlite3').exists())
        self.assertFalse((self.project / '.nexo').exists())

    def test_hooks_preserve_original_return_and_project_inertness(self):
        with patch.dict(os.environ, {'NEXO_WORKER': '0'}):
            output = handle_hook(self.project, 'SessionStart', {'session_id': 'native-session'})
            self.assertIn('no Nexo state', output['hookSpecificOutput']['additionalContext'])
            self.assertEqual(handle_hook(self.project, 'Stop', {'session_id': 'native-session'}), {})
        self.assertEqual(read_activity()['sessions'][0]['status'], 'stopped')
        self.assertFalse((self.project / '.nexo').exists())

    def test_hook_preserves_output_when_telemetry_raises(self):
        with patch.dict(os.environ, {'NEXO_WORKER': '0'}), \
                patch('nexo_kit.hooks.observe_hook', side_effect=RuntimeError('storage fault')):
            self.assertEqual(handle_hook(self.project, 'Stop', {}), {})
            self.assertIn('hookSpecificOutput', handle_hook(self.project, 'SessionStart', {}))

    def test_storage_errors_are_nonfatal_and_do_not_touch_project(self):
        broken_home = self.root / 'not-a-directory'
        broken_home.write_text('preserve', encoding='utf-8')
        self.assertFalse(observe_hook(self.project, 'SessionStart', {}, broken_home))
        self.assertIn('error', register_project(self.project, broken_home))
        self.assertEqual(registered_projects(broken_home), [])
        self.assertFalse((self.project / '.nexo').exists())
        (self.home / 'nexo-kit').mkdir()
        (self.home / 'nexo-kit/activity.sqlite3').write_text('corrupt', encoding='utf-8')
        self.assertIn('error', read_activity())
        self.assertFalse(observe_hook(self.project, 'Stop', {}))

    def test_concurrent_hook_writers_keep_all_observations(self):
        def write(number):
            return observe_hook(self.project, 'SessionStart', {'session_id': f'session-{number}'})
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(write, range(40)))
        self.assertTrue(all(results))
        activity = read_activity(limit=100)
        self.assertEqual(len(activity['events']), 40)
        self.assertEqual(len(activity['sessions']), 40)
        self.assertEqual(len(registered_projects()), 1)

    def test_session_freshness_stop_and_new_turn_activity_are_observed(self):
        with patch('nexo_kit.activity._now', return_value='2026-10-10T10:00:00+00:00'):
            self.tool(name='Read')
            self.assertEqual(read_activity()['sessions'][0]['status'], 'recent')
        with patch('nexo_kit.activity._now', return_value='2026-10-10T10:02:01+00:00'):
            self.assertEqual(read_activity()['sessions'][0]['status'], 'stale')
            observe_hook(self.project, 'Stop', {'session_id': 'session-1'})
            self.assertEqual(read_activity()['sessions'][0]['status'], 'stopped')
        with patch('nexo_kit.activity._now', return_value='2026-10-10T11:00:00+00:00'):
            self.assertEqual(read_activity()['sessions'][0]['status'], 'stopped')
            self.tool(name='Read')
            self.assertEqual(read_activity()['sessions'][0]['status'], 'recent')

    def test_sessions_are_project_scoped_and_missing_ids_are_not_invented(self):
        observe_hook(self.project, 'SessionStart', {})
        self.assertEqual(read_activity()['sessions'], [])
        for project in (self.project, self.root / 'second'):
            observe_hook(project, 'SessionStart', {'session_id': 'same-client-id'})
        self.assertEqual(len(read_activity()['sessions']), 2)

    def test_successful_shell_read_and_script_execution_are_distinct(self):
        loaded = self.skill_event(command=f"Get-Content -Raw '{self.skill / 'SKILL.md'}'",
                                  response={'exit_code': 0, 'output': 'private-output-marker'})
        self.assertEqual(loaded['status'], 'loaded')
        executed = self.skill_event(command=f"& 'python.exe' '{self.skill / 'scripts/check.py'}'",
                                    response={'exit_code': 0})
        self.assertEqual(executed['status'], 'executed')

    def test_native_read_requires_observed_content_or_success(self):
        path = str(self.skill / 'SKILL.md')
        event = self.skill_event(name='Read', file_path=path,
                                response={'file': {'filePath': path, 'content': 'Skill content'}})
        self.assertEqual(event['status'], 'loaded')
        event = self.skill_event(name='Read', file_path=str(self.skill / 'SKILL.md'), response=None)
        self.assertEqual(event['status'], 'referenced')
        event = self.skill_event(name='Read', file_path=path, response='Error reading file')
        self.assertEqual(event['status'], 'referenced')

    def test_echo_and_ambiguous_shell_commands_cannot_claim_skill_use(self):
        path = self.skill / 'SKILL.md'
        script = self.skill / 'scripts/check.py'
        for command in (f"echo '{path}'", f"Write-Output '{script}'",
                        f"Get-Content '{path}'; echo done", f"cat --help '{path}'",
                        f"python --version '{script}'", f"python -c '{script}'"):
            with self.subTest(command=command):
                event = self.skill_event(command=command, response={'exit_code': 0})
                self.assertEqual(event['status'], 'referenced')

    def test_missing_exit_code_is_honest_and_failed_read_is_not_loaded(self):
        command = f"Get-Content -Raw '{self.skill / 'SKILL.md'}'"
        event = self.skill_event(command=command, response={'output': 'some text'})
        self.assertEqual(event['status'], 'referenced')
        event = self.skill_event(command=command, response={'exit_code': 1, 'output': 'missing file'})
        self.assertEqual(event['status'], 'failed')
        event = self.skill_event(command=command, response=json.dumps({'result': {'exit_code': 0}}))
        self.assertEqual(event['status'], 'loaded')

    def test_role_reference_is_not_spawn_and_observed_completion_is_correlated(self):
        events = self.tool(name='collaboration.spawn_agent', agent_type='nexo-reviewer',
                           message='private-agent-prompt', response=None)
        role = next(event for event in events if event['kind'] == 'agent')
        self.assertEqual(role['status'], 'referenced')
        events = self.tool(name='collaboration.spawn_agent', agent_type='nexo-reviewer',
                           response={'agent_id': 'agent-1'})
        role = next(event for event in events if event['kind'] == 'agent')
        self.assertEqual(role['status'], 'spawned')
        self.assertEqual(role['agent_id'], 'agent-1')
        events = self.tool(name='collaboration.wait', response={
            'status': {'agent-1': {'completed': 'private-completion-output'}}})
        role = next(event for event in events if event['kind'] == 'agent')
        self.assertEqual(role['status'], 'completed')
        self.assertEqual(role['name'], 'nexo-reviewer')

    def test_storage_never_keeps_prompts_commands_outputs_or_transcripts(self):
        command = f"Get-Content '{self.skill / 'SKILL.md'}' # private-command-marker"
        self.tool(command=command, response={'exit_code': 0, 'output': 'private-output-marker'},
                  message='private-agent-prompt')
        connection = sqlite3.connect(self.home / 'nexo-kit/activity.sqlite3')
        try:
            stored = '\n'.join(connection.iterdump())
        finally:
            connection.close()
        for marker in ('private-command-marker', 'private-output-marker', 'private-prompt-marker',
                       'private-transcript-marker', 'private-agent-prompt'):
            self.assertNotIn(marker, stored)

    def test_recent_reads_do_not_mutate_database(self):
        self.tool(name='Read')
        database = self.home / 'nexo-kit/activity.sqlite3'
        before = database.read_bytes()
        self.assertEqual(len(read_activity(limit=1)['events']), 1)
        registered_projects()
        self.assertEqual(database.read_bytes(), before)

    def test_explicit_usage_reports_have_distinct_source_and_no_native_session_claim(self):
        for status in ('started', 'completed'):
            result = record_usage(self.project, 'skill', 'nexo-example', status, 'session-1')
            self.assertTrue(result['recorded'])
            self.assertEqual(result['event']['source'], 'agent-report')
            self.assertEqual(result['event']['status'], status)
            self.assertEqual(result['event']['path'], str(self.skill / 'SKILL.md'))
        activity = read_activity()
        self.assertEqual(len(activity['events']), 2)
        self.assertEqual(activity['sessions'], [])
        self.assertFalse((self.project / '.nexo').exists())

    def test_explicit_report_requires_nexo_or_known_local_skill_and_identifiers_only(self):
        local = self.project / '.agents/skills/local-check/SKILL.md'
        local.parent.mkdir(parents=True)
        local.write_text('local skill', encoding='utf-8')
        self.assertTrue(record_usage(self.project, 'skill', 'local-check', 'started')['recorded'])
        self.assertTrue(record_usage(self.project, 'agent', 'nexo-reviewer', 'started')['recorded'])
        for values in (('skill', 'unknown-skill', 'started', ''),
                       ('agent', 'local-check', 'started', ''),
                       ('skill', '../private-prompt', 'started', ''),
                       ('skill', 'nexo-build private-prompt', 'started', ''),
                       ('skill', 'nexo-build', 'loaded', ''),
                       ('command', 'nexo-build', 'started', ''),
                       ('skill', 'nexo-build', 'started', 'private prompt text')):
            with self.subTest(values=values), self.assertRaises(ValueError):
                record_usage(self.project, *values)

    def test_latest_report_survives_visible_history_limit_and_completion_replaces_it(self):
        started = record_usage(self.project, 'skill', 'nexo-example', 'started', 'session-1')['event']
        for _ in range(220):
            self.assertTrue(observe_hook(self.project, 'PostToolUse', {
                'session_id': 'session-1', 'tool_name': 'Read'}))
        activity = read_activity()
        self.assertEqual(len(activity['events']), 200)
        self.assertNotIn(started['id'], [event['id'] for event in activity['events']])
        self.assertEqual(activity['sessions'][0]['status'], 'recent')
        self.assertEqual(activity['latest_reports'], [started])
        completed = record_usage(self.project, 'skill', 'nexo-example', 'completed', 'session-1')['event']
        self.assertEqual(read_activity(limit=0)['latest_reports'], [completed])
        self.assertEqual(completed['source'], 'agent-report')

    def test_real_desktop_spawn_shape_and_known_namespaced_aliases(self):
        # Sanitized shape from the real Naval audit: input agent_type and
        # response {"task_name":"/root/codigo"}; hook name is flattened.
        for spelling in ('spawn_agent', 'collaboration.spawn_agent', 'collaboration:spawn_agent',
                         'collaboration__spawn_agent', 'collaborationspawn_agent',
                         'functions.spawn_agent', 'functions__spawn_agent'):
            with self.subTest(spelling=spelling):
                events = self.tool(name=spelling, agent_type='nexo-reviewer',
                                   task_name='codigo', response={'task_name': '/root/codigo'})
                event = events[0]
                self.assertEqual(event['kind'], 'agent')
                self.assertEqual(event['status'], 'spawned')
                self.assertEqual(event['agent_id'], '/root/codigo')
                self.assertEqual(event['session'], 'session-1')
                self.assertEqual(event['path'], str(self.role))

    def test_unknown_namespaces_and_requested_names_do_not_prove_spawn(self):
        for spelling in ('untrusted.spawn_agent', 'notcollaborationspawn_agent',
                         'collaboration.spawn_agent_extra', 'custom__spawn_agent'):
            events = self.tool(name=spelling, agent_type='nexo-reviewer',
                               response={'task_name': '/root/codigo'})
            self.assertFalse(any(event['kind'] == 'agent' for event in events))
        events = self.tool(name='collaborationspawn_agent', agent_type='nexo-reviewer',
                           task_name='/root/requested-only', response={})
        self.assertEqual(events[0]['status'], 'referenced')
        self.assertEqual(events[0]['agent_id'], '')
        events = self.tool(name='collaborationspawn_agent', agent_type='nexo-reviewer',
                           response={'task_name': '/root/failed', 'isError': True})
        self.assertEqual(events[0]['status'], 'failed')

    def test_same_agent_names_in_concurrent_sessions_never_cross_correlate(self):
        def spawn(number):
            return observe_hook(self.project, 'PostToolUse', {
                'session_id': f'chat-{number}', 'tool_name': 'collaborationspawn_agent',
                'tool_input': {'agent_type': f'nexo-role-{number}'},
                'tool_response': {'task_name': '/root/reviewer'}})
        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertTrue(all(executor.map(spawn, range(12))))
        for number in range(12):
            self.assertTrue(observe_hook(self.project, 'PostToolUse', {
                'session_id': f'chat-{number}', 'tool_name': 'collaboration__wait',
                'tool_response': {'status': {'/root/reviewer': {'completed': 'private-output'}}}}))
            event = read_activity()['events'][0]
            self.assertEqual(event['name'], f'nexo-role-{number}')
            self.assertEqual(event['session'], f'chat-{number}')
        self.assertTrue(observe_hook(self.project, 'PostToolUse', {
            'tool_name': 'collaboration:wait',
            'tool_response': {'status': {'/root/reviewer': {'completed': 'private-output'}}}}))
        self.assertEqual(read_activity()['events'][0]['name'], 'agent')

    def test_wait_timeout_cannot_complete_an_agent_and_explicit_target_can(self):
        self.tool(name='collaborationspawn_agent', agent_type='nexo-reviewer',
                  response={'task_name': '/root/codigo'})
        before = len([event for event in read_activity()['events'] if event['kind'] == 'agent'])
        events = self.tool(name='collaborationwait_agent', response={'timed_out': True, 'status': {}})
        self.assertEqual(len([event for event in events if event['kind'] == 'agent']), before)
        events = self.tool(name='collaborationinterrupt_agent', target='/root/codigo',
                           response={'status': 'interrupted'})
        self.assertEqual(events[0]['name'], 'nexo-reviewer')
        self.assertEqual(events[0]['status'], 'interrupted')

    def test_report_uses_current_thread_env_and_explicit_id_takes_precedence(self):
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'actual-child-thread', 'CODEX_SESSION_ID': 'inherited-parent'}):
            event = record_usage(self.project, 'skill', 'nexo-example', 'started')['event']
            self.assertEqual(event['session'], 'actual-child-thread')
            self.assertEqual(event['source'], 'agent-report')
            explicit = record_usage(self.project, 'skill', 'nexo-example', 'completed', 'explicit-real-id')['event']
            self.assertEqual(explicit['session'], 'explicit-real-id')
        self.assertEqual(read_activity()['sessions'], [])

    def test_missing_or_invalid_thread_env_does_not_guess_from_parent_or_recent_hooks(self):
        observe_hook(self.project, 'SessionStart', {'session_id': 'recent-unrelated-chat'})
        for value in ('', 'untrusted prose\nnot an id'):
            with patch.dict(os.environ, {'CODEX_THREAD_ID': value, 'CODEX_SESSION_ID': 'inherited-parent'}):
                event = record_usage(self.project, 'skill', 'nexo-example', 'started')['event']
                self.assertEqual(event['session'], '')

    def test_session_start_provides_exact_payload_id_only_when_environment_missing(self):
        with patch.dict(os.environ, {'NEXO_WORKER': '0', 'CODEX_THREAD_ID': ''}):
            result = handle_hook(self.project, 'SessionStart', {'sessionId': 'real-client-chat'})
            self.assertIn('Nexo activity session for this chat: real-client-chat.',
                          result['hookSpecificOutput']['additionalContext'])
            result = handle_hook(self.project, 'SessionStart', {'session_id': 'private prompt\ntext'})
            self.assertNotIn('Nexo activity session', result['hookSpecificOutput']['additionalContext'])
        with patch.dict(os.environ, {'NEXO_WORKER': '0', 'CODEX_THREAD_ID': 'environment-chat'}):
            result = handle_hook(self.project, 'SessionStart', {'session_id': 'real-client-chat'})
            self.assertNotIn('Nexo activity session', result['hookSpecificOutput']['additionalContext'])

    def test_reports_require_real_roles_and_skills_not_a_nexo_prefix(self):
        for kind, name in (('skill', 'nexo-does-not-exist'), ('agent', 'nexo-does-not-exist'),
                           ('agent', 'nexo-example'), ('skill', 'nexo-reviewer')):
            with self.subTest(kind=kind, name=name), self.assertRaises(ValueError):
                record_usage(self.project, kind, name, 'started')
        event = record_usage(self.project, 'agent', 'nexo-reviewer', 'started')['event']
        self.assertEqual(event['path'], str(self.role))
        self.role.write_text('name = "different-name"\ndeveloper_instructions = "Review."', encoding='utf-8')
        with self.assertRaises(ValueError):
            record_usage(self.project, 'agent', 'nexo-reviewer', 'started')

    def test_project_skill_and_role_shadow_same_name_personal_assets(self):
        skill = self.project / '.agents/skills/nexo-example/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('Project-specific instructions', encoding='utf-8')
        role = self.project / '.codex/agents/nexo-reviewer.toml'
        role.parent.mkdir(parents=True)
        role.write_text(self.role.read_text(encoding='utf-8'), encoding='utf-8')
        self.assertEqual(record_usage(self.project, 'skill', 'nexo-example', 'started')['event']['path'], str(skill))
        self.assertEqual(record_usage(self.project, 'agent', 'nexo-reviewer', 'started')['event']['path'], str(role))

    def test_deleted_and_symlink_skills_cannot_be_reported_as_installed(self):
        deleted = self.skill / 'SKILL.md'
        deleted.unlink()
        with self.assertRaises(ValueError):
            record_usage(self.project, 'skill', 'nexo-example', 'started')
        events = self.tool(name='Read', file_path=str(deleted), response={'success': True})
        self.assertFalse(any(event['kind'] == 'skill' for event in events))
        target = self.root / 'external.md'
        target.write_text('Unrelated instructions', encoding='utf-8')
        try:
            deleted.symlink_to(target)
        except OSError:
            return  # Link creation is unavailable on some Windows hosts.
        with self.assertRaises(ValueError):
            record_usage(self.project, 'skill', 'nexo-example', 'started')


if __name__ == '__main__':
    unittest.main()
