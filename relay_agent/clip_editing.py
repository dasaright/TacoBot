"""FFmpeg waveform analysis, local preview, and atomic clip cropping."""
from array import array
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
CODECS={'.mp3':'libmp3lame','.ogg':'libvorbis','.oga':'libvorbis','.opus':'libopus',
        '.wav':'pcm_s16le','.flac':'flac','.m4a':'aac','.aac':'aac','.wma':'wmav2',
        '.aif':'pcm_s16be','.aiff':'pcm_s16be'}


def fingerprint(path):
    stat=Path(path).stat(); return stat.st_size,stat.st_mtime_ns


def run_ffmpeg(command):
    result=subprocess.run(command,capture_output=True,timeout=300,creationflags=FLAGS)
    if result.returncode: raise ValueError(result.stderr.decode(errors='replace')[-600:] or 'Unable to process audio.')


def waveform(path,ffmpeg):
    original=fingerprint(path)
    with tempfile.TemporaryDirectory(prefix='TacoBot-waveform-') as folder:
        decoded=Path(folder)/'wave.wav'
        run_ffmpeg([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(path),
            '-vn','-ac','2','-ar','8000','-c:a','pcm_s16le',str(decoded)])
        with wave.open(str(decoded)) as audio:
            total=audio.getnframes(); duration=total/audio.getframerate(); step=max(1,math.ceil(total/1400)); peaks=[]
            while True:
                data=audio.readframes(step)
                if not data: break
                samples=array('h',data); peaks.append(max(abs(min(samples)),abs(max(samples)))/32768)
    if not peaks or duration<=0: raise ValueError('This file has no playable audio.')
    if fingerprint(path)!=original: raise ValueError('The clip changed while loading. Reload it.')
    return duration,peaks,original


def crop_clip(source,start,end,ffmpeg,save_new=False,folder=None,expected=None,release=None):
    source=Path(source)
    if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:
        raise ValueError('End must be after start.')
    if expected is not None and fingerprint(source)!=expected: raise ValueError('The clip changed. Reload before saving.')
    codec=CODECS.get(source.suffix.lower())
    if codec is None: raise ValueError('This file format cannot be saved by the clip editor.')
    target=source; reserved=False
    if save_new:
        folder=Path(folder or source.parent)
        if not folder.is_dir(): raise ValueError('Choose an existing clips folder.')
        number=1
        while True:
            target=folder/f'{source.stem} (edited {number}){source.suffix}'
            try:
                with target.open('xb'): pass
                reserved=True; break
            except FileExistsError: number+=1
    temp=None
    try:
        fd,temp=tempfile.mkstemp(prefix='.tacobot-edit-',suffix=source.suffix,dir=target.parent); os.close(fd)
        # Decode the input before trimming, rather than cutting compressed packets.
        command=[str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),
            '-ss',f'{start:.6f}','-t',f'{end-start:.6f}','-map','0:a:0','-vn','-sn','-dn','-c:a',codec]
        if codec=='libmp3lame': command+=['-q:a','2']
        run_ffmpeg(command+[temp])
        if Path(temp).stat().st_size==0: raise ValueError('The selected range contains no audio.')
        if expected is not None and fingerprint(source)!=expected: raise ValueError('The clip changed while saving. Reload it.')
        if not save_new and release: release()
        os.replace(temp,target)
        return target
    except Exception:
        if reserved: target.unlink(missing_ok=True)
        raise
    finally:
        if temp: Path(temp).unlink(missing_ok=True)


class ClipPreview:
    def __init__(self,ffmpeg,events):
        self.ffmpeg=ffmpeg; self.events=events; self.session=None; self.generation=0

    def stop(self):
        if self.session:
            self.session['stop'].set()
            process=self.session.get('process')
            if process and process.poll() is None:
                try: process.kill()
                except OSError: pass

    def pause(self):
        if self.session:
            paused=self.session['pause']
            if paused.is_set(): paused.clear()
            else: paused.set()
            return paused.is_set()
        return False

    @property
    def playing(self):
        return bool(self.session and self.session['thread'].is_alive() and not self.session['stop'].is_set())

    def start(self,path,start,end):
        self.stop(); self.generation+=1
        session={'stop':threading.Event(),'pause':threading.Event(),'generation':self.generation}
        self.session=session
        def work():
            process=None
            try:
                import sounddevice as sd
                if session['stop'].is_set(): return
                process=subprocess.Popen([str(self.ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-i',str(path),
                    '-ss',str(start),'-t',str(end-start),'-vn','-f','s16le','-ac','2','-ar','48000','pipe:1'],
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=FLAGS)
                session['process']=process; frames=0; next_update=0
                with sd.RawOutputStream(samplerate=48000,channels=2,dtype='int16',blocksize=960) as output:
                    while not session['stop'].is_set():
                        if session['pause'].is_set(): session['stop'].wait(.02); continue
                        data=process.stdout.read(3840)
                        if not data: break
                        output.write(data); frames+=len(data)//4
                        if time.monotonic()>=next_update:
                            self.events.put({'preview_position':start+frames/48000,'generation':session['generation']}); next_update=time.monotonic()+.05
                if not session['stop'].is_set() and process.wait(timeout=5): raise ValueError('Unable to preview this clip.')
            except Exception as exc: self.events.put({'preview_error':str(exc),'generation':session['generation']})
            finally:
                if process:
                    if process.poll() is None: process.kill()
                    process.wait(); process.stdout.close()
                self.events.put({'preview_finished':True,'generation':session['generation']})
        thread=threading.Thread(target=work,daemon=True); session['thread']=thread; thread.start()

    def wait(self):
        if self.session: self.session['thread'].join(timeout=5)
