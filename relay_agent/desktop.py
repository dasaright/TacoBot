"""Packaged desktop entry point; installer flags retain the established protocol."""
import os
import runpy
import sys
import threading

if not __package__:
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox
from relay_agent import main as engine
from relay_agent.qt_desktop import Desktop
from relay_agent.app_icon import configure_application


def application():
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('TacoBot'); app.setOrganizationName('TacoBot'); app.setStyle('Fusion')
    configure_application(app)
    return app


def main():
    if any(flag in sys.argv for flag in ('--update-launch-test','update-restart-test','--self-test')):
        runpy.run_module('relay_agent.main',run_name='__main__')
        if '--self-test' in sys.argv:
            app=application(); window=Desktop({},start_agent=False); window.show(); app.processEvents(); assert window.pages.count()==4; window.close()
            if sys.platform=='win32':
                import pyaudiowpatch
                import tempfile, wave
                from pathlib import Path
                from relay_agent.clip_recorder import encode_clip
                from relay_agent.clip_editing import crop_clip,waveform
                with pyaudiowpatch.PyAudio() as capture:
                    assert callable(capture.get_default_wasapi_loopback)
                with tempfile.TemporaryDirectory() as directory:
                    source=Path(directory)/'record-test.wav'
                    with wave.open(str(source),'wb') as audio:
                        audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000); audio.writeframes(b'\0'*48000*4)
                    assert encode_clip(source,directory,engine.FFMPEG_PATH).stat().st_size>100
                    assert encode_clip(source,directory,engine.FFMPEG_PATH,'Playback',False).name=='Playback 1.mp3'
                    duration,peaks,stamp=waveform(source,engine.FFMPEG_PATH); assert abs(duration-1)<.01
                    cropped=crop_clip(source,.2,.8,engine.FFMPEG_PATH,True,directory,stamp)
                    with wave.open(str(cropped)) as trimmed: assert abs(trimmed.getnframes()/trimmed.getframerate()-.6)<.01
        return
    app=application()
    try:
        config=engine.load_config() if engine.CONFIG_PATH.is_file() else {}
        if not config.get('relay_token'):
            token,ok=QInputDialog.getText(None,'TacoBot setup','Use !token in Discord, then paste your relay token:',QLineEdit.EchoMode.Password)
            if not ok or not token.strip(): return
            config['relay_token']=token.strip()
        config['server_url']=engine.RELAY_URL; engine.save_config(config); engine.verify_tools()
        threading.Thread(target=engine.update_ytdlp,daemon=True).start()
        window=Desktop(config); window.show(); app.exec()
    except Exception as exc:
        QMessageBox.critical(None,'TacoBot could not start',engine.clean_error(exc))
        raise


if __name__=='__main__':
    main()
