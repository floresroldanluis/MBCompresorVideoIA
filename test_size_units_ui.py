"""The entered limit and final output use the same size measure as Windows."""
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
        self.assertIn('según Windows (GiB)',self.window.quality_note.text())
        self.window.limit.setValue(3.5)
        self.assertIn('3.5 GB según Windows',self.window.quality_note.text())
        self.window.unlimited.setChecked(True)
        self.assertIn('Sin límite',self.window.quality_note.text())
        self.assertFalse(self.window.limit.isEnabled())
    def test_previous_completed_movie_uses_windows_size_and_new_maximum(self):
        item=app.Item('source.mkv',self.window.settings)
        item.info=dict(duration=11304.704,size=9578490766,width=1920,height=1080,audio=[],subtitles=[])
        self.window.items=[item];self.window.build_row(item)
        output=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=1925529102))
        with patch.object(app,'Path',return_value=output):
            self.window._apply_output_result(item,'Completado','output.mp4')
        text=self.window.rows[item.path]['detail'].text()
        for expected in ('1.79 GB (Windows)','ahorro 80%','92.0% del límite'):
            self.assertIn(expected,text)
        self.assertIn('1925529102 bytes',self.window.logs.toPlainText())

    def test_output_near_the_limit_shows_the_same_size_as_windows(self):
        item=app.Item('new.mkv',self.window.settings)
        item.info=dict(duration=11304.704,size=9578490766,width=1920,height=1080,audio=[],subtitles=[])
        self.window.items=[item];self.window.build_row(item)
        output=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=2090000000))
        with patch.object(app,'Path',return_value=output):
            self.window._apply_output_result(item,'Completado','new.mp4')
        self.assertIn('1.95 GB (Windows)',self.window.rows[item.path]['detail'].text())
        self.assertIn('99.8% del límite',self.window.rows[item.path]['detail'].text())


if __name__=='__main__':unittest.main()
