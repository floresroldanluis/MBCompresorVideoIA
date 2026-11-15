"""The numeric maximum matches Windows, with a strict cap for real encodes."""
from decimal import Decimal
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import acceleration
import engine
import rate_history


class WindowsLimitChecks(unittest.TestCase):
    def test_displayed_195_means_195_windows_gb_and_rounds_down(self):
        self.assertEqual(engine.BYTES_PER_GB,1073741824)
        self.assertEqual(engine.maximum_bytes(1.95),2093796556)
        self.assertLessEqual(Decimal(engine.maximum_bytes(1.95)),Decimal('1.95')*1073741824)
        self.assertEqual(engine.maximum_bytes(2.5),2684354560)
        self.assertEqual(engine.maximum_bytes(100000/2**30),100000)

    def test_invalid_limits_are_not_converted(self):
        for value in (0,-1,float('inf'),float('nan')):
            with self.assertRaises(ValueError):engine.maximum_bytes(value)

    def test_three_hour_plan_spends_the_windows_budget_at_full_hd(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'original.mkv';source.write_bytes(b'keep original')
            info=dict(duration=11304.704,width=1920,height=1080,video={'index':0,'avg_frame_rate':'24/1'},
                      audio=[dict(index=1,codec_name='eac3',bit_rate='256000',channels=6)],subtitles=[])
            calls=[];messages=[]
            def encode(cmd,*args):
                calls.append(cmd)
                if Path(cmd[-1]).suffix=='.part':Path(cmd[-1]).write_bytes(b'complete output')
            with patch.object(engine,'probe',return_value=info),patch.object(engine,'_run',side_effect=encode):
                engine.compress(source,root,engine.CompressionOptions(mode='cpu',codec='h265',max_gb=1.95),ffmpeg='ffmpeg',log=messages.append)
            expected=int(2093796556*8/11304.704*.985-256000)
            for cmd in calls:
                self.assertEqual(int(cmd[cmd.index('-b:v')+1]),expected)
                self.assertIn('scale=-2:1080:',cmd[cmd.index('-vf')+1])
            self.assertTrue(any('2093796556 bytes' in message for message in messages))
            self.assertEqual(source.read_bytes(),b'keep original')

    def test_real_cpu_and_nvidia_hevc_stay_under_the_windows_byte_cap(self):
        ffmpeg,ffprobe=engine.discover_tools()
        if not ffmpeg or not ffprobe:self.skipTest('FFmpeg/FFprobe required')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'full hd original.mkv'
            subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=1920x1080:rate=24',
                            '-t','0.5','-c:v','ffv1',str(source)],check=True,**engine._flags())
            original=source.read_bytes()
            modes=['cpu']
            if acceleration.find_nvenc(ffmpeg,'h265')['available']:modes.append('nvidia')
            for mode in modes:
                output=engine.compress(source,root,engine.CompressionOptions(mode=mode,codec='h265',height=1080,max_gb=.0002),
                                       ffmpeg=ffmpeg,ffprobe=ffprobe)
                self.assertLessEqual(output.stat().st_size,214748)
                result=engine.probe(output,ffprobe)
                self.assertEqual((result['width'],result['height']),(1920,1080))
                self.assertEqual(result['video']['codec_name'],'hevc')
                self.assertEqual(result['video']['avg_frame_rate'],'24/1')
                self.assertGreater(result['duration'],.4)
            self.assertEqual(source.read_bytes(),original)


if __name__=='__main__':unittest.main()
