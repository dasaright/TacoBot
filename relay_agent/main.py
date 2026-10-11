import asyncio
import math
from array import array
import json
import re
from urllib.parse import urlsplit
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import queue
import hashlib
import tkinter as tk
from tkinter import filedialog, ttk, simpledialog, messagebox
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk
import certifi
from websockets.asyncio.client import connect
if __package__:
    from .shared_clips import sync_clips, edit_shared_clip, check_share_target
    from .updater import find_update, prepare_update, launch_installer
    from .direct_audio import DirectAudio, audio_devices
    from .translation import LocalTranslator, LANGUAGES
    from .tts import PiperSpeech, MAX_TEXT, VOICE, VOICE_OPTIONS, VOICE_CATALOG
else:
    from shared_clips import sync_clips, edit_shared_clip, check_share_target
    from updater import find_update, prepare_update, launch_installer
    from direct_audio import DirectAudio, audio_devices
    from translation import LocalTranslator, LANGUAGES
    from tts import PiperSpeech, MAX_TEXT, VOICE, VOICE_OPTIONS, VOICE_CATALOG


RELAY_BUILD = 0
RELAY_URL = "wss://runsformingbot-production.up.railway.app/relay"
PROTOCOL_VERSION = 1
PCM_FRAME_BYTES = 3840
MAX_TRACK_SECONDS = 6 * 60 * 60
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma", ".aif", ".aiff"}


LABEL_COLORS = {
    "Light Red": "#ffb3b3", "Light Orange": "#ffd1a3", "Light Yellow": "#fff2a3",
    "Light Green": "#bde8b3", "Light Blue": "#b3d9ff", "Light Purple": "#d9b3ff",
    "Light Pink": "#ffb3d9",
}


def clip_gain(frame, adjustment, master_volume=100):
    gain = (1 + max(-100, min(100, float(adjustment))) / 100) * max(0, min(100, float(master_volume))) / 100
    if gain == 1:
        return frame
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, round(sample * gain)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def sorted_clip_ids(files, origins, settings, sort_keys):
    ids = sorted(files, key=lambda key: files[key].stem.casefold())
    for column, reverse in reversed(sort_keys):
        if column == 'shared':
            key = lambda ident: origins.get(ident) != 'Shared'
        elif column == 'label':
            key = lambda ident: settings.get(ident, {}).get('label', '').casefold()
        else:
            key = lambda ident: files[ident].stem.casefold()
        ids.sort(key=key, reverse=reverse)
    return ids


CLIP_COLUMNS = ("name", "volume", "shared", "label")


def clip_column_order(saved):
    return list(saved) if isinstance(saved, (list, tuple)) and len(saved) == 4 and set(saved) == set(CLIP_COLUMNS) else list(CLIP_COLUMNS)


def reordered_columns(order, source, target):
    result = list(order)
    if source in result and target in result and source != target:
        old, new = result.index(source), result.index(target)
        result.pop(old)
        result.insert(new, source)
    return result


def rounded_badge(canvas, x1, y1, x2, y2, color):
    r = min(6, (y2-y1)/2)
    return canvas.create_polygon(x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,
                                 x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1,
                                 smooth=True, splinesteps=12, fill=color, outline="")


class SleekScale(tk.Canvas):
    def __init__(self, master, variable, command, width=100):
        super().__init__(master, width=width, height=28, background="#14171c", highlightthickness=0, bd=0)
        self.variable, self.command = variable, command
        self.bind("<Button-1>", self.change)
        self.bind("<B1-Motion>", self.change)
        self.bind("<Configure>", self.draw)
        self.trace_id = variable.trace_add("write", self.draw)
        self.bind("<Destroy>", lambda e: variable.trace_remove("write", self.trace_id) if e.widget is self else None)
        self.draw()

    def change(self, event):
        value = max(0, min(100, (event.x-8) / max(1, self.winfo_width()-16) * 100))
        self.variable.set(value)
        self.command(str(value))

    def draw(self, *args):
        width = self.winfo_width() if self.winfo_width() > 1 else int(self.cget("width"))
        value = max(0, min(100, self.variable.get()))
        signature = (width, value)
        if signature == getattr(self, "draw_signature", None):
            return
        self.draw_signature = signature
        factor = 4
        image = Image.new("RGBA", (width * factor, 28 * factor), "#14171c")
        draw = ImageDraw.Draw(image)
        x = 8 + (width-16) * value/100
        draw.rounded_rectangle((8*factor,12*factor,(width-8)*factor,16*factor), radius=2*factor, fill="#303a45")
        if x > 8:
            draw.rounded_rectangle((8*factor,12*factor,x*factor,16*factor), radius=2*factor, fill="#52d4ba")
        draw.ellipse(((x-6)*factor,8*factor,(x+6)*factor,20*factor), fill="#d9fff5")
        self.track_image = ImageTk.PhotoImage(image.resize((width,28), Image.Resampling.LANCZOS), master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self.track_image)


def render_button_icon(name, size=28, color="#52d4ba"):
    # Draw at 4x the actual display resolution, then antialias the edges.
    scale = size * 4 / 24
    image = Image.new("RGBA", (size*4, size*4))
    draw = ImageDraw.Draw(image)
    stroke = max(1, round(1.7*scale))
    def line(points):
        pts = [(round(x*scale), round(y*scale)) for x,y in points]
        draw.line(pts, fill=color, width=stroke, joint="curve")
        radius=stroke/2
        for x,y in (pts[0],pts[-1]):
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=color)
    def arc(box, start, end):
        draw.arc(tuple(round(v*scale) for v in box), start, end, fill=color, width=stroke)
    if name == "clear":
        line([(7,7),(17,17)])
        line([(17,7),(7,17)])
    elif name == "refresh":
        arc((4,4,20,20), 35, 315)
        line([(17.6,4.8),(20,7.8),(15.5,8)])
    elif name == "check":
        arc((3,3,21,21),0,360)
        line([(7.5,12),(10.5,15),(16.5,8.5)])
    elif name == "sync":
        line([(4,7),(20,7),(16,3)])
        line([(20,17),(4,17),(8,21)])
    elif name == "stop":
        draw.rounded_rectangle((6*scale,6*scale,18*scale,18*scale),radius=2*scale,fill=color)
    elif name == "folder":
        line([(3,18),(3,6),(10,6),(12,9),(21,9),(21,18),(3,18)])
        line([(4,12),(20,12)])
    elif name == "download":
        line([(12,4),(12,15)])
        line([(8,11),(12,15),(16,11)])
        line([(5,20),(19,20)])
    elif name == "audio":
        line([(3,9),(7,9),(12,5),(12,19),(7,15),(3,15),(3,9)])
        arc((9,5,21,19),300,60)
        arc((12,8,18,16),300,60)
    elif name == "pause":
        line([(8,5),(8,19)])
        line([(16,5),(16,19)])
    elif name == "previous":
        line([(18,5),(7,12),(18,19),(18,5)])
        line([(4,5),(4,19)])
    elif name == "next":
        line([(6,5),(17,12),(6,19),(6,5)])
        line([(20,5),(20,19)])
    elif name == "speech":
        line([(4,4),(20,4),(20,16),(11,16),(6,21),(6,16),(4,16),(4,4)])
        for x in (8,12,16):
            draw.ellipse(((x-0.8)*scale,9*scale,(x+0.8)*scale,10.6*scale),fill=color)
    elif name == "soundboard":
        for x,height in ((4,8),(8,14),(12,20),(16,14),(20,8)):
            line([(x,12-height/2),(x,12+height/2)])
    else:
        line([(7,4),(20,12),(7,20),(7,4)])
    return image.resize((size,size), Image.Resampling.LANCZOS)


def add_button_icons(root):
    size = max(28, round(28 * float(root.tk.call("tk", "scaling")) / (96/72)))
    cache = {}
    def visit(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button):
                text = child.cget("text")
                lower = text.lower()
                name = next((key for key, terms in (("speech",("tts",)),("refresh",("refresh",)),("check",("check",)),("sync",("sync",)),("stop",("stop",)),("folder",("folder",)),("download",("install",)),("audio",("audio",)),("previous",("previous",)),("next",("next",)),("soundboard",("soundboard",)),("play",("play","youtube"))) if any(term in lower for term in terms)), "play")
                color = "#10211e" if child.cget("style") == "Primary.TButton" else "#52d4ba"
                key = (name,color)
                if key not in cache:
                    cache[key] = ImageTk.PhotoImage(render_button_icon(name,size,color), master=root)
                disabled_key = (name,"#66727f")
                if disabled_key not in cache:
                    cache[disabled_key] = ImageTk.PhotoImage(render_button_icon(name,size,"#66727f"), master=root)
                child.graphite_icon = cache[key]
                child.graphite_disabled_icon = cache[disabled_key]
                child.configure(image=(cache[key], "disabled", cache[disabled_key]), compound="left", text=text.lstrip("↻⇄■ "))
            visit(child)
    visit(root)



def style_relay(root):
    root.configure(background="#14171c")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background="#14171c", foreground="#e6edf3")
    style.configure("TFrame", background="#14171c")
    style.configure("Card.TFrame", background="#14171c")
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("TLabel", background="#14171c")
    style.configure("Card.TLabel", background="#14171c")
    style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
    style.configure("Muted.TLabel", foreground="#a3adb9", font=("Segoe UI", 9))
    style.configure("CardMuted.TLabel", background="#14171c", foreground="#a3adb9", font=("Segoe UI", 9))
    style.configure("Song.TLabel", background="#14171c", font=("Segoe UI", 17, "bold"))
    style.configure("TButton", background="#1b2027", borderwidth=0, padding=(16, 10), relief="flat")
    style.map("TButton", background=[("active", "#2b3540")], foreground=[("disabled", "#66727f")])
    style.configure("Primary.TButton", background="#52d4ba", foreground="#10211e")
    style.map("Primary.TButton", background=[("active", "#75e4ce")], foreground=[("disabled", "#66727f")])
    style.configure("TNotebook", background="#14171c", borderwidth=0, tabmargins=0)
    style.layout("TNotebook.Tab", [])
    style.configure("SelectedTab.TButton", background="#1b2027", foreground="#52d4ba", font=("Segoe UI", 11, "bold"), padding=(20, 12))
    style.configure("OtherTab.TButton", background="#252d36", foreground="#a3adb9", font=("Segoe UI", 10), padding=(16, 8))
    style.configure("TNotebook.Tab", padding=(26, 12), font=("Segoe UI", 11, "bold"), background="#252d36", borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", "#1b2027")], foreground=[("selected", "#e6edf3"), ("!selected", "#a3adb9")])
    style.configure("Treeview", background="#1b2027", fieldbackground="#1b2027", foreground="#e6edf3", rowheight=36, borderwidth=0)
    style.configure("Soundboard.Treeview", rowheight=33, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", background="#202730", foreground="#c3cdd8", font=("Segoe UI", 10, "bold"), padding=(10, 7), relief="flat")
    style.map("Treeview.Heading", background=[("active", "#2b3540")], foreground=[("active", "#e6edf3")])
    style.map("Treeview", background=[("selected", "#25443f")], foreground=[("selected", "#e6edf3")])
    style.configure("Clip.TFrame", background="#1b2027")
    style.configure("Clip.TLabel", background="#14171c", foreground="#a3adb9", font=("Segoe UI", 9))
    style.configure("TCheckbutton", background="#14171c", padding=6)
    style.configure("Horizontal.TScale", background="#52d4ba", troughcolor="#252d36", borderwidth=0)
    style.configure("TEntry", fieldbackground="#1b2027", foreground="#e6edf3", insertcolor="#52d4ba",
                    bordercolor="#39434f", lightcolor="#39434f", darkcolor="#39434f", padding=5)
    style.configure("TCombobox", fieldbackground="#1b2027", background="#252d36", foreground="#e6edf3",
                    arrowcolor="#a3adb9", bordercolor="#39434f")
    style.map("TCombobox", fieldbackground=[("readonly", "#1b2027")], foreground=[("readonly", "#e6edf3")])
    style.map("TCheckbutton", background=[("active", "#14171c")], foreground=[("active", "#e6edf3")])
    style.configure("TScrollbar", background="#39434f", troughcolor="#14171c", arrowcolor="#a3adb9",
                    borderwidth=0, arrowsize=12)
    style.map("TScrollbar", background=[("active", "#52d4ba")])
    for orientation, sticky in (("Vertical", "ns"), ("Horizontal", "we")):
        style.layout(orientation + ".TScrollbar", [(orientation + ".Scrollbar.trough", {
            "sticky": "nswe", "children": [(orientation + ".Scrollbar.thumb", {"sticky": sticky})]})])
        style.configure(orientation + ".TScrollbar", width=8, borderwidth=0,
                        relief="flat", lightcolor="#39434f", darkcolor="#39434f")
    root.option_add("*TCombobox*Listbox.background", "#1b2027")
    root.option_add("*TCombobox*Listbox.foreground", "#e6edf3")
    root.option_add("*TCombobox*Listbox.selectBackground", "#25443f")
    root.graphite_toggle_images = []
    dpi = max(1, float(root.tk.call("tk", "scaling")) / (96/72))
    width, height = round(40*dpi), round(22*dpi)
    for color, knob_x in (("#39434f", 10), ("#52d4ba", 30), ("#252d36", 10)):
        image = Image.new("RGBA", (160,88))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0,0,159,87), radius=44, fill=color)
        draw.ellipse(((knob_x-7.5)*4,14,(knob_x+7.5)*4,74), fill="#e6edf3")
        root.graphite_toggle_images.append(ImageTk.PhotoImage(image.resize((width,height), Image.Resampling.LANCZOS), master=root))
    # Stretch only the middle of rounded thumbs; keep smooth end caps intact.
    root.graphite_scroll_images = []
    for color in ("#526170", "#52d4ba"):
        image = Image.new("RGBA", (40,96))
        ImageDraw.Draw(image).rounded_rectangle((4,0,35,95), radius=16, fill=color)
        root.graphite_scroll_images.append(ImageTk.PhotoImage(image.resize((10,24), Image.Resampling.LANCZOS), master=root))
    style.element_create("Graphite.Vertical.Scrollbar.thumb", "image", root.graphite_scroll_images[0],
                         ("active", root.graphite_scroll_images[1]), border=(0,5,0,5), sticky="ns")
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky":"nswe", "children":
        [("Graphite.Vertical.Scrollbar.thumb", {"sticky":"ns"})]})])
    style.configure("Vertical.TScrollbar", troughcolor="#14171c", bordercolor="#14171c",
                    lightcolor="#14171c", darkcolor="#14171c", relief="flat", borderwidth=0, width=10)
    style.layout("TNotebook", [("Notebook.client", {"sticky":"nswe"})])
    style.configure("TNotebook", borderwidth=0, relief="flat", bordercolor="#14171c",
                    lightcolor="#14171c", darkcolor="#14171c", background="#14171c")
    style.layout("Soundboard.Treeview", [("Treeview.treearea", {"sticky":"nswe"})])
    style.configure("Soundboard.Treeview", borderwidth=0, relief="flat", bordercolor="#14171c")
    style.configure("Toolbar.TLabel", foreground="#e6edf3", font=("Segoe UI",9))
    style.configure("Search.TEntry", padding=(5,5,27,5))
    switches = root.graphite_toggle_images
    style.element_create("Graphite.switch", "image", switches[0],
                         ("disabled", switches[2]), ("selected", switches[1]), sticky="w")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children":
        [("Graphite.switch", {"side": "left", "sticky": "w"}),
         ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
    # Rounded, stretchable button surfaces without adding native widgets per clip.
    root.graphite_images = []
    for button_style, normal, hover in (
        ("TButton", "#202730", "#2d3843"),
        ("Primary.TButton", "#52d4ba", "#75e4ce"),
        ("SelectedTab.TButton", "#25443f", "#2e554d"),
        ("OtherTab.TButton", "#1b2027", "#252d36"),
    ):
        pictures = []
        for color in (normal, hover, "#1b2027"):
            picture = tk.PhotoImage(master=root, width=18, height=18)
            for y in range(18):
                for x in range(18):
                    dx, dy = max(6-x, 0, x-11), max(6-y, 0, y-11)
                    if dx*dx + dy*dy <= 36:
                        picture.put(color, (x, y))
            pictures.append(picture)
        root.graphite_images.extend(pictures)
        element = "Graphite." + button_style + ".surface"
        style.element_create(element, "image", pictures[0], ("disabled", pictures[2]),
                             ("active", pictures[1]), border=7, sticky="nsew")
        style.layout(button_style, [(element, {"sticky": "nsew", "children":
            [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])




def themed_menu(parent):
    return tk.Menu(parent, tearoff=False, background="#1b2027", foreground="#e6edf3",
                   activebackground="#25443f", activeforeground="#52d4ba",
                   relief="flat", borderwidth=0, font=("Segoe UI", 10))


def copy_clip_to_shared(source, folder):
    source, folder = Path(source), Path(folder)
    if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Select an available audio file.")
    if not 0 < source.stat().st_size <= 20 * 1024 * 1024:
        raise ValueError("Shared clips must be between 1 byte and 20 MB.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if source.resolve() == target.resolve():
        raise ValueError("This clip is already in the shared folder.")
    if target.exists():
        if source.read_bytes() != target.read_bytes():
            raise ValueError("A different shared clip already uses this filename. Rename before sharing.")
        return target
    try:
        with target.open("xb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    except FileExistsError:
        raise ValueError("This shared filename was created during copying. Retry or rename.") from None
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


NORMALIZATION_VERSION = 1


def clip_normalization_gain(report):
    mean = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB", report)
    peak = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB", report)
    if not mean or not peak:
        raise ValueError("Could not measure soundboard clip loudness.")
    mean, peak = float(mean[1]), float(peak[1])
    if not math.isfinite(mean) or not math.isfinite(peak) or peak <= -90:
        return 0.0
    # Average loudness target with 7 dB of peak headroom. User gain runs later.
    return round(min(-20.0 - mean, -7.0 - peak, 24.0), 3)


def clipboard_youtube_link(text):
    # Ignore unrelated clipboard text; submit only the first actual YouTube URL.
    pattern = r"https?://[^\s<>\"']+|(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+"
    for match in re.finditer(pattern, str(text), re.IGNORECASE):
        url = match.group().rstrip(".,;!)]}")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
            return url
    return None


def soundboard_rename_target(path, name):
    path = Path(path)
    name = name.strip()
    if name.lower().endswith(path.suffix.lower()):
        name = name[:-len(path.suffix)]
    if (not name or name.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}):
        raise ValueError("Enter a valid filename without folder separators or reserved Windows names.")
    target = path.with_name(name + path.suffix)
    if target == path:
        return path
    if target.exists() and not target.samefile(path):
        raise ValueError("A clip with that filename already exists.")
    return target


def rename_soundboard_clip(path, name):
    path = Path(path)
    target = soundboard_rename_target(path, name)
    path.rename(target)
    return target


def application_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = application_directory()
SHARED_CLIPS_DIR = APP_DIR / "sharedclips"
CONFIG_PATH = APP_DIR / "relay-config.json"
FILES_DIR = APP_DIR / "Files"
YTDLP_PATH = FILES_DIR / "yt-dlp.exe"
FFMPEG_PATH = FILES_DIR / "ffmpeg.exe"


CONFIG_LOCK = threading.RLock()


def read_config_file(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Relay settings must be a JSON object.")
    return value


def atomic_config_write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_config(config):
    contents = json.dumps(config, indent=2)
    with CONFIG_LOCK:
        if CONFIG_PATH.is_file():
            try:
                previous = read_config_file(CONFIG_PATH)
            except (ValueError, UnicodeError):
                pass
            else:
                atomic_config_write(CONFIG_PATH.with_suffix(".json.bak"), json.dumps(previous, indent=2))
        atomic_config_write(CONFIG_PATH, contents)


def create_config():
    token = input("Relay token from !token in Discord: ").strip()
    config = {"server_url": RELAY_URL, "relay_token": token, "relay_name": "Discord relay", "cookies_file": ""}
    save_config(config)
    return config


def load_config():
    if not CONFIG_PATH.is_file():
        return create_config()
    try:
        config = read_config_file(CONFIG_PATH)
    except (ValueError, UnicodeError):
        # Preserve the damaged file before restoring settings or asking for a token.
        damaged = CONFIG_PATH.with_name(f"relay-config.corrupt-{time.time_ns()}.json")
        CONFIG_PATH.replace(damaged)
        try:
            config = read_config_file(CONFIG_PATH.with_suffix(".json.bak"))
        except (OSError, ValueError, UnicodeError):
            config = {}
        save_config(config)
    config["server_url"] = RELAY_URL
    config.setdefault("relay_name", "Discord relay")
    return config


def verify_tools():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    for tool in (YTDLP_PATH, FFMPEG_PATH):
        legacy = APP_DIR / tool.name
        if not tool.exists() and legacy.is_file():
            shutil.move(str(legacy), str(tool))
    missing = [
        path.name for path in (YTDLP_PATH, FFMPEG_PATH) if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Missing " + ", ".join(missing) + ". Extract the complete relay ZIP."
        )


def youtube_common_args(config):
    args = [
        str(YTDLP_PATH),
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
    ]
    cookies_file = str(config.get("cookies_file") or "").strip()
    if cookies_file:
        cookie_path = Path(cookies_file).expanduser()
        if not cookie_path.is_absolute():
            cookie_path = APP_DIR / cookie_path
        args.extend(("--cookies", str(cookie_path)))
    return args


def youtube_stream_args(config, url):
    # Keep downloads below a burst that can fill residential router buffers.
    return youtube_common_args(config) + [
        "--format", "bestaudio/best[height<=360]", "--limit-rate", "256K",
        "--concurrent-fragments", "1", "--no-progress", "--output", "-", url,
    ]


async def drain_stderr(stream):
    chunks = []
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(map(len, chunks)) > 32_000:
            chunks = chunks[-4:]
    return b"".join(chunks).decode("utf-8", errors="replace")[-2000:]


def clean_error(message):
    text = " ".join(str(message).split())
    return text[-500:] if text else "The relay could not process this video."


def relay_ssl_context():
    """Use Mozilla's public roots rather than importing stale Windows roots."""
    return ssl.create_default_context(cafile=certifi.where())


class RelayAgent:
    def __init__(self, config):
        self.config = config
        self.websocket = None
        self.send_lock = asyncio.Lock()
        self.stream_task = None
        self.stream_request_id = None
        self.local_files = {}
        self.tts_files = {}
        self.tts_lock = asyncio.Lock()
        self.speech = PiperSpeech(FILES_DIR / "TTS")
        self.translator = LocalTranslator(FILES_DIR / "Translation")
        self.normalization_cache = dict(config.get("clip_normalization", {}))
        self.normalization_locks = {}
        self.preload_task = None
        self.preload_lock = asyncio.Lock()
        self.preload_path = None
        self.clip_tasks = {}
        self.clip_file_ids = {}
        self.playback_gate = asyncio.Event()
        self.playback_gate.set()
        self.events = queue.Queue()
        self.direct_audio = DirectAudio(FFMPEG_PATH, self.events)
        self.direct_tasks = {}
        self.token_ready = asyncio.Event()
        self.token_ready.set()

    async def send_json(self, payload):
        async with self.send_lock:
            await self.websocket.send(json.dumps(payload))

    async def send_binary(self, payload):
        async with self.send_lock:
            await self.websocket.send(payload)

    async def browse_playlist(self, key, url):
        """Preview up to 50 available songs without starting audio playback."""
        process = None
        try:
            args = youtube_common_args(self.config)
            args.remove("--no-playlist")
            args += ["--yes-playlist", "--playlist-end", "50", "--flat-playlist", "--dump-single-json", "--skip-download", url]
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace"))
            info = json.loads(stdout)
            tracks = [{"webpage_url": "https://www.youtube.com/watch?v=" + str(entry["id"]),
                       "title": str(entry.get("title") or "YouTube song")[:200]}
                      for entry in info.get("entries", [])[:50] if entry and entry.get("id")
                      and entry.get("availability") not in {"private", "needs_auth", "subscriber_only", "premium_only"}]
            self.events.put({"favorite_playlist": key, "tracks": tracks})
        except Exception as exc:
            self.events.put({"favorite_playlist": key, "error": clean_error(exc)})
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()

    async def probe(self, request_id, url, playlist=False):
        args = youtube_common_args(self.config) + [
            "--dump-single-json",
            "--skip-download",
            url,
        ]
        if playlist:
            args.remove("--no-playlist")
            args[1:1] = ["--yes-playlist", "--playlist-end", "50", "--flat-playlist"]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            stdout, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace"))
            info = json.loads(stdout.decode("utf-8"))
            if playlist:
                if "entries" not in info:
                    raise ValueError("Provide a YouTube playlist URL.")
                tracks = [{"webpage_url": "https://www.youtube.com/watch?v=" + str(entry["id"]),
                           "title": str(entry.get("title") or "YouTube song")[:200],
                           "duration": int(entry.get("duration") or 0)}
                          for entry in info.get("entries", [])[:50]
                          if entry and entry.get("id") and entry.get("availability") not in
                          {"private", "needs_auth", "subscriber_only", "premium_only"}]
                await self.send_json({"type": "probe_result", "request_id": request_id,
                                      "ok": True, "tracks": tracks})
                return
            duration = int(info.get("duration") or 0)
            if info.get("is_live") or info.get("live_status") in {
                "is_live",
                "is_upcoming",
            }:
                raise ValueError("Live and upcoming streams are not supported.")
            if duration and duration > MAX_TRACK_SECONDS:
                raise ValueError("YouTube videos are limited to six hours.")
            await self.send_json({
                "type": "probe_result",
                "request_id": request_id,
                "ok": True,
                "title": str(info.get("title") or "Untitled YouTube video")[:200],
                "duration": duration,
                "webpage_url": str(info.get("webpage_url") or url),
            })
        except Exception as exc:
            await self.send_json({
                "type": "probe_result",
                "request_id": request_id,
                "ok": False,
                "error": clean_error(exc),
            })

    async def play_direct_clip(self, file_id):
        task = asyncio.current_task()
        self.direct_tasks[task] = file_id
        try:
            path = self.local_files[file_id]
            gain = await self.normalize_clip(path)
            adjustment = self.config.get("clip_settings", {}).get(file_id, {}).get("volume", 0)
            self.direct_audio.play(file_id, path, gain, adjustment)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.events.put({"error": "Direct playback: " + clean_error(exc)})
        finally:
            self.direct_tasks.pop(task, None)

    async def stop_direct_clips(self, file_id=None):
        tasks = [task for task, key in list(self.direct_tasks.items()) if file_id is None or key == file_id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.direct_audio.stop_file, file_id)

    async def normalize_clip(self, path):
        key = str(path.resolve())
        async with self.normalization_locks.setdefault(key, asyncio.Lock()):
            stat = path.stat()
            fingerprint = [stat.st_mtime_ns, stat.st_size, NORMALIZATION_VERSION]
            cached = self.normalization_cache.get(key)
            if cached and cached.get("fingerprint") == fingerprint:
                return cached["gain_db"]
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    str(FFMPEG_PATH), "-hide_banner", "-threads", "1", "-i", str(path),
                    "-vn", "-sn", "-dn", "-af", "volumedetect", "-f", "null", "-",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                _, report = await asyncio.wait_for(process.communicate(), timeout=60)
                if process.returncode:
                    raise ValueError("Could not analyze this clip for normalization.")
                gain_db = clip_normalization_gain(report.decode(errors="replace"))
                self.normalization_cache = {**self.normalization_cache,
                                            key: {"fingerprint": fingerprint, "gain_db": gain_db}}
                self.events.put({"normalization_cache": self.normalization_cache})
                return gain_db
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.communicate()

    async def preload_normalization(self, files):
        async with self.preload_lock:
            if self.preload_task and not self.preload_task.done():
                self.preload_task.cancel()
                await asyncio.gather(self.preload_task, return_exceptions=True)
            self.preload_task = asyncio.create_task(self.analyze_library(files))

    async def analyze_library(self, files):
        failed = 0
        try:
            for path in files.values():
                self.preload_path = path
                try:
                    await self.normalize_clip(path)
                except (OSError, ValueError, asyncio.TimeoutError):
                    failed += 1
                finally:
                    self.preload_path = None
                await asyncio.sleep(0)
            self.events.put({"status": "Clip audio ready." + (f" Could not analyze {failed} files; playback will retry." if failed else "")})
        finally:
            self.preload_path = None

    def apply_stream_volume(self, frame, url):
        """Apply the current tab gain before the bot applies its global baseline."""
        file_id = url.removeprefix("local:")
        if not url.startswith("local:"):
            return clip_gain(frame, 0, self.config.get("music_volume", 100))
        if file_id.startswith("tts:"):
            return clip_gain(frame, 0, self.config.get("tts_volume", 100))
        adjustment = self.config.get("clip_settings", {}).get(file_id, {}).get("volume", 0)
        return clip_gain(frame, adjustment, self.config.get("bot_clip_volume", 100))

    async def stream(self, request_id, url):
        ytdlp = None
        ffmpeg = None
        pump_task = None
        ytdlp_error_task = None
        ffmpeg_error_task = None
        terminal = {"type": "stream_end", "request_id": request_id}
        try:
            local_path = (self.local_files.get(url[6:]) or self.tts_files.get(url[6:])) if url.startswith("local:") else None
            if url.startswith("local:") and local_path is None:
                raise ValueError("This audio file is no longer in the selected folder. Refresh the library.")
            ytdlp_args = youtube_stream_args(self.config, url)
            if local_path is None:
                ytdlp = await asyncio.create_subprocess_exec(
                    *ytdlp_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
            normalization_args = []
            if local_path is not None:
                gain_db = await self.normalize_clip(local_path)
                normalization_args = ["-af", f"volume={gain_db}dB"]
            ffmpeg = await asyncio.create_subprocess_exec(
                str(FFMPEG_PATH),
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads", "1",
                "-i",
                str(local_path) if local_path is not None else "pipe:0",
                "-vn",
                *normalization_args,
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )

            async def pump_download():
                while True:
                    chunk = await ytdlp.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    ffmpeg.stdin.write(chunk)
                    await ffmpeg.stdin.drain()
                ffmpeg.stdin.close()

            if ytdlp is not None:
                pump_task = asyncio.create_task(pump_download())
                ytdlp_error_task = asyncio.create_task(drain_stderr(ytdlp.stderr))
            ffmpeg_error_task = asyncio.create_task(drain_stderr(ffmpeg.stderr))
            await self.send_json({
                "type": "stream_started",
                "request_id": request_id,
            })
            next_frame_at = asyncio.get_running_loop().time()
            while True:
                try:
                    frame = await ffmpeg.stdout.readexactly(PCM_FRAME_BYTES)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        if local_path is None:
                            await self.playback_gate.wait()
                        frame = exc.partial + bytes(PCM_FRAME_BYTES - len(exc.partial))
                        frame = self.apply_stream_volume(frame, url)
                        await self.send_binary(request_id.encode("ascii") + frame)
                    break
                if local_path is None:
                    was_paused = not self.playback_gate.is_set()
                    await self.playback_gate.wait()
                    if was_paused:
                        next_frame_at = asyncio.get_running_loop().time()
                now = asyncio.get_running_loop().time()
                # Absolute deadlines avoid accumulating Windows sleep/send overhead.
                # Bound catch-up after a stalled download so buffers cannot flood.
                next_frame_at = max(next_frame_at, now - 0.02)
                await asyncio.sleep(max(0, next_frame_at - now))
                frame = self.apply_stream_volume(frame, url)
                await self.send_binary(request_id.encode("ascii") + frame)
                next_frame_at += 0.02
            if pump_task is not None:
                await pump_task
            ytdlp_code = await ytdlp.wait() if ytdlp is not None else 0
            ffmpeg_code = await ffmpeg.wait()
            ytdlp_error = await ytdlp_error_task if ytdlp_error_task is not None else ""
            ffmpeg_error = await ffmpeg_error_task
            if ytdlp_code or ffmpeg_code:
                raise RuntimeError(ytdlp_error or ffmpeg_error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.events.put({"error": clean_error(exc)})
            terminal = {"type": "stream_error", "request_id": request_id, "error": clean_error(exc)}
        finally:
            for process in (ffmpeg, ytdlp):
                if process is not None and process.returncode is None:
                    process.kill()
            for task in (pump_task, ytdlp_error_task, ffmpeg_error_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (pump_task, ytdlp_error_task, ffmpeg_error_task)
                                   if task is not None), return_exceptions=True)
            for process in (ffmpeg, ytdlp):
                if process is not None:
                    try:
                        await asyncio.wait_for(process.communicate(), timeout=5)
                    except (asyncio.TimeoutError, OSError):
                        pass
            if url.startswith("local:"):
                self.clip_file_ids.pop(request_id, None)
                self.clip_tasks.pop(request_id, None)
            elif self.stream_request_id == request_id:
                self.stream_task = None
                self.stream_request_id = None
            # A completion acknowledgement means the remote slot is actually free.
            # Send it only after process cleanup and task-slot release.
            try:
                await asyncio.shield(self.send_json(terminal))
            except Exception:
                pass

    async def share_clip_file(self, file_id, path, folder, remove_local=False):
        target = await asyncio.to_thread(copy_clip_to_shared, path, folder)
        if remove_local:
            await self.edit_clip_file(file_id, path)
        return target

    async def edit_clip_file(self, file_id, path, new_name=None):
        await self.stop_direct_clips(file_id)
        if self.preload_path == path and self.preload_task:
            self.preload_task.cancel()
            await asyncio.gather(self.preload_task, return_exceptions=True)
        for attempt in range(30):
            tasks = [task for request_id, task in list(self.clip_tasks.items())
                     if self.clip_file_ids.get(request_id) == file_id]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if new_name is None:
                    await asyncio.to_thread(path.unlink)
                    return "Deleted clip " + path.stem
                target = await asyncio.to_thread(rename_soundboard_clip, path, new_name)
                return "Renamed clip to " + target.stem
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {32, 33} or attempt == 29:
                    raise
                await asyncio.sleep(0.1)

    async def play_text(self, text, voice_id=VOICE, speaker_id=0):
        if self.tts_lock.locked():
            self.events.put({"error": "Wait for the current text to finish generating."})
            return
        try:
            async with self.tts_lock:
                if self.websocket is None:
                    raise ValueError("Connect your relay token before playing text.")
                self.events.put({"status": "Generating speech… First use downloads the Piper voice."})
                path = await asyncio.to_thread(self.speech.synthesize, text, voice_id, speaker_id)
                file_id = "tts:" + path.stem
                self.tts_files[file_id] = path
                await self.send_json({"type": "local_play", "guild_id": None,
                                      "file_id": file_id, "title": "Text to speech"})
                self.events.put({"status": "Speech ready. Sending to your Discord voice channel."})
        except Exception as exc:
            self.events.put({"error": "Could not play text: " + clean_error(exc)})
        finally:
            self.events.put({"tts_done": True})

    async def handle_command(self, raw):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        message_type = payload.get("type")
        request_id = str(payload.get("request_id") or "")
        url = str(payload.get("url") or "")
        if message_type == "control_result":
            self.events.put(payload)
        elif message_type == "probe" and request_id and url:
            asyncio.create_task(self.probe(request_id, url, bool(payload.get("playlist"))))
        elif message_type == "pause" and request_id == self.stream_request_id:
            self.playback_gate.clear()
        elif message_type == "resume" and request_id == self.stream_request_id:
            self.playback_gate.set()
        elif message_type == "cancel" and request_id in self.clip_tasks:
            self.clip_tasks[request_id].cancel()
        elif message_type == "stream" and request_id and url:
            if url.startswith("local:"):
                if len(self.clip_tasks) >= 8:
                    await self.send_json({"type": "stream_error", "request_id": request_id, "error": "Too many soundboard clips."})
                else:
                    self.clip_file_ids[request_id] = url.removeprefix("local:")
                    task = asyncio.create_task(self.stream(request_id, url))
                    self.clip_tasks[request_id] = task
                    def finished(_task):
                        if self.clip_file_ids.pop(request_id, None) is not None:
                            async def notify():
                                try:
                                    await self.send_json({"type": "stream_end", "request_id": request_id})
                                except Exception:
                                    pass
                            asyncio.create_task(notify())
                        self.clip_tasks.pop(request_id, None)
                    task.add_done_callback(finished)
                return
            if self.stream_task is not None:
                await self.send_json({
                    "type": "stream_error",
                    "request_id": request_id,
                    "error": "This relay is already streaming audio.",
                })
                return
            self.playback_gate.set()
            self.stream_request_id = request_id
            self.stream_task = asyncio.create_task(self.stream(request_id, url))
        elif (
            message_type == "cancel"
            and request_id == self.stream_request_id
            and self.stream_task is not None
        ):
            self.stream_task.cancel()

    async def connect_once(self):
        async with connect(
            self.config["server_url"],
            ssl=relay_ssl_context(),
            ping_interval=20,
            ping_timeout=30,
            max_size=1024 * 1024,
        ) as websocket:
            self.websocket = websocket
            await self.send_json({
                "type": "hello",
                "capabilities": ["playlist", "multistream"],
                "protocol": PROTOCOL_VERSION,
                "token": self.config["relay_token"],
                "name": self.config["relay_name"],
            })
            raw_hello = await asyncio.wait_for(websocket.recv(), timeout=15)
            hello = json.loads(raw_hello)
            if hello.get("type") != "hello_ok":
                raise ConnectionError("Relay authentication failed.")
            self.config["shared_owner"] = bool(hello.get("shared_owner", False))
            self.config["relay_name"] = hello.get("relay_name", "Discord relay")
            self.events.put({"relay_name": self.config["relay_name"], "status": "Connected as " + self.config["relay_name"]})
            await self.send_json({"type": "targets"})
            async for message in websocket:
                if isinstance(message, str):
                    await self.handle_command(message)

    async def run(self):
        delay = 2
        while True:
            try:
                await self.connect_once()
                delay = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for task in list(self.clip_tasks.values()):
                    task.cancel()
                await asyncio.gather(*list(self.clip_tasks.values()), return_exceptions=True)
                if self.stream_task is not None:
                    self.stream_task.cancel()
                    await asyncio.gather(self.stream_task, return_exceptions=True)
                self.websocket = None
                self.events.put({"error": f"Disconnected: {clean_error(exc)}"})
                close_frame = getattr(exc, "rcvd", None)
                if close_frame is not None and close_frame.code == 4003:
                    self.token_ready.clear()
                    self.events.put({"token_required": True})
                    await self.token_ready.wait()
                    delay = 2
                    continue
                print(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)


def update_ytdlp():
    try:
        subprocess.run(
            [str(YTDLP_PATH), "--update"],
            check=False,
            timeout=120,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        print("yt-dlp update check failed; continuing with the bundled version.")


def is_playable_audio(file, ffmpeg_path=FFMPEG_PATH):
    file = Path(file)
    try:
        if not file.is_file() or file.suffix.lower() not in AUDIO_EXTENSIONS or file.stat().st_size == 0:
            return False
        result = subprocess.run(
            [str(ffmpeg_path), "-v", "error", "-xerror", "-i", str(file),
             "-t", "0.05", "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def scan_audio_folder(folder, ffmpeg_path=FFMPEG_PATH):
    """Only indexed, decodable audio files can be requested by the server."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        return {}
    return {hashlib.sha256(str(file.resolve()).encode()).hexdigest(): file.resolve()
            for file in sorted(path.iterdir(), key=lambda file: file.name.casefold())
            if is_playable_audio(file, ffmpeg_path)}


# Keep compatibility with existing imports and relay configuration.
scan_mp3_folder = scan_audio_folder


def copy_soundboard_files(paths, folder, ffmpeg_path=FFMPEG_PATH):
    destination = Path(folder).expanduser()
    if not destination.is_dir():
        raise ValueError("Choose an existing soundboard folder first.")
    copied, skipped = [], []
    for raw in paths:
        source = Path(raw)
        if not is_playable_audio(source, ffmpeg_path):
            skipped.append(source.name)
            continue
        if source.resolve().parent == destination.resolve():
            skipped.append(source.name)
            continue
        candidate = destination / source.name
        number = 2
        while True:
            try:
                # Exclusive creation preserves existing files and concurrent drops.
                with candidate.open("xb") as output:
                    try:
                        with source.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
                    except Exception:
                        output.close()
                        candidate.unlink(missing_ok=True)
                        raise
                copied.append(candidate)
                break
            except FileExistsError:
                candidate = destination / f"{source.stem} ({number}){source.suffix}"
                number += 1
            except OSError:
                skipped.append(source.name)
                break
    return copied, skipped


def create_root():
    from tkinterdnd2 import TkinterDnD
    return TkinterDnD.Tk()


class RelayWindow:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.agent = RelayAgent(config)
        self.loop = asyncio.new_event_loop()
        self.file_ids = []
        root.title("TacoBot")
        root.geometry("1180x780")
        root.minsize(1160, 620)
        style_relay(root)
        self.status = tk.StringVar(value="Connecting…")
        self.folder = tk.StringVar(value=config.get("mp3_folder", ""))
        top_bar = ttk.Frame(root)
        top_bar.pack(fill="x", padx=28, pady=(18, 8))
        tabs_bar = ttk.Frame(top_bar)
        tabs_bar.pack(fill="x")
        ttk.Style(root).configure("Brand.TLabel", font=("Segoe UI", 16, "bold"))
        brand = ttk.Frame(tabs_bar)
        brand.pack(side="left", padx=(0, 30))
        mark = tk.Canvas(brand, width=38, height=32, background="#14171c", highlightthickness=0)
        mark.pack(side="left", padx=(0, 10))
        for x, height in zip((5, 11, 17, 23, 29), (8, 18, 28, 18, 8)):
            mark.create_line(x, 16-height/2, x, 16+height/2, fill="#52d4ba", width=3, capstyle="round")
        ttk.Label(brand, text="TacoBot", style="Brand.TLabel").pack(side="left")
        audio_bar = ttk.Frame(top_bar)
        audio_bar.pack(fill="x", pady=(22, 0))
        audio_bar.pack_propagate(False)
        ttk.Style(root).configure("Compact.TButton", padding=(6, 7), font=("Segoe UI", 9))
        self.identity = tk.StringVar(value="Connecting your relay")
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=28)
        self.tab_buttons = []
        for index, text in enumerate(("YouTube Music", "Soundboard", "TTS")):
            button = ttk.Button(tabs_bar, text=text, style="SelectedTab.TButton" if index == 0 else "OtherTab.TButton",
                                command=lambda i=index: self.notebook.select(i))
            button.pack(side="left", anchor="s", padx=(0, 6))
            self.tab_buttons.append(button)
        self.tab_toolbars = [ttk.Frame(audio_bar) for _ in range(3)]
        self.toolbar_buttons = []
        button = ttk.Button(self.tab_toolbars[1], text="Open folder", style="Compact.TButton", command=self.choose_folder)
        button.pack(side="right", anchor="center", padx=(6, 0))
        self.toolbar_buttons.append(button)
        self.install_button = ttk.Button(tabs_bar, text="Install", style="Compact.TButton", command=self.install_update_now, state="disabled")
        self.install_button.pack(side="right", anchor="center", padx=(6, 0))
        self.update_button = ttk.Button(tabs_bar, text="Check updates", style="Compact.TButton", command=lambda: self.check_updates(manual=True))
        self.update_button.pack(side="right", anchor="center", padx=(6, 0))
        self.auto_update = tk.BooleanVar(value=bool(config.get("auto_update", True)))
        self.auto_update_checkbox = ttk.Checkbutton(tabs_bar, text="Auto-update", variable=self.auto_update,
                                                    command=self.toggle_auto_update)
        self.auto_update_checkbox.pack(side="right", anchor="center", padx=(6, 0))
        self.direct_mode = tk.BooleanVar(value=False)
        self.direct_checkbox = ttk.Checkbutton(tabs_bar,
            text="Direct", variable=self.direct_mode,
            command=self.toggle_direct_mode)
        self.direct_checkbox.pack(side="right", anchor="center", padx=(6, 0))
        ttk.Button(tabs_bar, text="Audio ▾", style="Compact.TButton", command=self.direct_audio_settings).pack(side="right", anchor="center", padx=(6, 0))
        left, right = ttk.Frame(self.notebook, padding=(0, 18)), ttk.Frame(self.notebook, padding=0)
        self.notebook.add(left, text="YouTube Music")
        self.notebook.add(right, text="Soundboard")
        tts_panel = ttk.Frame(self.notebook, padding=(0, 22))
        self.notebook.add(tts_panel, text="TTS")
        ttk.Label(tts_panel, text="TEXT TO SPEECH", style="CardMuted.TLabel").pack(anchor="w")
        ttk.Label(tts_panel, text="Type text and press Enter to speak through the bot. Shift+Enter adds a new line.").pack(anchor="w", pady=(10, 14))
        voice_row = self.tab_toolbars[2]

        ttk.Label(voice_row, text="Voice").pack(side="left", padx=(0, 10))
        chosen_voice = config.get("tts_voice", VOICE)
        if "tts_enabled_voices" not in config:
            config["tts_enabled_voices"] = list(dict.fromkeys([chosen_voice] + [voice for voice in VOICE_CATALOG if self.agent.speech.downloaded(voice)]))
        enabled_options = {label: voice for label, voice in VOICE_OPTIONS.items() if voice in config["tts_enabled_voices"]}

        self.tts_voice = tk.StringVar(value=next((label for label, voice in enabled_options.items() if voice == chosen_voice), next(iter(enabled_options), "")))
        self.tts_voice_picker = ttk.Combobox(voice_row, textvariable=self.tts_voice,
                                           values=list(enabled_options), state="readonly", width=35)
        self.tts_voice_picker.pack(side="left")
        self.tts_voice_picker.bind("<<ComboboxSelected>>", self.save_tts_voice)
        ttk.Label(voice_row, text="Speaker").pack(side="left", padx=(16, 8))
        self.tts_speaker = tk.StringVar(value=str(config.get("tts_speaker", 0)))
        self.tts_speaker_picker = ttk.Combobox(voice_row, textvariable=self.tts_speaker, state="readonly", width=18)
        self.tts_speaker_picker.pack(side="left")
        self.tts_speaker_picker.bind("<<ComboboxSelected>>", self.save_tts_voice)
        self.update_tts_speakers()
        ttk.Button(voice_row, text="Manage voices", command=self.manage_voices).pack(side="left", padx=12)
        self.tts_text = tk.Text(tts_panel, height=8, wrap="word", font=("Segoe UI", 12),
                                background="#1b2027", foreground="#e6edf3", insertbackground="#52d4ba",
                                relief="flat", highlightthickness=0, padx=16, pady=12)
        self.tts_text.pack(fill="x")
        self.tts_text.bind("<Control-BackSpace>", self.delete_tts_word)
        self.tts_text.bind("<Shift-BackSpace>", self.clear_tts_text)
        self.tts_text.bind("<Return>", self.play_text)
        self.tts_text.bind("<Shift-Return>", lambda event: None)
        tts_controls = ttk.Frame(tts_panel)
        tts_controls.pack(fill="x", pady=14)
        self.tts_play_button = ttk.Button(tts_controls, text="▶  Play text", style="Primary.TButton", command=self.play_text)
        self.tts_play_button.pack(side="left")
        ttk.Button(tts_controls, text="🛑  Stop speech / clips", command=lambda: self.send({"type": "local_stop", "guild_id": None})).pack(side="left", padx=10)
        ttk.Label(tts_panel, text=f"Piper · English, Dutch, German, French, Bulgarian · Up to {MAX_TEXT} characters · Generated locally, no API fees").pack(anchor="w")

        translation_row = ttk.Frame(tts_panel)
        translation_row.pack(fill="x", pady=(20, 10))
        ttk.Label(translation_row, text="Translate from").pack(side="left", padx=(0, 8))
        self.translation_source = tk.StringVar(value=config.get("translation_source", "English"))
        self.translation_target = tk.StringVar(value=config.get("translation_target", "German"))
        ttk.Combobox(translation_row, textvariable=self.translation_source, values=list(LANGUAGES), state="readonly", width=12).pack(side="left")
        ttk.Label(translation_row, text="to").pack(side="left", padx=8)
        ttk.Combobox(translation_row, textvariable=self.translation_target, values=list(LANGUAGES), state="readonly", width=12).pack(side="left")
        self.translate_button = ttk.Button(translation_row, text="Translate", command=self.translate_text)
        self.translate_button.pack(side="left", padx=10)
        self.translated_play_button = ttk.Button(translation_row, text="Play translated", command=self.play_translated, state="disabled")
        self.translated_play_button.pack(side="left")
        self.translated_text = tk.Text(tts_panel, height=5, wrap="word", font=("Segoe UI", 12),
                                       background="#1b2027", foreground="#e6edf3", relief="flat", padx=16, pady=12, state="disabled")
        self.translated_text.pack(fill="x")
        ttk.Label(tts_panel, text="Translation runs locally. First use downloads the required language models.").pack(anchor="w", pady=8)

        self.notebook.bind("<<NotebookTabChanged>>", self.update_tabs)
        now_card = ttk.Frame(left, style="Card.TFrame", padding=22)
        now_card.pack(fill="x", pady=(0, 16))
        ttk.Label(now_card, text="NOW PLAYING", style="CardMuted.TLabel").pack(anchor="w")
        self.current_song = tk.StringVar(value="Your next song starts here")
        ttk.Label(now_card, textvariable=self.current_song, style="Song.TLabel", wraplength=850).pack(anchor="w", pady=(8, 4))
        music_controls = self.tab_toolbars[0]
        self.previous_button = ttk.Button(music_controls, text="Previous", command=lambda: self.music_control("previous"))
        self.previous_button.pack(side="left")
        self.toggle_button = ttk.Button(music_controls, text="Play", style="Primary.TButton", command=lambda: self.music_control("toggle"))
        self.toggle_button.pack(side="left", padx=8)
        ttk.Button(music_controls, text="Next", command=lambda: self.music_control("next")).pack(side="left")
        self.music_status = tk.StringVar(value="No music playing")
        ttk.Label(now_card, textvariable=self.music_status, style="CardMuted.TLabel").pack(anchor="w", pady=4)
        music_split = ttk.Panedwindow(left, orient="horizontal")
        music_split.pack(fill="both", expand=True)
        saved_pane = ttk.Frame(music_split, padding=(0, 0, 16, 0))
        queue_pane = ttk.Frame(music_split)
        music_split.add(saved_pane, weight=1)
        music_split.add(queue_pane, weight=2)
        self.favorites_view = ttk.Treeview(saved_pane, columns=("song",), show="tree headings", selectmode="browse")
        self.favorites_view.column("#0", width=24, minwidth=24, stretch=False)
        self.favorites_view.bind("<<TreeviewOpen>>", self.expand_favorite_playlist)
        self.favorites_view.heading("song", text="Favorites / playlists")
        self.favorites_view.column("song", width=270, minwidth=140)
        saved_scroll = ttk.Scrollbar(saved_pane, orient="vertical", command=self.favorites_view.yview)
        self.favorites_view.configure(yscrollcommand=saved_scroll.set)
        saved_scroll.pack(side="right", fill="y")
        ttk.Button(saved_pane, text="Save playlist link", command=self.save_playlist_link).pack(side="bottom", anchor="w", pady=8)
        self.favorites_view.pack(fill="both", expand=True)
        self.bind_smooth_scroll(self.favorites_view)
        self.favorites_view.bind("<Double-Button-1>", self.play_favorite)
        self.favorites_view.bind("<Button-3>", self.favorite_context_menu)
        self.favorite_menu = themed_menu(root)
        self.favorite_menu.add_command(label="Remove saved item", command=self.remove_favorite)
        self.favorite_context_id = None
        self.render_favorites()
        self.queue_view = ttk.Treeview(queue_pane, columns=("song",), show="headings", selectmode="extended")
        self.queue_view.heading("song", text="Your music queue")
        self.queue_view.bind("<Button-3>", self.song_context_menu)
        self.queue_context_id = None
        self.queue_menu = themed_menu(root)
        self.queue_menu.add_command(label="Favorite", command=self.favorite_song)
        self.queue_menu.add_command(label="Remove from queue", command=self.remove_queue_song)
        self.queue_pressed = None
        self.queue_view.bind("<ButtonPress-1>", self.song_mouse_down)
        self.queue_view.bind("<ButtonRelease-1>", self.song_selected)
        self.queue_view.bind("<Delete>", lambda event: self.remove_queue_song())
        self.queue_view.column("song", width=400)
        self.queue_view.tag_configure("current", background="#25443f", foreground="#75e4ce", font=("Segoe UI", 10, "bold"))
        queue_scroll = ttk.Scrollbar(queue_pane, orient="vertical", command=self.queue_view.yview)
        self.queue_view.configure(yscrollcommand=queue_scroll.set)
        queue_scroll.pack(side="right", fill="y")
        self.paste_playlist = tk.BooleanVar(value=bool(config.get("paste_playlist", False)))
        self.paste_playlist_switch = ttk.Checkbutton(music_controls, text="Paste full playlist",
                        variable=self.paste_playlist, command=self.save_paste_setting)
        self.paste_playlist_switch.pack(side="left", padx=12)
        self.queue_view.pack(fill="both", expand=True, padx=(0, 12))
        root.bind("<Control-v>", self.paste_youtube_link)
        root.bind("<Control-V>", self.paste_youtube_link)
        search_bar = ttk.Frame(self.tab_toolbars[1])
        search_bar.pack(side="left", anchor="center")
        # Stop stays beside search; library and audio settings share the top toolbar.
        self.stop_clips_button = ttk.Button(self.tab_toolbars[1], text="■  Stop clips", style="Compact.TButton", command=self.stop)
        self.stop_clips_button.pack(side="left", anchor="center", padx=(0, 6), before=search_bar)
        ttk.Label(search_bar, text="Search clips", style="Toolbar.TLabel").pack(side="left", padx=(0, 6))
        self.clip_search = tk.StringVar(value="")
        self.search_field = ttk.Frame(search_bar)
        self.search_field.pack(side="left")
        self.clip_search_entry = ttk.Entry(self.search_field, textvariable=self.clip_search, width=15, style="Search.TEntry")
        self.clip_search_entry.pack()
        clear_icon = ImageTk.PhotoImage(render_button_icon("clear",20,"#a3adb9"), master=root)
        self.clear_search_button = tk.Label(self.search_field, image=clear_icon, background="#1b2027", bd=0, cursor="hand2")
        self.clear_search_button.graphite_icon = clear_icon
        self.clear_search_button.bind("<Button-1>", self.clear_clip_search)
        self.clip_search.trace_add("write", self.update_clip_search)
        ttk.Label(search_bar, text="Volume", style="Toolbar.TLabel").pack(side="left", padx=(12, 4))
        self.bot_clip_volume = tk.DoubleVar(value=max(0, min(100, config.get("bot_clip_volume", 100))))
        self.bot_volume_text = tk.StringVar(value=f"{round(self.bot_clip_volume.get())}%")
        SleekScale(search_bar, variable=self.bot_clip_volume, command=self.set_bot_clip_volume, width=100).pack(side="left", padx=(6, 4))
        self.bot_volume_label = ttk.Label(search_bar, textvariable=self.bot_volume_text, width=6, anchor="w", style="Muted.TLabel")
        self.bot_volume_label.pack(side="left", padx=(0, 6))
        details = ttk.Frame(right)
        details.pack(fill="x", pady=(4, 8))
        ttk.Label(details, text="Click a clip to play • Right-click to manage", anchor="e", style="Muted.TLabel").pack(side="left", padx=(12, 18))
        self.folder_label = ttk.Label(details, textvariable=self.folder, width=30, anchor="e", style="Muted.TLabel")
        self.folder_label.pack(side="left")
        self.sync_running = False
        self.sort_keys = [("name", False)]
        self.sort_column = "name"
        self.sort_reverse = False
        self.clip_origins = {}
        self.listbox = ttk.Treeview(right, columns=("name", "volume", "shared", "label"), show="headings", selectmode="browse", style="Soundboard.Treeview")
        self.listbox.tag_configure("even", background="#1b2027")
        self.listbox.tag_configure("odd", background="#191e25")
        self.listbox.heading("name", text="Name", anchor="w")
        self.listbox.heading("shared", text="Shared")
        # Fixed widths leave unused space on the right after resizing columns.
        self.listbox.column("name", width=440, minwidth=120, stretch=False)
        self.listbox.heading("volume", text="Volume")
        self.listbox.column("volume", width=250, minwidth=150, stretch=False)
        self.listbox.heading("label", text="Label")
        self.listbox.column("label", width=200, minwidth=100, stretch=False)
        self.clip_widgets = {}
        self.header_drag = None
        self.column_order = clip_column_order(config.get("clip_column_order"))
        self.listbox.configure(displaycolumns=self.column_order)
        self.listbox.bind("<Configure>", self.defer_clip_resize)
        self.listbox.bind("<Map>", lambda event: self.root.after_idle(self.settle_clip_tab_layout))
        self.listbox.column("shared", width=100, stretch=False, anchor="center")
        clip_scroll = ttk.Scrollbar(right, orient="vertical", command=self.scroll_clips)
        self.listbox.configure(yscrollcommand=lambda *args: (clip_scroll.set(*args), self.schedule_clip_position()))
        clip_scroll.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True)
        self.bind_smooth_scroll(self.listbox)
        self.bind_smooth_scroll(self.queue_view)
        self.clip_pressed_index = None
        self.listbox.bind("<ButtonPress-1>", self.clip_mouse_down)
        self.listbox.bind("<ButtonRelease-1>", self.play_selected)
        self.listbox.bind("<B1-Motion>", self.clip_mouse_move, add="+")
        self.listbox.bind("<ButtonRelease-1>", lambda event: self.root.after_idle(self.position_clip_widgets), add="+")
        self.clip_menu = themed_menu(root)
        self.clip_menu.add_command(label="Share", command=lambda: self.share_clip(False))
        self.clip_menu.add_command(label="Share then del local", command=lambda: self.share_clip(True))
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Rename", command=self.rename_clip)
        self.clip_menu.add_command(label="Delete", command=self.delete_clip)
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Add Label", command=self.add_label)
        color_menu = themed_menu(self.clip_menu)
        for name, color in LABEL_COLORS.items():
            color_menu.add_command(label=name, foreground=color, command=lambda n=name: self.change_label_color(n))
        self.clip_menu.add_cascade(label="Change Label Color", menu=color_menu)
        self.context_clip_id = None
        self.listbox.bind("<Button-3>", self.clip_context_menu)
        self.status_label = ttk.Label(root, textvariable=self.status, wraplength=1000, style="Muted.TLabel")

        self.register_drop_targets(root)
        update_bar = ttk.Frame(root)
        # Reserve the footer before the expanding notebook consumes the window.
        update_bar.pack(side="bottom", fill="x", padx=28, pady=(4, 12), before=self.notebook)
        self.status_label.pack(in_=update_bar, side="left", fill="x", expand=True, padx=(0, 12))
        self.sync_button = ttk.Button(self.tab_toolbars[1], text="⇄  Sync", style="Compact.TButton", command=self.sync_shared)
        self.sync_button.pack(side="left", anchor="center", padx=(0, 6), before=search_bar)
        self.refresh_button = ttk.Button(self.tab_toolbars[1], text="↻  Refresh", style="Compact.TButton", command=self.refresh_files)
        self.refresh_button.pack(side="left", anchor="center", padx=(0, 6), before=self.sync_button)
        # Toolbar and search retain the same command handlers as the previous layout.
        self.clip_action_buttons = [self.refresh_button, self.stop_clips_button, self.sync_button]
        ttk.Label(update_bar, text=f"TacoBot v1.{RELAY_BUILD}", style="Muted.TLabel").pack(side="right")
        ttk.Label(update_bar, textvariable=self.identity, style="Muted.TLabel").pack(side="right", padx=18)
        self.add_tab_volume(self.tab_toolbars[0], "music_volume")
        self.add_tab_volume(self.tab_toolbars[2], "tts_volume")
        self.tab_toolbars[0].pack(fill="x")
        add_button_icons(root)
        self.set_music_playing(False)
        root.update_idletasks()
        audio_bar.configure(height=max(toolbar.winfo_reqheight() for toolbar in self.tab_toolbars))
        self.prewarm_tabs()
        root.minsize(max(1160, max(audio_bar.winfo_reqwidth(), tabs_bar.winfo_reqwidth()) + 56), 620)
        self.update_check_running = False
        self.pending_update = None
        status_file = FILES_DIR / "update-status.txt"
        failed_update = status_file.is_file() and "failed" in status_file.read_text(encoding="utf-8-sig", errors="replace").lower()
        self.next_update_check = time.monotonic() + 6 * 60 * 60 if failed_update else 0
        if failed_update:
            self.status.set("Previous update failed. See Files/update-install.log; use Check for updates to retry.")
        self.update_idle_since = None
        self.last_music = None
        self.next_music_refresh = 0
        self.refresh_files()
        threading.Thread(target=self.run_agent, daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if config.get("direct_soundboard"):
            root.after(300, self.restore_direct_mode)

    def delete_tts_word(self, event=None):
        selection = self.tts_text.tag_ranges("sel")
        if selection:
            self.tts_text.delete(selection[0], selection[1])
        else:
            before = self.tts_text.get("1.0", "insert")
            match = re.search(r"\S+\s*$", before)
            if match:
                self.tts_text.delete(f"insert-{len(before)-match.start()}c", "insert")
            elif before:
                self.tts_text.delete("1.0", "insert")
        return "break"

    def clear_tts_text(self, event=None):
        self.tts_text.delete("1.0", "end")
        return "break"

    def refresh_enabled_voices(self):
        enabled = self.config.get("tts_enabled_voices", [VOICE])
        options = [label for label, voice in VOICE_OPTIONS.items() if voice in enabled]
        self.tts_voice_picker.configure(values=options)
        if self.tts_voice.get() not in options:
            self.tts_voice.set(options[0] if options else "")
            self.config["tts_voice"] = VOICE_OPTIONS.get(self.tts_voice.get(), "")
            self.config["tts_speaker"] = 0
        if options:
            self.update_tts_speakers()
        else:
            self.tts_speaker.set("")
            self.tts_speaker_picker.configure(values=[])
        self.save()

    def manage_voices(self):
        if getattr(self, "voice_manager", None) is not None and self.voice_manager.winfo_exists():
            self.voice_manager.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.voice_manager = dialog
        dialog.title("TacoBot · Voice models")
        dialog.geometry("820x540")
        dialog.configure(background="#14171c")
        ttk.Label(dialog, text="Download models to keep locally. Enable the voices you want in your TTS selector.").pack(anchor="w", padx=18, pady=16)
        self.voice_filter = tk.StringVar()
        ttk.Entry(dialog, textvariable=self.voice_filter).pack(fill="x", padx=18, pady=(0,10))
        area = ttk.Frame(dialog)
        area.pack(fill="both", expand=True, padx=18)
        self.voice_models = ttk.Treeview(area, columns=("model","downloaded","enabled","speakers"),show="headings",selectmode="extended",style="Soundboard.Treeview")
        for column, title, width in (("model","Voice model",470),("downloaded","Downloaded",90),("enabled","Enabled",75),("speakers","Speakers",65)):
            self.voice_models.heading(column,text=title)
            self.voice_models.column(column,width=width)
        scroll = ttk.Scrollbar(area, orient="vertical",command=self.voice_models.yview)
        scroll.pack(side="right", fill="y")
        self.voice_models.configure(yscrollcommand=scroll.set)
        self.voice_models.pack(fill="both", expand=True)
        actions = ttk.Frame(dialog)
        actions.pack(fill="x", padx=18,pady=14)
        self.voice_manager_buttons = []
        for text, action in (("Download & enable","download"),("Enable","enable"),("Disable","disable"),("Remove download","remove"),("Use selected","use")):
            button = ttk.Button(actions,text=text,command=lambda mode=action:self.change_voice_models(mode))
            button.pack(side="left",padx=(0,8))
            self.voice_manager_buttons.append(button)
        self.voice_model_status = tk.StringVar(value="Select one or more models. Downloads may take a minute.")
        ttk.Label(dialog,textvariable=self.voice_model_status).pack(anchor="w",padx=18,pady=(0,14))
        self.voice_filter.trace_add("write",lambda *_:self.refresh_voice_models())
        self.refresh_voice_models()

    def refresh_voice_models(self):
        if not getattr(self,"voice_manager",None) or not self.voice_manager.winfo_exists():
            return
        selection = self.voice_models.selection()
        self.voice_models.delete(*self.voice_models.get_children())
        query = self.voice_filter.get().casefold()
        enabled = self.config.get("tts_enabled_voices",[VOICE])
        for label, voice in VOICE_OPTIONS.items():
            if query not in label.casefold():
                continue
            self.voice_models.insert("","end",iid=voice,values=(label,"Yes" if self.agent.speech.downloaded(voice) else "No","✓" if voice in enabled else "",VOICE_CATALOG[voice]['speakers']))
        self.voice_models.selection_set([voice for voice in selection if self.voice_models.exists(voice)])

    def change_voice_models(self, action):
        selected = list(self.voice_models.selection())
        if not selected:
            self.voice_model_status.set("Select a model first.")
            return
        if action in ("enable","disable","use"):
            enabled = set(self.config.get("tts_enabled_voices",[VOICE]))
            if action == "disable":
                enabled.difference_update(selected)
            else:
                enabled.update(selected)
            self.config["tts_enabled_voices"] = sorted(enabled)
            if action == "use":
                voice = selected[0]
                self.tts_voice.set(next(label for label,value in VOICE_OPTIONS.items() if value==voice))
                self.save_tts_voice()
            self.refresh_enabled_voices()
            self.refresh_voice_models()
            self.voice_model_status.set("Voice choices saved. Enabled models download automatically if used before downloading.")
            return
        for button in self.voice_manager_buttons:
            button.configure(state="disabled")
        self.voice_model_status.set("Downloading selected models…" if action=="download" else "Removing selected downloads…")
        def worker():
            finished = []
            try:
                for voice in selected:
                    if action=="download":
                        self.agent.speech.download(voice)
                    else:
                        self.agent.speech.remove(voice)
                    finished.append(voice)
                self.agent.events.put({"voice_models_done":True,"voice_models_action":action,"voice_models_finished":finished})
            except Exception as exc:
                self.agent.events.put({"voice_models_done":True,"voice_models_action":action,"voice_models_finished":finished,"error":"Voice model update failed: "+clean_error(exc)})
        threading.Thread(target=worker,daemon=True).start()

    def update_tts_speakers(self):
        if self.tts_voice.get() not in VOICE_OPTIONS:
            self.tts_speaker.set("")
            self.tts_speaker_picker.configure(values=[])
            return
        voice = VOICE_OPTIONS[self.tts_voice.get()]
        info = VOICE_CATALOG[voice]
        names = {number: name for name, number in info['speaker_names'].items()}
        self.tts_speaker_options = {f"{number}: {names.get(number, 'Speaker '+str(number))}": number for number in range(info['speakers'])}
        values = list(self.tts_speaker_options)
        current = self.config.get("tts_speaker", 0)
        self.tts_speaker_picker.configure(values=values)
        self.tts_speaker.set(next((label for label, number in self.tts_speaker_options.items() if number == current),values[0]))

    def save_tts_voice(self, event=None):
        voice = VOICE_OPTIONS[self.tts_voice.get()]
        changed = voice != self.config.get("tts_voice", VOICE)
        self.config["tts_voice"] = voice
        if changed:
            self.config["tts_speaker"] = 0
            self.update_tts_speakers()
        self.config["tts_speaker"] = self.tts_speaker_options.get(self.tts_speaker.get(),0)
        self.save()

    def translate_text(self):
        text = self.tts_text.get("1.0", "end-1c").strip()
        if not text or len(text) > MAX_TEXT:
            self.status.set(f"Enter between 1 and {MAX_TEXT} characters.")
            return
        source, target = self.translation_source.get(), self.translation_target.get()
        self.config.update(translation_source=source, translation_target=target)
        self.save()
        self.translate_button.configure(state="disabled")
        self.translated_play_button.configure(state="disabled")
        self.status.set("Translating locally… First use downloads language models.")
        def worker():
            try:
                output = self.agent.translator.translate(text, LANGUAGES[source], LANGUAGES[target])
                self.agent.events.put({"translation_done": True,"translated_text": output,"translated_language": LANGUAGES[target]})
            except Exception as exc:
                self.agent.events.put({"translation_done": True,"error": "Translation failed: " + clean_error(exc)})
        threading.Thread(target=worker,daemon=True).start()

    def play_translated(self):
        text = self.translated_text.get("1.0", "end-1c").strip()
        if not text or self.tts_play_button.instate(["disabled"]):
            return
        language = self.translated_language
        current = VOICE_OPTIONS.get(self.tts_voice.get(),VOICE)
        if self.tts_voice.get() not in VOICE_OPTIONS or VOICE_CATALOG[current]['language'] != language:
            choice = next(label for label, voice in VOICE_OPTIONS.items() if VOICE_CATALOG[voice]['language'] == language and VOICE_CATALOG[voice]['quality']=='medium')
            voice = VOICE_OPTIONS[choice]
            enabled = self.config.setdefault("tts_enabled_voices",[VOICE])
            if voice not in enabled:
                enabled.append(voice)
            self.tts_voice.set(choice)
            self.save_tts_voice()
            self.refresh_enabled_voices()
        if len(text)>MAX_TEXT:
            self.status.set("Translation is longer than the speech limit. Shorten the input text.")
            return
        self.tts_play_button.configure(state="disabled")
        asyncio.run_coroutine_threadsafe(self.agent.play_text(text, VOICE_OPTIONS[self.tts_voice.get()], self.tts_speaker_options[self.tts_speaker.get()]), self.loop)

    def play_text(self, event=None):
        if self.tts_voice.get() not in VOICE_OPTIONS:
            self.status.set("Enable a voice in Manage voices first.")
            return "break"
        if self.tts_play_button.instate(["disabled"]):
            return "break"
        text = self.tts_text.get("1.0", "end-1c").strip()
        if not text or len(text) > MAX_TEXT:
            self.status.set(f"Enter between 1 and {MAX_TEXT} characters.")
            return "break"
        self.tts_play_button.configure(state="disabled")
        asyncio.run_coroutine_threadsafe(self.agent.play_text(text, VOICE_OPTIONS[self.tts_voice.get()], self.tts_speaker_options[self.tts_speaker.get()]), self.loop)
        return "break"

    def update_tabs(self, event=None):
        selected = self.notebook.index(self.notebook.select())
        for index, button in enumerate(self.tab_buttons):
            button.configure(style="SelectedTab.TButton" if index == selected else "OtherTab.TButton")
        for index, toolbar in enumerate(self.tab_toolbars):
            toolbar.pack_forget()
            if index == selected:
                toolbar.pack(fill="x")
        if selected == 1:
            self.root.after_idle(self.position_clip_widgets)

    def settle_clip_tab_layout(self):
        self.position_clip_widgets()

    def prewarm_tabs(self):
        """Prepare every panel once; retain geometry and graphics between visits."""
        selected = self.notebook.select()
        for index in range(3):
            self.notebook.select(index)
            self.update_tabs()
            self.root.update_idletasks()
            if index == 1:
                self.position_clip_widgets()
        self.notebook.select(selected)
        self.update_tabs()


    def restore_direct_mode(self):
        self.direct_mode.set(True)
        self.toggle_direct_mode()

    def toggle_direct_mode(self):
        if self.direct_mode.get() and not self.config.get("direct_audio_devices"):
            self.direct_mode.set(False)
            self.direct_audio_settings(enable=True)
            return
        enabled = self.direct_mode.get()
        self.direct_checkbox.configure(state="disabled")
        self.status.set("Configuring direct audio…" if enabled else "Returning soundboard playback to the bot…")
        async def change():
            await self.agent.stop_direct_clips()
            try:
                if enabled:
                    await asyncio.to_thread(self.agent.direct_audio.configure, self.config["direct_audio_devices"])
                else:
                    await asyncio.to_thread(self.agent.direct_audio.close)
                self.agent.events.put({"direct_ready": enabled})
            except Exception as exc:
                self.agent.events.put({"direct_failed": clean_error(exc)})
        asyncio.run_coroutine_threadsafe(change(), self.loop)

    def direct_audio_settings(self, enable=False):
        if self.direct_checkbox.instate(["disabled"]):
            self.status.set("Wait for direct audio setup to finish.")
            return
        try:
            devices = audio_devices()
        except Exception as exc:
            messagebox.showerror("Direct audio", str(exc), parent=self.root)
            return
        outputs = [d for d in devices if d['output'] >= 2]
        inputs = [d for d in devices if d['input'] >= 1 and not any(
            word in d['name'].lower() for word in ('cable output', 'voicemeeter output'))]
        cable = next((d for d in outputs if 'cable input' in d['name'].lower()
                      and 'WASAPI' in d['key']), None) or next((d for d in outputs if 'cable input' in d['name'].lower()), None)
        if not outputs:
            messagebox.showerror("Direct audio", "No stereo audio outputs found.", parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Direct soundboard audio")
        dialog.transient(self.root)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Install VB-CABLE if needed: https://vb-audio.com/Cable/\n"
            "Select CABLE Input below, then select CABLE Output as Discord's Input Device.\n"
            "Microphone passthrough mixes your voice into the cable; speakers play clips only.", wraplength=640).pack(anchor="w", pady=(0, 12))
        previous = self.config.get('direct_audio_devices', {})
        choices = {}
        physical = [d for d in outputs if not any(word in d['name'].lower() for word in ('cable', 'voicemeeter'))]
        default_speaker = next((d for d in physical if 'WASAPI' in d['key']), None) or next(iter(physical), outputs[0])
        default_mic = next((d for d in inputs if 'WASAPI' in d['key']), None) or next(iter(inputs), None)
        for key, title, options, default in (
            ('cable', 'Virtual microphone output (usually CABLE Input)', [d['key'] for d in outputs], cable['key'] if cable else ''),
            ('speakers', 'Speakers / headphones for hearing clips', [d['key'] for d in outputs], default_speaker['key']),
            ('microphone', 'Physical microphone to pass through (optional)', ['No microphone passthrough'] + [d['key'] for d in inputs], default_mic['key'] if default_mic else 'No microphone passthrough')):
            ttk.Label(panel, text=title).pack(anchor="w", pady=(6, 2))
            selected = previous.get(key) or default
            value = tk.StringVar(value=selected if selected in options else default)
            ttk.Combobox(panel, textvariable=value, values=options, state="readonly", width=80).pack(fill="x")
            choices[key] = value
        def apply():
            settings = {key: value.get() for key, value in choices.items()}
            if settings['microphone'] == 'No microphone passthrough':
                settings['microphone'] = None
            if not settings['cable']:
                messagebox.showerror("Direct audio", "Install a virtual audio cable and select its playback output.", parent=dialog)
                return
            self.config['direct_audio_devices'] = settings
            self.save(); dialog.destroy()
            if enable or self.direct_mode.get():
                self.direct_mode.set(True); self.toggle_direct_mode()
        ttk.Button(panel, text="Save and enable" if enable else "Save", command=apply).pack(anchor="e", pady=(16, 0))

    def toggle_auto_update(self):
        self.config["auto_update"] = self.auto_update.get()
        self.save()
        status_file = FILES_DIR / "update-status.txt"
        failed_update = status_file.is_file() and "failed" in status_file.read_text(encoding="utf-8-sig", errors="replace").lower()
        self.next_update_check = time.monotonic() + 6 * 60 * 60 if failed_update else 0
        if failed_update:
            self.status.set("Previous update failed. See Files/update-install.log; use Check for updates to retry.")

    def check_updates(self, manual=False):
        if self.update_check_running or self.pending_update is not None:
            return
        if not getattr(sys, "frozen", False):
            self.next_update_check = time.monotonic() + 6 * 60 * 60
            if manual:
                self.status.set("Self-updates are available in the packaged Windows executable.")
            return
        self.update_check_running = True
        self.next_update_check = time.monotonic() + 6 * 60 * 60
        self.update_button.configure(state="disabled")
        if manual:
            self.status.set("Checking GitHub for relay updates…")
        def check():
            try:
                update = find_update(RELAY_BUILD)
                if update is None:
                    self.agent.events.put({"update_checked": True,
                        "status": "Relay is up to date." if manual else None})
                else:
                    stage = prepare_update(update)
                    self.agent.events.put({"update_checked": True, "update_stage": stage,
                        "update_manual": manual, "status": f"Relay build {update['build']} downloaded; waiting for audio to finish."})
            except Exception as exc:
                self.agent.events.put({"update_checked": True,
                    "status": "Update check failed; playback remains available. " + clean_error(exc)})
        threading.Thread(target=check, daemon=True).start()

    def install_update_now(self):
        if self.pending_update:
            stage, _ = self.pending_update
            self.pending_update = (stage, True)
            self.update_idle_since = time.monotonic() - 30
        else:
            self.install_button.configure(state="disabled")

    def poll_updates(self):
        now = time.monotonic()
        if hasattr(self, "install_button"):
            self.install_button.configure(state="normal" if self.pending_update else "disabled")
        if self.pending_update is not None:
            stage, manual = self.pending_update
            if not manual and not self.auto_update.get():
                self.pending_update = None
                threading.Thread(target=lambda: shutil.rmtree(stage, ignore_errors=True), daemon=True).start()
                return
            busy = self.agent.direct_audio.playing or bool(self.agent.direct_tasks) or bool(self.agent.stream_task and not self.agent.stream_task.done()) or any(not task.done() for task in self.agent.clip_tasks.values())
            busy = busy and not manual
            if busy:
                self.update_idle_since = None
            elif self.update_idle_since is None:
                self.update_idle_since = now
            elif manual or now - self.update_idle_since >= 5:
                try:
                    self.save()
                    launch_installer(stage, APP_DIR)
                except Exception as exc:
                    self.status.set("Could not install update: " + clean_error(exc))
                    self.pending_update = None
                    return
                self.close()
                return True
        elif self.auto_update.get() and now >= self.next_update_check:
            self.check_updates()

    def register_drop_targets(self, widget):
        from tkinterdnd2 import DND_FILES
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self.drop_files)
        for child in widget.winfo_children():
            self.register_drop_targets(child)

    def drop_files(self, event):
        self.clip_pressed_index = None
        from tkinterdnd2 import COPY, REFUSE_DROP
        folder = self.folder.get()
        if not folder or not Path(folder).is_dir():
            self.status.set("Choose a soundboard folder before dropping files.")
            return REFUSE_DROP
        paths = self.root.tk.splitlist(event.data)
        self.status.set("Copying dropped audio files…")
        def copy():
            try:
                copied, skipped = copy_soundboard_files(paths, folder)
                self.agent.events.put({"import_done": True, "folder": folder,
                    "status": f"Copied {len(copied)} audio file(s); skipped {len(skipped)}."})
            except Exception as exc:
                self.agent.events.put({"error": clean_error(exc)})
        threading.Thread(target=copy, daemon=True).start()
        return COPY

    def save(self):
        save_config(self.config)

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder.get() or None)
        if folder:
            self.config["mp3_folder"] = folder
            self.folder.set(folder)
            self.save()
            self.refresh_files()

    def refresh_files(self):
        folder = self.folder.get()
        self.scan_generation = getattr(self, "scan_generation", 0) + 1
        generation = self.scan_generation
        self.status.set("Scanning audio files…")
        def scan():
            files = scan_audio_folder(folder) if folder else {}
            origins = {key: "Local" for key in files}
            SHARED_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            shared = scan_audio_folder(str(SHARED_CLIPS_DIR))
            files.update(shared)
            origins.update({key: "Shared" for key in shared})
            self.agent.events.put({"library": files, "origins": origins, "generation": generation})
        threading.Thread(target=scan, daemon=True).start()

    def send(self, payload):
        if self.agent.websocket is None:
            self.status.set("Not connected yet.")
            return
        future = asyncio.run_coroutine_threadsafe(self.agent.send_json(payload), self.loop)
        def done(result):
            try:
                result.result()
            except Exception as exc:
                self.agent.events.put({"error": clean_error(exc)})
        future.add_done_callback(done)

    def target_id(self):
        # The server resolves the personal token's current voice channel each time.
        return None

    def refresh_music(self):
        self.send({"type": "music_state", "guild_id": None})

    def set_music_playing(self, playing):
        name = "pause" if playing else "play"
        if not hasattr(self, "music_button_images"):
            size = max(28, round(28 * float(self.root.tk.call("tk", "scaling")) / (96/72)))
            self.music_button_images = {key: ImageTk.PhotoImage(render_button_icon(key, size, "#10211e"), master=self.root) for key in ("play", "pause")}
        self.toggle_button.configure(text="Pause" if playing else "Play", image=self.music_button_images[name])

    def music_control(self, action):
        self.send({"type": "music_control", "guild_id": None, "action": action})
        self.refresh_music()

    def sync_shared(self):
        if self.sync_running or getattr(self, "editing_clips", set()):
            return
        self.sync_running = True
        self.status.set("Syncing shared clips…")
        def worker():
            try:
                up, down, conflicts = sync_clips(SHARED_CLIPS_DIR, self.config, AUDIO_EXTENSIONS, is_playable_audio)
                message = f"Shared sync: uploaded {up}, downloaded {down}."
                if conflicts:
                    message += " Rename conflicting/oversized clips: " + ", ".join(conflicts)
                self.agent.events.put({"sync_done": True, "status": message})
            except Exception as exc:
                self.agent.events.put({"sync_done": True, "error": clean_error(exc)})
        threading.Thread(target=worker, daemon=True).start()

    def sort_clips(self, column):
        keys = getattr(self, "sort_keys", [("name", False)])
        reverse = not keys[0][1] if keys and keys[0][0] == column else False
        self.sort_keys = [(column, reverse)] + [(c, r) for c, r in keys if c != column]
        self.render_clips()

    def render_clips(self):
        ids = sorted_clip_ids(self.agent.local_files, self.clip_origins,
                              self.config.get("clip_settings", {}), getattr(self, "sort_keys", [("name", False)]))
        query = self.clip_search.get().strip().casefold() if hasattr(self, "clip_search") else ""
        if query:
            settings = self.config.get("clip_settings", {})
            ids = [key for key in ids if query in self.agent.local_files[key].stem.casefold()
                   or query in settings.get(key, {}).get("label", "").casefold()]
        self.file_ids = ids
        for key in set(self.clip_widgets) - set(self.agent.local_files):
            cell = self.clip_widgets.pop(key)
            cell["menu"].destroy()
        self.listbox.delete(*self.listbox.get_children())
        for row_index, key in enumerate(ids):
            self.listbox.insert("", "end", iid=key, tags=("even" if row_index % 2 == 0 else "odd",), values=("▶    " + self.agent.local_files[key].stem, "", "☑" if self.clip_origins[key] == "Shared" else "☐", ""))
        self.preload_clip_widgets()
        self.root.after_idle(self.position_clip_widgets)

    def bind_smooth_scroll(self, widget, tree=None):
        tree = tree or widget
        widget.bind("<MouseWheel>", lambda event: self.smooth_scroll(event, tree))
        widget.bind("<Button-4>", lambda event: self.smooth_scroll(event, tree, -1))
        widget.bind("<Button-5>", lambda event: self.smooth_scroll(event, tree, 1))

    def smooth_scroll(self, event, tree, units=None):
        # Preserve high-resolution wheel deltas rather than rounding each event.
        if units is None:
            units = -event.delta / 120 * 9 if sys.platform != "darwin" else -event.delta
        states = getattr(self, "scroll_states", None)
        if states is None:
            self.scroll_states = states = {}
        state = states.setdefault(tree, {"pending": 0.0, "after": None})
        if state["pending"] * units < 0:
            state["pending"] = 0.0
        state["pending"] = max(-36, min(36, state["pending"] + units))
        if state["after"] is None and abs(state["pending"]) >= 1:
            self.scroll_step(tree, state)
        return "break"

    def scroll_step(self, tree, state):
        state["after"] = None
        if abs(state["pending"]) < 1:
            return
        step = min(3, int(abs(state["pending"]))) * (1 if state["pending"] > 0 else -1)
        before = tree.yview()
        tree.yview_scroll(step, "units")
        state["pending"] -= step
        if tree is self.listbox:
            self.position_clip_widgets()
        if tree.yview() == before:
            state["pending"] = 0.0
        elif abs(state["pending"]) >= 1:
            state["after"] = self.root.after(16, lambda: self.scroll_step(tree, state))

    def defer_clip_resize(self, event=None):
        size = (self.listbox.winfo_width(), self.listbox.winfo_height())
        previous = getattr(self, "clip_last_size", None)
        self.clip_last_size = size
        if previous is None:
            self.position_clip_widgets()
            return
        if size == previous:
            return
        pending = getattr(self, "clip_resize_after", None)
        if pending is not None:
            self.root.after_cancel(pending)
        # Preserve existing graphics during the drag; update once it settles.
        self.clip_resize_after = self.root.after(150, self.finish_clip_resize)

    def finish_clip_resize(self):
        self.clip_resize_after = None
        self.clip_position_signature = None
        self.position_clip_widgets()

    def schedule_clip_position(self):
        if getattr(self, "clip_resize_after", None) is not None:
            return
        if getattr(self, "clip_position_after", None) is None:
            self.clip_position_after = self.root.after_idle(self.flush_clip_position)

    def flush_clip_position(self):
        self.clip_position_after = None
        self.position_clip_widgets()

    def scroll_clips(self, *args):
        state = getattr(self, "scroll_states", {}).get(self.listbox)
        if state:
            if state["after"] is not None:
                self.root.after_cancel(state["after"])
            state.update(pending=0.0, after=None)
        self.listbox.yview(*args)
        self.position_clip_widgets()

    def update_clip_search(self, *args):
        if self.clip_search.get():
            self.clear_search_button.place(relx=1, rely=0.5, anchor="e", x=-5)
        else:
            self.clear_search_button.place_forget()
        self.render_clips()

    def clear_clip_search(self, event=None):
        self.clip_search.set("")
        self.clip_search_entry.focus_set()
        return "break"

    def clip_setting(self, key):
        return self.config.setdefault("clip_settings", {}).setdefault(key, {})

    def add_tab_volume(self, toolbar, key):
        ttk.Label(toolbar, text="Volume", style="Toolbar.TLabel").pack(side="left", padx=(16, 4))
        variable = tk.DoubleVar(value=max(0, min(100, self.config.get(key, 100))))
        text = tk.StringVar(value=f"{round(variable.get())}%")
        def changed(value):
            self.config[key] = round(float(value))
            text.set(f"{self.config[key]}%")
            pending = getattr(self, "volume_save_after", None)
            if pending:
                self.root.after_cancel(pending)
            self.volume_save_after = self.root.after(300, self.save_volume)
        scale = SleekScale(toolbar, variable=variable, command=changed, width=100)
        scale.pack(side="left", padx=6)
        ttk.Label(toolbar, textvariable=text, width=6, style="Muted.TLabel").pack(side="left")
        setattr(self, key + "_variable", variable)
        setattr(self, key + "_text", text)

    def set_bot_clip_volume(self, value):
        volume = round(float(value))
        self.config["bot_clip_volume"] = volume
        self.bot_volume_text.set(f"{volume}%")
        pending = getattr(self, "volume_save_after", None)
        if pending:
            self.root.after_cancel(pending)
        self.volume_save_after = self.root.after(300, self.save_volume)

    def set_clip_volume(self, key, value, text):
        volume = round(float(value))
        self.clip_setting(key)["volume"] = volume
        if text is not None:
            text.configure(text=f"{volume:+d}%" if volume else "0%")
        # Save once dragging settles, keeping the audio loop and UI responsive.
        pending = getattr(self, "volume_save_after", None)
        if pending:
            self.root.after_cancel(pending)
        self.volume_save_after = self.root.after(300, self.save_volume)

    def save_volume(self):
        self.volume_save_after = None
        self.save()

    def ensure_clip_canvases(self):
        if hasattr(self, "clip_canvases"):
            return
        self.clip_canvases = {}
        self.clip_slider_drag = None
        for column in ("volume", "label", "shared"):
            canvas = tk.Canvas(self.listbox, background="#1b2027", borderwidth=0,
                               highlightthickness=0, yscrollincrement=1)
            self.clip_canvases[column] = canvas
            self.bind_smooth_scroll(canvas, self.listbox)
            canvas.bind("<Button-3>", self.canvas_clip_menu)
        volume = self.clip_canvases["volume"]
        volume.bind("<ButtonPress-1>", self.canvas_volume_press)
        volume.bind("<B1-Motion>", self.canvas_volume_drag)
        volume.bind("<ButtonRelease-1>", self.canvas_volume_release)
        label = self.clip_canvases["label"]
        label.bind("<Button-1>", self.canvas_label_menu)

    def preload_clip_widgets(self):
        # Two drawable surfaces replace hundreds of separately painted HWNDs.
        self.ensure_clip_canvases()
        boxes = (self.listbox.bbox(key) for key in self.file_ids)
        first_box = next((box for box in boxes if box), None)
        self.clip_row_height = first_box[3] if first_box else int(ttk.Style(self.root).lookup(
            self.listbox.cget("style") or "Treeview", "rowheight") or 18)
        self.clip_body_top = first_box[1] if first_box else getattr(self, "clip_body_top", 29)
        self.clip_body_left = first_box[0] if first_box else getattr(self, "clip_body_left", 1)
        self.clip_position_signature = None
        self.clip_row_indices = {key: i for i, key in enumerate(self.file_ids)}
        self.clip_canvas_widths = {c: self.listbox.column(c, "width") for c in self.clip_canvases}
        for canvas in self.clip_canvases.values():
            canvas.delete("all")
        for key in self.file_ids:
            if key not in self.clip_widgets:
                menu = themed_menu(self.root)
                menu.configure(postcommand=lambda k=key: self.select_label_row(k))
                self.clip_widgets[key] = {"menu": menu}
            cell = self.clip_widgets[key]
            y = self.clip_row_indices[key] * self.clip_row_height
            for canvas in self.clip_canvases.values():
                canvas.create_rectangle(0, y, 20000,
                                        y + self.clip_row_height, fill="#1b2027" if self.clip_row_indices[key] % 2 == 0 else "#191e25", outline="")
                canvas.create_line(0, y+self.clip_row_height-1, 2000, y+self.clip_row_height-1, fill="#252d36")
            shared = self.clip_canvases["shared"]
            cx = self.clip_canvas_widths["shared"] / 2
            cy = y + self.clip_row_height / 2
            is_shared = self.clip_origins.get(key) == "Shared"
            shared.create_rectangle(cx-8, cy-8, cx+8, cy+8, fill="#52d4ba" if is_shared else "#252d36", outline="#52d4ba" if is_shared else "#526170", tags=("shared_" + key,))
            if is_shared:
                shared.create_line(cx-4, cy, cx-1, cy+3, cx+5, cy-4, fill="#10211e", width=2, tags=("shared_" + key,))
            volume = self.clip_canvases["volume"]
            cell["volume"] = (volume.create_line(0, 0, 0, 0, fill="#39434f", width=4, capstyle="round"),
                              volume.create_oval(0, 0, 0, 0, fill="#52d4ba", outline=""),
                              volume.create_text(0, 0, anchor="e", fill="#c3cdd8", font=("Segoe UI", 9)),
                              volume.create_line(0, 0, 0, 0, fill="#52d4ba", width=4, capstyle="round"))
            self.draw_clip_volume(key)
            label = self.clip_canvases["label"]
            current = self.clip_setting(key).get("label", "")
            color = self.config.get("clip_labels", {}).get(current, "#1b2027")
            cell["label"] = (label.create_rectangle(0, y, self.clip_canvas_widths["label"], y + self.clip_row_height, fill=color, outline=""),
                             label.create_text(10, y + self.clip_row_height / 2, text=current or "Select…",
                                               anchor="w", fill="#14231f" if current and color != "#1b2027" else "#c3cdd8", font=("Segoe UI", 9)))
            menu = cell["menu"]
            menu.delete(0, "end")
            for name in ("", *sorted(self.config.get("clip_labels", {}), key=str.casefold)):
                menu.add_command(label=name or "No label", command=lambda k=key, n=name: self.set_clip_label(k, n))

    def draw_clip_volume(self, key):
        canvas = self.clip_canvases["volume"]
        line, knob, text, progress = self.clip_widgets[key]["volume"]
        width = self.clip_canvas_widths["volume"]
        y = self.clip_row_indices[key] * self.clip_row_height + self.clip_row_height / 2
        left, right = 10, max(11, width - 52)
        value = max(-100, min(100, self.clip_setting(key).get("volume", 0)))
        x = left + (right - left) * (value + 100) / 200
        canvas.coords(line, left, y, right, y)
        canvas.coords(progress, left, y, x, y)
        canvas.tag_lower(progress, knob)
        canvas.coords(knob, x - 6, y - 6, x + 6, y + 6)
        canvas.coords(text, width - 4, y)
        canvas.itemconfigure(text, text=f"{value:+d}%" if value else "0%")

    def canvas_clip_key(self, event):
        # Canvas coordinates cover only the row body, never the table header.
        index = int((getattr(self, "clip_scroll_offset", 0) + event.y) // self.clip_row_height)
        return self.file_ids[index] if 0 <= index < len(self.file_ids) else None

    def canvas_volume_press(self, event):
        self.clip_slider_drag = self.canvas_clip_key(event)
        if self.clip_slider_drag:
            self.select_label_row(self.clip_slider_drag)
            self.canvas_volume_drag(event)
        return "break"

    def canvas_volume_drag(self, event):
        key = self.clip_slider_drag
        if key:
            right = max(11, self.clip_canvas_widths["volume"] - 52)
            value = max(-100, min(100, (event.x - 10) / (right - 10) * 200 - 100))
            self.set_clip_volume(key, value, None)
            self.draw_clip_volume(key)
        return "break"

    def canvas_volume_release(self, event):
        self.clip_slider_drag = None
        return "break"

    def canvas_label_menu(self, event):
        key = self.canvas_clip_key(event)
        if key:
            self.select_label_row(key)
            menu = self.clip_widgets[key]["menu"]
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        return "break"

    def canvas_clip_menu(self, event):
        key = self.canvas_clip_key(event)
        if key:
            return self.open_clip_menu(key, event)
        return "break"

    def resize_clip_graphics(self, widths):
        previous = self.clip_canvas_widths
        self.clip_canvas_widths = widths
        for key in self.file_ids:
            y = self.clip_row_indices[key] * self.clip_row_height
            if widths["volume"] != previous["volume"]:
                self.draw_clip_volume(key)
            if widths["label"] != previous["label"]:
                self.clip_canvases["label"].coords(self.clip_widgets[key]["label"][0],
                    0, y, widths["label"], y + self.clip_row_height)
            if widths["shared"] != previous["shared"]:
                self.clip_canvases["shared"].move("shared_" + key,
                    (widths["shared"] - previous["shared"]) / 2, 0)
        self.clip_position_signature = None

    def position_clip_widgets(self):
        if not self.file_ids:
            for canvas in getattr(self, "clip_canvases", {}).values():
                canvas.place_forget()
            return
        if not hasattr(self, "clip_canvases"):
            self.preload_clip_widgets()
        widths = {c: self.listbox.column(c, "width") for c in self.clip_canvases}
        if widths != self.clip_canvas_widths:
            self.resize_clip_graphics(widths)
        first_box = self.listbox.bbox(self.file_ids[0]) if self.listbox.yview()[0] == 0 else ()
        signature = (self.listbox.yview(), self.listbox.winfo_width(), self.listbox.winfo_height(), self.listbox.winfo_ismapped(),
                     tuple(self.listbox.cget("displaycolumns")), first_box[1] if first_box else None)
        if signature == getattr(self, "clip_position_signature", None):
            return
        self.clip_position_signature = signature
        # Treeview bbox can lag a scroll until Tk's next display pass. Use the
        # scroll fraction and fixed column geometry so both layers move together.
        # The yscroll callback also reconciles Tk's final clamped position.
        offset = round(self.listbox.yview()[0] * len(self.file_ids)) * self.clip_row_height
        self.clip_scroll_offset = offset
        # Header height changes with Windows DPI and Tk's first mapped layout.
        # Locate the actual row body instead of reusing an unmapped bbox.
        if first_box:
            self.clip_body_top = first_box[1]
        y = self.clip_body_top
        x = self.clip_body_left
        positions = {}
        for column in self.column_order if hasattr(self, "column_order") else ("name", "volume", "shared", "label"):
            width = self.listbox.column(column, "width")
            positions[column] = (x, width)
            x += width
        for column, canvas in self.clip_canvases.items():
            x, width = positions[column]
            height = max(1, self.listbox.winfo_height() - y - 2)
            total = len(self.file_ids) * self.clip_row_height + height
            canvas.configure(scrollregion=(0, 0, width, total))
            canvas.place(x=x, y=y, width=width, height=height)
            canvas.yview_moveto(offset / total)

    def select_label_row(self, key):
        self.clip_pressed_index = None
        self.listbox.selection_set(key)
        self.listbox.focus(key)

    def set_clip_label(self, key, name):
        self.clip_setting(key)["label"] = name
        self.save()
        self.render_clips()
        self.select_label_row(key)

    def add_label(self):
        name = simpledialog.askstring("Add Label", "Label name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        self.config.setdefault("clip_labels", {}).setdefault(name, LABEL_COLORS["Light Blue"])
        self.set_clip_label(self.context_clip_id, name)

    def change_label_color(self, color_name):
        name = self.clip_setting(self.context_clip_id).get("label")
        if name:
            self.config.setdefault("clip_labels", {})[name] = LABEL_COLORS[color_name]
            self.save()
            self.render_clips()

    def open_clip_menu(self, file_id, event):
        self.context_clip_id = file_id
        self.clip_pressed_index = None
        self.listbox.selection_set(file_id)
        can_share = self.clip_origins.get(file_id) == "Local" and not self.sync_running and file_id not in getattr(self, "editing_clips", set())
        for action in ("Share", "Share then del local"):
            self.clip_menu.entryconfigure(action, state="normal" if can_share else "disabled")
        has_label = bool(self.clip_setting(file_id).get("label"))
        self.clip_menu.entryconfigure("Change Label Color", state="normal" if has_label else "disabled")
        try:
            self.clip_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.clip_menu.grab_release()
        return "break"

    def save_paste_setting(self):
        self.config["paste_playlist"] = bool(self.paste_playlist.get())
        self.save()

    def paste_youtube_link(self, event=None):
        try:
            url = clipboard_youtube_link(self.root.clipboard_get())
        except tk.TclError:
            return
        if url is None:
            return
        guild_id = None
        self.status.set("Adding YouTube playlist…" if self.paste_playlist.get() else "Adding YouTube song…")
        self.send({"type": "music_enqueue", "guild_id": guild_id, "url": url,
                   "playlist": bool(self.paste_playlist.get())})
        return "break"

    def render_favorites(self):
        opened = {key for key in self.favorites_view.get_children() if self.favorites_view.item(key, "open")}
        self.favorites_view.delete(*self.favorites_view.get_children())
        self.favorite_items = {}
        for item in self.config.get("music_favorites", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            key = hashlib.sha256(item["url"].encode()).hexdigest()[:24]
            if key in self.favorite_items:
                continue
            self.favorite_items[key] = item
            title = ("Playlist: " if item.get("playlist") else "") + item.get("title", item["url"])
            self.favorites_view.insert("", "end", iid=key, values=(title,), open=key in opened)
            if item.get("playlist"):
                if "tracks" not in item:
                    self.favorites_view.insert(key, "end", values=("Expand to load songs…",))
                else:
                    for index, track in enumerate(item["tracks"]):
                        child = f"{key}:{index}"
                        self.favorite_items[child] = {"url": track["webpage_url"], "title": track["title"], "playlist": False}
                        self.favorites_view.insert(key, "end", iid=child, values=(track["title"],))

    def expand_favorite_playlist(self, event=None):
        key = self.favorites_view.focus()
        item = self.favorite_items.get(key)
        if not item or not item.get("playlist") or "tracks" in item:
            return
        pending = getattr(self, "loading_playlists", set())
        self.loading_playlists = pending
        if key in pending:
            return
        pending.add(key)
        self.status.set("Loading playlist songs…")
        asyncio.run_coroutine_threadsafe(self.agent.browse_playlist(key, item["url"]), self.loop)

    def save_music_favorite(self, item):
        favorites = self.config.setdefault("music_favorites", [])
        if not any(saved.get("url") == item["url"] for saved in favorites):
            favorites.append(item)
            self.save()
            self.render_favorites()
        self.status.set("Saved to favorites.")

    def favorite_song(self):
        track = next((track for track in (self.last_music or {}).get("playlist", [])
                      if track["id"] == self.queue_context_id), None)
        if not track or not track.get("url"):
            self.status.set("Song link unavailable. Refresh the queue and try again.")
            return
        self.save_music_favorite({"url": track["url"], "title": track["title"], "playlist": False})

    def song_context_menu(self, event):
        self.queue_pressed = None
        if self.queue_view.identify_region(event.x, event.y) != "cell":
            return "break"
        self.queue_context_id = self.queue_view.identify_row(event.y)
        if not self.queue_context_id:
            return "break"
        if self.queue_context_id not in self.queue_view.selection():
            self.queue_view.selection_set(self.queue_context_id)
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()
        return "break"

    def remove_queue_song(self):
        """Remove the complete selection in one bot-side transaction."""
        selected = list(self.queue_view.selection())
        if not selected and self.queue_context_id:
            selected = [self.queue_context_id]
        if selected:
            payload = {"type": "music_control", "guild_id": None, "action": "remove"}
            payload.update({"track_id": selected[0]} if len(selected) == 1 else {"track_ids": selected})
            self.send(payload)
            self.refresh_music()
        return "break"

    def play_favorite(self, event):
        if self.favorites_view.identify_region(event.x, event.y) != "cell":
            return "break"
        key = self.favorites_view.identify_row(event.y)
        item = self.favorite_items.get(key)
        if item:
            self.send({"type": "music_enqueue", "guild_id": None,
                       "url": item["url"], "playlist": bool(item.get("playlist"))})
        return "break"

    def favorite_context_menu(self, event):
        if self.favorites_view.identify_region(event.x, event.y) != "cell":
            return "break"
        self.favorite_context_id = self.favorites_view.identify_row(event.y)
        parent = self.favorites_view.parent(self.favorite_context_id) if self.favorite_context_id else ""
        if parent:
            self.favorite_context_id = parent
        if self.favorite_context_id:
            self.favorites_view.selection_set(self.favorite_context_id)
            try:
                self.favorite_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.favorite_menu.grab_release()
        return "break"

    def remove_favorite(self):
        item = self.favorite_items.get(self.favorite_context_id)
        if item:
            self.config["music_favorites"] = [saved for saved in self.config.get("music_favorites", [])
                                               if saved.get("url") != item["url"]]
            self.save()
            self.render_favorites()

    def save_playlist_link(self):
        text = simpledialog.askstring("Save playlist", "YouTube playlist URL:", parent=self.root)
        if not text:
            return
        url = clipboard_youtube_link(text)
        if not url or not re.search(r"[?&]list=[^&]+", url):
            messagebox.showerror("Invalid playlist", "Enter a YouTube link containing a playlist.", parent=self.root)
            return
        title = simpledialog.askstring("Playlist name", "Name for this playlist:", parent=self.root)
        if title and title.strip():
            self.save_music_favorite({"url": url, "title": title.strip(), "playlist": True})

    def song_mouse_down(self, event):
        self.queue_pressed = self.queue_view.identify_row(event.y) if self.queue_view.identify_region(event.x, event.y) == "cell" else None

    def song_selected(self, event):
        pressed = self.queue_pressed
        self.queue_pressed = None
        if event.widget is not self.queue_view or self.queue_view.identify_region(event.x, event.y) != "cell":
            return
        if getattr(event, "state", 0) & 5 or len(self.queue_view.selection()) > 1:
            return
        track_id = self.queue_view.identify_row(event.y)
        if not track_id or track_id != pressed or not self.last_music or "playlist" not in self.last_music:
            return
        guild_id = self.target_id()
        self.send({"type": "music_control", "guild_id": guild_id, "action": "select", "track_id": track_id})

    def clip_context_menu(self, event):
        self.clip_pressed_index = None
        file_id = self.listbox.identify_row(event.y)
        if self.listbox.identify_region(event.x, event.y) != "cell" or file_id not in self.agent.local_files:
            return
        return self.open_clip_menu(file_id, event)

    def editable_clip(self):
        file_id = self.context_clip_id
        if file_id in getattr(self, "editing_clips", set()):
            self.status.set("This clip is already being edited.")
            return None
        path = self.agent.local_files.get(file_id)
        if not path or not path.is_file():
            self.status.set("This clip is no longer available. Refreshing files.")
            self.refresh_files()
            return None
        if self.clip_origins.get(file_id) == "Shared" and self.sync_running:
            self.status.set("Wait for shared clip sync to finish before editing shared files.")
            return None
        return path

    def share_clip(self, remove_local=False):
        file_id = self.context_clip_id
        path = self.editable_clip()
        if path is None or self.clip_origins.get(file_id) != "Local":
            return
        if self.sync_running or getattr(self, "editing_clips", set()):
            self.status.set("Wait for the current clip operation to finish.")
            return
        if not hasattr(self, "editing_clips"):
            self.editing_clips = set()
        self.editing_clips.add(file_id)
        settings = dict(self.clip_setting(file_id))
        self.status.set("Copying clip to shared folder…")
        async def share_checked():
            await asyncio.to_thread(check_share_target, self.config, path)
            return await self.agent.share_clip_file(file_id, path, SHARED_CLIPS_DIR, remove_local)
        future = asyncio.run_coroutine_threadsafe(share_checked(), self.loop)
        def done(result):
            try:
                target = result.result()
                self.agent.events.put({"share_done": file_id, "shared_path": str(target),
                                       "share_settings": settings})
            except Exception as exc:
                self.agent.events.put({"share_done": file_id, "share_error": str(exc)})
        future.add_done_callback(done)

    def rename_clip(self):
        path = self.editable_clip()
        if path is None:
            return
        name = simpledialog.askstring("Rename clip", "New filename (audio extension is preserved):", initialvalue=path.stem, parent=self.root)
        if name is None:
            return
        self.start_clip_edit(path, name)

    def delete_clip(self):
        path = self.editable_clip()
        if path is None:
            return
        shared = self.clip_origins.get(self.context_clip_id) == "Shared"
        prompt = f"Delete {path.name} from this PC?"
        if shared:
            prompt += "\nThis also deletes the GitHub shared file for everyone." if self.config.get("shared_owner") else "\nThis deletes your shared copy only. Sync can download it again."
        if not messagebox.askyesno("Delete clip", prompt, parent=self.root):
            return
        self.start_clip_edit(path)

    def start_clip_edit(self, path, new_name=None):
        file_id = self.context_clip_id
        if not hasattr(self, "editing_clips"):
            self.editing_clips = set()
        self.editing_clips.add(file_id)
        self.status.set("Releasing clip audio before editing…")
        try:
            target = soundboard_rename_target(path, new_name) if new_name is not None else None
        except ValueError as exc:
            self.editing_clips.discard(file_id)
            messagebox.showerror("Could not edit clip", str(exc), parent=self.root)
            return
        async def edit():
            if self.clip_origins.get(file_id) == "Shared" and self.config.get("shared_owner"):
                await asyncio.to_thread(edit_shared_clip, self.config, path, target.name if target else None)
            return await self.agent.edit_clip_file(file_id, path, new_name)
        future = asyncio.run_coroutine_threadsafe(edit(), self.loop)
        def done(result):
            try:
                status = result.result()
                self.agent.events.put({"clip_edit_done": file_id, "status": status,
                                       "renamed_path": str(target) if target else None})
            except Exception as exc:
                self.agent.events.put({"clip_edit_done": file_id, "clip_edit_error": str(exc)})
        future.add_done_callback(done)

    def clip_column_at(self, x):
        display = self.listbox.identify_column(x)
        try:
            index = int(display[1:]) - 1
            return getattr(self, "column_order", list(CLIP_COLUMNS))[index] if index >= 0 else None
        except (ValueError, IndexError):
            return None

    def clip_mouse_down(self, event):
        region = self.listbox.identify_region(event.x, event.y)
        self.header_drag = None
        if region == "heading":
            self.header_drag = {"column": self.clip_column_at(event.x), "x": event.x, "moved": False}
            self.clip_pressed_index = None
            return "break"
        self.clip_pressed_index = self.listbox.identify_row(event.y) if region == "cell" else None

    def clip_mouse_move(self, event):
        drag = getattr(self, "header_drag", None)
        if drag:
            if abs(event.x - drag["x"]) > 8:
                drag["moved"] = True
                self.listbox.configure(cursor="fleur")
            return "break"
        self.root.after_idle(self.position_clip_widgets)

    def play_selected(self, event):
        drag = getattr(self, "header_drag", None)
        if drag:
            self.header_drag = None
            self.listbox.configure(cursor="")
            if drag["moved"]:
                target = self.clip_column_at(event.x)
                self.column_order = reordered_columns(self.column_order, drag["column"], target)
                self.listbox.configure(displaycolumns=self.column_order)
                self.config["clip_column_order"] = self.column_order
                self.save()
                self.root.after_idle(self.position_clip_widgets)
            elif drag["column"] in {"name", "shared", "label"}:
                self.sort_clips(drag["column"])
            return "break"
        pressed = self.clip_pressed_index
        self.clip_pressed_index = None
        if event.widget is not self.listbox or self.listbox.identify_region(event.x, event.y) != "cell":
            return
        file_id = self.listbox.identify_row(event.y)
        if file_id in getattr(self, "editing_clips", set()):
            return
        if not file_id or pressed != file_id or self.clip_column_at(event.x) != "name":
            return
        if hasattr(self, "direct_checkbox") and self.direct_checkbox.instate(["disabled"]):
            self.status.set("Wait for direct audio setup to finish.")
            return
        guild_id = None
        self.status.set("Starting clip " + self.agent.local_files[file_id].name)
        if self.config.get("direct_soundboard"):
            asyncio.run_coroutine_threadsafe(self.agent.play_direct_clip(file_id), self.loop)
            return
        self.send({"type": "local_play", "guild_id": guild_id, "file_id": file_id,
                   "title": self.agent.local_files[file_id].name})

    def stop(self):
        if self.config.get("direct_soundboard"):
            asyncio.run_coroutine_threadsafe(self.agent.stop_direct_clips(), self.loop)
            return
        guild_id = self.target_id()
        self.send({"type": "local_stop", "guild_id": guild_id})

    def run_agent(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.agent.run())
        except asyncio.CancelledError:
            pass

    def poll(self):
        while not self.agent.events.empty():
            event = self.agent.events.get_nowait()
            if "direct_ready" in event or "direct_failed" in event:
                enabled = event.get("direct_ready", False)
                self.direct_mode.set(enabled)
                self.config["direct_soundboard"] = enabled
                self.direct_checkbox.configure(state="normal")
                self.save()
                if event.get("direct_failed"):
                    messagebox.showerror("Direct audio unavailable", event["direct_failed"], parent=self.root)
                self.status.set("Direct soundboard enabled. Use the virtual cable as Discord's microphone input." if enabled else "Soundboard playback uses the bot.")
            if "normalization_cache" in event:
                self.config["clip_normalization"] = event["normalization_cache"]
                if getattr(self, "normalization_save_after", None) is None:
                    self.normalization_save_after = self.root.after(1000, self.save_normalization)
            if event.get("share_done"):
                self.editing_clips.discard(event["share_done"])
                if event.get("share_error"):
                    messagebox.showerror("Could not share clip", event["share_error"], parent=self.root)
                    self.refresh_files()
                else:
                    key = hashlib.sha256(str(Path(event["shared_path"]).resolve()).encode()).hexdigest()
                    self.config.setdefault("clip_settings", {})[key] = event["share_settings"]
                    self.save()
                    self.refresh_files()
                    self.sync_shared()
            if event.get("clip_edit_done"):
                self.editing_clips.discard(event["clip_edit_done"])
                if event.get("renamed_path"):
                    settings = self.config.setdefault("clip_settings", {})
                    old = settings.pop(event["clip_edit_done"], None)
                    if old is not None:
                        new_key = hashlib.sha256(str(Path(event["renamed_path"]).resolve()).encode()).hexdigest()
                        settings[new_key] = old
                        self.save()
                if event.get("clip_edit_error"):
                    messagebox.showerror("Could not edit clip", event["clip_edit_error"], parent=self.root)
                self.refresh_files()
            if event.get("relay_name"):
                self.root.title("TacoBot — " + event["relay_name"])
                self.identity.set(event["relay_name"] + " • Personal relay")
                self.save()
            if event.get("token_required"):
                token = simpledialog.askstring("New relay token needed", "Token expired or invalid. Use !token in Discord, then paste your new token:", show="*", parent=self.root)
                if token and token.strip():
                    self.config["relay_token"] = token.strip()
                    self.save()
                    self.loop.call_soon_threadsafe(self.agent.token_ready.set)
                else:
                    self.status.set("Use !token in Discord, then restart the relay to enter the new token.")
                    self.config["relay_token"] = ""
                    self.save()
            if event.get("update_checked"):
                self.update_check_running = False
                self.update_button.configure(state="normal")
                if event.get("update_stage") is not None:
                    self.install_button.configure(state="normal")
                    self.pending_update = (event["update_stage"], event["update_manual"])
                    self.update_idle_since = None
            if event.get("import_done") and event.get("folder") == self.folder.get():
                self.refresh_files()
            if event.get("voice_models_done"):
                enabled = set(self.config.get("tts_enabled_voices",[VOICE]))
                completed = event["voice_models_finished"]
                if event["voice_models_action"] == "download":
                    enabled.update(completed)
                else:
                    enabled.difference_update(completed)
                self.config["tts_enabled_voices"] = sorted(enabled)
                self.refresh_enabled_voices()
                if getattr(self,"voice_manager",None) and self.voice_manager.winfo_exists():
                    for button in self.voice_manager_buttons:
                        button.configure(state="normal")
                    self.voice_model_status.set("Voice model update finished." if "error" not in event else event["error"])
                    self.refresh_voice_models()
            if event.get("translation_done"):
                self.translate_button.configure(state="normal")
                if "translated_text" in event:
                    self.translated_text.configure(state="normal")
                    self.translated_text.delete("1.0", "end")
                    self.translated_text.insert("1.0", event["translated_text"])
                    self.translated_text.configure(state="disabled")
                    self.translated_language = event["translated_language"]
                    self.translated_play_button.configure(state="normal")
                    self.status.set("Translation ready.")
            if event.get("tts_done"):
                self.tts_play_button.configure(state="normal")
            if event.get("sync_done"):
                self.sync_running = False
                self.refresh_files()
            if "library" in event and event["generation"] == self.scan_generation:
                files = event["library"]
                self.agent.local_files = files
                self.clip_origins = event["origins"]
                self.render_clips()
                asyncio.run_coroutine_threadsafe(self.agent.preload_normalization(dict(files)), self.loop)
                self.status.set(f"Loaded {len(files)} valid audio files. Preparing clip audio…")
            if "favorite_playlist" in event:
                key = event["favorite_playlist"]
                getattr(self, "loading_playlists", set()).discard(key)
                if "tracks" in event:
                    item = self.favorite_items.get(key)
                    if item:
                        item["tracks"] = event["tracks"]
                        self.save()
                        self.render_favorites()
                        self.status.set(f"Loaded {len(event['tracks'])} playlist songs.")
            if "music" in event:
                music = event["music"]
                self.current_song.set(music.get("current") or "Your next song starts here")
                if music != self.last_music:
                    self.last_music = music
                    selected_ids = self.queue_view.selection()
                    self.queue_view.delete(*self.queue_view.get_children())
                    playlist = music.get("playlist")
                    if playlist is not None:
                        for song in playlist:
                            label = ("Paused: " if music["paused"] else "Playing: ") if song["current"] else ""
                            self.queue_view.insert("", "end", iid=song["id"], values=(label + song["title"],), tags=("current",) if song["current"] else ())
                    else:
                        if music["current"]:
                            self.queue_view.insert("", "end", values=(music["current"],), tags=("current",))
                        for number, title in enumerate(music["queue"], 1):
                            self.queue_view.insert("", "end", values=(f"{number}. {title}",))
                    self.queue_view.selection_set([key for key in selected_ids if self.queue_view.exists(key)])
                    self.music_status.set("Paused" if music["paused"] else ("Playing" if music["current"] else "No music playing"))
                    self.set_music_playing(bool(music["current"]) and not music["paused"])
                    self.previous_button.configure(state="normal" if music["has_previous"] else "disabled")
            self.status.set(event.get("error") or event.get("status") or self.status.get())
        if time.monotonic() >= self.next_music_refresh:
            self.next_music_refresh = time.monotonic() + 2
            self.refresh_music()
        if not self.poll_updates():
            self.root.after(100, self.poll)

    def save_normalization(self):
        self.normalization_save_after = None
        self.save()

    def close(self):
        self.agent.direct_audio.close()
        self.config["clip_normalization"] = self.agent.normalization_cache
        self.save()
        def cancel_all():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
        self.loop.call_soon_threadsafe(cancel_all)
        self.root.destroy()


def main():
    root = create_root()
    root.withdraw()
    try:
        if CONFIG_PATH.is_file():
            config = load_config()
        else:
            token = simpledialog.askstring("Setup", "Relay token:", show="*", parent=root)
            if not token:
                root.destroy()
                return
            config = {"server_url": RELAY_URL, "relay_token": token.strip(),
                      "relay_name": "Discord relay", "cookies_file": "", "mp3_folder": ""}
            save_config(config)
        if not config.get("relay_token"):
            token = simpledialog.askstring("Setup", "Use !token in Discord, then paste your relay token:", show="*", parent=root)
            if not token:
                root.destroy()
                return
            config["relay_token"] = token.strip()
        config["server_url"] = RELAY_URL
        save_config(config)
        verify_tools()
        threading.Thread(target=update_ytdlp, daemon=True).start()
        root.deiconify()
        RelayWindow(root, config)
        root.mainloop()
    except Exception as exc:
        from tkinter import messagebox
        messagebox.showerror("Relay could not start", clean_error(exc), parent=root)
        root.destroy()


if __name__ == "__main__":
    if "--update-launch-test" in sys.argv:
        stage = Path(sys.argv[sys.argv.index("--update-launch-test") + 1])
        launch_installer(stage, APP_DIR, restart_argument="update-restart-test")
    elif "update-restart-test" in sys.argv:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        (FILES_DIR / "update-restart-ok.txt").write_text(f"Restarted v1.{RELAY_BUILD}", encoding="utf-8")
    elif "--self-test" in sys.argv:
        try:
            verify_tools()
            from piper import PiperVoice
            assert PiperVoice
            if os.getenv("TACOBOT_TTS_TEST_DIR"):
                import wave
                speech_test = PiperSpeech(os.environ["TACOBOT_TTS_TEST_DIR"])
                with wave.open(str(speech_test.synthesize("Packaged TacoBot speech test."))) as audio:
                    assert audio.getnframes() > 0
            if os.getenv("TACOBOT_TRANSLATION_TEST_DIR"):
                translated = LocalTranslator(os.environ["TACOBOT_TRANSLATION_TEST_DIR"]).translate("Hello", "en", "fr")
                assert translated and translated != "Hello"
            import sounddevice
            assert sounddevice.get_portaudio_version()[0] > 0
            test_root = create_root()
            test_root.withdraw()
            from tkinterdnd2 import DND_FILES
            test_root.drop_target_register(DND_FILES)
            assert relay_ssl_context().get_ca_certs()
            test_root.destroy()
        except Exception as exc:
            FILES_DIR.mkdir(parents=True, exist_ok=True)
            (FILES_DIR / "self-test-error.txt").write_text(str(exc), encoding="utf-8")
            sys.exit(1)
    else:
        main()
