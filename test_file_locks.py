"""Read-only lock diagnostics, including an isolated Windows file-handle fixture."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import file_locks


class FileLockTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.RmStartSession.return_value = 0
        self.api.RmRegisterResources.return_value = 0
        self.api.RmGetList.return_value = 0
        self.api.RmEndSession.return_value = 0

    def describe(self, paths=('pending.mp4', 'final.mp4')):
        with patch.object(file_locks.os, 'name', 'nt'), \
                patch.object(file_locks, '_load_api', return_value=self.api):
            return file_locks.describe_locking_processes(paths)

    def test_non_windows_does_not_load_native_library(self):
        with patch.object(file_locks.os, 'name', 'posix'), \
                patch.object(file_locks, '_load_api') as loader:
            self.assertEqual(file_locks.describe_locking_processes(['pending.mp4']), '')
        loader.assert_not_called()

    def test_success_registers_both_paths_and_closes_session(self):
        def get_list(session, needed, count, processes, reasons):
            if processes is None:
                needed._obj.value = 2
                return file_locks.ERROR_MORE_DATA
            count._obj.value = 2
            processes[0].Process.dwProcessId = 123
            processes[0].strAppName = 'Vídeo\nPlayer\t\ud800'
            processes[1].Process.dwProcessId = 456
            processes[1].strServiceShortName = 'Scanner'
            return 0
        self.api.RmGetList.side_effect = get_list
        message = self.describe(('pending.mp4', 'final.mp4', 'pending.mp4'))
        self.assertEqual(message, 'Procesos que usan los archivos: Vídeo Player (PID 123); Scanner (PID 456).')
        registration = self.api.RmRegisterResources.call_args.args
        self.assertEqual(registration[1], 2)
        self.assertEqual(list(registration[2]), [os.path.abspath('pending.mp4'), os.path.abspath('final.mp4')])
        self.assertEqual(self.api.RmGetList.call_count, 2)
        self.api.RmEndSession.assert_called_once()
        self.assertFalse(self.api.RmShutdown.called)

    def test_native_structure_matches_windows_header(self):
        # Microsoft WinSDK header, not values inferred from this implementation:
        # https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/RestartManager.h
        self.assertEqual(file_locks.CCH_RM_MAX_APP_NAME, 255)
        self.assertEqual(file_locks.CCH_RM_MAX_SVC_NAME, 63)
        self.assertEqual(ctypes.sizeof(file_locks.FILETIME), 8)
        self.assertEqual(ctypes.sizeof(file_locks.RM_UNIQUE_PROCESS), 12)
        if os.name == 'nt':
            self.assertEqual(ctypes.sizeof(file_locks.RM_PROCESS_INFO), 668)
            self.assertEqual(file_locks.RM_PROCESS_INFO.strServiceShortName.offset, 524)
            self.assertEqual(file_locks.RM_PROCESS_INFO.ApplicationType.offset, 652)
            self.assertEqual(file_locks.RM_PROCESS_INFO.bRestartable.offset, 664)

    def test_start_failure_does_not_end_unopened_session(self):
        self.api.RmStartSession.return_value = 353
        self.assertIn('código 353', self.describe())
        self.api.RmRegisterResources.assert_not_called()
        self.api.RmEndSession.assert_not_called()

    def test_registration_access_failure_still_closes_session(self):
        self.api.RmRegisterResources.return_value = 5
        self.assertIn('código 5', self.describe())
        self.api.RmGetList.assert_not_called()
        self.api.RmEndSession.assert_called_once()

    def test_list_failure_and_exception_still_close_session(self):
        for value in (5, OSError('private fixture path')):
            with self.subTest(value=type(value).__name__):
                self.api.RmEndSession.reset_mock()
                self.api.RmGetList.side_effect = value if isinstance(value, Exception) else None
                self.api.RmGetList.return_value = value
                message = self.describe()
                self.assertIn('No se pudo identificar', message)
                self.assertNotIn('private fixture', message)
                self.api.RmEndSession.assert_called_once()

    def test_list_growth_is_retried_only_once(self):
        def growing(session, needed, count, processes, reasons):
            needed._obj.value = 2 if processes is None else 3
            return file_locks.ERROR_MORE_DATA
        self.api.RmGetList.side_effect = growing
        self.assertIn('código 234', self.describe())
        self.assertEqual(self.api.RmGetList.call_count, 2)
        self.api.RmEndSession.assert_called_once()

    def test_no_results_explains_diagnostic_limit(self):
        self.assertIn('no estar visible', self.describe())
        self.api.RmEndSession.assert_called_once()

    def test_missing_library_and_session_cleanup_failure_are_nonfatal(self):
        with patch.object(file_locks.os, 'name', 'nt'), \
                patch.object(file_locks, '_load_api', side_effect=OSError()):
            self.assertIn('No se pudo identificar', file_locks.describe_locking_processes(['file.mp4']))
        self.api.RmEndSession.side_effect = OSError()
        self.assertIn('no estar visible', self.describe())

    @unittest.skipUnless(os.name == 'nt', 'Windows Restart Manager integration')
    def test_real_two_child_handles_are_reported_without_closing_children(self):
        child_code = '''
import ctypes, os, sys
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                          ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
k.CreateFileW.restype = ctypes.c_void_p
k.CloseHandle.argtypes = [ctypes.c_void_p]
h = k.CreateFileW(sys.argv[1], 0x80000000, 3, None, 3, 0x80, None)
if h == ctypes.c_void_p(-1).value:
    print('unavailable', flush=True)
else:
    print('ready ' + str(os.getpid()), flush=True)
    try: sys.stdin.readline()
    finally: k.CloseHandle(h)
'''
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            children, handle_pids, paths = [], [], []
            try:
                for index in range(2):
                    path = Path(folder) / f'held-{index}.mp4'
                    path.write_bytes(b'isolated MP4 fixture')
                    paths.append(path)
                    child = subprocess.Popen([sys.executable, '-c', child_code, str(path)],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, text=True,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                    children.append(child)
                    ready = child.stdout.readline().strip()
                    if not ready.startswith('ready '):
                        self.skipTest('Windows could not open the isolated fixture handle')
                    # The Windows venv launcher PID can differ from its Python child.
                    handle_pids.append(int(ready.split()[1]))
                message = file_locks.describe_locking_processes(paths)
                for child in children:
                    self.assertIsNone(child.poll(), 'The diagnostic must leave both children running')
                if message.startswith(('No se pudo identificar', 'Windows no identificó')):
                    self.skipTest('Restart Manager cannot report this isolated handle with current permissions')
                self.assertEqual(len(set(handle_pids)), 2)
                for pid in handle_pids:
                    self.assertIn(f'(PID {pid})', message)
            finally:
                for child in children:
                    child.communicate(input='release\n', timeout=10)


if __name__ == '__main__':
    unittest.main()
