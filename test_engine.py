"""Integration checks. Run: python -m unittest test_engine -v (FFmpeg required)."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
import subprocess
from engine import CompressionOptions, Control, EncodeCancelled, EncodeError, compress, discover_tools, probe, _flags

class EngineChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg, cls.ffprobe=discover_tools()
        if not cls.ffmpeg or not cls.ffprobe: raise unittest.SkipTest('FFmpeg required')
        cls.tmp=tempfile.TemporaryDirectory(); cls.root=Path(cls.tmp.name)
        cls.source=cls.root/'source.mkv'
        subprocess.run([cls.ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x240:rate=24','-f','lavfi','-i','sine=frequency=440','-t','3','-c:v','libx264','-c:a','aac',str(cls.source)],check=True,**_flags())
        cls.original=cls.source.read_bytes()
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def test_codecs_cap_unique_and_no_upscale(self):
        for codec in ('h264','h265'):
            p=compress(self.source,self.root,CompressionOptions(mode='cpu',max_gb=.0003,codec=codec,speed='rapido',height=1080))
            self.assertLessEqual(p.stat().st_size,300000)
            info=probe(p); self.assertLessEqual(info['height'],240); self.assertEqual(len(info['audio']),1)
            self.assertNotEqual(p,self.source)
        self.assertEqual(self.original,self.source.read_bytes())
    def test_trim_without_audio(self):
        p=compress(self.source,self.root,CompressionOptions(mode='cpu',max_gb=.0002,speed='rapido',audio_indices=[],trim_start=.5,trim_end=1.5))
        info=probe(p); self.assertEqual(info['audio'],[]); self.assertLess(info['duration'],1.2)
    def test_default_output_same_folder_and_mp4_source_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'pelicula.mp4'
            subprocess.run([self.ffmpeg,'-v','error','-y','-i',str(self.source),'-c','copy',str(source)],check=True,**_flags())
            original=source.read_bytes()
            output=compress(source,root,CompressionOptions(mode='cpu',max_gb=.0003,speed='rapido'))
            self.assertEqual(output.parent,source.parent)
            self.assertEqual(output.name,'pelicula_1.mp4')
            self.assertEqual(source.read_bytes(),original)
            self.assertLessEqual(output.stat().st_size,300000)
    def test_invalid_track(self):
        with self.assertRaises(EncodeError): compress(self.source,self.root,CompressionOptions(mode='cpu',audio_indices=[999]))
    def test_output_name_validation_and_collision(self):
        for name in ('../escape.mp4','folder\\escape.mp4','CON.mp4','  ','.mp4','video.mkv'):
            with self.assertRaises(EncodeError): compress(self.source,self.root,CompressionOptions(mode='cpu',output_name=name))
        existing=self.root/'custom.mp4'; existing.write_bytes(b'keep')
        result=compress(self.source,self.root,CompressionOptions(mode='cpu',max_gb=.0003,speed='rapido',output_name='custom.mp4',downmix_audio=True))
        self.assertEqual(result.name,'custom_1.mp4'); self.assertEqual(existing.read_bytes(),b'keep')
        self.assertEqual(probe(result)['audio'][0]['channels'],2)
    def test_subtitle_burn_and_selectable(self):
        srt=self.root/'captions.srt'; srt.write_text('1\n00:00:00,000 --> 00:00:02,000\nHello world\n',encoding='utf-8')
        src=self.root/'with_subs.mkv'
        subprocess.run([self.ffmpeg,'-v','error','-y','-i',str(self.source),'-i',str(srt),'-map','0','-map','1','-c','copy',str(src)],check=True,**_flags())
        index=probe(src)['subtitles'][0]['index']
        for mode in ('selectable','burn'):
            p=compress(src,self.root,CompressionOptions(mode='cpu',max_gb=.0003,speed='rapido',subtitle_indices=[index],subtitle_mode=mode))
            self.assertEqual(len(probe(p)['subtitles']),1 if mode=='selectable' else 0)
    def test_cancel_paused_process_cleans_up(self):
        control=Control(); control.pause(); result=[]
        def worker():
            try: compress(self.source,self.root,CompressionOptions(mode='cpu',speed='calidad'),control)
            except Exception as e: result.append(e)
        thread=threading.Thread(target=worker); thread.start(); time.sleep(.4); control.cancel(); thread.join(10)
        self.assertFalse(thread.is_alive()); self.assertIsInstance(result[0],EncodeCancelled)
        self.assertFalse(list(self.root.glob('.mb-compress-*')))

if __name__=='__main__': unittest.main()
