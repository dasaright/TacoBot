"""One bundled taco asset for the desktop, taskbar, and notification area."""
import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def asset_path(name='taco.png'):
    return Path(__file__).resolve().parent / 'assets' / name


def application_icon():
    return QIcon(str(asset_path()))


def configure_application(app):
    if sys.platform == 'win32':
        # Group the running Qt window under the executable rather than Python.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TacoBot.Desktop')
    app.setWindowIcon(application_icon())
