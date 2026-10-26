import unittest
from unittest.mock import patch
import motors
class MotorLatestChecks(unittest.TestCase):
 def test_online_version_endpoint(self):
  with patch('motors._read',return_value=b'9.0.2') as fetch:
   versions,errors=motors._latest()
  fetch.assert_called_once_with('https://www.gyan.dev/ffmpeg/builds/release-version')
  self.assertEqual(versions,{'ffmpeg':'9.0.2','ffprobe':'9.0.2'});self.assertEqual(errors,{})
 def test_offline_preserves_diagnostic(self):
  with patch('motors._read',side_effect=OSError('sin conexion')):
   versions,errors=motors._latest()
  self.assertIsNone(versions['ffprobe']);self.assertIn('sin conexion',errors['ffmpeg'])
 def test_online_inspection(self):
  with patch('motors._read',return_value=b'9.0.2'):
   report=motors.inspect_motors({'compression_only':True},online=True)
  self.assertTrue(report['ready']);self.assertEqual(report['entries']['ffmpeg']['latest'],'9.0.2')
if __name__=='__main__':unittest.main()
