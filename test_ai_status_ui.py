"""The settings panel reports actual model checks and inference results."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent
from settings_dialog import SettingsDialog


class AIStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):self.dialog=SettingsDialog({'ollama_model':'qwen3:0.6b'},check_motors_on_open=False)
    def wait(self):
        deadline=time.monotonic()+5
        while self.dialog.ai_busy and time.monotonic()<deadline:
            self.qt.processEvents();time.sleep(.005)
        self.assertFalse(self.dialog.ai_busy)
    def tearDown(self):
        self.wait();self.dialog.reject();self.dialog.deleteLater();self.qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    def test_search_reports_installed_service_and_models_without_inference(self):
        with patch('local_ai.OllamaClient.status',return_value=dict(version='0.40.16',models=['qwen3:0.6b'],local_only=True)),patch('local_ai.OllamaClient.test_connection') as infer:
            self.dialog.refresh_models();self.wait();infer.assert_not_called()
        self.assertIn('instalado y disponible',self.dialog.ai_status.text())
        self.assertEqual(self.dialog.fields['ollama_model'].currentText(),'qwen3:0.6b')
    def test_explicit_test_reports_real_inference_result(self):
        with patch('local_ai.OllamaClient.test_connection',return_value=dict(version='0.40.16',model='qwen3:0.6b',verified=True,elapsed_seconds=1.5)) as infer:
            self.dialog.test_local_ai();self.wait()
        infer.assert_called_once_with('qwen3:0.6b')
        self.assertIn('instalado y probado',self.dialog.ai_status.text())
    def test_failure_does_not_claim_the_model_is_ready(self):
        with patch('local_ai.OllamaClient.test_connection',side_effect=RuntimeError('offline local service unavailable')):
            self.dialog.test_local_ai();self.wait()
        self.assertIn('no disponible',self.dialog.ai_status.text())
        self.assertNotIn('instalado y probado',self.dialog.ai_status.text())


if __name__=='__main__':unittest.main()
