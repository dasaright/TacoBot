from array import array
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from relay_agent.playback_recorder import PlaybackBuffer
from relay_agent.unshare_clip import copy_local
from relay_agent.clip_recorder import reserve_clip


class PlaybackTests(unittest.TestCase):
    def buffer(self):
        value=PlaybackBuffer(2); value.rate=10; value.channels=1; value.started=0
        return value

    def test_snapshot_contains_only_latest_seconds(self):
        value=self.buffer()
        for end,sample in [(1,1),(2,2),(3,3),(4,4)]: value.append(array('h',[sample]*10).tobytes(),end)
        data,rate,channels=value.snapshot(4)
        self.assertEqual(list(array('h',data)),[3]*10+[4]*10)
        self.assertEqual(value.byte_count,40)

    def test_native_silence_is_preserved_without_inventing_clock_gaps(self):
        value=self.buffer(); value.append(array('h',[7]*5+[0]*5).tobytes(),.5)
        data,_,_=value.snapshot(1)
        self.assertEqual(list(array('h',data)),[7]*5+[0]*5)
        data,_,_=value.snapshot(4)
        self.assertEqual(list(array('h',data)),[7]*5+[0]*5)
        value.clear()
        with self.assertRaisesRegex(ValueError,'Enable'): value.snapshot(5)

    def test_callback_jitter_preserves_exact_native_samples(self):
        value=PlaybackBuffer(1); value.configure(100,2)
        original=array('h',range(400)).tobytes()
        # Delayed/batched callbacks must not insert silence or overwrite audio.
        arrival=[10,10.001,10.7,10.71,12,12.001,12.9,13]
        for n,offset in enumerate(range(0,len(original),100)):
            value.append(original[offset:offset+100],arrival[n])
        self.assertEqual(value.snapshot(20)[0],original[-400:])
        chunks,rate,channels=value.freeze_snapshot()
        value.append(b'\0'*100,21)
        self.assertEqual(b''.join(chunks),original[-400:])
        self.assertEqual(value.byte_count,400); self.assertEqual(value.available_seconds(),1)

    def test_no_audio_is_reported_instead_of_saving_synthetic_silence(self):
        value=self.buffer()
        with self.assertRaisesRegex(ValueError,'speaker audio'): value.snapshot(30)

    def test_unshare_copy_preserves_source_and_existing_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shared=root/'shared'; local=root/'local'; shared.mkdir(); local.mkdir()
            source=shared/'clip.mp3'; source.write_bytes(b'audio'); (local/'clip.mp3').write_bytes(b'keep')
            result=copy_local(source,local)
            self.assertEqual(result.name,'clip (2).mp3'); self.assertEqual(result.read_bytes(),b'audio')
            self.assertEqual(source.read_bytes(),b'audio'); self.assertEqual((local/'clip.mp3').read_bytes(),b'keep')
            with self.assertRaises(ValueError): copy_local(source,shared)

    def test_playback_names_are_numbered_without_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(reserve_clip(directory,'Playback',False).name,'Playback 1.mp3')
            self.assertEqual(reserve_clip(directory,'Playback',False).name,'Playback 2.mp3')
