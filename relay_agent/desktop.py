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


def application():
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('TacoBot'); app.setOrganizationName('TacoBot'); app.setStyle('Fusion')
    return app


def main():
    if any(flag in sys.argv for flag in ('--update-launch-test','update-restart-test','--self-test')):
        runpy.run_module('relay_agent.main',run_name='__main__')
        if '--self-test' in sys.argv:
            app=application(); window=Desktop({},start_agent=False); window.show(); app.processEvents(); assert window.pages.count()==3; window.close()
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
