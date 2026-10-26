import os,tempfile,unittest,dataclasses
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import app
class UnlimitedUITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
 def test_toggle_persistence_and_pending_options(self):
  with tempfile.TemporaryDirectory() as t,patch.object(app,'DATA',Path(t)),patch.object(app,'CONFIG',Path(t)/'settings.json'):
   w=app.MainWindow();self.assertTrue(w.limit.isEnabled());w.unlimited.setChecked(True);self.assertFalse(w.limit.isEnabled());self.assertFalse(app.load_settings()['unlimited'])
   item=app.Item('sample.mkv',app.load_settings());item.state='Pendiente';w.items=[item]
   with patch.object(w,'refresh_row'),patch.object(w,'persist_queue'):w.apply_globals()
   self.assertTrue(item.options.unlimited);self.assertTrue(dataclasses.asdict(item.options)['unlimited'])
   w.unlimited.setChecked(False)
   with patch.object(w,'refresh_row'),patch.object(w,'persist_queue'):w.apply_globals()
   self.assertFalse(item.options.unlimited);self.assertTrue(w.limit.isEnabled());w.items=[];w._allow_close=True;w.close()
 def test_previous_saved_check_does_not_enable_on_start(self):
  import json
  with tempfile.TemporaryDirectory() as t,patch.object(app,'DATA',Path(t)),patch.object(app,'CONFIG',Path(t)/'settings.json'):
   app.CONFIG.write_text(json.dumps({'unlimited':True,'max_gb':2.5}),encoding='utf-8')
   w=app.MainWindow();self.assertFalse(w.unlimited.isChecked());self.assertTrue(w.limit.isEnabled());self.assertEqual(w.limit.value(),2.5)
   self.assertFalse(any(label.text()=='Calidad sin tamaño objetivo' for label in w.findChildren(app.QLabel)))
   w.unlimited.setChecked(True);self.assertTrue(w.unlimited.isChecked());self.assertFalse(w.limit.isEnabled())
   w._allow_close=True;w.close();w2=app.MainWindow();self.assertFalse(w2.unlimited.isChecked());self.assertTrue(w2.limit.isEnabled());w2._allow_close=True;w2.close()
if __name__=='__main__':unittest.main()
