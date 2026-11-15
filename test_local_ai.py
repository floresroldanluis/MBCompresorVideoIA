"""Offline adviser privacy, profile preservation and bounded inference checks."""
import json
import unittest
from unittest.mock import patch

from local_ai import LocalAIError,OllamaClient,_NoRedirect,_technical_metadata


class LocalAIChecks(unittest.TestCase):
    def setUp(self):
        self.client=OllamaClient()
        self.requests=[]
        self.response=dict(codec='hevc',resolution=720,fps=30,preset='slow',audio_kbps=256,reason='Mejor detalle por tamaño.')
        self.details={}
        self.test_answer={'ok':True}

    def request(self,path,payload=None):
        self.requests.append((path,payload))
        if path=='/api/version':return {'version':'0.40.16'}
        if path=='/api/tags':return {'models':[{'name':'qwen3:0.6b'}]}
        if path=='/api/show':return self.details
        if path=='/api/generate':
            answer=self.test_answer if payload.get('options',{}).get('num_predict')==32 else self.response
            return {'response':json.dumps(answer)}
        raise AssertionError(path)

    def recommend(self,metadata=None):
        metadata=metadata or dict(duration=120,height=1080,width=1920,video={'codec_name':'h264','avg_frame_rate':'24/1'},audio=[{'channels':6}])
        with patch.object(self.client,'_request',side_effect=self.request):
            return self.client.recommend(metadata,1.95,'qwen3:0.6b')

    def test_nested_probe_metadata_is_normalized_without_paths_or_media(self):
        metadata=dict(path='C:/private/movie.mkv',title='private title',frames=b'media',
                      duration=11304.704,width=1920,height=1080,size=9578490766,
                      video={'codec_name':'h264','avg_frame_rate':'24/1','bit_rate':'9000000','tags':{'filename':'secret'}},
                      audio=[{'channels':6,'tags':{'title':'secret track'}}])
        self.recommend(metadata)
        payload=next(payload for path,payload in self.requests if path=='/api/generate')
        facts=json.loads(payload['prompt'])['metadata']
        self.assertEqual(facts,dict(duration=11304.704,width=1920,height=1080,size=9578490766,
                                    bitrate=9000000,audio_channels=6,fps=24,codec='h264'))
        self.assertNotIn('private',payload['prompt']);self.assertNotIn('secret',payload['prompt'])
        self.assertNotIn('media',payload['prompt'])

    def test_invalid_technical_values_and_codec_path_are_not_transmitted(self):
        facts=_technical_metadata(dict(width=True,height=float('inf'),duration=float('nan'),
            fps='0/0',codec='C:/private/title.mp4',size=-1))
        self.assertEqual(facts,{})

    def test_explicit_resolution_and_original_fps_are_preserved(self):
        advice=self.recommend(dict(duration=120,height=1080,width=1920,fps=24,profile_height=1080,profile_fps=0))
        self.assertEqual((advice['resolution'],advice['fps']),(1080,0))

    def test_without_explicit_profile_advice_cannot_upscale_or_raise_source_fps(self):
        self.response.update(resolution=2160,fps=60)
        advice=self.recommend()
        self.assertEqual((advice['resolution'],advice['fps']),(0,0))

    def test_own_recommendation_unloads_model_after_answer(self):
        self.recommend()
        payload=next(payload for path,payload in self.requests if path=='/api/generate')
        self.assertEqual(payload['keep_alive'],0)
        self.assertFalse(payload['stream']);self.assertFalse(payload['think'])
        self.assertEqual(payload['options']['num_predict'],256)

    def test_lower_quality_model_advice_cannot_override_explicit_quality_profile(self):
        self.response.update(codec='h264',resolution=720,fps=30,audio_kbps=128,preset='medium')
        advice=self.recommend(dict(duration=11304.704,height=1080,width=1920,fps=24,
            profile_codec='h265',profile_height=1080,profile_fps=0,profile_audio_kbps=512,profile_preset='slow'))
        self.assertEqual((advice['codec'],advice['resolution'],advice['fps'],advice['audio_kbps'],advice['preset']),('hevc',1080,0,512,'slow'))
        self.assertIn('Se mantiene el perfil elegido',advice['reason'])
        self.assertLessEqual(len(advice['reason']),500)

    def test_invalid_explicit_profile_does_not_generate_advice(self):
        for profile in (dict(profile_codec='av1'),dict(profile_audio_kbps=999),
                        dict(profile_height=True),dict(profile_fps=30.0),dict(profile_preset='ultrafast')):
            self.requests=[]
            with self.subTest(profile=profile),self.assertRaises(LocalAIError):self.recommend(dict(duration=120,**profile))
            self.assertNotIn('/api/generate',[path for path,_ in self.requests])

    def test_none_resolution_and_fps_profiles_preserve_original(self):
        advice=self.recommend(dict(duration=120,height=1080,fps=24,profile_height=None,profile_fps=None))
        self.assertEqual((advice['resolution'],advice['fps']),(0,0))
        payload=next(payload for path,payload in self.requests if path=='/api/generate')
        facts=json.loads(payload['prompt'])['metadata']
        self.assertEqual((facts['profile_height'],facts['profile_fps']),(0,0))

    def test_preset_suggestions_cannot_lower_the_selected_quality(self):
        for selected,suggested,expected in (('slow','medium','slow'),('medium','fast','medium'),('fast','slow','slow')):
            with self.subTest(selected=selected,suggested=suggested):
                self.response['preset']=suggested
                advice=self.recommend(dict(duration=120,profile_preset=selected))
                self.assertEqual(advice['preset'],expected)

    def test_explicit_audio_minimum_allows_a_higher_suggestion(self):
        self.response['audio_kbps']=320
        advice=self.recommend(dict(duration=120,profile_audio_kbps=256))
        self.assertEqual(advice['audio_kbps'],320)

    def test_reason_length_is_bounded_before_policy_annotation(self):
        self.response['reason']='r'*501
        with self.assertRaises(LocalAIError):self.recommend()
        self.response['reason']='r'*500
        advice=self.recommend(dict(duration=120,profile_height=1080))
        self.assertLessEqual(len(advice['reason']),500)

    def test_models_excludes_cloud_and_remote_models(self):
        with patch.object(self.client,'_request',return_value={'models':[
            {'name':'local:1'},{'name':'model-cloud'},{'name':'remote:1','remote_host':'host'},None]}):
            self.assertEqual(self.client.models(),['local:1'])

    def test_status_does_not_load_a_model(self):
        with patch.object(self.client,'_request',side_effect=self.request):status=self.client.status()
        self.assertEqual(status,dict(version='0.40.16',models=['qwen3:0.6b'],local_only=True))
        self.assertEqual([path for path,_ in self.requests],['/api/version','/api/tags'])

    def test_connection_uses_short_local_inference_and_unloads_it(self):
        with patch.object(self.client,'_request',side_effect=self.request):report=self.client.test_connection('qwen3:0.6b')
        self.assertTrue(report['verified']);self.assertTrue(report['local_only'])
        self.assertEqual(report['model'],'qwen3:0.6b')
        payload=next(payload for path,payload in self.requests if path=='/api/generate')
        self.assertEqual(payload['options']['num_predict'],32)
        self.assertEqual(payload['keep_alive'],0)
        self.assertEqual([path for path,_ in self.requests],['/api/version','/api/tags','/api/show','/api/generate'])

    def test_connection_rejects_invalid_model_response(self):
        for answer in ({'ok':1},{'ok':False},{'ok':True,'extra':1},[],None):
            with self.subTest(answer=answer),patch.object(self.client,'_request',side_effect=self.request):
                self.test_answer=answer
                with self.assertRaises(LocalAIError):self.client.test_connection('qwen3:0.6b')

    def test_connection_refuses_cloud_or_uninstalled_model_without_generation(self):
        for model in ('new:1','qwen3:cloud',None,True):
            self.requests=[]
            with self.subTest(model=model),patch.object(self.client,'_request',side_effect=self.request):
                with self.assertRaises(LocalAIError):self.client.test_connection(model)
            self.assertNotIn('/api/generate',[path for path,_ in self.requests])

    def test_remote_show_metadata_is_refused_before_generation(self):
        for details in ({'remote_host':'https://host'},{'remote_model':'cloud-model'}):
            self.requests=[];self.details=details
            with self.subTest(details=details),patch.object(self.client,'_request',side_effect=self.request):
                with self.assertRaises(LocalAIError):self.client.recommend({},1.95,'qwen3:0.6b')
            self.assertNotIn('/api/generate',[path for path,_ in self.requests])

    def test_only_loopback_http_is_allowed(self):
        for url in ('https://127.0.0.1:11434','http://example.com','http://192.168.1.5',
                    'http://user:secret@127.0.0.1','http://127.0.0.1/path','http://127.0.0.1?x=1'):
            with self.subTest(url=url),self.assertRaises(LocalAIError):OllamaClient(url)
        self.assertEqual(OllamaClient('http://[::1]:11434/').base_url,'http://[::1]:11434')

    def test_redirects_cannot_escape_the_loopback_endpoint(self):
        with self.assertRaises(LocalAIError):_NoRedirect().redirect_request(None,None,None,None,None,None)
        self.assertTrue(any(isinstance(handler,_NoRedirect) for handler in self.client.opener.handlers))

    def test_non_integer_schema_values_are_rejected(self):
        for invalid in (True,1080.0,'1080'):
            self.response['resolution']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(LocalAIError):self.recommend()


if __name__=='__main__':unittest.main()
