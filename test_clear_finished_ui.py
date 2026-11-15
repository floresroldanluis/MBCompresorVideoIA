"""Clearing finished rows also clears the idle summary, without interrupting work."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
import app


class ClearFinishedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        for key,value in (('DATA',self.root/'state'),('CONFIG',self.root/'state/settings.json')):
            replacement=patch.object(app,key,value);replacement.start();self.addCleanup(replacement.stop)
        self.window=app.MainWindow();self.window.show();self.qt.processEvents()

    def tearDown(self):
        self.window.active=False;self.window.saving=False;self.window.ai_busy=False
        self.window._allow_close=True;self.window.close();self.window.deleteLater()
        self.qt.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def add(self,name,state):
        source=self.root/name;source.write_bytes(b'original remains')
        item=app.Item(str(source),self.window.settings)
        item.info=dict(duration=10,width=320,height=240,size=1000,audio=[],subtitles=[])
        item.state=state
        self.window.items.append(item);self.window.build_row(item);self.window.refresh_row(item)
        self.window.empty.hide();self.window.update_title()
        return item

    def completed_summary(self,items):
        w=self.window
        w._run_paths=[item.path for item in items];w._run_processed=set(w._run_paths)
        w._current_path=None;w._current_percent=100.;w.total_count=len(items);w.completed_count=len(items)
        w.total_progress.setValue(100);w.status.setText(f'Cola finalizada · {len(items)} procesados.')
        w.paused=True;w.disk_paused=True;w.stop_event.set();w.pause_btn.setText('Reanudar')

    def assert_ready(self):
        w=self.window
        self.assertEqual(w.status.text(),'Listo para comprimir.');self.assertEqual(w.total_progress.value(),0)
        self.assertEqual((w.completed_count,w.total_count),(0,0));self.assertEqual(w._run_paths,[])
        self.assertEqual(w._run_processed,set());self.assertIsNone(w._current_path)
        self.assertEqual(w._current_percent,0.);self.assertIsNone(w.control)
        self.assertFalse(w.paused);self.assertFalse(w.disk_paused);self.assertFalse(w.stop_event.is_set())
        self.assertEqual(w.pause_btn.text(),'Pausar');self.assertFalse(w.pause_btn.isEnabled())
        self.assertFalse(w.stop_btn.isEnabled());self.assertTrue(w.start_btn.isEnabled())

    def test_clearing_all_finished_rows_resets_summary_and_keeps_files(self):
        item=self.add('finished.mkv','Completado');self.completed_summary([item])
        output=self.root/'finished.mp4';output.write_bytes(b'compressed video remains');item.output=str(output)
        self.window.clear_finished()
        self.assertEqual(self.window.items,[]);self.assertEqual(self.window.rows,{})
        self.assertFalse(self.window.empty.isHidden());self.assertIn('0 VIDEOS',self.window.queue_title.text())
        self.assertEqual(Path(item.path).read_bytes(),b'original remains');self.assert_ready()
        self.assertEqual(output.read_bytes(),b'compressed video remains')

    def test_pending_rows_remain_ready_after_finished_rows_are_cleared(self):
        completed=self.add('finished.mkv','Completado');pending=self.add('pending.mkv','Pendiente')
        self.completed_summary([completed]);self.window.clear_finished()
        self.assertEqual(self.window.items,[pending]);self.assertEqual(pending.state,'Pendiente')
        self.assertTrue(self.window.empty.isHidden());self.assert_ready()

    def test_stale_summary_resets_even_when_no_finished_rows_remain(self):
        failed=self.add('failed.mkv','Error');self.completed_summary([])
        self.window.completed_count=1;self.window.total_count=1
        self.window.status.setText('Cola finalizada · 1 procesados.')
        self.window.clear_finished()
        self.assertEqual(self.window.items,[failed]);self.assertEqual(failed.state,'Error');self.assert_ready()

    def test_active_compression_keeps_current_progress_and_control(self):
        completed=self.add('finished.mkv','Completado');current=self.add('current.mkv','Comprimiendo')
        pending=self.add('next.mkv','Pendiente');w=self.window
        w.active=True;w._run_paths=[i.path for i in (completed,current,pending)]
        w._run_processed={completed.path};w._current_path=current.path;w._current_percent=40.
        w.total_count=3;w.completed_count=1;w.control=app.engine.Control();control=w.control
        w.start_btn.setEnabled(False);w.pause_btn.setEnabled(True);w.stop_btn.setEnabled(True)
        w._refresh_run_summary();w.clear_finished()
        self.assertEqual(w.items,[current,pending]);self.assertTrue(w.active);self.assertIs(w.control,control)
        self.assertEqual(w._current_path,current.path);self.assertEqual(w._current_percent,40.)
        self.assertEqual((w.total_count,w.completed_count),(2,0));self.assertEqual(w.total_progress.value(),20)
        self.assertIn('video 1 de 2',w.status.text());self.assertIn('current.mkv',w.status.text())
        self.assertFalse(w.start_btn.isEnabled());self.assertTrue(w.pause_btn.isEnabled())
        self.assertTrue(w.stop_btn.isEnabled())

    def test_saving_and_ai_do_not_reenable_start_during_their_work(self):
        item=self.add('finished.mkv','Completado');self.completed_summary([item])
        self.window.saving=True;self.window.start_btn.setEnabled(False)
        self.window.clear_finished()
        self.assertEqual(self.window.total_progress.value(),100);self.assertFalse(self.window.start_btn.isEnabled())
        self.window.saving=False;self.window.ai_busy=True;self.window.clear_finished()
        self.assertEqual(self.window.total_progress.value(),0);self.assertFalse(self.window.start_btn.isEnabled())
        self.assertFalse(self.window.ai_btn.isEnabled())


if __name__=='__main__':unittest.main()
