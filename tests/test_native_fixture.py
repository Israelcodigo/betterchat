"""Regressions for native Windows fixture ACLs; never alter permissions."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from scripts.native_fixture import create_native_fixture


class NativeFixtureTests(unittest.TestCase):
    def test_collision_is_not_reused_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            collision, fresh = uuid.uuid4(), uuid.uuid4()
            occupied = parent / ('probe-' + collision.hex)
            occupied.mkdir()
            (occupied / 'marker').write_text('keep', encoding='utf-8')
            with patch('scripts.native_fixture.uuid.uuid4', side_effect=[collision, fresh]):
                result = create_native_fixture('probe-', parent)
            self.assertEqual(result, parent / ('probe-' + fresh.hex))
            self.assertEqual((occupied / 'marker').read_text(encoding='utf-8'), 'keep')
            self.assertEqual(list(result.iterdir()), [])

    def test_collision_limit_is_finite_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, identity = Path(directory), uuid.uuid4()
            occupied = parent / ('probe-' + identity.hex)
            occupied.write_text('keep', encoding='utf-8')
            with patch('scripts.native_fixture.uuid.uuid4', return_value=identity) as generator:
                with self.assertRaises(FileExistsError):
                    create_native_fixture('probe-', parent)
            self.assertEqual(generator.call_count, 8)
            self.assertEqual(occupied.read_text(encoding='utf-8'), 'keep')

    def test_rejects_prefix_traversal(self):
        for prefix in ('', '.', '..', '../outside', '..\\outside', 'C:outside'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                create_native_fixture(prefix)

    @unittest.skipUnless(os.name == 'nt', 'Windows DACL regression')
    def test_windows_directory_inherits_acl_without_protection(self):
        from ctypes import wintypes
        path = create_native_fixture('nexo-native-acl-test-')
        try:
            api = ctypes.WinDLL('advapi32', use_last_error=True)
            api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                            wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            api.GetFileSecurityW.restype = wintypes.BOOL
            api.GetSecurityDescriptorControl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD),
                                                        ctypes.POINTER(wintypes.DWORD)]
            api.GetSecurityDescriptorControl.restype = wintypes.BOOL
            needed = wintypes.DWORD()
            api.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(needed))
            self.assertGreater(needed.value, 0)
            buffer = ctypes.create_string_buffer(needed.value)
            self.assertTrue(api.GetFileSecurityW(str(path), 4, buffer, len(buffer), ctypes.byref(needed)))
            control, revision = wintypes.WORD(), wintypes.DWORD()
            self.assertTrue(api.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision)))
            self.assertFalse(control.value & 0x1000, 'Fixture must not set SE_DACL_PROTECTED')
            self.assertTrue(control.value & 0x0400, 'Fixture must inherit its parent DACL automatically')
        finally:
            path.rmdir()
