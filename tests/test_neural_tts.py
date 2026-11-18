import hashlib
import math
import os
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from relay_agent import neural_tts, tts

FFMPEG = os.getenv('TEST_FFMPEG_PATH') or shutil.which('ffmpeg')
MODELS = os.getenv('TACOBOT_NEURAL_TTS_MODELS')


def tone(path, seconds, rate=24000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(n / 20))) for n in range(int(rate * seconds))))


def wav_seconds(path):
    with wave.open(str(path)) as audio:
        return audio.getnframes() / audio.getframerate()


class CatalogTests(unittest.TestCase):
    def test_kokoro_voices_are_listed_with_unique_labels(self):
        kokoro = [key for key, info in tts.VOICE_CATALOG.items() if info.get('engine') == 'kokoro']
        self.assertEqual(len(kokoro), len(neural_tts.KOKORO_VOICES))
        self.assertIn('English · Heart, female (US, Kokoro)', tts.VOICE_OPTIONS)
        self.assertEqual(len(set(tts.VOICE_OPTIONS.values())), len(tts.VOICE_CATALOG))
        self.assertEqual(tts.VOICE_OPTIONS[tts.voice_label(tts.VOICE_CATALOG[tts.VOICE])], tts.VOICE)  # Piper default unchanged

    def test_cloned_voices_register_and_unregister_in_place(self):
        options = tts.VOICE_OPTIONS
        tts.register_clones({'clone-sam': 'Sam'})
        self.assertIs(options, tts.VOICE_OPTIONS)
        self.assertEqual(tts.VOICE_OPTIONS['English · Sam (cloned voice)'], 'clone-sam')
        self.assertEqual(tts.engine_of('clone-sam'), 'clone')
        tts.register_clones({})
        self.assertNotIn('clone-sam', tts.VOICE_CATALOG)
        self.assertNotIn('English · Sam (cloned voice)', tts.VOICE_OPTIONS)


class DownloadTests(unittest.TestCase):
    def test_verified_download_installs_and_rejects_bad_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'model.bin'; source.write_bytes(b'model data')
            digest = hashlib.sha256(b'model data').hexdigest(); target = Path(directory) / 'out' / 'model.bin'
            neural_tts.download_verified(source.as_uri(), target, 10, digest)
            self.assertEqual(target.read_bytes(), b'model data')
            with patch.object(neural_tts.urllib.request, 'urlopen') as fetch:
                neural_tts.download_verified(source.as_uri(), target, 10, digest); fetch.assert_not_called()
            bad = Path(directory) / 'bad.bin'
            with self.assertRaises(ValueError):
                neural_tts.download_verified(source.as_uri(), bad, 10, '0' * 64)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ['model.bin', 'out'])


class ChunkTests(unittest.TestCase):
    def test_chunks_respect_limit_and_keep_every_word(self):
        text = 'First sentence here. ' * 30 + 'x' * 10 + ' ' + 'word ' * 120
        chunks = neural_tts.text_chunks(text, limit=100)
        self.assertTrue(all(0 < len(chunk) <= 100 for chunk in chunks))
        self.assertEqual(' '.join(chunks).split(), text.split())
        self.assertEqual(neural_tts.text_chunks('Short line.'), ['Short line.'])


@unittest.skipUnless(FFMPEG, 'ffmpeg not available')
class CloneVoiceTests(unittest.TestCase):
    def test_add_validates_reference_and_last_removal_frees_model(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = neural_tts.CloneEngine(directory, FFMPEG)
            short, good = Path(directory) / 'short.wav', Path(directory) / 'good.wav'
            tone(short, 2, 44100); tone(good, 20, 44100)
            with self.assertRaisesRegex(ValueError, 'at least 5 seconds'):
                engine.add('Too Short', short)
            self.assertEqual(engine.voices(), {})
            voice = engine.add('  Taco   Night!  ', good)
            self.assertEqual(voice, 'clone-taco-night'); self.assertEqual(engine.voices(), {voice: 'Taco Night!'})
            reference = Path(directory) / 'clones' / 'taco-night' / 'reference.wav'
            self.assertAlmostEqual(wav_seconds(reference), 15, delta=.1)  # Trimmed to the maximum
            with self.assertRaisesRegex(ValueError, 'already exists'):
                engine.add('taco night', good)
            (engine.directory / 'onnx').mkdir(parents=True); (engine.directory / 'onnx' / 'x').write_bytes(b'x')
            engine.remove(voice)
            self.assertEqual(engine.voices(), {}); self.assertFalse(engine.directory.exists())

    def test_speech_add_clone_rolls_back_when_model_download_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            speech = tts.PiperSpeech(directory, FFMPEG); good = Path(directory) / 'good.wav'; tone(good, 8)
            with patch.object(speech.cloning, 'download', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    speech.add_clone('Offline', good)
            self.assertEqual(speech.cloning.voices(), {}); self.assertNotIn('clone-offline', tts.VOICE_CATALOG)


class DispatchTests(unittest.TestCase):
    def test_kokoro_synthesis_is_dispatched_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            speech = tts.PiperSpeech(directory)
            def fake(text, voice, target): Path(target).write_bytes(b'RIFF')
            with patch.object(speech.kokoro, 'synthesize', side_effect=fake) as synthesize:
                first = speech.synthesize('Hello there', 'kokoro-af_heart')
                second = speech.synthesize('Hello there', 'kokoro-af_heart')
            self.assertEqual(first, second); synthesize.assert_called_once()
            self.assertEqual(first.parent, Path(directory) / 'audio')


@unittest.skipUnless(MODELS and FFMPEG, 'set TACOBOT_NEURAL_TTS_MODELS to a folder with downloaded models')
class RealModelTests(unittest.TestCase):
    """Runs the real Kokoro and Chatterbox Turbo models (about 1.7 GB) when they are available locally."""

    def test_kokoro_and_cloned_voice_synthesize_real_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('kokoro', 'chatterbox-turbo'):
                shutil.copytree(Path(MODELS) / name, Path(directory) / name)
            speech = tts.PiperSpeech(directory, FFMPEG)
            self.assertTrue(speech.downloaded('kokoro-af_heart'))
            spoken = speech.synthesize('Welcome to taco night! Grab a snack, find a seat, and get ready, because the queue is full of great songs tonight.', 'kokoro-af_heart')
            self.assertGreater(wav_seconds(spoken), 5)
            speech.remove('kokoro-af_heart')  # Must not be blocked by open file handles on Windows
            self.assertFalse(speech.downloaded('kokoro-af_heart'))
            voice = speech.add_clone('Reference', spoken)  # Model already present: no download
            self.assertIn(voice, tts.VOICE_OPTIONS.values())
            long_text = 'This sentence is about twenty words long so that the cloned voice has to speak in chunks. ' * 4
            cloned = speech.synthesize(long_text, voice)
            self.assertGreater(wav_seconds(cloned), 12)
            speech.remove(voice)
            self.assertNotIn(voice, tts.VOICE_CATALOG); self.assertFalse((Path(directory) / 'chatterbox-turbo').exists())


if __name__ == '__main__':
    unittest.main()
