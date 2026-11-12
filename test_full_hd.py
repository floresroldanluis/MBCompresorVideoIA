import subprocess,tempfile,unittest
from pathlib import Path
import engine
class FullHDChecks(unittest.TestCase):
 def test_fits_full_hd_without_stretching_or_upscaling(self):
  ff,probe=engine.discover_tools()
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)
   for width,height,expected in [(3840,1600,(1920,800)),(3840,2160,(1920,1080)),(1280,720,(1280,720))]:
    source=root/f'{width}x{height}.mkv'
    subprocess.run([ff,'-v','error','-y','-f','lavfi','-i',f'color=size={width}x{height}:rate=24','-t','1','-c:v','ffv1',str(source)],check=True,**engine._flags())
    output=engine.compress(source,root,engine.CompressionOptions(mode='cpu',height=1080,max_gb=.001),ffmpeg=ff,ffprobe=probe)
    info=engine.probe(output,probe);self.assertEqual((info['width'],info['height']),expected);self.assertLessEqual(output.stat().st_size,1000000)
if __name__=='__main__':unittest.main()
