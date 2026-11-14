"""Fresh installation and one-time migration of the high-efficiency profile."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class QualityProfileTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for key,value in (('DATA',root),('CONFIG',root/'settings.json')):
            replacement=patch.object(app,key,value)
            replacement.start();self.addCleanup(replacement.stop)

    def test_fresh_installation_prioritizes_hevc_full_hd_with_limit(self):
        settings=app.load_settings()
        item=app.Item('movie.mkv',settings)
        self.assertEqual((item.options.codec,item.options.height,item.options.speed),('h265',1080,'calidad'))
        self.assertEqual(item.options.max_gb,1.95)
        self.assertFalse(item.options.unlimited)

    def test_upgrade_keeps_size_and_location_but_adopts_quality_profile_once(self):
        old=dict(codec='h264',height=720,speed='rapido',max_gb=3.5,output_folder='D:/Output',
                 default_profile_version=2,output_profile_version=5,full_hd_profile_version=1)
        app.CONFIG.write_text(json.dumps(old),encoding='utf-8')
        settings=app.load_settings()
        self.assertEqual((settings['codec'],settings['height'],settings['speed']),('h265',1080,'calidad'))
        self.assertEqual((settings['max_gb'],settings['output_folder']),(3.5,'D:/Output'))
        app.save_settings(dict(settings,codec='h264',height=720,speed='rapido'))
        chosen=app.load_settings()
        self.assertEqual((chosen['codec'],chosen['height'],chosen['speed']),('h264',720,'rapido'))


if __name__=='__main__':unittest.main()
