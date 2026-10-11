"""Derive a fixed YouTube image URL; never fetch arbitrary track URLs."""
import re
from urllib.parse import parse_qs, urlsplit


def youtube_thumbnail(url):
    try:
        parts = urlsplit(str(url))
        if parts.scheme != 'https' or parts.username or parts.password:
            return None
        host = (parts.hostname or '').lower()
        if host == 'youtu.be':
            video = parts.path.strip('/').split('/')[0]
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            pieces = parts.path.strip('/').split('/')
            video = pieces[1] if len(pieces) == 2 and pieces[0] in {'shorts', 'embed', 'live'} else parse_qs(parts.query).get('v', [''])[0]
        else:
            return None
        return f'https://i.ytimg.com/vi/{video}/mqdefault.jpg' if re.fullmatch(r'[A-Za-z0-9_-]{11}', video) else None
    except ValueError:
        return None
