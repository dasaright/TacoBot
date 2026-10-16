import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import json
from relay_agent.shared_clips import sync_clips, blob_sha


class OwnerSyncTests(unittest.TestCase):
    def test_owner_collection_uploads_local_audio_to_separate_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            clip=Path(folder)/'Owner sound.mp3'; clip.write_bytes(b'audio')
            (Path(folder)/'tacobot-clips.json').write_text('{}')
            requests=[]
            remote=[]
            def open_request(request,**kwargs):
                requests.append((request.full_url,request.get_method()))
                if request.get_method()=='PUT':
                    remote.append({'name':clip.name,'sha':blob_sha(b'audio'),'size':5})
                    data=b'{}'
                else: data=json.dumps({'files':list(remote),'changes':{}}).encode()
                response=Mock(); response.read.return_value=data
                response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
                return response
            with patch('relay_agent.shared_clips.urlopen',side_effect=open_request):
                result=sync_clips(folder,{'server_url':'wss://example.test/relay','relay_token':'owner'}, {'.mp3'},lambda p:True,collection='ownerclips')
            self.assertEqual(result,(1,0,[]))
            self.assertTrue(all('/ownerclips' in url for url,method in requests))
            self.assertEqual(sum(method=='PUT' for url,method in requests),1)

    def test_invalid_collection_cannot_choose_arbitrary_url(self):
        with self.assertRaises(ValueError):
            sync_clips('.',{},set(),lambda p:True,collection='../secret')
