"""Maintenance regressions: honest remote status and recoverable Windows locks."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import motors
from settings_dialog import SettingsDialog


def sharing_violation():
    error = PermissionError('archivo en uso')
    error.winerror = 32
    return error


class MaintenanceChecks(unittest.TestCase):
    def entries(self, comparison=False, error=''):
        return {name: dict(healthy=True, path='current/' + name + '.exe',
                           update_available=comparison, update_error=error)
                for name in ('ffmpeg', 'ffprobe')}

    def test_invalid_remote_version_is_an_error_for_both_tools(self):
        with patch('motors._read', return_value=b'<html>unavailable</html>'):
            versions, errors = motors._latest()
        self.assertIsNone(versions['ffprobe'])
        self.assertEqual(set(errors), {'ffmpeg', 'ffprobe'})

    def test_nvidia_uses_new_effective_paths(self):
        paths = {'ffmpeg': 'new/ffmpeg.exe', 'ffprobe': 'new/ffprobe.exe'}
        with patch('motors.ensure_nvidia', return_value=['NVIDIA verificado']) as ensure:
            result = motors._with_nvidia(paths, {'ffmpeg': 'old/ffmpeg.exe'}, None, [])
        self.assertEqual(ensure.call_args.args[0]['ffmpeg'], paths['ffmpeg'])
        self.assertEqual(result['messages'], ['NVIDIA verificado'])

    def test_offline_healthy_engines_are_kept_without_download_or_false_claim(self):
        state = {'entries': self.entries(None, 'sin conexión')}
        with patch('motors.inspect_motors', return_value=state), \
             patch('motors._install_ffmpeg') as install, \
             patch('motors.ensure_nvidia', return_value=[]) as ensure:
            result = motors.update_motors({})
        install.assert_not_called()
        self.assertFalse(ensure.call_args.kwargs['allow_download'])
        message = ' '.join(result['messages'])
        self.assertIn('No se pudo comprobar', message)
        self.assertIn('sin conexión', message)
        self.assertNotIn('no requieren actualización', message)

    def test_noncomparable_version_is_not_called_current(self):
        with patch('motors.inspect_motors', return_value={'entries': self.entries(None)}), \
             patch('motors._install_ffmpeg') as install, \
             patch('motors.ensure_nvidia', return_value=[]):
            result = motors.update_motors({})
        install.assert_not_called()
        self.assertIn('actualización no comprobada', ' '.join(result['messages']))

    def test_confirmed_current_engines_are_not_reinstalled(self):
        with patch('motors.inspect_motors', return_value={'entries': self.entries(False)}), \
             patch('motors._install_ffmpeg') as install, \
             patch('motors.ensure_nvidia', return_value=[]):
            result = motors.update_motors({})
        install.assert_not_called()
        self.assertIn('comprobados con la versión oficial', ' '.join(result['messages']))

    def test_a_broken_engine_repairs_the_pair_and_checks_new_nvidia_path(self):
        entries = self.entries(False)
        entries['ffprobe']['healthy'] = False
        paths = {'ffmpeg': 'new/ffmpeg.exe', 'ffprobe': 'new/ffprobe.exe'}
        with patch('motors.inspect_motors', return_value={'entries': entries}), \
             patch('motors._install_ffmpeg', return_value=paths) as install, \
             patch('motors.ensure_nvidia', return_value=[]) as ensure:
            result = motors.update_motors({'ffmpeg': 'old/ffmpeg.exe'})
        install.assert_called_once()
        self.assertEqual(result['ffmpeg'], paths['ffmpeg'])
        self.assertEqual(ensure.call_args.args[0]['ffmpeg'], paths['ffmpeg'])

    def test_offline_nvidia_checks_existing_engine_but_does_not_download(self):
        with patch('motors.discover', return_value='ffmpeg.exe'), \
             patch('acceleration.find_nvenc', return_value={'available': False}), \
             patch('acceleration.nvidia_info', return_value='NVIDIA'), \
             patch('acceleration.compatible_candidates', return_value=[]), \
             patch('motors._archive') as archive:
            messages = motors.ensure_nvidia(allow_download=False)
        archive.assert_not_called()
        self.assertIn('No se descargó', ' '.join(messages))

    def test_healthy_pinned_nvidia_component_is_not_reinstalled_for_driver_failure(self):
        with patch('motors.discover', return_value='ffmpeg.exe'), \
             patch('acceleration.find_nvenc', return_value={'available': False, 'error': 'controlador incompatible'}), \
             patch('acceleration.nvidia_info', return_value='NVIDIA'), \
             patch('acceleration.compatible_candidates', return_value=[str(Path('tools/nvenc/ffmpeg.exe'))]), \
             patch('motors._version', return_value=('8.0.1-essentials_build', '')), \
             patch('motors._archive') as archive:
            messages = motors.ensure_nvidia()
        archive.assert_not_called()
        self.assertIn('controlador incompatible', ' '.join(messages))
        self.assertIn('Automático usará CPU', ' '.join(messages))


class PublishChecks(unittest.TestCase):
    def files(self, root):
        destination = root / 'tools'
        staging = root / 'staging'
        destination.mkdir()
        staging.mkdir()
        staged = {}
        for name in ('ffmpeg', 'ffprobe'):
            (destination / (name + '.exe')).write_bytes(('old-' + name).encode())
            path = staging / (name + '.exe')
            path.write_bytes(('new-' + name).encode())
            staged[name] = path
        return destination, staged

    def test_transient_windows_lock_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            destination, staged = self.files(Path(folder))
            original = Path.replace
            blocked = []

            def replace(source, target):
                if source == staged['ffprobe'] and not blocked:
                    blocked.append(True)
                    raise sharing_violation()
                return original(source, target)

            with patch.object(Path, 'replace', replace), patch('motors.time.sleep') as sleep:
                motors._publish_staged(staged, destination)
            sleep.assert_called_once()
            self.assertEqual((destination / 'ffprobe.exe').read_bytes(), b'new-ffprobe')
            self.assertEqual((destination / 'ffmpeg.exe').read_bytes(), b'new-ffmpeg')

    def test_locked_second_engine_restores_first_and_names_correct_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination, staged = self.files(Path(folder))
            original = Path.replace

            def replace(source, target):
                if source == staged['ffprobe']:
                    raise sharing_violation()
                return original(source, target)

            with patch.object(Path, 'replace', replace), patch('motors.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'ffprobe.exe') as failure:
                    motors._publish_staged(staged, destination)
            self.assertIn('Se conservaron los motores anteriores', str(failure.exception))
            self.assertEqual((destination / 'ffmpeg.exe').read_bytes(), b'old-ffmpeg')
            self.assertEqual((destination / 'ffprobe.exe').read_bytes(), b'old-ffprobe')
            self.assertEqual(list(destination.glob('motor-respaldo-*')), [])

    def test_failed_rollback_keeps_original_backup_outside_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            destination, staged = self.files(Path(folder))
            original = Path.replace

            def replace(source, target):
                if source == staged['ffprobe'] or source.name == 'ffmpeg.backup':
                    raise sharing_violation()
                return original(source, target)

            with patch.object(Path, 'replace', replace), patch('motors.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'archivos de recuperación') as failure:
                    motors._publish_staged(staged, destination)
            backup = next(destination.glob('motor-respaldo-*/ffmpeg.backup'))
            self.assertEqual(backup.read_bytes(), b'old-ffmpeg')
            self.assertIn(str(backup), str(failure.exception))
            self.assertEqual((destination / 'ffprobe.exe').read_bytes(), b'old-ffprobe')


class Text:
    def setText(self, value):
        self.value = value


class DialogMaintenanceChecks(unittest.TestCase):
    def test_compression_or_ai_activity_does_not_start_maintenance(self):
        for activity in ('active', 'ai_busy'):
            with self.subTest(activity=activity):
                target = SimpleNamespace(motor_busy=False, motor_status=Text(), run_task=Mock(),
                                         parent=lambda: SimpleNamespace(**{activity: True}))
                SettingsDialog.run_motor_task(target, Mock(), Mock(), 'Comprobando')
                target.run_task.assert_not_called()
                self.assertIn('Espera a que terminen', target.motor_status.value)

    def test_result_refresh_waits_for_worker_to_finish(self):
        target = SimpleNamespace(values={}, status=Text(), motor_status=Text(),
                                 motor_refresh_pending=False)
        SettingsDialog.motors_changed(target, {'ffmpeg': 'new.exe', 'messages': ['Comprobado']})
        self.assertEqual(target.values['ffmpeg'], 'new.exe')
        self.assertTrue(target.motor_refresh_pending)


if __name__ == '__main__':
    unittest.main()
