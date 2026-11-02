import unittest
from unittest.mock import patch
import engine
import acceleration
import test_engine as _engine_tests

class AccelerationChecks(_engine_tests.EngineChecks):
    def test_auto_without_gpu_uses_cpu(self):
        events=[]
        with patch('acceleration.find_nvenc',return_value=dict(available=False,error='simulado')):
            result=engine.compress(self.source,self.root,engine.CompressionOptions(mode='auto',max_gb=.0003),progress=events.append)
        self.assertTrue(result.exists());self.assertEqual(events[-1]['backend'],'cpu')
    def test_explicit_nvidia_unavailable(self):
        with patch('acceleration.find_nvenc',return_value=dict(available=False,error='simulado')):
            with self.assertRaisesRegex(engine.EncodeError,'NVIDIA no está disponible'):
                engine.compress(self.source,self.root,engine.CompressionOptions(mode='nvidia'))
    def test_actual_nvidia_codecs(self):
        for codec in ('h264','h265'):
            if not acceleration.find_nvenc(self.ffmpeg,codec)['available']:self.skipTest('NVIDIA no disponible')
            events=[]
            result=engine.compress(self.source,self.root,engine.CompressionOptions(mode='nvidia',codec=codec,max_gb=.0003),progress=events.append)
            self.assertLessEqual(result.stat().st_size,300000)
            self.assertEqual(events[-1]['backend'],'nvidia');self.assertEqual(events[-1]['total_passes'],1)
            self.assertEqual(engine.probe(result)['height'],240)
    def test_auto_runtime_failure_falls_back(self):
        original=engine._run; calls=[];events=[]
        def failing_once(*args,**kwargs):
            calls.append(1)
            if len(calls)==1:raise engine.EncodeError('GPU failure simulation')
            return original(*args,**kwargs)
        with patch('acceleration.find_nvenc',return_value=dict(available=True,path=self.ffmpeg)),patch('engine._run',side_effect=failing_once):
            result=engine.compress(self.source,self.root,engine.CompressionOptions(mode='auto',max_gb=.0003),progress=events.append)
        self.assertTrue(result.exists());self.assertEqual(events[-1]['backend'],'cpu');self.assertEqual(len(calls),3)

if __name__=='__main__':unittest.main()
