from .clip_speed import tempo_filter
"""Local soundboard playback to a virtual cable and speakers, without Discord APIs."""
import subprocess
import threading


def audio_devices():
    import sounddevice as sd
    apis = sd.query_hostapis()
    devices = []
    for index, device in enumerate(sd.query_devices()):
        api = apis[device['hostapi']]['name']
        devices.append({'id': index, 'key': api + ': ' + device['name'],
                        'name': device['name'], 'input': device['max_input_channels'],
                        'output': device['max_output_channels']})
    return devices


def resolve_routes(devices, settings):
    def find(key, kind):
        device = next((d for d in devices if d['key'] == key and d[kind] >= (2 if kind == 'output' else 1)), None)
        if device is None:
            raise ValueError('Audio device unavailable. Open Direct audio settings and select devices again.')
        return device['id']
    cable = find(settings.get('cable'), 'output')
    speakers = find(settings.get('speakers'), 'output')
    if cable == speakers:
        raise ValueError('Select separate cable and speaker outputs to avoid duplicate audio.')
    microphone = find(settings['microphone'], 'input') if settings.get('microphone') else None
    if settings.get('microphone') == settings.get('cable') or (settings.get('microphone') and
            any(word in settings['microphone'].lower() for word in ('cable output', 'voicemeeter output'))):
        raise ValueError('Choose your physical microphone, not the virtual cable recording device.')
    return cable, speakers, microphone


class DirectAudio:
    def __init__(self, ffmpeg, events):
        self.ffmpeg = ffmpeg
        self.events = events
        self.lock = threading.Lock()
        self.clips = {}
        self.streams = []
        self.mic_stop = threading.Event()
        self.mic_thread = None
        self.routes = None

    @property
    def playing(self):
        with self.lock:
            return bool(self.clips)

    def configure(self, settings):
        import sounddevice as sd
        self.close()
        cable, speakers, microphone = resolve_routes(audio_devices(), settings)
        for device in (cable, speakers):
            sd.check_output_settings(device=device, channels=2, dtype='int16', samplerate=48000)
        if microphone is not None:
            sd.check_input_settings(device=microphone, channels=1, dtype='int16', samplerate=48000)
        self.routes = (cable, speakers)
        if microphone is not None:
            try:
                incoming = sd.RawInputStream(device=microphone, samplerate=48000, channels=1,
                                             dtype='int16', blocksize=960, latency='high')
                self.streams = [incoming]
                outgoing = sd.RawOutputStream(device=cable, samplerate=48000, channels=2,
                                              dtype='int16', blocksize=960, latency='high')
                self.streams = [incoming, outgoing]
                incoming.start(); outgoing.start()
                self.mic_stop.clear()
                def passthrough():
                    from array import array
                    try:
                        while not self.mic_stop.is_set():
                            data, _ = incoming.read(960)
                            samples = array('h', bytes(data))
                            stereo = array('h', (value for sample in samples for value in (sample, sample)))
                            outgoing.write(stereo.tobytes())
                    except Exception as exc:
                        if not self.mic_stop.is_set():
                            self.events.put({'error': 'Microphone passthrough stopped: ' + str(exc)})
                self.mic_thread = threading.Thread(target=passthrough, daemon=True)
                self.mic_thread.start()
            except Exception:
                self.close()
                raise

    def play(self, file_id, path, gain_db, adjustment, speed=0):
        if self.routes is None:
            raise ValueError('Configure Direct audio settings before playing clips locally.')
        import sounddevice as sd
        marker = object()
        entry = {'file_id': file_id, 'stop': threading.Event(), 'process': None, 'thread': None}
        routes = self.routes
        def worker():
            streams = []
            process = None
            try:
                for device in routes:
                    stream = sd.RawOutputStream(device=device, samplerate=48000, channels=2,
                                                dtype='int16', blocksize=960, latency='high')
                    streams.append(stream); stream.start()
                gain = max(0, min(2, 1 + adjustment / 100))
                process = subprocess.Popen([str(self.ffmpeg), '-hide_banner', '-loglevel', 'error',
                    '-i', str(path), '-vn', '-af', f'volume={gain_db}dB,volume={gain},' + tempo_filter(speed),
                    '-ar', '48000', '-ac', '2', '-f', 's16le', 'pipe:1'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name == 'nt' else 0)
                with self.lock:
                    entry['process'] = process
                while not entry['stop'].is_set():
                    frame = process.stdout.read(3840)
                    if not frame:
                        break
                    frame += bytes(3840 - len(frame))
                    for stream in streams:
                        if entry['stop'].is_set():
                            break
                        stream.write(frame)
                if not entry['stop'].is_set() and process.wait() != 0:
                    raise ValueError('FFmpeg could not decode this clip.')
            except Exception as exc:
                if not entry['stop'].is_set():
                    self.events.put({'error': 'Direct soundboard playback failed: ' + str(exc)})
            finally:
                if process:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
                for stream in streams:
                    for cleanup in (stream.abort, stream.close):
                        try:
                            cleanup()
                        except Exception:
                            pass
                with self.lock:
                    self.clips.pop(marker, None)
        entry['thread'] = threading.Thread(target=worker, daemon=True)
        with self.lock:
            self.clips[marker] = entry
            try:
                # Stop cannot observe an entry whose worker has not started yet.
                entry['thread'].start()
            except Exception:
                self.clips.pop(marker, None)
                raise

    def stop_file(self, file_id=None):
        with self.lock:
            entries = [entry for entry in self.clips.values() if file_id is None or entry['file_id'] == file_id]
            for entry in entries:
                entry['stop'].set()
                process = entry['process']
                if process and process.poll() is None:
                    process.kill()
        for entry in entries:
            entry['thread'].join(timeout=3)

    def close(self):
        self.stop_file()
        self.mic_stop.set()
        for stream in self.streams:
            for cleanup in (stream.abort, stream.close):
                try:
                    cleanup()
                except Exception:
                    pass
        self.streams = []
        if self.mic_thread:
            self.mic_thread.join(timeout=1)
        self.mic_thread = None
        self.routes = None
