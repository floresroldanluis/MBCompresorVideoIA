"""Decimal/binary size labels describe the same bytes and update with the limit."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent
import app


class SizeUnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for key,value in (('DATA',root),('CONFIG',root/'settings.json')):
            replacement=patch.object(app,key,value);replacement.start();self.addCleanup(replacement.stop)
        self.window=app.MainWindow()
    def tearDown(self):
        self.window._allow_close=True;self.window.close();self.window.deleteLater();self.qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    def test_limit_hint_updates_both_units(self):
        self.assertIn('1.95 GB',self.window.quality_note.text())
        self.assertIn('1.82 GiB',self.window.quality_note.text())
        self.window.limit.setValue(3.5)
        self.assertIn('3.26 GiB',self.window.quality_note.text())
        self.window.unlimited.setChecked(True)
        self.assertIn('Sin límite',self.window.quality_note.text())
        self.assertFalse(self.window.limit.isEnabled())
    def test_completed_movie_shows_same_bytes_in_gb_and_gib(self):
        item=app.Item('source.mkv',self.window.settings)
        item.info=dict(duration=11304.704,size=9578490766,width=1920,height=1080,audio=[],subtitles=[])
        self.window.items=[item];self.window.build_row(item)
        output=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=1925529102))
        with patch.object(app,'Path',return_value=output):
            self.window._apply_output_result(item,'Completado','output.mp4')
        text=self.window.rows[item.path]['detail'].text()
        for expected in ('1.93 GB','1.79 GiB','ahorro 80%','98.7% del límite'):
            self.assertIn(expected,text)
        self.assertIn('1925529102 bytes',self.window.logs.toPlainText())


if __name__=='__main__':unittest.main()
