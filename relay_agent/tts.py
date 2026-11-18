"""Offline speech: Piper and Kokoro voices plus Chatterbox voice cloning, downloaded into Files on first use."""
import hashlib
import json
import ssl
import shutil
import threading
import urllib.request
import wave
from pathlib import Path

import certifi

VOICE = 'en_US-lessac-medium'
try:
    from .voice_catalog import VOICE_CATALOG
    from .neural_tts import CloneEngine, KokoroEngine, kokoro_catalog
except ImportError:
    from voice_catalog import VOICE_CATALOG
    from neural_tts import CloneEngine, KokoroEngine, kokoro_catalog

VOICE_CATALOG.update(kokoro_catalog())
LANGUAGES = {'English': 'en', 'Dutch': 'nl', 'German': 'de', 'French': 'fr', 'Bulgarian': 'bg',
             'Spanish': 'es', 'Italian': 'it', 'Portuguese': 'pt', 'Hindi': 'hi'}


def voice_label(info):
    language = next(label for label, code in LANGUAGES.items() if code == info['language'])
    if info.get('engine') == 'clone':
        return f"{language} · {info['name']} (cloned voice)"
    return f"{language} · {info['name']} ({info['region']}, {info['quality']})"


VOICE_OPTIONS = {voice_label(info): key for key, info in sorted(VOICE_CATALOG.items())}


def register_clones(clones):
    """Sync cloned voices into the shared catalog in place, so imported references stay current."""
    for key in [key for key, info in VOICE_CATALOG.items() if info.get('engine') == 'clone' and key not in clones]:
        VOICE_OPTIONS.pop(voice_label(VOICE_CATALOG.pop(key)), None)
    for key, name in clones.items():
        if key not in VOICE_CATALOG:
            VOICE_CATALOG[key] = {'language': 'en', 'region': '', 'name': name, 'quality': 'cloned',
                                  'speakers': 1, 'speaker_names': {}, 'engine': 'clone'}
            VOICE_OPTIONS[voice_label(VOICE_CATALOG[key])] = key


def engine_of(voice_id):
    return VOICE_CATALOG.get(voice_id, {}).get('engine', 'piper')


BASE_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/' 
MAX_TEXT = 1000


class PiperSpeech:
    def __init__(self, directory, ffmpeg=None):
        self.directory = Path(directory)
        self.voice = None
        self.loaded_voice = VOICE
        self.lock = threading.Lock()
        self.kokoro = KokoroEngine(self.directory)
        self.cloning = CloneEngine(self.directory, ffmpeg)
        register_clones(self.cloning.voices())

    def add_clone(self, name, source):
        """Add a cloned voice from a reference recording and fetch the cloning model."""
        with self.lock:
            voice_id = self.cloning.add(name, source)
            register_clones(self.cloning.voices())
            try:
                self.cloning.download()
            except Exception:
                self.cloning.remove(voice_id)
                register_clones(self.cloning.voices())
                raise
            return voice_id

    def voice_folder(self, voice_id):
        if voice_id not in VOICE_CATALOG:
            raise ValueError("Choose a supported Piper voice.")
        return self.directory / 'voice' if voice_id == VOICE else self.directory / 'voices' / voice_id

    def downloaded(self, voice_id):
        if engine_of(voice_id) == 'kokoro':
            return self.kokoro.downloaded()
        if engine_of(voice_id) == 'clone':
            return self.cloning.model_downloaded()
        folder = self.voice_folder(voice_id)
        return all((folder / name).is_file() and (folder / name).stat().st_size > 0
                   for name in (voice_id+'.onnx', voice_id+'.onnx.json', 'MODEL_CARD'))

    def download(self, voice_id):
        with self.lock:
            if engine_of(voice_id) == 'kokoro':
                return self.kokoro.download()
            if engine_of(voice_id) == 'clone':
                return self.cloning.download()
            return self.ensure_voice(voice_id)

    def remove(self, voice_id):
        with self.lock:
            if engine_of(voice_id) == 'kokoro':
                return self.kokoro.remove()  # Shared by every Kokoro voice.
            if engine_of(voice_id) == 'clone':
                self.cloning.remove(voice_id)  # Deletes the cloned voice itself.
                register_clones(self.cloning.voices())
                return
            if self.loaded_voice == voice_id:
                self.voice = None  # Release ONNX file handles before deleting on Windows.
                self.loaded_voice = None
            folder = self.voice_folder(voice_id)
            if folder.exists():
                shutil.rmtree(folder)

    def ensure_voice(self, voice_id=VOICE):
        locale, name, quality = voice_id.split('-')
        base_url = BASE_URL + f'{locale.split("_")[0]}/{locale}/{name}/{quality}/'
        folder = self.voice_folder(voice_id)
        folder.mkdir(parents=True, exist_ok=True)
        for name in (voice_id + '.onnx', voice_id + '.onnx.json', 'MODEL_CARD'):
            target = folder / name
            if target.is_file() and target.stat().st_size:
                continue
            temporary = target.with_suffix(target.suffix + '.download')
            try:
                with urllib.request.urlopen(base_url + name, timeout=60,
                                            context=ssl.create_default_context(cafile=certifi.where())) as response:
                    with temporary.open('wb') as output:
                        total = 0
                        while chunk := response.read(65536):
                            total += len(chunk)
                            if total > 150 * 1024 * 1024:
                                raise ValueError('Voice download exceeds the supported size.')
                            output.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return folder / (voice_id + '.onnx')

    def synthesize(self, text, voice_id=VOICE, speaker_id=0):
        if voice_id not in VOICE_OPTIONS.values():
            raise ValueError("Choose a supported voice.")
        if not 0 <= int(speaker_id) < VOICE_CATALOG[voice_id]["speakers"]:
            raise ValueError("Choose a valid speaker.")
        text = text.strip()
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f'Enter between 1 and {MAX_TEXT} characters.')
        with self.lock:
            cache = self.directory / 'audio'
            cache.mkdir(parents=True, exist_ok=True)
            engine = engine_of(voice_id)
            # A re-created clone with the same name must not reuse old audio.
            version = str(self.cloning.reference_version(voice_id)) if engine == 'clone' else ''
            target = cache / (hashlib.sha256((voice_id + str(speaker_id) + version + text).encode()).hexdigest() + '.wav')
            if target.is_file():
                return target
            if engine != 'piper':
                (self.kokoro if engine == 'kokoro' else self.cloning).synthesize(text, voice_id, target)
                self.trim_cache(cache)
                return target
            if self.voice is None or self.loaded_voice != voice_id:
                from piper import PiperVoice
                from piper.config import PiperConfig
                import onnxruntime
                model = self.ensure_voice(voice_id)
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.voice = PiperVoice(
                    config=PiperConfig.from_dict(json.loads(Path(str(model) + '.json').read_text(encoding='utf-8'))),
                    session=onnxruntime.InferenceSession(str(model), sess_options=options,
                                                         providers=['CPUExecutionProvider']))
                self.loaded_voice = voice_id
            temporary = target.with_suffix('.tmp')
            try:
                with wave.open(str(temporary), 'wb') as audio:
                    if VOICE_CATALOG[voice_id]['speakers'] > 1:
                        from piper import SynthesisConfig
                        self.voice.synthesize_wav(text, audio, syn_config=SynthesisConfig(speaker_id=int(speaker_id)))
                    else:
                        self.voice.synthesize_wav(text, audio)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            self.trim_cache(cache)
            return target

    @staticmethod
    def trim_cache(cache):
        # Keep disk use bounded, preserving the newest reusable messages.
        for old in sorted(cache.glob('*.wav'), key=lambda p: p.stat().st_mtime, reverse=True)[100:]:
            old.unlink(missing_ok=True)
