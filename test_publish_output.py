"""Publication regressions; real Windows share locks, no FFmpeg/GPU required."""
import ctypes
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import engine


PAYLOAD=b'complete verified MP4 payload'


def hold_without_delete_sharing(path):
    """An Explorer/antivirus-style handle that prevents rename and removal."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,
                                ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle.restype=ctypes.c_int
    handle=kernel.CreateFileW(str(path),0x80000000,1|2,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    handle_lock=threading.Lock();pending=[handle]
    def release():
        with handle_lock:
            if pending[0] is not None:
                kernel.CloseHandle(pending[0]);pending[0]=None
    return release


class PublishOutputTests(unittest.TestCase):
    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root=Path(self.workspace.name)
        self.source=self.root/'original.mkv';self.source.write_bytes(b'original untouched')
        self.target=self.root/'final.mp4'
        self.options=engine.CompressionOptions(mode='cpu',unlimited=True,output_name=self.target.name)
        self.info={'duration':1.,'height':240,'width':320,'video':{'index':0},'audio':[],'subtitles':[]}

    def make_result(self,name='result.mp4'):
        path=self.root/name;path.write_bytes(PAYLOAD)
        return path

    def fake_encode(self,cmd,*args):
        if Path(cmd[-1]).suffix=='.part':
            result=Path(cmd[-1]);result.write_bytes(PAYLOAD)
            (result.parent/'pass-0.log').write_bytes(b'owned analysis')

    def compress(self,run=None,control=None,messages=None):
        with patch.object(engine,'probe',return_value=self.info),patch.object(engine,'_run',side_effect=run or self.fake_encode):
            return engine.compress(self.source,self.root,self.options,control=control,log=(messages if messages is not None else []).append,ffmpeg='unused',ffprobe='unused')

    def test_atomic_publication_preserves_existing_files_and_source(self):
        self.target.write_bytes(b'existing untouched')
        (self.root/'final_1.mp4').mkdir()
        result=self.make_result()
        output=engine.finalize_output(result,self.target,source=self.source)
        self.assertEqual(output.name,'final_2.mp4')
        self.assertEqual(output.read_bytes(),PAYLOAD)
        self.assertEqual(self.target.read_bytes(),b'existing untouched')
        self.assertEqual(self.source.read_bytes(),b'original untouched')
        self.assertFalse(result.exists())

    def test_same_name_mp4_source_is_never_replaced(self):
        source=self.root/'source.mp4';source.write_bytes(b'original MP4')
        output=engine.finalize_output(self.make_result(),source,source=source)
        self.assertEqual(output.name,'source_1.mp4')
        self.assertEqual(source.read_bytes(),b'original MP4')

    def test_existing_symlink_does_not_redirect_output_to_another_directory(self):
        elsewhere=self.root/'elsewhere';elsewhere.mkdir()
        other=elsewhere/'other.mp4';other.write_bytes(b'other untouched')
        try:self.target.symlink_to(other)
        except OSError as exc:self.skipTest(f'Creating symlinks is unavailable: {exc}')
        output=engine.finalize_output(self.make_result(),self.target)
        self.assertEqual(output,self.root/'final_1.mp4')
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(other.read_bytes(),b'other untouched')
        self.assertFalse((elsewhere/'other_1.mp4').exists())

    def test_concurrent_workers_choose_distinct_names_without_empty_placeholders(self):
        self.target.write_bytes(b'existing untouched')
        workers=8;barrier=threading.Barrier(workers);outputs=[];failures=[]
        def publish(index):
            try:
                result=self.root/f'work-{index}.mp4';result.write_bytes(str(index).encode())
                barrier.wait(5)
                outputs.append(engine.finalize_output(result,self.target))
            except BaseException as exc:failures.append(exc)
        threads=[threading.Thread(target=publish,args=(i,)) for i in range(workers)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(10)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(failures,[])
        self.assertEqual(len(set(outputs)),workers)
        self.assertEqual({p.name for p in outputs},{f'final_{i}.mp4' for i in range(1,workers+1)})
        self.assertEqual({p.read_bytes() for p in outputs},{str(i).encode() for i in range(workers)})
        self.assertEqual(self.target.read_bytes(),b'existing untouched')

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_sharing_lock_is_retried_until_released(self):
        result=self.make_result();release=hold_without_delete_sharing(result);messages=[]
        # This is the actual pre-fix failure, not a mocked rename exception.
        try:
            with self.assertRaises(OSError) as failure:os.rename(result,self.target)
            self.assertEqual(failure.exception.winerror,32)
            timer=threading.Timer(.3,release);timer.start()
            started=time.monotonic()
            try:output=engine.finalize_output(result,self.target,log=messages.append)
            finally:timer.join(5)
            self.assertGreaterEqual(time.monotonic()-started,.25)
            self.assertEqual(output.read_bytes(),PAYLOAD)
            self.assertEqual(sum('esperando' in message for message in messages),1)
        finally:release()

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_permanent_real_lock_retains_complete_encode_and_can_retry_without_encode(self):
        release=None;messages=[]
        def encode(cmd,*args):
            nonlocal release
            self.fake_encode(cmd,*args)
            release=hold_without_delete_sharing(Path(cmd[-1]))
        try:
            with patch.object(engine,'PUBLISH_RETRY_SECONDS',.2),self.assertRaises(engine.OutputPublishError) as failure:
                self.compress(run=encode,messages=messages)
            pending=failure.exception
            self.assertFalse(pending.cancelled)
            self.assertEqual(pending.desired_target.parent,self.target.parent)
            self.assertEqual(pending.desired_target.name,'final.mp4')
            self.assertEqual(pending.recovery_path.read_bytes(),PAYLOAD)
            self.assertFalse(self.target.exists())
            self.assertFalse(ctypes.windll.kernel32.GetFileAttributesW(str(pending.recovery_path.parent))&2)
            self.assertTrue((pending.recovery_path.parent/'recovery.json').is_file())
            release();release=None
            with patch.object(engine,'_run',side_effect=AssertionError('retry must not encode')):
                output=engine.finalize_output(pending.recovery_path,pending.desired_target)
            self.assertEqual(output.read_bytes(),PAYLOAD)
            self.assertTrue(engine.cleanup_recovery_workspace(pending.recovery_path))
            self.assertFalse(pending.recovery_path.parent.exists())
            self.assertEqual(self.source.read_bytes(),b'original untouched')
        finally:
            if release:release()

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_cancel_during_real_lock_preserves_complete_result_and_is_prompt(self):
        result=self.make_result();release=hold_without_delete_sharing(result);control=engine.Control()
        timer=threading.Timer(.1,control.cancel);timer.start();started=time.monotonic()
        try:
            with self.assertRaises(engine.OutputPublishCancelled) as failure:
                engine.finalize_output(result,self.target,control=control)
            self.assertIsInstance(failure.exception,engine.EncodeCancelled)
            self.assertTrue(failure.exception.cancelled)
            self.assertLess(time.monotonic()-started,1)
            self.assertEqual(result.read_bytes(),PAYLOAD)
            self.assertFalse(self.target.exists())
        finally:timer.join(5);release()

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_lock_violation_33_is_retried_and_does_not_create_reservation(self):
        result=self.make_result();original_rename=os.rename;attempts=[];messages=[]
        def rename(old,new):
            attempts.append(new)
            self.assertFalse(self.target.exists())
            if len(attempts)<3:
                error=PermissionError('locked region');error.winerror=33;raise error
            return original_rename(old,new)
        with patch.object(engine.os,'rename',side_effect=rename),patch.object(engine,'PUBLISH_RETRY_INTERVAL',.01):
            output=engine.finalize_output(result,self.target,log=messages.append)
        self.assertEqual(output.read_bytes(),PAYLOAD);self.assertEqual(len(attempts),3)
        self.assertEqual(sum('esperando' in m for m in messages),1)

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_other_os_errors_do_not_retry_and_retain_the_completed_file(self):
        error=PermissionError('access denied');error.winerror=5
        with patch.object(engine.os,'rename',side_effect=error) as rename:
            with self.assertRaises(engine.OutputPublishError) as failure:self.compress()
        self.assertEqual(rename.call_count,1)
        self.assertEqual(failure.exception.recovery_path.read_bytes(),PAYLOAD)
        self.assertFalse(self.target.exists())

    def test_cancel_after_encode_retains_complete_result(self):
        control=engine.Control()
        def encode(cmd,*args):self.fake_encode(cmd,*args);control.cancel()
        with self.assertRaises(engine.OutputPublishCancelled) as failure:self.compress(run=encode,control=control)
        self.assertEqual(failure.exception.recovery_path.read_bytes(),PAYLOAD)
        self.assertFalse(self.target.exists())

    def test_failed_incomplete_encode_is_cleaned_up(self):
        def encode(cmd,*args):
            self.fake_encode(cmd,*args)
            raise engine.EncodeError('incomplete encode')
        with self.assertRaisesRegex(engine.EncodeError,'incomplete'):self.compress(run=encode)
        self.assertFalse(list(self.root.glob('.mb-compress-*')))
        self.assertFalse(self.target.exists())

    def test_oversized_output_is_not_retained_or_published(self):
        self.options.unlimited=False;self.options.max_gb=.0001
        def encode(cmd,*args):
            if Path(cmd[-1]).suffix=='.part':Path(cmd[-1]).write_bytes(b'x'*100001)
        with patch.object(engine,'_retry_video_bitrate',return_value=50000):
            with self.assertRaises(engine.EncodeError):self.compress(run=encode)
        self.assertFalse(list(self.root.glob('.mb-compress-*')))
        self.assertFalse(self.target.exists())

    def test_empty_output_is_not_retained_or_published(self):
        def encode(cmd,*args):Path(cmd[-1]).write_bytes(b'')
        with self.assertRaisesRegex(engine.EncodeError,'vacío'):self.compress(run=encode)
        self.assertFalse(list(self.root.glob('.mb-compress-*')))
        self.assertFalse(self.target.exists())

    def test_cleanup_never_removes_unknown_or_changed_files(self):
        control=engine.Control()
        def encode(cmd,*args):self.fake_encode(cmd,*args);control.cancel()
        with self.assertRaises(engine.OutputPublishCancelled) as failure:self.compress(run=encode,control=control)
        pending=failure.exception;directory=pending.recovery_path.parent
        foreign=directory/'user-notes.txt';foreign.write_bytes(b'not owned')
        changed=directory/'pass-0.log';changed.write_bytes(b'changed after recovery')
        output=engine.finalize_output(pending.recovery_path,pending.desired_target)
        self.assertFalse(engine.cleanup_recovery_workspace(pending.recovery_path))
        self.assertEqual(foreign.read_bytes(),b'not owned')
        self.assertEqual(changed.read_bytes(),b'changed after recovery')
        self.assertEqual(output.read_bytes(),PAYLOAD)

    def test_cleanup_requires_a_recovery_marker(self):
        directory=self.root/'.mb-compress-unowned';directory.mkdir()
        foreign=directory/'unrelated.txt';foreign.write_bytes(b'keep')
        self.assertFalse(engine.cleanup_recovery_workspace(directory/'result.mp4'))
        self.assertEqual(foreign.read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
