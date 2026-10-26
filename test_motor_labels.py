import unittest
from types import SimpleNamespace
from settings_dialog import SettingsDialog
class Text:
 def setText(self,value):self.value=value
class MotorLabelTests(unittest.TestCase):
 def render(self,entry):
  target=SimpleNamespace(motor_status=Text(),status=Text())
  SettingsDialog.show_motor_report(target,{'entries':{'ffmpeg':entry},'installed_encoders':['libx264','h264_amf','h264_nvenc']})
  return target.motor_status.value
 def test_missing(self):self.assertIn('No instalado',self.render({'installed':False}))
 def test_broken(self):self.assertIn('Instalado · requiere reparación',self.render({'installed':True,'healthy':False}))
 def test_update(self):self.assertIn('requiere actualización',self.render({'installed':True,'healthy':True,'update_available':True,'latest':'10'}))
 def test_unknown(self):self.assertIn('actualización no comprobada',self.render({'installed':True,'healthy':True}))
 def test_current(self):self.assertIn('Instalado · actualizado',self.render({'installed':True,'healthy':True,'update_available':False}))
 def test_included_but_incompatible(self):self.assertIn('Instalado en FFmpeg · requiere GPU/controlador compatible',self.render({}))
if __name__=='__main__':unittest.main()
