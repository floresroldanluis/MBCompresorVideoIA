"""Exercise automatic disk pauses using a real subprocess without filling a disk."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import engine

class DiskSpaceTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('Timed out waiting for process state')

    def test_running_process_auto_pause_recovery_preserves_manual_pause(self):
        free=[3_000_000_000];events=[];messages=[];suspensions=[];errors=[]
        control=engine.Control()
        original=engine._suspend
        def suspend(proc,paused):
            original(proc,paused);suspensions.append(paused)
        code="import time\nfor i in range(1000):\n print('out_time_us='+str(i*10000),flush=True)\n time.sleep(.02)"
        with tempfile.TemporaryDirectory() as folder, patch.object(engine.shutil,'disk_usage',side_effect=lambda _:SimpleNamespace(free=free[0])), patch.object(engine,'_suspend',side_effect=suspend):
            guard=engine.DiskSpaceGuard(folder,messages.append,interval=.01)
            def run():
                try:engine._run([sys.executable,'-u','-c',code],control,100,events.append,2,messages.append,guard)
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=run);thread.start()
            try:
                self.wait_for(lambda:bool(events))
                free[0]=1_999_999_999
                self.wait_for(lambda:True in suspensions)
                self.wait_for(lambda:any(e.get('disk_paused') for e in events))
                free[0]=2_000_000_000
                self.wait_for(lambda:suspensions.count(False)==1)
                self.assertTrue(any('Reanudando automáticamente' in m for m in messages))
                free[0]=1_000_000_000
                self.wait_for(lambda:suspensions.count(True)==2)
                control.pause();free[0]=2_000_000_000
                self.wait_for(lambda:not guard.paused)
                time.sleep(.15)
                self.assertEqual(suspensions.count(False),1)
                self.assertTrue(control.paused.is_set())
                control.resume()
                self.wait_for(lambda:suspensions.count(False)==2)
                self.assertEqual(sum('Pausa automática' in m for m in messages),2)
                self.assertTrue(any('pausa manual' in m for m in messages))
            finally:control.cancel();thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(errors[0],engine.EncodeCancelled)

    def test_low_space_before_start_waits_and_can_cancel(self):
        control=engine.Control();events=[];errors=[]
        with tempfile.TemporaryDirectory() as folder, patch.object(engine.shutil,'disk_usage',return_value=SimpleNamespace(free=1_000_000_000)), patch.object(engine.subprocess,'Popen') as popen:
            def run():
                try:engine._run(['unused'],control,1,events.append,1,lambda _:None,engine.DiskSpaceGuard(folder,lambda _:None,interval=.01))
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=run);thread.start()
            try:self.wait_for(lambda:bool(events));self.assertTrue(events[0]['disk_paused']);popen.assert_not_called()
            finally:control.cancel();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],engine.EncodeCancelled)

    @unittest.skipUnless(os.name=='nt','Windows hidden attribute')
    def test_temporary_directory_is_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)/'.mb-compress-test';directory.mkdir()
            engine._hide_work_directory(directory,self.fail)
            import ctypes
            self.assertTrue(ctypes.windll.kernel32.GetFileAttributesW(str(directory)) & 2)

if __name__=='__main__':unittest.main()
