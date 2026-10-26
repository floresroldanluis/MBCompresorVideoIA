"""Exercise live queue additions and metadata races through the Qt event loop."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import app

class DynamicQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.calls=[];self.release={};self.probe_release={};self.failures=set()
        self.patches=[patch.object(app,'DATA',self.root/'state'),patch.object(app,'CONFIG',self.root/'state'/'settings.json'),patch.object(app.engine,'probe',side_effect=self.probe),patch.object(app.engine,'compress',side_effect=self.compress)]
        for p in self.patches:p.start()
        self.window=app.MainWindow();self.window.show();self.qt.processEvents()
    def wait_for(self,condition):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            self.qt.processEvents()
            if condition():return
            time.sleep(.005)
        self.fail('Timed out waiting for queue state: '+self.window.status.text())
    def probe(self,path,*_):
        gate=self.probe_release.get(Path(path).name)
        if gate and not gate.wait(5):raise RuntimeError('metadata timeout')
        return dict(duration=10,width=320,height=240,size=10000,audio=[],subtitles=[])
    def compress(self,path,directory,options,control,progress,**_):
        name=Path(path).name;self.calls.append(name)
        progress(dict(percent=50,status='Comprimiendo',pass_number=2))
        while not self.release[name].wait(.005):
            if control.cancelled.is_set():raise app.engine.EncodeCancelled()
        if name in self.failures:raise app.engine.EncodeError('simulated failure')
        result=Path(directory)/(Path(path).stem+'.mp4');result.write_bytes(b'mp4')
        return result
    def add(self,name,delayed=False):
        path=self.root/name;path.write_bytes(b'video');self.release[name]=threading.Event()
        if delayed:self.probe_release[name]=threading.Event()
        self.window.add_paths([str(path)])
        return str(path.resolve())
    def tearDown(self):
        for gate in self.release.values():gate.set()
        for gate in self.probe_release.values():gate.set()
        if self.window.active:self.window.stop()
        self.wait_for(lambda:not self.window.active)
        if hasattr(self.window,'worker'):self.window.worker.join(5)
        self.qt.processEvents();self.window.close()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_scroll_follows_only_when_next_video_starts(self):
        from PySide6.QtCore import QPoint
        self.window.resize(1200,1000)
        paths=[self.add(f'episode-{n}.mkv') for n in range(9)]
        self.wait_for(lambda:all(item.info for item in self.window.items))
        self.window.start();self.wait_for(lambda:len(self.calls)==1)
        for n in range(7):
            self.release[f'episode-{n}.mkv'].set()
            self.wait_for(lambda:len(self.calls)==n+2)
        self.qt.processEvents()
        frame=self.window.rows[paths[7]]['frame'];viewport=self.window.scroll.viewport()
        def visible():
            top=frame.mapTo(viewport,QPoint(0,0)).y()
            return top>=0 and top+frame.height()<=viewport.height()
        self.assertTrue(visible());self.assertIn('episode-7.mkv',self.window.status.text())
        self.window.scroll.verticalScrollBar().setValue(0)
        self.window.on_progress(paths[7],dict(percent=60,status='Comprimiendo',pass_number=1,total_passes=1,backend='nvidia'))
        until=time.monotonic()+.7
        while time.monotonic()<until:self.qt.processEvents();time.sleep(.01)
        self.assertEqual(self.window.scroll.verticalScrollBar().value(),0)
        self.assertFalse(visible())
        self.release['episode-7.mkv'].set();self.wait_for(lambda:len(self.calls)==9)
        frame=self.window.rows[paths[8]]['frame'];self.wait_for(visible)
        self.assertIn('episode-8.mkv',self.window.status.text())
        self.window.scroll.verticalScrollBar().setValue(0)
        self.window.on_progress(paths[8],dict(percent=30,status='Pausado',pass_number=1,total_passes=1,backend='nvidia'))
        self.qt.processEvents();self.assertEqual(self.window.scroll.verticalScrollBar().value(),0)

    def test_added_videos_update_total_and_run_without_starting_again(self):
        self.add('one.mkv');self.wait_for(lambda:self.window.items[0].info)
        self.window.start();self.wait_for(lambda:self.calls==['one.mkv'])
        self.add('two.mkv');self.add('three.mkv')
        self.assertEqual(self.window.total_count,3)
        self.assertIn('video 1 de 3',self.window.status.text())
        self.release['one.mkv'].set();self.wait_for(lambda:len(self.calls)==2)
        self.assertIn('video 2 de 3',self.window.status.text())
        self.release['two.mkv'].set();self.wait_for(lambda:len(self.calls)==3)
        self.release['three.mkv'].set();self.wait_for(lambda:not self.window.active)
        self.assertEqual(self.calls,['one.mkv','two.mkv','three.mkv'])
        self.assertEqual(self.window.completed_count,3)
        self.assertEqual(self.window.total_progress.value(),100)
    def test_waits_for_metadata_after_last_ready_video_finishes(self):
        self.add('one.mkv');self.wait_for(lambda:self.window.items[0].info)
        self.window.start();self.wait_for(lambda:bool(self.calls))
        self.add('two.mkv',delayed=True);self.release['one.mkv'].set()
        self.wait_for(lambda:self.window.completed_count==1)
        self.assertTrue(self.window.active)
        self.assertEqual(self.calls,['one.mkv'])
        self.probe_release['two.mkv'].set();self.wait_for(lambda:len(self.calls)==2)
        self.release['two.mkv'].set();self.wait_for(lambda:not self.window.active)
        self.assertEqual(self.window.completed_count,2)
    def test_stop_while_waiting_for_metadata_does_not_start_next(self):
        self.add('one.mkv');self.wait_for(lambda:self.window.items[0].info)
        self.window.start();self.wait_for(lambda:bool(self.calls))
        self.add('two.mkv',delayed=True);self.release['one.mkv'].set()
        self.wait_for(lambda:self.window.completed_count==1 and self.window.control is None)
        self.window.stop();self.assertFalse(self.window.active)
        self.probe_release['two.mkv'].set();self.wait_for(lambda:self.window.items[1].info)
        self.assertEqual(self.calls,['one.mkv'])
    def test_remove_pending_video_during_encoding_updates_total_and_skips_it(self):
        first=self.add('one.mkv');second=self.add('two.mkv');self.add('three.mkv')
        self.wait_for(lambda:all(i.info for i in self.window.items))
        self.window.start();self.wait_for(lambda:bool(self.calls))
        self.assertFalse(self.window.rows[first]['remove'].isEnabled())
        self.assertTrue(self.window.rows[second]['remove'].isEnabled())
        self.window.rows[second]['remove'].click()
        self.assertIsNone(self.window.find(second));self.assertTrue(Path(second).exists())
        self.assertEqual(self.window.total_count,2)
        self.assertIn('video 1 de 2',self.window.status.text())
        self.window.remove_item(self.window.find(first))
        self.assertIsNotNone(self.window.find(first))
        self.release['one.mkv'].set();self.wait_for(lambda:len(self.calls)==2)
        self.assertEqual(self.calls,['one.mkv','three.mkv'])
        self.release['three.mkv'].set();self.wait_for(lambda:not self.window.active)
        self.assertEqual(self.window.completed_count,2)

    def test_remove_video_while_waiting_for_its_metadata_advances_queue(self):
        self.add('one.mkv');self.wait_for(lambda:self.window.items[0].info)
        self.window.start();self.wait_for(lambda:bool(self.calls))
        delayed=self.add('two.mkv',delayed=True);self.add('three.mkv')
        self.release['one.mkv'].set()
        self.wait_for(lambda:self.window.completed_count==1 and self.window.control is None)
        self.window.rows[delayed]['remove'].click()
        self.wait_for(lambda:len(self.calls)==2)
        self.probe_release['two.mkv'].set();self.release['three.mkv'].set()
        self.wait_for(lambda:not self.window.active)
        self.assertEqual(self.calls,['one.mkv','three.mkv'])

    def test_error_is_not_retried_and_new_videos_continue(self):
        self.failures.add('one.mkv');self.add('one.mkv')
        self.wait_for(lambda:self.window.items[0].info);self.window.start()
        self.wait_for(lambda:bool(self.calls));self.add('two.mkv')
        self.release['one.mkv'].set();self.wait_for(lambda:len(self.calls)==2)
        self.release['two.mkv'].set();self.wait_for(lambda:not self.window.active)
        self.assertEqual(self.window.items[0].state,'Error')
        self.assertEqual(self.window.items[1].state,'Completado')
        self.assertEqual(self.calls,['one.mkv','two.mkv'])

if __name__=='__main__':unittest.main()
