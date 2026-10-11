"""Deterministic demo worker; no model, network or external dependency."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

task = os.environ['NEXO_TASK_ID']
prompt = sys.stdin.read()
Path('tests').mkdir(exist_ok=True)
if task == 'arithmetic':
    # First proposal intentionally fails. Nexo must pass the diagnostic to repair.
    operator = '+' if 'Previous failure to investigate:' in prompt else '-'
    Path('arithmetic.py').write_text(f'def add(a, b):\n    return a {operator} b\n', encoding='utf-8')
    Path('tests/test_arithmetic.py').write_text('''import unittest
from arithmetic import add
class AdditionTests(unittest.TestCase):
    def test_positive(self): self.assertEqual(add(2, 3), 5)
    def test_negative(self): self.assertEqual(add(-2, -3), -5)
''', encoding='utf-8')
elif task == 'formatting':
    Path('formatting.py').write_text('def label(value):\n    return f"Result: {value}"\n', encoding='utf-8')
    Path('tests/test_formatting.py').write_text('''import unittest
from formatting import label
class FormatTests(unittest.TestCase):
    def test_label(self): self.assertEqual(label(5), 'Result: 5')
''', encoding='utf-8')
elif task == 'application':
    # These imports prove both prerequisite commits reached this worktree.
    from arithmetic import add
    from formatting import label
    assert label(add(2, 3)) == 'Result: 5'
    Path('application.py').write_text('''from arithmetic import add
from formatting import label
def calculate(a, b):
    return label(add(a, b))
''', encoding='utf-8')
    Path('tests/test_application.py').write_text('''import unittest
from application import calculate
class ApplicationTests(unittest.TestCase):
    def test_composition(self): self.assertEqual(calculate(20, 22), 'Result: 42')
''', encoding='utf-8')
else:
    raise SystemExit('Unknown demo task')
print('Implemented demo task:', task)
