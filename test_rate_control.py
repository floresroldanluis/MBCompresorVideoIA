"""Rate-control regression checks, including real CPU pass-log reuse."""
import subprocess
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import acceleration


class RateBudgetChecks(unittest.TestCase):
    def test_retry_accounts_for_fixed_audio_and_container_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder)/'result.mp4'
            result.write_bytes(b'0'*120_000)
            # 40 kB video, 80 kB fixed: a 100 kB cap leaves 15 kB for video
            # with the retry safety margin. Scaling the whole file is wrong.
            with patch('engine.probe',return_value={'video':{'bit_rate':'320000','duration':'1'}}):
                value=engine._retry_video_bitrate(result,320000,100000,1,0,None)
            self.assertEqual(value,120000)

    def test_missing_video_bitrate_uses_audio_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder)/'result.mp4';result.write_bytes(b'0'*120_000)
            with patch('engine.probe',return_value={'video':{}}):
                value=engine._retry_video_bitrate(result,640000,100000,1,320000,None)
            self.assertEqual(value,440000)

    def test_fixed_tracks_cannot_fit_fails_without_another_encode(self):
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder)/'result.mp4';result.write_bytes(b'0'*120_000)
            with patch('engine.probe',return_value={'video':{'bit_rate':'80000','duration':'1'}}):
                with self.assertRaises(engine.EncodeError):
                    engine._retry_video_bitrate(result,80000,100000,1,0,None)


class EncodingPlanChecks(unittest.TestCase):
    def capture_plan(self, mode, unlimited, codec='h264', oversize=False, cancel_retry=False):
        calls=[];messages=[];events=[]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mkv';source.write_bytes(b'original')
            info=dict(duration=1,height=240,width=320,video={'index':0},audio=[],subtitles=[])
            def run(cmd,control,duration,callback,pn,log,guard,total,backend):
                calls.append((cmd,pn,total))
                if cmd[-1].endswith('result.mp4'):
                    if cancel_retry and len(calls)>2:raise engine.EncodeCancelled('cancel')
                    size=120000 if oversize and len(calls)==2 else 80000
                    Path(cmd[-1]).write_bytes(b'0'*size)
            with patch('engine.probe',return_value=info),patch('engine._run',side_effect=run),patch('acceleration.find_nvenc',return_value={'available':True,'path':'ffmpeg'}):
                options=engine.CompressionOptions(mode=mode,codec=codec,max_gb=.0001,unlimited=unlimited)
                if cancel_retry:
                    with self.assertRaises(engine.EncodeCancelled):
                        engine.compress(source,root,options,ffmpeg='ffmpeg',log=messages.append,progress=events.append)
                    self.assertEqual(list(root.glob('*.mp4')),[])
                else:
                    output=engine.compress(source,root,options,ffmpeg='ffmpeg',log=messages.append,progress=events.append)
                    self.assertLessEqual(output.stat().st_size,100000)
                self.assertEqual(source.read_bytes(),b'original')
                self.assertEqual(list(root.glob('.mb-compress-*')),[])
        return calls,messages,events

    def test_nvenc_capped_uses_bitrate_and_unlimited_retains_cq(self):
        for codec in ('h264','h265'):
            calls,_,_=self.capture_plan('nvidia',False,codec)
            cmd=calls[0][0]
            self.assertNotIn('-cq',cmd)
            self.assertEqual(cmd[cmd.index('-b:v')+1],'760000')
            self.assertEqual(cmd[cmd.index('-preset')+1],'p6')
            calls,_,_=self.capture_plan('nvidia',True,codec)
            cmd=calls[0][0]
            self.assertEqual(cmd[cmd.index('-cq')+1],'18')
            self.assertEqual(cmd[cmd.index('-b:v')+1],'0')
            self.assertNotIn('-maxrate',cmd)

    def test_retry_reuses_analysis_and_reports_one_remaining_pass(self):
        for codec in ('h264','h265'):
            calls,messages,events=self.capture_plan('cpu',False,codec,oversize=True)
            self.assertEqual(len(calls),3)
            self.assertEqual([(pn,total) for _,pn,total in calls],[(1,2),(2,2),(1,1)])
            self.assertEqual(events[-1]['total_passes'],1)
            self.assertTrue(any('reutiliza' in message for message in messages))
            self.assertIn('Compresión CPU con análisis reutilizado.',messages)
            self.assertTrue(any('Intento 2:' in message and 'análisis reutilizado' in message for message in messages))
            if codec=='h264':
                self.assertEqual([cmd[cmd.index('-pass')+1] for cmd,_,_ in calls],['1','2','2'])
            else:
                self.assertEqual([cmd[cmd.index('-x265-params')+1].split(':')[0] for cmd,_,_ in calls],['pass=1','pass=2','pass=2'])

    def test_cancel_during_retry_removes_temporary_output(self):
        self.capture_plan('cpu',False,oversize=True,cancel_retry=True)

    def test_unlimited_cpu_logs_do_not_claim_two_passes(self):
        _,messages,_=self.capture_plan('cpu',True)
        self.assertFalse(any('dos pasadas' in message for message in messages))


class RealAnalysisReuseChecks(unittest.TestCase):
    def test_x264_and_x265_reuse_real_pass_logs_after_oversize(self):
        ffmpeg,ffprobe=engine.discover_tools()
        if not ffmpeg or not ffprobe:self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mkv'
            subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x240:rate=24','-t','3','-c:v','ffv1',str(source)],check=True,**engine._flags())
            original=source.read_bytes()
            for codec in ('h264','h265'):
                calls=[];run=engine._run
                def force_oversize_once(cmd,*args,**kwargs):
                    calls.append(cmd)
                    if len(calls)==2:
                        cmd=list(cmd);index=cmd.index('-b:v')+1
                        cmd[index]=str(int(cmd[index])*3)
                    return run(cmd,*args,**kwargs)
                with patch('engine._run',side_effect=force_oversize_once):
                    output=engine.compress(source,root,engine.CompressionOptions(mode='cpu',codec=codec,max_gb=.00012),ffmpeg=ffmpeg,ffprobe=ffprobe)
                self.assertGreaterEqual(len(calls),3)
                self.assertEqual(sum(cmd[-1]==engine.os.devnull for cmd in calls),1)
                self.assertLessEqual(output.stat().st_size,120000)
                info=engine.probe(output,ffprobe)
                self.assertGreater(info['duration'],2.9)
                self.assertEqual((info['width'],info['height']),(320,240))
                self.assertEqual(source.read_bytes(),original)


class CappedAudioCopyChecks(unittest.TestCase):
    def test_only_known_aac_within_budget_is_copyable(self):
        options=engine.CompressionOptions()
        tracks=[{'codec_name':'aac','bit_rate':value} for value in ('64000','190000','190001',None,'N/A','0','-1','nan','inf')]
        tracks += [{'codec_name':'ac3','bit_rate':'64000'},{'codec_name':'opus','bit_rate':'64000'}]
        self.assertEqual(engine._capped_audio_copy_rates(tracks,options,190000),[64000,190000]+[None]*9)

    def test_trim_and_downmix_always_encode_audio(self):
        track=[{'codec_name':'aac','bit_rate':'64000'}]
        for options in (engine.CompressionOptions(trim_start=.1),engine.CompressionOptions(trim_end=3),engine.CompressionOptions(downmix_audio=True)):
            self.assertEqual(engine._capped_audio_copy_rates(track,options,190000),[None])

    def test_budget_releases_only_the_copyable_track_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4';source.write_bytes(b'original')
            info=dict(duration=1,height=240,width=320,video={'index':0},audio=[{'index':1,'codec_name':'aac','bit_rate':'64000'},{'index':2,'codec_name':'aac','bit_rate':'200000'}],subtitles=[])
            calls=[]
            def run(cmd,*args,**kwargs):
                calls.append(cmd)
                if cmd[-1].endswith('result.mp4'):Path(cmd[-1]).write_bytes(b'0'*80000)
            with patch('engine.probe',return_value=info),patch('engine._run',side_effect=run):
                engine.compress(source,root,engine.CompressionOptions(mode='cpu',max_gb=.0001),ffmpeg='ffmpeg')
            # 760 kb/s total; 95 kb/s budget per audio track. Only the64kb/s
            # AAC fits. The200kb/s track keeps its95kb/s AAC encode budget.
            for cmd in calls:self.assertEqual(cmd[cmd.index('-b:v')+1],'601000')
            final=calls[-1]
            self.assertEqual(final[final.index('-c:a:0')+1],'copy')
            self.assertNotIn('-c:a:1',final)
            self.assertEqual(final[final.index('-c:a')+1],'aac')
            self.assertEqual(final[final.index('-b:a')+1],'95000')

    def test_real_capped_aac_copy_is_bit_exact_cpu_and_nvidia(self):
        ffmpeg,ffprobe=engine.discover_tools()
        if not ffmpeg or not ffprobe:self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4'
            subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x240:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-c:a','aac','-b:a','64k',str(source)],check=True,**engine._flags())
            original=source.read_bytes()
            def audio_hash(path):
                return hashlib.sha256(subprocess.check_output([ffmpeg,'-v','error','-i',str(path),'-map','0:a:0','-c:a','copy','-f','adts','-'],**engine._flags())).digest()
            expected=audio_hash(source)
            modes=['cpu']
            if acceleration.find_nvenc(ffmpeg,'h264')['available']:modes.append('nvidia')
            for mode in modes:
                calls=[];run=engine._run
                def capture(cmd,*args,**kwargs):
                    calls.append(cmd);return run(cmd,*args,**kwargs)
                with patch('engine._run',side_effect=capture):
                    output=engine.compress(source,root,engine.CompressionOptions(mode=mode,max_gb=.0003),ffmpeg=ffmpeg,ffprobe=ffprobe)
                self.assertLessEqual(output.stat().st_size,300000)
                self.assertEqual(audio_hash(output),expected)
                self.assertEqual(source.read_bytes(),original)
                cmd=calls[-1]
                self.assertEqual(cmd[cmd.index('-c:a:0')+1],'copy')
                self.assertGreater(engine.probe(output,ffprobe)['duration'],2.9)


if __name__=='__main__':unittest.main()
