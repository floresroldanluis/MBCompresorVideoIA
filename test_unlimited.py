import unittest,subprocess,hashlib
from unittest.mock import patch
import engine,acceleration
from test_engine import EngineChecks
class UnlimitedChecks(EngineChecks):
 def test_cpu_and_gpu_quality_without_cap(self):
  for mode in ('cpu','nvidia','auto'):
   for codec in ('h264','h265'):
    if mode=='nvidia' and not acceleration.find_nvenc(self.ffmpeg,codec)['available']:continue
    events=[];commands=[];run=engine._run
    def capture(cmd,*args,**kwargs):
     commands.append(cmd);return run(cmd,*args,**kwargs)
    with patch('engine._run',side_effect=capture):
     output=engine.compress(self.source,self.root,engine.CompressionOptions(mode=mode,codec=codec,unlimited=True,max_gb=.000001,height=0),progress=events.append)
    self.assertGreater(output.stat().st_size,1000)
    self.assertEqual(events[-1]['total_passes'],1);self.assertEqual(len(commands),1)
    self.assertNotIn('-pass',commands[0]);self.assertNotIn('-maxrate',commands[0]);self.assertEqual(engine.probe(output)['height'],240)
    def audio_hash(path):
     return hashlib.sha256(subprocess.check_output([self.ffmpeg,'-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','adts','-'],**engine._flags())).digest()
    self.assertEqual(audio_hash(output),audio_hash(self.source))
 def test_flac_audio_preserved_as_alac(self):
  source=self.root/'flac.mkv'
  subprocess.run([self.ffmpeg,'-y','-v','error','-i',str(self.source),'-c:v','copy','-c:a','flac',str(source)],check=True,**engine._flags())
  output=engine.compress(source,self.root,engine.CompressionOptions(mode='cpu',unlimited=True))
  self.assertEqual(engine.probe(output)['audio'][0]['codec_name'],'alac')
  def decoded(path):return subprocess.check_output([self.ffmpeg,'-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-'],**engine._flags())
  self.assertEqual(decoded(source),decoded(output))
if __name__=='__main__':unittest.main()
