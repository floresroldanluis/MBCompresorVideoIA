"""Unique output/temporary names and safe recovery of old and new workspaces."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import engine


PAYLOAD=b'complete MP4 payload'
NONCE='123456789abc'


class UniqueNameChecks(unittest.TestCase):
    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root=Path(self.workspace.name)
        self.source=self.root/'película.mp4';self.source.write_bytes(b'original video')
        self.info=dict(duration=1.,height=240,width=320,video={'index':0},audio=[],subtitles=[])
        self.commands=[]

    def encode(self,cmd,*args):
        self.commands.append(cmd)
        self.assertEqual(cmd[-3:-1],['-f','mp4'])
        result=Path(cmd[-1])
        self.assertRegex(result.name,r'^payload_[0-9a-f]{12}\.part$')
        self.assertFalse(list(result.parent.glob('*.mp4')))
        result.write_bytes(PAYLOAD)
        (result.parent/'pass-0.log').write_bytes(b'owned analysis')

    def compress(self,options=None,run=None,control=None):
        options=options or engine.CompressionOptions(mode='cpu',unlimited=True)
        with patch.object(engine,'probe',return_value=self.info),patch.object(engine,'_run',side_effect=run or self.encode):
            return engine.compress(self.source,self.root,options,control=control,ffmpeg='unused',ffprobe='unused')

    def test_original_name_is_preserved_and_repeated_jobs_are_numbered(self):
        self.source=self.source.rename(self.source.with_suffix('.mkv'))
        names=[];temporary_names=[]
        for _ in range(3):
            output=self.compress()
            self.assertEqual(output.read_bytes(),PAYLOAD)
            names.append(output.name);temporary_names.append(Path(self.commands[-1][-1]).name)
        self.assertEqual(names,['película.mp4','película_1.mp4','película_2.mp4'])
        self.assertEqual(len(set(temporary_names)),3)
        self.assertEqual(self.source.read_bytes(),b'original video')
        self.assertEqual(list(self.root.glob('.mb-compress-*')),[])

    def test_free_original_name_can_be_reused_after_the_output_is_removed(self):
        self.source=self.source.rename(self.source.with_suffix('.mkv'))
        for _ in range(2):
            output=self.compress()
            self.assertEqual(output.name,'película.mp4')
            output.unlink()

    def test_original_mp4_name_is_reserved_and_never_overwritten(self):
        self.assertEqual([self.compress().name for _ in range(2)],['película_1.mp4','película_2.mp4'])
        self.assertEqual(self.source.read_bytes(),b'original video')

    def test_custom_name_is_preserved_when_free(self):
        output=self.compress(engine.CompressionOptions(mode='cpu',unlimited=True,output_name='mi vídeo.mp4'))
        self.assertEqual(output.name,'mi vídeo.mp4')

    def test_custom_name_collisions_are_numbered(self):
        existing=self.root/'mi vídeo.mp4';existing.write_bytes(b'keep custom output')
        options=engine.CompressionOptions(mode='cpu',unlimited=True,output_name=existing.name)
        outputs=[self.compress(options) for _ in range(2)]
        self.assertEqual([output.name for output in outputs],['mi vídeo_1.mp4','mi vídeo_2.mp4'])
        self.assertEqual(existing.read_bytes(),b'keep custom output')

    def test_temporary_nonce_is_not_added_to_the_final_name(self):
        existing=self.root/f'película_comprimido_{NONCE}.mp4';existing.write_bytes(b'keep old output')
        with patch.object(engine.uuid,'uuid4') as random_id:
            random_id.return_value.hex=NONCE+'0'*20
            output=self.compress()
        self.assertEqual(output.name,'película_1.mp4')
        self.assertEqual(Path(self.commands[-1][-1]).name,f'payload_{NONCE}.part')
        self.assertEqual(existing.read_bytes(),b'keep old output')
        self.assertEqual(self.source.read_bytes(),b'original video')

    def test_long_names_are_preserved_and_invalid_names_are_rejected(self):
        self.source=self.root/('á'*200+'.mp4');self.source.write_bytes(b'original video')
        output=self.compress()
        self.assertEqual(output.name,'á'*200+'_1.mp4')
        custom_name='b'*200+'.mp4'
        output=self.compress(engine.CompressionOptions(mode='cpu',unlimited=True,output_name=custom_name))
        self.assertEqual(output.name,custom_name)
        for name in ('x'*252+'.mp4','bad\x01name.mp4'):
            with self.assertRaises(engine.EncodeError):
                self.compress(engine.CompressionOptions(mode='cpu',unlimited=True,output_name=name))

    def test_only_a_collision_suffix_truncates_a_maximum_length_name(self):
        self.source=self.root/('x'*251+'.mkv');self.source.write_bytes(b'original video')
        first=self.compress();second=self.compress()
        self.assertEqual(first.name,'x'*251+'.mp4')
        self.assertEqual(second.name,'x'*249+'_1.mp4')
        self.assertEqual(first.read_bytes(),PAYLOAD)
        self.assertEqual(self.source.read_bytes(),b'original video')

    def make_pending(self,run=None):
        control=engine.Control()
        def encode(cmd,*args):
            (run or self.encode)(cmd,*args)
            control.cancel()
        with self.assertRaises(engine.OutputPublishCancelled) as failure:self.compress(run=encode,control=control)
        return failure.exception

    def test_new_registered_part_is_recovered_without_encoding_and_cleaned_up(self):
        pending=self.make_pending()
        result=pending.recovery_path;directory=result.parent
        marker=json.loads((directory/'recovery.json').read_text(encoding='utf-8'))
        self.assertEqual(marker['owner'],'MBCompresorPro.output-recovery.v2')
        self.assertEqual(marker['result_name'],result.name)
        self.assertEqual(marker['result_identity'],engine._file_identity(result))
        self.assertRegex(result.name,r'^payload_[0-9a-f]{12}\.part$')
        with patch.object(engine,'_run',side_effect=AssertionError('must not encode again')):
            output=engine.finalize_output(result,pending.desired_target)
        self.assertEqual(output.read_bytes(),PAYLOAD)
        self.assertTrue(engine.cleanup_recovery_workspace(result))
        self.assertFalse(directory.exists())

    def test_legacy_result_mp4_recovery_remains_supported(self):
        directory=self.root/'.mb-compress-legacy';directory.mkdir()
        result=directory/'result.mp4';result.write_bytes(PAYLOAD)
        stats=directory/'pass-0.log';stats.write_bytes(b'old analysis')
        marker=dict(owner='MBCompresorPro.output-recovery.v1',directory=str(directory),
                    desired_target=str(self.root/'legacy.mp4'),files={stats.name:engine._file_identity(stats)})
        (directory/'recovery.json').write_text(json.dumps(marker),encoding='utf-8')
        output=engine.finalize_output(result,self.root/'legacy.mp4')
        self.assertEqual(output.read_bytes(),PAYLOAD)
        self.assertTrue(engine.cleanup_recovery_workspace(result))
        self.assertFalse(directory.exists())

    def test_cleanup_requires_the_exact_registered_random_temporary_name(self):
        pending=self.make_pending();result=pending.recovery_path;directory=result.parent
        engine.finalize_output(result,pending.desired_target)
        wrong=directory/f'payload_{NONCE}.part'
        if wrong==result:wrong=directory/'payload_000000000000.part'
        self.assertFalse(engine.cleanup_recovery_workspace(wrong))
        self.assertTrue((directory/'pass-0.log').exists())
        self.assertTrue((directory/'recovery.json').exists())
        self.assertTrue(engine.cleanup_recovery_workspace(result))

    def test_cleanup_keeps_unknown_files_even_if_present_before_the_recovery_marker(self):
        def encode(cmd,*args):
            self.encode(cmd,*args)
            (Path(cmd[-1]).parent/'personal.txt').write_bytes(b'keep personal file')
        pending=self.make_pending(run=encode);directory=pending.recovery_path.parent
        marker_path=directory/'recovery.json';marker=json.loads(marker_path.read_text(encoding='utf-8'))
        self.assertNotIn('personal.txt',marker['files'])
        # Even a forged/older manifest entry may not authorize deleting user files.
        marker['files']['personal.txt']=engine._file_identity(directory/'personal.txt')
        marker_path.write_text(json.dumps(marker),encoding='utf-8')
        engine.finalize_output(pending.recovery_path,pending.desired_target)
        self.assertFalse(engine.cleanup_recovery_workspace(pending.recovery_path))
        self.assertEqual((directory/'personal.txt').read_bytes(),b'keep personal file')

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_lock_on_old_custom_output_does_not_block_a_new_compression(self):
        from test_publish_output import hold_without_delete_sharing
        existing=self.root/'custom.mp4';existing.write_bytes(b'old output in use')
        release=hold_without_delete_sharing(existing)
        try:
            output=self.compress(engine.CompressionOptions(mode='cpu',unlimited=True,output_name='custom.mp4'))
            self.assertEqual(output.name,'custom_1.mp4')
            self.assertEqual(existing.read_bytes(),b'old output in use')
        finally:release()

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_lock_on_new_part_retains_and_recovers_the_complete_result(self):
        from test_publish_output import hold_without_delete_sharing
        releases=[]
        def encode(cmd,*args):
            self.encode(cmd,*args)
            releases.append(hold_without_delete_sharing(Path(cmd[-1])))
        try:
            with patch.object(engine,'PUBLISH_RETRY_SECONDS',.05),self.assertRaises(engine.OutputPublishError) as failure:
                self.compress(run=encode)
            pending=failure.exception
            self.assertRegex(pending.recovery_path.name,r'^payload_[0-9a-f]{12}\.part$')
            self.assertEqual(pending.recovery_path.read_bytes(),PAYLOAD)
            self.assertEqual(pending.desired_target,self.source)
            self.assertFalse((self.root/'película_1.mp4').exists())
            releases.pop()()
            output=engine.finalize_output(pending.recovery_path,pending.desired_target)
            self.assertEqual(output.read_bytes(),PAYLOAD)
            self.assertTrue(engine.cleanup_recovery_workspace(pending.recovery_path))
        finally:
            for release in releases:release()


class UniqueNameRealEncodeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg,cls.ffprobe=engine.discover_tools()
        if not cls.ffmpeg or not cls.ffprobe:raise unittest.SkipTest('FFmpeg/FFprobe required')

    def test_explicit_mp4_muxer_produces_valid_video_from_part_with_unicode_paths(self):
        import acceleration
        modes=['cpu']
        if acceleration.find_nvenc(self.ffmpeg,'h264')['available']:modes.append('nvidia')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'vídeos únicos';root.mkdir()
            source=root/'película fuente.mkv'
            subprocess.run([self.ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=160x120:rate=12',
                            '-t','0.5','-c:v','libx264',str(source)],check=True,**engine._flags())
            original=source.read_bytes()
            for index,mode in enumerate(modes):
                calls=[];real_run=engine._run
                def capture(cmd,*args):
                    calls.append(cmd)
                    self.assertEqual(cmd[-3:-1],['-f','mp4'])
                    self.assertEqual(Path(cmd[-1]).suffix,'.part')
                    real_run(cmd,*args)
                    self.assertGreater(engine.probe(cmd[-1],self.ffprobe)['duration'],0)
                with patch.object(engine,'_run',side_effect=capture):
                    output=engine.compress(source,root,engine.CompressionOptions(mode=mode,unlimited=True),
                                           ffmpeg=self.ffmpeg,ffprobe=self.ffprobe)
                self.assertEqual(output.name,'película fuente'+(f'_{index}' if index else '')+'.mp4')
                self.assertEqual(engine.probe(output,self.ffprobe)['width'],160)
                self.assertTrue(calls)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(list(root.glob('.mb-compress-*')),[])


if __name__=='__main__':unittest.main()
