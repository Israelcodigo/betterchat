"""Evidence gating for the real implicit-learning probe, without model calls."""
import unittest
import shlex

from scripts.codex_autolearn_probe import reuse_evidence


def event(command, output='', code=0, thread='new'):
    return {'method': 'item/completed', 'params': {'threadId': thread, 'item': {
        'type': 'commandExecution', 'id': command, 'status': 'completed',
        'exitCode': code, 'command': command, 'aggregatedOutput': output}}}


class AutolearnEvidenceTests(unittest.TestCase):
    def test_native_installed_skill_read_execution_and_validation(self):
        events = [event("Get-Content '.agents/skills/csv-convert/SKILL.md'", '---\nname: csv-convert\n---'),
                  event("& 'C:\\Python\\python.exe' '.agents/skills/csv-convert/scripts/convert.py' incoming.csv converted.json"),
                  event('python verify_conversion.py incoming.csv converted.json', 'CSV_CONVERSION_VERIFIED\r\n')]
        evidence = reuse_evidence(events, 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}])
        self.assertTrue(all(evidence.values()))

    def test_rejects_old_thread_failed_execution_file_read_and_wrong_skill(self):
        commands = [event("Get-Content '.agents/skills/csv-convert/SKILL.md'", 'name: csv-convert', thread='old'),
                    event("python .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json", code=1),
                    event("Get-Content .agents/skills/csv-convert/scripts/convert.py # python incoming.csv converted.json"),
                    event("python .agents/skills/other/scripts/convert.py incoming.csv converted.json"),
                    event('python verify_conversion.py incoming.csv converted.json', 'I say CSV_CONVERSION_VERIFIED')]
        self.assertFalse(any(reuse_evidence(commands, 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}]).values()))

    def test_rejects_echo_comments_inline_python_and_other_project_absolute_path(self):
        commands = [event("Write-Output 'python .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json'"),
                    event("# python .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json"),
                    event('python -c "print(\'.agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json\')"'),
                    event('python C:/other/.agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json'),
                    event("Write-Output 'Get-Content .agents/skills/csv-convert/SKILL.md'", 'name: csv-convert')]
        self.assertFalse(any(reuse_evidence(commands, 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}]).values()))

    def test_accepts_actual_native_powershell_wrapper_and_exact_installed_absolute_path(self):
        command = ('"C:\\Native Runtime\\pwsh.exe" -Command "& \'C:\\Python\\python.exe\' -B '
                   "'C:\\fixture\\.agents\\skills\\csv-convert\\scripts\\convert.py' incoming.csv converted.json\"")
        evidence = reuse_evidence([event(command)], 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}])
        self.assertEqual(len(evidence['installed_script_executions']), 1)

    def test_does_not_count_unexecuted_control_flow_or_fake_validator_marker(self):
        commands = [event('if ($false) {\npython .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json\n}'),
                    event('exit\npython .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json'),
                    event("Write-Output 'CSV_CONVERSION_VERIFIED' # verify_conversion.py incoming.csv converted.json", 'CSV_CONVERSION_VERIFIED')]
        self.assertFalse(any(reuse_evidence(commands, 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}]).values()))

    def test_native_render_with_error_guard_and_single_quoted_command(self):
        script = ("& 'C:\\Python\\python.exe' -B .agents/skills/csv-convert/scripts/convert.py incoming.csv converted.json\n"
                  'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n'
                  "& 'C:\\Python\\python.exe' -B verify_conversion.py incoming.csv converted.json")
        events = [event(shlex.join(['C:\\Runtime\\pwsh.exe', '-Command', script]), 'CSV_CONVERSION_VERIFIED'),
                  event(shlex.join(['C:\\Runtime\\pwsh.exe', '-Command',
                                   'Get-Content -Raw .agents/skills/csv-convert/SKILL.md']), 'name: csv-convert')]
        self.assertTrue(all(reuse_evidence(events, 'new', [{'name': 'csv-convert', 'path': 'C:/fixture/.agents/skills/csv-convert'}]).values()))
