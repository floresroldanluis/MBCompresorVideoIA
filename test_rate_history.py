"""Exact-input local rate history, isolated from real user preferences."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import engine
import rate_history


class RateHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.source=self.root/'source.mkv';self.source.write_bytes(b'keep original')
        self.ffmpeg=self.root/'encoder.exe';self.ffmpeg.write_bytes(b'encoder binary')
        self.ffprobe=self.root/'probe.exe';self.ffprobe.write_bytes(b'probe binary')
        self.output=self.root/'output.mp4';self.output.write_bytes(b'v'*980000)
        self.options=engine.CompressionOptions(codec='h265',height=240,max_gb=1000000/2**30)
        self.info=dict(duration=120.,width=320,height=240,video={'index':0,'avg_frame_rate':'24/1'},audio=[],subtitles=[])
        self.cache=self.root/'state/history.json'
        self.context=self.context_for()

    def context_for(self,**changes):
        arguments=dict(source=self.source,ffmpeg=self.ffmpeg,ffprobe=self.ffprobe,
                       options=self.options,driver='GPU test, driver 1',source_info=self.info)
        arguments.update(changes)
        return rate_history.build_context(**arguments)

    def probe(self,path,*args):
        if Path(path)==self.source:return self.info
        size=Path(path).stat().st_size
        return dict(width=320,height=240,video=dict(codec_name='hevc',pix_fmt='yuv420p',
                    bit_rate=str(size*8/120),duration='120',avg_frame_rate='24/1'))

    def record(self,context=None,probe=None):
        return rate_history.record_validated(context or self.context,64000,self.output,
                                             probe or self.probe,'verified test',self.cache)

    def test_exact_plan_reuses_verified_rate_and_keeps_source_and_output(self):
        self.assertTrue(self.record())
        entry=rate_history.lookup(self.context,self.cache)
        self.assertEqual(entry['requested_bitrate'],64000)
        self.assertEqual(entry['provenance'],'verified test')
        self.assertEqual(self.source.read_bytes(),b'keep original')
        self.assertEqual(self.output.stat().st_size,980000)
        stored=self.cache.read_text()
        self.assertNotIn(str(self.source),stored)

    def test_output_names_and_installation_path_do_not_change_identical_plan(self):
        renamed=copy.copy(self.options);renamed.output_name='custom.mp4'
        self.assertEqual(self.context_for(options=renamed)['key'],self.context['key'])
        other=self.root/'new-version/encoder.exe';other.parent.mkdir();other.write_bytes(self.ffmpeg.read_bytes())
        self.assertEqual(self.context_for(ffmpeg=other)['key'],self.context['key'])

    def test_changed_profile_limit_driver_binary_or_source_misses(self):
        self.assertTrue(self.record())
        changed=copy.copy(self.options);changed.max_gb=1100000/2**30
        other=copy.copy(self.options);other.audio_indices=[]
        for context in (self.context_for(options=changed),self.context_for(options=other),
                        self.context_for(driver='GPU test, driver 2')):
            self.assertIsNone(rate_history.lookup(context,self.cache))
        self.ffmpeg.write_bytes(b'different encoder binary')
        self.assertIsNone(rate_history.lookup(self.context_for(),self.cache))
        self.source.write_bytes(b'changed original, keep it')
        self.assertIsNone(rate_history.lookup(self.context,self.cache))

    def test_unknown_short_unlimited_or_adaptive_plan_is_not_cached(self):
        for changes in (dict(duration=2.),dict(video={'avg_frame_rate':'0/0'})):
            info=dict(self.info,**changes)
            self.assertIsNone(self.context_for(source_info=info))
        for options in (engine.CompressionOptions(unlimited=True),engine.CompressionOptions(local_ai=True)):
            self.assertIsNone(self.context_for(options=options))
        self.assertIsNone(self.context_for(driver=''))
        self.assertIsNone(self.context_for(ffmpeg=self.root/'missing.exe'))

    def test_wrong_resolution_fps_duration_codec_or_oversize_is_not_recorded(self):
        for field,value in (('codec_name','h264'),('pix_fmt','yuv420p10le'),('duration','100'),('avg_frame_rate','30/1')):
            info=self.probe(self.output);info['video'][field]=value
            self.assertFalse(self.record(probe=lambda _,info=info:info))
        info=self.probe(self.output);info['height']=120
        self.assertFalse(self.record(probe=lambda _:info))
        self.output.write_bytes(b'v'*1000001)
        self.assertFalse(self.record())
        self.assertFalse(self.cache.exists())

    def test_probe_or_permission_failure_is_best_effort(self):
        def failed(_):raise engine.EncodeError('metadata unavailable')
        self.assertFalse(self.record(probe=failed))
        with patch('rate_history.os.replace',side_effect=PermissionError('read only')):
            self.assertFalse(self.record())
        self.assertFalse(list(self.cache.parent.glob('*.tmp')))
        self.assertEqual(self.output.stat().st_size,980000)

    def test_invalid_json_or_entry_is_ignored_and_can_be_replaced(self):
        self.cache.parent.mkdir();self.cache.write_text('{incomplete')
        self.assertIsNone(rate_history.lookup(self.context,self.cache))
        self.assertTrue(self.record())
        data=json.loads(self.cache.read_text());data['records'][self.context['key']]['requested_bitrate']='999999'
        self.cache.write_text(json.dumps(data))
        self.assertIsNone(rate_history.lookup(self.context,self.cache))
        self.assertTrue(self.record())

    def test_concurrent_writes_and_bounded_history_remain_valid_json(self):
        contexts=[dict(self.context,key=hashlib.sha256(str(i).encode()).hexdigest()) for i in range(20)]
        results=[]
        threads=[threading.Thread(target=lambda context=context:results.append(self.record(context))) for context in contexts]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertEqual(results,[True]*20)
        self.assertEqual(len(json.loads(self.cache.read_text())['records']),20)
        with patch('rate_history.MAX_RECORDS',8):
            self.cache.unlink()
            for context in contexts[:12]:self.assertTrue(self.record(context))
            self.assertEqual(len(json.loads(self.cache.read_text())['records']),8)

    def run_encode(self,actions,mode='auto'):
        calls=[];options=copy.copy(self.options);options.mode=mode
        def run(cmd,*args):
            calls.append(cmd)
            action=actions[len(calls)-1]
            if isinstance(action,Exception):raise action
            if cmd[-1]!=engine.os.devnull:Path(cmd[-1]).write_bytes(b'v'*action)
        with patch('rate_history.history_path',return_value=self.cache),patch.object(engine,'probe',side_effect=self.probe),\
             patch.object(engine,'_run',side_effect=run),patch('acceleration.nvidia_info',return_value='GPU test, driver 1'),\
             patch('acceleration.find_nvenc',return_value=dict(available=True,path=str(self.ffmpeg))):
            output=engine.compress(self.source,self.root,options,ffmpeg=str(self.ffmpeg),ffprobe=str(self.ffprobe))
        return calls,output

    def test_engine_reuses_measured_rate_on_first_encode_and_records_the_chosen_rate(self):
        self.assertTrue(self.record())
        calls,output=self.run_encode([980000])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][calls[0].index('-b:v')+1],'64000')
        self.assertEqual(output.stat().st_size,980000)
        self.assertEqual(rate_history.lookup(self.context,self.cache)['requested_bitrate'],64000)

    def test_failed_cached_gpu_fallback_uses_normal_cpu_budget(self):
        self.assertTrue(self.record())
        calls,output=self.run_encode([engine.EncodeError('GPU unavailable'),0,980000])
        self.assertEqual(calls[0][calls[0].index('-b:v')+1],'64000')
        for cmd in calls[1:]:self.assertEqual(cmd[cmd.index('-b:v')+1],str(int(1000000*8/120*.985)))
        self.assertLessEqual(output.stat().st_size,1000000)

    def test_refinement_records_the_selected_candidate_not_a_later_worse_rate(self):
        self.assertTrue(self.record())
        calls,output=self.run_encode([800000,790000])
        self.assertEqual(len(calls),2)
        self.assertGreater(int(calls[1][calls[1].index('-b:v')+1]),64000)
        self.assertEqual(output.stat().st_size,800000)
        self.assertEqual(rate_history.lookup(self.context,self.cache)['requested_bitrate'],64000)

    def test_failed_optional_history_write_does_not_turn_success_into_an_error(self):
        with patch('rate_history.os.replace',side_effect=PermissionError('read only')):
            _,output=self.run_encode([980000])
        self.assertTrue(output.is_file())
        self.assertEqual(self.source.read_bytes(),b'keep original')

    def test_unexpected_history_failure_does_not_report_a_successful_mp4_as_failed(self):
        with patch('rate_history.record_validated',side_effect=RuntimeError('optional history unavailable')):
            _,output=self.run_encode([980000])
        self.assertEqual(output.stat().st_size,980000)
        self.assertEqual(self.source.read_bytes(),b'keep original')


if __name__=='__main__':unittest.main()
