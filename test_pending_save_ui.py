import os,json,tempfile,time,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import app
class PendingSaveUITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.patches=[patch.object(app,'DATA',self.root/'state'),patch.object(app,'CONFIG',self.root/'state/settings.json')]
  for p in self.patches:p.start()
  self.windows=[]
 def make(self):
  w=app.MainWindow();w.show();self.qt.processEvents();self.windows.append(w);return w
 def pending(self,w):
  source=self.root/'input.mkv';source.write_bytes(b'original')
  result=self.root/'.mb-compress-test/result.mp4';result.parent.mkdir(exist_ok=True);result.write_bytes(b'validated-video')
  target=self.root/'input.mp4';item=app.Item(str(source),w.settings);item.info=dict(duration=10,width=1920,height=1080,size=1000,audio=[],subtitles=[]);w.items=[item];w.build_row(item)
  w._apply_output_result(item,'Guardado pendiente',json.dumps(dict(recovery_path=str(result),desired_target=str(target),message='MP4 completo conservado')));w.refresh_row(item)
  return item,result,target
 def test_retry_only_publishes_without_encoding(self):
  w=self.make();item,result,target=self.pending(w)
  self.assertTrue(w.rows[item.path]['open'].isEnabled());self.assertTrue(w.rows[item.path]['retry_save'].isEnabled())
  with patch('app.engine.compress',side_effect=AssertionError('No recodificar')) as encode:
   w.retry_save(item);deadline=time.monotonic()+5
   while w.saving and time.monotonic()<deadline:self.qt.processEvents();time.sleep(.005)
   self.assertFalse(w.saving);encode.assert_not_called()
  self.assertEqual(item.state,'Completado');self.assertEqual(target.read_bytes(),b'validated-video');self.assertFalse(result.exists());self.assertEqual(Path(item.path).read_bytes(),b'original');self.assertEqual(w.pending_saves,{})
 def test_restart_keeps_pending_but_initial_queue_empty(self):
  w=self.make();item,result,target=self.pending(w);w._allow_close=True;w.close()
  w2=self.make();self.assertEqual(w2.items,[]);self.assertFalse(w2.recovery_notice.isHidden())
  with patch('app.engine.probe',side_effect=AssertionError('No analizar original')):w2.restore_pending_saves()
  self.assertEqual(len(w2.items),1);restored=w2.items[0];self.assertEqual(restored.state,'Guardado pendiente');self.assertEqual(restored.output,str(result));self.assertEqual(restored.recovery_target,str(target))
 def test_recovery_failure_keeps_file(self):
  w=self.make();item,result,target=self.pending(w)
  error=app.engine.OutputPublishError('Ocupado',result,target)
  with patch('app.engine.finalize_output',side_effect=error):
   w.retry_save(item);deadline=time.monotonic()+5
   while w.saving and time.monotonic()<deadline:self.qt.processEvents();time.sleep(.005)
  self.assertEqual(item.state,'Guardado pendiente');self.assertTrue(result.exists());self.assertTrue(w.rows[item.path]['retry_save'].isEnabled())
 def test_full_hd_profile_and_obsolete_update_log(self):
  state=self.root/'state';(state/'updates').mkdir(parents=True)
  (state/'settings.json').write_text(json.dumps(dict(height=720,speed='rapido',max_gb=2.5)),encoding='utf-8')
  (state/'updates/last-result.json').write_text(json.dumps(dict(ok=True,version='v1.0.39')),encoding='utf-8')
  w=self.make();self.assertEqual(w.res.currentData(),1080);self.assertEqual(w.speed.currentData(),'calidad');self.assertEqual(w.limit.value(),2.5)
  self.assertNotIn('Actualización instalada: v1.0.39',w.logs.toPlainText());self.assertIn(app.VERSION,w.logs.toPlainText())
 def tearDown(self):
  for w in self.windows:
   if hasattr(w,'save_worker'):w.save_worker.join(5)
   w._allow_close=True;w.close()
  self.qt.processEvents()
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
if __name__=='__main__':unittest.main()
