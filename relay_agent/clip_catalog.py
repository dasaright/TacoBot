"""Portable clip metadata. Never stores relay tokens or machine-specific paths."""
import json
import threading
from pathlib import Path

from .main import atomic_config_write

CATALOG_NAME = 'tacobot-clips.json'
LOCK = threading.RLock()


def read_catalog(folder):
    path = Path(folder) / CATALOG_NAME
    for candidate in (path, path.with_suffix('.json.bak')):
        if not candidate.exists():
            continue
        try:
            value = json.loads(candidate.read_text(encoding='utf-8-sig'))
            if (not isinstance(value, dict) or value.get('schema') != 1
                    or not isinstance(value.get('clips'), dict)
                    or not isinstance(value.get('labels'), dict)):
                raise ValueError('Unsupported clip metadata format.')
            if candidate != path:
                atomic_config_write(path, json.dumps(value, indent=2, ensure_ascii=False))
            return value
        except (ValueError, UnicodeError):
            continue
    if path.exists() or path.with_suffix('.json.bak').exists():
        raise ValueError(f'Could not read {path}. Original metadata was preserved.')
    return {'schema': 1, 'clips': {}, 'labels': {}}


def write_catalog(folder, value):
    path = Path(folder) / CATALOG_NAME
    contents = json.dumps(value, indent=2, ensure_ascii=False)
    if path.exists():
        previous = path.read_text(encoding='utf-8-sig')
        if previous == contents:
            return
        # read_catalog already validates/recoveries before every mutation.
        atomic_config_write(path.with_suffix('.json.bak'), previous)
    atomic_config_write(path, contents)


def clean_settings(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get('label'), str):
        result['label'] = value['label']
    volume = value.get('volume')
    if isinstance(volume, (int, float)) and not isinstance(volume, bool):
        result['volume'] = max(-100, min(100, int(volume)))
    return result


def load_folder(folder, files, config):
    """Create on first selection, migrate config, then prefer portable metadata."""
    with LOCK:
        value = read_catalog(folder)
        settings = config.get('clip_settings', {})
        colors = {**config.get('clip_labels', {}), **value['labels']}
        loaded = {}
        for key, path in files.items():
            path = Path(path)
            if path.parent.resolve() != Path(folder).resolve():
                continue
            entry = value['clips'].get(path.name)
            if entry is None:
                entry = clean_settings(settings.get(key, {}))
                value['clips'][path.name] = entry
            loaded[key] = clean_settings(entry)
        # Store only color definitions, never global config/private credentials.
        value['labels'] = colors
        write_catalog(folder, value)
        return loaded, colors


def save_folders(files, config):
    grouped = {}
    for key, path in files.items():
        path = Path(path)
        grouped.setdefault(path.parent.resolve(), {})[key] = path
    with LOCK:
        for folder, clips in grouped.items():
            if not folder.is_dir():
                continue
            value = read_catalog(folder)
            value['labels'].update(config.get('clip_labels', {}))
            for key, path in clips.items():
                if path.is_file():
                    value['clips'][path.name] = clean_settings(config.get('clip_settings', {}).get(key, {}))
            write_catalog(folder, value)


def rename_entry(old_path, new_path):
    """Retain metadata when the relay renames an audio file."""
    old_path, new_path = Path(old_path), Path(new_path)
    with LOCK:
        value = read_catalog(old_path.parent)
        entry = value['clips'].pop(old_path.name, None)
        if entry is not None:
            value['clips'][new_path.name] = entry
            write_catalog(old_path.parent, value)
