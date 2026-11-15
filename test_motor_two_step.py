"""The motor search is read-only; installation/repair is an explicit action."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QCoreApplication,QEvent
from settings_dialog import SettingsDialog


def report(update=False,healthy=True,error=''):
    return dict(ready=healthy,entries={name:dict(installed=True,healthy=healthy,
        version='9.0.2',latest='9.0.3',path='current/'+name+'.exe',
        update_available=update,update_error=error)
        for name in ('ffmpeg','ffprobe')},installed_encoders=['libx264','libx265'])


class MotorTwoStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog=SettingsDialog({},check_motors_on_open=False)

    def tearDown(self):
        self.wait(lambda:not any(task.isRunning() for task in self.dialog.tasks))
        self.dialog.reject();self.dialog.deleteLater();self.qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            self.qt.processEvents()
            if predicate():return
            time.sleep(.005)
        self.fail('Motor task did not finish')

    def search(self):
        self.dialog.check_engines()
        self.wait(lambda:not self.dialog.motor_busy)

    def test_exactly_two_actions_and_install_needs_a_search(self):
        self.assertEqual([b.text() for b in self.dialog.motor_buttons],
                         ['Buscar motores','Instalar actualizaciones o reparar'])
        self.assertFalse(self.dialog.motor_buttons[1].isEnabled())
        with patch('motors.update_motors') as update:
            self.dialog.install_or_repair_engines()
        update.assert_not_called()
        self.assertIn('Primero pulsa Buscar motores',self.dialog.status.text())

    def test_search_inspects_only_and_never_installs(self):
        with patch('motors.inspect_motors',return_value=report(True)) as inspect, \
             patch('motors.update_motors') as update,patch('motors._install_ffmpeg') as install:
            self.search()
        inspect.assert_called_once_with({'compression_only':True},online=True)
        update.assert_not_called();install.assert_not_called()
        self.assertTrue(self.dialog.motor_buttons[1].isEnabled())
        self.assertIn('requiere actualización',self.dialog.motor_status.text())

    def test_install_updates_a_reported_new_version_then_rechecks(self):
        installed=[]
        paths={name:'new/'+name+'.exe' for name in ('ffmpeg','ffprobe')}
        def inspect(*args,**kwargs):return report(not bool(installed))
        def install():installed.append(True);return paths
        with patch('motors.inspect_motors',side_effect=inspect) as inspect_mock, \
             patch('motors._install_ffmpeg',side_effect=install) as install_mock, \
             patch('motors.ensure_nvidia',return_value=[]) as nvidia:
            self.search();self.dialog.install_or_repair_engines()
            self.wait(lambda:inspect_mock.call_count>=3 and not self.dialog.motor_busy)
        install_mock.assert_called_once()
        self.assertEqual(self.dialog.values['ffmpeg'],paths['ffmpeg'])
        self.assertEqual(nvidia.call_args.args[0]['ffmpeg'],paths['ffmpeg'])
        self.assertTrue(self.dialog.motor_buttons[1].isEnabled())

    def test_install_repairs_an_unhealthy_engine(self):
        state=report(False,healthy=False)
        paths={name:'repaired/'+name+'.exe' for name in ('ffmpeg','ffprobe')}
        with patch('motors.inspect_motors',return_value=state) as inspect, \
             patch('motors._install_ffmpeg',return_value=paths) as install, \
             patch('motors.ensure_nvidia',return_value=[]):
            self.search();self.dialog.install_or_repair_engines()
            self.wait(lambda:inspect.call_count>=3 and not self.dialog.motor_busy)
        install.assert_called_once()
        self.assertEqual(self.dialog.values['ffprobe'],paths['ffprobe'])

    def test_current_or_offline_healthy_engines_are_not_reinstalled(self):
        for state in (report(False),report(None,error='sin conexión')):
            with self.subTest(state=state['entries']['ffmpeg']['update_available']), \
                 patch('motors.inspect_motors',return_value=state) as inspect, \
                 patch('motors._install_ffmpeg') as install, \
                 patch('motors.ensure_nvidia',return_value=[]) as nvidia:
                self.search();self.dialog.install_or_repair_engines()
                self.wait(lambda:inspect.call_count>=3 and not self.dialog.motor_busy)
                install.assert_not_called()
                if state['entries']['ffmpeg']['update_available'] is None:
                    self.assertFalse(nvidia.call_args.kwargs['allow_download'])
                    self.assertNotIn('Instalado · actualizado',self.dialog.motor_status.text())

    def test_failed_search_does_not_leave_an_old_install_plan_enabled(self):
        with patch('motors.inspect_motors',return_value=report(True)):
            self.search()
        with patch('motors.inspect_motors',side_effect=RuntimeError('sin conexión')):
            self.search()
        self.assertIsNone(self.dialog.motor_report)
        self.assertFalse(self.dialog.motor_buttons[1].isEnabled())
        self.assertTrue(self.dialog.motor_buttons[0].isEnabled())

    def test_active_compression_prevents_mutation(self):
        parent=QWidget();parent.active=True
        self.dialog.setParent(parent)
        self.dialog.motor_report=report(True)
        with patch('motors.update_motors') as update:
            self.dialog.install_or_repair_engines()
        update.assert_not_called()
        self.assertIn('Espera a que terminen',self.dialog.motor_status.text())
        self.dialog.setParent(None);parent.deleteLater()


if __name__=='__main__':unittest.main()
