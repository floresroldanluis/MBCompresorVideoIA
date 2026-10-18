"""Release checks, ZIP validation, and real Windows apply/rollback in temporary roots."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import zipfile
import app_updates as updates

class UpdateTests(unittest.TestCase):
    def test_no_release_and_version_comparison(self):
        with patch.object(updates,'_read',side_effect=urllib.error.HTTPError('url',404,'Not Found',{},None)):
            result=updates.check_update(updates.DEFAULT_REPOSITORY,'1.0.6')
        self.assertFalse(result['installable']);self.assertIn('Todavía',result['message'])
        with patch.object(updates,'_read',return_value=json.dumps({'tag_name':'v1.0.6','assets':[]}).encode()):
            self.assertFalse(updates.check_update(updates.DEFAULT_REPOSITORY,'1.0.6')['available'])
        self.assertGreater(updates.version_tuple('v1.10.0'),updates.version_tuple('1.9.9'))
    def test_release_digest_and_foreign_repository(self):
        asset=dict(name='MBCompresorVideoIA-v1.0.7-windows-x64.zip',browser_download_url=updates.DEFAULT_REPOSITORY+'/releases/download/v1.0.7/app.zip',digest='sha256:'+'a'*64)
        with patch.object(updates,'_read',return_value=json.dumps({'tag_name':'v1.0.7','assets':[asset]}).encode()):
            result=updates.check_update(updates.DEFAULT_REPOSITORY,'1.0.6');self.assertTrue(result['installable'])
        asset['browser_download_url']='https://github.com/other/repo/releases/download/v1/app.zip'
        with patch.object(updates,'_read',return_value=json.dumps({'tag_name':'v1.0.7','assets':[asset]}).encode()):
            with self.assertRaises(ValueError):updates.check_update(updates.DEFAULT_REPOSITORY,'1.0.6')
    def test_zip_validation_and_hash_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape.exe',b'MZ')
            with self.assertRaises(ValueError):updates.extract_package(archive,root/'extracted')
            self.assertFalse((root/'escape.exe').exists())
            with zipfile.ZipFile(root/'good.zip','w') as z:
                for name,content in [(updates.APP_EXE,b'MZnew'),('_internal/python314.dll',b'runtime'),('tools/ffmpeg.exe',b'ffmpeg'),('tools/ffprobe.exe',b'ffprobe')]:z.writestr('MB Compresor Pro/'+name,content)
            self.assertEqual(updates.extract_package(root/'good.zip',root/'good') .name,'MB Compresor Pro')
            release=dict(installable=True,repository='floresroldanluis/MBCompresorVideoIA',latest='v1.0.7',asset=dict(browser_download_url=updates.DEFAULT_REPOSITORY+'/releases/download/v1.0.7/a.zip',sha256='0'*64))
            with patch.object(updates.urllib.request,'urlopen',return_value=io.BytesIO((root/'good.zip').read_bytes())):
                with self.assertRaisesRegex(ValueError,'SHA256'):updates.prepare_update(release,root/'updates')
            self.assertEqual(list((root/'updates').iterdir()),[])
    @unittest.skipUnless(os.name=='nt','Windows updater')
    def test_real_apply_and_rollback_preserve_preferences(self):
        for fail in (False,True):
            with self.subTest(failure=fail),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);install=root/'installed';package=root/'stage'/'package';stage=package.parent
                for base,marker in [(install,b'old'),(package,b'new')]:
                    (base/'_internal').mkdir(parents=True);(base/'_internal'/'python314.dll').write_bytes(marker)
                    (base/updates.APP_EXE).write_bytes(b'MZ'+marker)
                    if base==install or not fail:
                        (base/'tools').mkdir();(base/'tools'/'ffmpeg.exe').write_bytes(marker);(base/'tools'/'ffprobe.exe').write_bytes(marker)
                (install/'settings.json').write_text('keep preferences');(install/'queue.json').write_text('keep queue')
                prepared=dict(stage=str(stage),package=str(package),version='v1.0.7')
                script,plan=updates.make_install_plan(prepared,install,0,restart=False)
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Manifest',str(plan)],capture_output=True,text=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode,1 if fail else 0,result.stderr+((root/'last-result.json').read_text(encoding='utf-8-sig') if (root/'last-result.json').exists() else ''))
                expected=b'old' if fail else b'new'
                self.assertEqual((install/'_internal'/'python314.dll').read_bytes(),expected)
                self.assertEqual((install/updates.APP_EXE).read_bytes(),b'MZ'+expected)
                self.assertEqual((install/'tools'/'ffmpeg.exe').read_bytes(),expected)
                self.assertEqual((install/'settings.json').read_text(),'keep preferences')
                self.assertEqual((install/'queue.json').read_text(),'keep queue')

if __name__=='__main__':unittest.main()

