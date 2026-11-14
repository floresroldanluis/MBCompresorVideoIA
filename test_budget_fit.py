"""Bounded NVENC quality refinement keeps a verified result and the exact cap."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine


class BudgetFitChecks(unittest.TestCase):
    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root=Path(self.workspace.name)
        self.source=self.root/'película original.mkv'
        self.source.write_bytes(b'original video, never replace')
        self.info=dict(duration=1.,width=1920,height=1080,video={'index':0},audio=[],subtitles=[])
        self.calls=[];self.paths=[];self.messages=[];self.output_payloads=[]
        self.actions=[];self.fixed_by_size={};self.metadata_missing=False;self.missing_sizes=set()

    def probe(self,path,*args):
        if Path(path)==self.source:return self.info
        if self.metadata_missing:raise engine.EncodeError('optional metadata unavailable')
        size=Path(path).stat().st_size
        if size in self.missing_sizes:raise OSError('optional metadata became unavailable')
        fixed=self.fixed_by_size.get(size,10000)
        return dict(video={'bit_rate':str(max(1,size-fixed)*8),'duration':'1'})

    def encode(self,cmd,control,*args):
        self.calls.append(cmd)
        if cmd[-1]==engine.os.devnull:return
        result=Path(cmd[-1]);self.paths.append(result)
        self.assertEqual(cmd[cmd.index('-i')+1],str(self.source))
        self.assertEqual(result.suffix,'.part')
        self.assertEqual(cmd[-3:-1],['-f','mp4'])
        action=self.actions[len(self.paths)-1]
        if callable(action):return action(result,control)
        if isinstance(action,Exception):
            result.write_bytes(b'incomplete refinement')
            raise action
        payload=bytes([len(self.paths)])*action
        result.write_bytes(payload);self.output_payloads.append(payload)

    def compress(self,actions,options=None,control=None):
        self.actions=actions
        options=options or engine.CompressionOptions(mode='nvidia',max_gb=.0001)
        with patch.object(engine,'probe',side_effect=self.probe),patch.object(engine,'_run',side_effect=self.encode),patch('acceleration.find_nvenc',return_value=dict(available=True,path='gpu-ffmpeg')):
            output=engine.compress(self.source,self.root,options,control=control,log=self.messages.append,ffmpeg='ffmpeg',ffprobe='ffprobe')
        self.assertEqual(self.source.read_bytes(),b'original video, never replace')
        self.assertEqual(list(self.root.glob('.mb-compress-*')),[])
        return output

    def test_underfilled_nvenc_refines_from_original_with_separate_temporaries(self):
        output=self.compress([80000,97000])
        self.assertEqual(output.stat().st_size,97000)
        self.assertEqual(len(self.paths),2)
        self.assertEqual(len(set(self.paths)),2)
        first=int(self.calls[0][self.calls[0].index('-b:v')+1])
        second=int(self.calls[1][self.calls[1].index('-b:v')+1])
        self.assertEqual(first,788000);self.assertGreater(second,first)
        for cmd in self.calls:
            self.assertNotIn('-cq',cmd)
            self.assertIn('scale=-2:1080:',cmd[cmd.index('-vf')+1])
            self.assertEqual(cmd[cmd.index('-profile:v')+1],'high')
        self.assertTrue(any('Se refina desde el original' in message for message in self.messages))

    def test_near_cap_output_does_not_repeat(self):
        self.compress([95000])
        self.assertEqual(len(self.paths),1)

    def test_missing_optional_video_metadata_does_not_repeat_or_fail(self):
        self.metadata_missing=True
        self.compress([80000])
        self.assertEqual(len(self.paths),1)

    def test_no_upward_refinement_for_cpu_or_unlimited(self):
        for options in (engine.CompressionOptions(mode='cpu',max_gb=.0001),engine.CompressionOptions(mode='nvidia',unlimited=True)):
            self.calls=[];self.paths=[]
            output=self.compress([80000],options)
            self.assertEqual(output.stat().st_size,80000)
            self.assertEqual(len(self.paths),1)

    def test_failed_refinement_keeps_the_verified_candidate_without_cpu_fallback(self):
        output=self.compress([80000,engine.EncodeError('refinement failed')],engine.CompressionOptions(mode='auto',max_gb=.0001))
        self.assertEqual(output.read_bytes(),self.output_payloads[0])
        self.assertEqual(len(self.paths),2)
        self.assertTrue(all(cmd[0]=='gpu-ffmpeg' for cmd in self.calls))

    def test_os_error_during_refinement_keeps_verified_candidate(self):
        output=self.compress([80000,OSError('encoder could not start')])
        self.assertEqual(output.read_bytes(),self.output_payloads[0])

    def test_lost_optional_measurement_keeps_previous_verified_candidate(self):
        self.missing_sizes={90000}
        output=self.compress([80000,90000])
        self.assertEqual(output.read_bytes(),self.output_payloads[0])

    def test_optional_budget_stat_error_does_not_destroy_a_valid_output(self):
        with patch.object(engine,'_measured_output_budget',side_effect=OSError('transient access error')):
            output=self.compress([80000])
        self.assertEqual(output.stat().st_size,80000)

    def test_four_attempts_never_publish_an_oversize_refinement(self):
        output=self.compress([80000,110000,105000,101000])
        self.assertEqual(len(self.paths),4)
        self.assertEqual(output.read_bytes(),self.output_payloads[0])
        self.assertLessEqual(output.stat().st_size,100000)

    def test_impossible_downward_retry_keeps_the_valid_candidate(self):
        with patch.object(engine,'_retry_video_bitrate',return_value=5000):
            output=self.compress([80000,110000])
        self.assertEqual(output.read_bytes(),self.output_payloads[0])
        self.assertEqual(len(self.paths),2)

    def test_obsolete_attempts_are_removed_before_the_next_encoding(self):
        def third(result,control):
            self.assertTrue(self.paths[0].exists())
            self.assertFalse(self.paths[1].exists())
            result.write_bytes(bytes([3])*97000)
        output=self.compress([80000,110000,third])
        self.assertEqual(output.stat().st_size,97000)

    def test_oversize_only_results_still_fail_without_publication(self):
        with self.assertRaises(engine.EncodeError):self.compress([110000]*4)
        self.assertEqual(len(self.paths),4)
        self.assertEqual(list(self.root.glob('*.mp4')),[])
        self.assertEqual(list(self.root.glob('.mb-compress-*')),[])

    def test_a_smaller_worse_refinement_does_not_replace_the_best_result(self):
        output=self.compress([80000,79000])
        self.assertEqual(len(self.paths),2)
        self.assertEqual(output.read_bytes(),self.output_payloads[0])

    def test_best_candidate_uses_video_bytes_rather_than_total_file_size(self):
        self.fixed_by_size={90000:20000,85000:10000}
        output=self.compress([90000,85000,engine.EncodeError('stop refinement')])
        self.assertEqual(output.read_bytes(),self.output_payloads[1])
        self.assertEqual(output.stat().st_size,85000)

    def test_cancel_keeps_verified_candidate_for_manual_save_without_deleting_unknowns(self):
        unknown=[]
        def cancel(result,control):
            result.write_bytes(b'incomplete refinement')
            other=result.parent/'payload_ffffffffffff.part'
            other.write_bytes(b'unknown file must remain');unknown.append(other)
            control.cancel();raise engine.EncodeCancelled('user stopped')
        with self.assertRaises(engine.OutputPublishCancelled) as failure:self.compress([80000,cancel])
        pending=failure.exception
        self.assertIsInstance(pending,engine.EncodeCancelled)
        self.assertEqual(pending.recovery_path.read_bytes(),self.output_payloads[0])
        self.assertFalse(self.paths[-1].exists())
        self.assertEqual(list(self.root.glob('*.mp4')),[])
        marker=json.loads((pending.recovery_path.parent/'recovery.json').read_text(encoding='utf-8'))
        self.assertEqual(marker['result_name'],pending.recovery_path.name)
        self.assertNotIn(unknown[0].name,marker['files'])
        output=engine.finalize_output(pending.recovery_path,pending.desired_target,source=self.source)
        self.assertEqual(output.stat().st_size,80000)
        self.assertFalse(engine.cleanup_recovery_workspace(pending.recovery_path))
        self.assertEqual(unknown[0].read_bytes(),b'unknown file must remain')


class RefinementMathChecks(unittest.TestCase):
    def test_actual_three_hour_output_has_a_useful_video_budget_increase(self):
        duration=11304.704
        fixed=325802*duration/8+2000000
        next_rate=engine._refine_video_bitrate(983280,1950000000,1691103297,fixed)
        self.assertGreater(next_rate,1100000)
        self.assertLess(next_rate,1500000)

    def test_no_refinement_without_useful_measured_gain(self):
        self.assertIsNone(engine._refine_video_bitrate(1000000,100000,94000,10000))
        self.assertIsNone(engine._refine_video_bitrate(1000000,100000,93000,0))


if __name__=='__main__':unittest.main()
