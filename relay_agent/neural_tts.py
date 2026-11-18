"""Kokoro voices and Chatterbox Turbo voice cloning, both run locally with ONNX Runtime.

Model files download on first use into Files/TTS, pinned to exact releases and
verified by SHA-256, so the app download stays small.
"""
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
import urllib.request
import wave
from pathlib import Path

import certifi

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def onnx_threads():
    # Leave cores free for live music streaming and the UI.
    return max(2, min(8, (os.cpu_count() or 4) // 2))


def download_verified(url, target, size, sha256, progress=None):
    """Download url to target unless an identical file is already there."""
    target = Path(target)
    if target.is_file() and target.stat().st_size == size:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.download')
    digest = hashlib.sha256(); total = 0
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'TacoBot-Relay'})
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=60, context=context) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    raise ValueError(f'{target.name} is larger than expected.')
                digest.update(chunk); output.write(chunk)
                if progress: progress(len(chunk))
        if total != size or digest.hexdigest() != sha256:
            raise ValueError(f'{target.name} failed its checksum. Try the download again.')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def write_wav(path, samples, sample_rate):
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype('<i2')
    temporary = Path(path).with_suffix('.tmp')
    try:
        with wave.open(str(temporary), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(sample_rate); audio.writeframes(pcm.tobytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------- Kokoro

KOKORO_RELEASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
KOKORO_FILES = {
    'kokoro-v1.0.onnx': (325532387, '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5'),
    'voices-v1.0.bin': (28214398, 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'),
}
# Voice prefix: language, region, espeak language. Japanese and Chinese need
# extra phonemizers that are not bundled, so their voices are left out.
KOKORO_LANGUAGES = {'a': ('en', 'US', 'en-us'), 'b': ('en', 'GB', 'en-gb'), 'e': ('es', 'ES', 'es'),
                    'f': ('fr', 'FR', 'fr-fr'), 'h': ('hi', 'IN', 'hi'), 'i': ('it', 'IT', 'it'),
                    'p': ('pt', 'BR', 'pt-br')}
KOKORO_VOICES = (
    'af_alloy', 'af_aoede', 'af_bella', 'af_heart', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river',
    'af_sarah', 'af_sky', 'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx',
    'am_puck', 'am_santa', 'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily', 'bm_daniel', 'bm_fable',
    'bm_george', 'bm_lewis', 'ef_dora', 'em_alex', 'em_santa', 'ff_siwis', 'hf_alpha', 'hf_beta', 'hm_omega',
    'hm_psi', 'if_sara', 'im_nicola', 'pf_dora', 'pm_alex', 'pm_santa')


def kokoro_catalog():
    catalog = {}
    for voice in KOKORO_VOICES:
        language, region, _ = KOKORO_LANGUAGES[voice[0]]
        gender = 'female' if voice[1] == 'f' else 'male'
        catalog['kokoro-' + voice] = {'language': language, 'region': region, 'name': f'{voice[3:].title()}, {gender}',
                                      'quality': 'Kokoro', 'speakers': 1, 'speaker_names': {}, 'engine': 'kokoro'}
    return catalog


class KokoroEngine:
    def __init__(self, directory):
        self.directory = Path(directory) / 'kokoro'
        self.model = None

    def downloaded(self):
        return all((self.directory / name).is_file() and (self.directory / name).stat().st_size == size
                   for name, (size, _) in KOKORO_FILES.items())

    def download(self):
        for name, (size, sha256) in KOKORO_FILES.items():
            download_verified(KOKORO_RELEASE + name, self.directory / name, size, sha256)

    def remove(self):
        self.model = None  # Release ONNX file handles before deleting on Windows.
        if self.directory.exists():
            shutil.rmtree(self.directory)

    def synthesize(self, text, voice_id, target):
        voice = voice_id.removeprefix('kokoro-')
        if voice not in KOKORO_VOICES:
            raise ValueError('Choose a supported Kokoro voice.')
        if self.model is None:
            self.download()
            import onnxruntime
            from kokoro_onnx import Kokoro
            options = onnxruntime.SessionOptions(); options.intra_op_num_threads = onnx_threads(); options.inter_op_num_threads = 1
            session = onnxruntime.InferenceSession(str(self.directory / 'kokoro-v1.0.onnx'), sess_options=options,
                                                   providers=['CPUExecutionProvider'])
            model = Kokoro.from_session(session, str(self.directory / 'voices-v1.0.bin'))
            # np.load keeps the voice pack open; copy it so Remove download works on Windows.
            packed = model.voices; model.voices = {name: packed[name] for name in packed.files}; packed.close()
            self.model = model
        samples, sample_rate = self.model.create(text, voice=voice, lang=KOKORO_LANGUAGES[voice[0]][2])
        write_wav(target, samples, sample_rate)


# ---------------------------------------------------------------- Chatterbox Turbo voice cloning

TURBO_REVISION = 'd21799bd0354adb85e348b8a0442a8405110a2cf'
TURBO_URL = f'https://huggingface.co/ResembleAI/chatterbox-turbo-ONNX/resolve/{TURBO_REVISION}/'
# 8-bit speech encoder (runs once per voice), 4-bit language model (fast on CPUs)
# and the full-precision single-step decoder: about 1.3 GB in total.
TURBO_FILES = {
    'onnx/speech_encoder_quantized.onnx': (1205728, '5b6f15870a43cf97892df86fc550a0ef4763522d527cde72b2a4316f80a34de4'),
    'onnx/speech_encoder_quantized.onnx_data': (354676576, 'd59861fb55e806fbeee731da9d4f8ff819fb5735de5d15e262d902594ee4dbb6'),
    'onnx/embed_tokens_q4.onnx': (2844, 'fd6ba1d22902e8f539d3dd6d7c1c44b98ebb4c84ebbb5e47fcb826ddcf667561'),
    'onnx/embed_tokens_q4.onnx_data': (37286384, 'f54a51e234b509b64c3a03bb79e1149fba7e2eba6c2d9c222f18883379e1f5d8'),
    'onnx/language_model_q4.onnx': (274572, 'b39d03d3f8b943b9e60c6fce3fb41191dbc1df4589f913291db1e214eef669b1'),
    'onnx/language_model_q4.onnx_data': (204456572, '2c029dc0acf48752473d8c74c72b5ceaaad76b9886fe106eaf2022142d5b5d5e'),
    'onnx/conditional_decoder.onnx': (1889468, '8c43f3a1d0ddb1a86e226a244d7cda5396c67f5c6412789c23900c646e3ffc50'),
    'onnx/conditional_decoder.onnx_data': (768593792, '05f162a519f3e9abaf0b7337ae037f4af8b2b30c4455d39b2c61ed3a9b2b5476'),
    'tokenizer.json': (3562272, '3f04e34bea22f9144d1a19151154095bc9ce0430bf421304f5797e716288a906'),
}
TURBO_SAMPLE_RATE = 24000
START_SPEECH, STOP_SPEECH, SILENCE = 6561, 6562, 4299
REFERENCE_MIN_SECONDS, REFERENCE_MAX_SECONDS = 5, 15
CHUNK_CHARACTERS = 280  # About 20 seconds of speech; the model stops at 1024 tokens (~40 s).


def clone_slug(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.casefold()).strip('-')[:40]
    if not slug:
        raise ValueError('Give the cloned voice a name with letters or numbers.')
    return slug


def text_chunks(text, limit=CHUNK_CHARACTERS):
    sentences = re.split(r'(?<=[.!?])\s+', ' '.join(text.split()))
    chunks, current = [], ''
    for sentence in sentences:
        while len(sentence) > limit:  # Very long sentence: break at the last space before the limit.
            cut = sentence.rfind(' ', 0, limit); cut = cut if cut > 0 else limit
            if current: chunks.append(current); current = ''
            chunks.append(sentence[:cut]); sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            chunks.append(current); current = sentence
        else:
            current = f'{current} {sentence}'.strip()
    if current: chunks.append(current)
    return chunks


class CloneEngine:
    def __init__(self, directory, ffmpeg):
        self.directory = Path(directory) / 'chatterbox-turbo'
        self.clones_dir = Path(directory) / 'clones'
        self.ffmpeg = ffmpeg
        self.sessions = None
        self.tokenizer = None
        self.conditioning = {}

    # Model files

    def model_downloaded(self):
        return all((self.directory / name).is_file() and (self.directory / name).stat().st_size == size
                   for name, (size, _) in TURBO_FILES.items())

    def download(self, progress=None):
        for name, (size, sha256) in TURBO_FILES.items():
            download_verified(TURBO_URL + name, self.directory / name, size, sha256, progress)

    def remove_model(self):
        self.sessions = None; self.tokenizer = None; self.conditioning.clear()
        if self.directory.exists():
            shutil.rmtree(self.directory)

    # Cloned voices

    def voices(self):
        found = {}
        if self.clones_dir.is_dir():
            for folder in sorted(self.clones_dir.iterdir()):
                try:
                    info = json.loads((folder / 'voice.json').read_text(encoding='utf-8'))
                    if (folder / 'reference.wav').is_file():
                        found['clone-' + folder.name] = info['name']
                except (OSError, ValueError, KeyError):
                    continue
        return found

    def add(self, name, source):
        name = ' '.join(str(name).split())[:40]
        folder = self.clones_dir / clone_slug(name)
        if folder.exists():
            raise ValueError(f'A cloned voice called "{name}" already exists. Remove it first or pick another name.')
        folder.mkdir(parents=True)
        try:
            # Mono 24 kHz, leading silence removed, at most 15 seconds.
            result = subprocess.run(
                [str(self.ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(source), '-vn',
                 '-af', 'silenceremove=start_periods=1:start_threshold=-45dB', '-ac', '1', '-ar', str(TURBO_SAMPLE_RATE),
                 '-t', str(REFERENCE_MAX_SECONDS), '-c:a', 'pcm_s16le', str(folder / 'reference.wav')],
                capture_output=True, timeout=60, creationflags=FLAGS)
            if result.returncode:
                raise ValueError('Could not read that audio file.')
            with wave.open(str(folder / 'reference.wav')) as audio:
                seconds = audio.getnframes() / audio.getframerate()
            if seconds < REFERENCE_MIN_SECONDS:
                raise ValueError(f'Use at least {REFERENCE_MIN_SECONDS} seconds of clear speech (this clip has {seconds:.1f}).')
            (folder / 'voice.json').write_text(json.dumps({'name': name, 'created': int(time.time()), 'consent': True}), encoding='utf-8')
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return 'clone-' + folder.name

    def reference_version(self, voice_id):
        reference = self.clones_dir / voice_id.removeprefix('clone-') / 'reference.wav'
        return reference.stat().st_mtime_ns if reference.is_file() else 0

    def remove(self, voice_id):
        self.conditioning.pop(voice_id, None)
        folder = self.clones_dir / voice_id.removeprefix('clone-')
        if folder.exists():
            shutil.rmtree(folder)
        if not self.voices():  # Last cloned voice gone: free the 1.3 GB model too.
            self.remove_model()

    # Synthesis

    def load(self):
        if self.sessions is not None:
            return
        self.download()
        import onnxruntime
        from tokenizers import Tokenizer
        options = onnxruntime.SessionOptions(); options.intra_op_num_threads = onnx_threads(); options.inter_op_num_threads = 1
        def session(name):
            return onnxruntime.InferenceSession(str(self.directory / 'onnx' / name), sess_options=options,
                                                providers=['CPUExecutionProvider'])
        self.sessions = {'encoder': session('speech_encoder_quantized.onnx'), 'embed': session('embed_tokens_q4.onnx'),
                         'language': session('language_model_q4.onnx'), 'decoder': session('conditional_decoder.onnx')}
        self.tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))

    def voice_conditioning(self, voice_id):
        import numpy as np
        folder = self.clones_dir / voice_id.removeprefix('clone-')
        reference = folder / 'reference.wav'
        if not reference.is_file():
            raise ValueError('This cloned voice was removed. Choose another voice.')
        if voice_id not in self.conditioning:
            with wave.open(str(reference)) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), '<i2').astype(np.float32) / 32768
            self.conditioning[voice_id] = self.sessions['encoder'].run(None, {'audio_values': samples[np.newaxis]})
        return self.conditioning[voice_id]

    def generate(self, text, conditioning, max_tokens=1024):
        import numpy as np
        cond, prompt_tokens, speaker_embeddings, speaker_features = conditioning
        embed, language, decoder = self.sessions['embed'], self.sessions['language'], self.sessions['decoder']
        caches = [item for item in language.get_inputs() if 'past_key_values' in item.name]
        ids = np.array([self.tokenizer.encode(text).ids], dtype=np.int64)
        generated = np.array([[START_SPEECH]], dtype=np.int64)
        for step in range(max_tokens):
            embeds = embed.run(None, {'input_ids': ids})[0]
            if step == 0:
                embeds = np.concatenate((cond, embeds), axis=1)
                past = {item.name: np.zeros([1, item.shape[1], 0, item.shape[3]],
                                            np.float16 if item.type == 'tensor(float16)' else np.float32) for item in caches}
                mask = np.ones((1, embeds.shape[1]), np.int64)
                positions = np.arange(embeds.shape[1], dtype=np.int64)[np.newaxis]
            logits, *present = language.run(None, dict(inputs_embeds=embeds, attention_mask=mask, position_ids=positions, **past))
            logits = logits[:, -1, :].copy()
            # Repetition penalty 1.2, as in Resemble's reference inference.
            seen = np.take_along_axis(logits, generated, 1)
            np.put_along_axis(logits, generated, np.where(seen < 0, seen * 1.2, seen / 1.2), 1)
            ids = np.argmax(logits, axis=-1, keepdims=True).astype(np.int64)
            generated = np.concatenate((generated, ids), axis=-1)
            if ids.item() == STOP_SPEECH:
                break
            mask = np.concatenate([mask, np.ones((1, 1), np.int64)], axis=1)
            positions = positions[:, -1:] + 1
            past = dict(zip(past, present))
        speech = generated[:, 1:]
        if speech.shape[1] and speech[0, -1] == STOP_SPEECH:
            speech = speech[:, :-1]
        tokens = np.concatenate([prompt_tokens, speech, np.full((1, 3), SILENCE, np.int64)], axis=1)
        return decoder.run(None, {'speech_tokens': tokens, 'speaker_embeddings': speaker_embeddings,
                                  'speaker_features': speaker_features})[0].squeeze(0)

    def synthesize(self, text, voice_id, target):
        import numpy as np
        self.load()
        conditioning = self.voice_conditioning(voice_id)
        pause = np.zeros(int(TURBO_SAMPLE_RATE * .2), np.float32)
        parts = []
        for chunk in text_chunks(text):
            parts += [self.generate(chunk, conditioning), pause]
        write_wav(target, np.concatenate(parts[:-1]), TURBO_SAMPLE_RATE)
