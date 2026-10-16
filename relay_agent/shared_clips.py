"""Additive clip sync. Credentials stay on the bot; only relay authentication is sent."""
import hashlib
import ssl
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import json
import certifi

MAX_CLIP_BYTES = 20 * 1024 * 1024


def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def check_share_target(config, path):
    server = urlsplit(config['server_url'])
    if server.scheme not in ('wss', 'https'):
        raise ValueError('Sharing requires a secure relay URL.')
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_CLIP_BYTES:
        raise ValueError('Shared clips must be between 1 byte and 20 MB.')
    req = Request('https://' + server.netloc + '/sharedclips',
                  headers={'Authorization': 'Bearer ' + config['relay_token']})
    with urlopen(req, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
        listing = json.loads(response.read(MAX_CLIP_BYTES))
    sha = blob_sha(path.read_bytes())
    if any(change['sha'] == sha for change in listing.get('changes', {}).get(path.name, [])):
        raise ValueError('This shared filename was retired by the owner. Rename the local clip before sharing.')
    remote = next((item for item in listing['files'] if item['name'] == path.name), None)
    if remote and remote['sha'] != sha:
        raise ValueError('A different GitHub clip already has this name. Rename the local clip before sharing.')


def edit_shared_clip(config, path, new_name=None):
    server = urlsplit(config['server_url'])
    if server.scheme not in ('wss', 'https'):
        raise ValueError('Shared editing requires a secure relay URL.')
    payload = {'sha': blob_sha(Path(path).read_bytes())}
    if new_name is not None:
        payload['new_name'] = new_name
    req = Request('https://' + server.netloc + '/sharedclips/' + quote(Path(path).name, safe=''),
                  data=json.dumps(payload).encode(), method='POST' if new_name is not None else 'DELETE',
                  headers={'Authorization': 'Bearer ' + config['relay_token'], 'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
            return json.loads(response.read())
    except Exception as exc:
        from urllib.error import HTTPError
        if isinstance(exc, HTTPError):
            raise ValueError(exc.read(4096).decode(errors='replace')) from None
        raise


def sync_clips(folder, config, extensions, validate, collection='sharedclips'):
    if collection not in {'sharedclips', 'ownerclips'}:
        raise ValueError('Invalid clip collection.')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    server = urlsplit(config['server_url'])
    if server.scheme not in ('wss', 'https'):
        raise ValueError('Shared sync requires a secure relay URL.')
    base = 'https://' + server.netloc + '/' + collection
    def request(name='', data=None):
        url = base + ('/' + quote(name, safe='') if name else '')
        req = Request(url, data=data, method='PUT' if data is not None else 'GET',
                      headers={'Authorization': 'Bearer ' + config['relay_token'], 'Content-Type': 'application/octet-stream'})
        with urlopen(req, timeout=90, context=ssl.create_default_context(cafile=certifi.where())) as response:
            content = response.read(MAX_CLIP_BYTES + 1)
            if len(content) > MAX_CLIP_BYTES:
                raise ValueError('Shared clip exceeds 20 MB.')
            return content
    listing = json.loads(request())
    # Owner edits retire the exact old content, not unrelated local files.
    # Reconcile before uploads so stale copies never resurrect deleted names.
    for name, changes in listing.get('changes', {}).items():
        if Path(name).name != name or any(c in name for c in '/\\:'):
            continue
        path = folder / name
        if not path.is_file():
            continue
        sha = blob_sha(path.read_bytes())
        if not any(change['sha'] == sha for change in changes):
            continue
        path.unlink()
    remote = {f['name']: f for f in listing['files']}
    uploaded = downloaded = 0
    conflicts = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if not 0 < path.stat().st_size <= MAX_CLIP_BYTES:
            conflicts.append(path.name + ' (size limit)')
            continue
        if path.name in remote:
            if blob_sha(path.read_bytes()) != remote[path.name]['sha']:
                if collection == 'ownerclips' and validate(path):
                    request(path.name, path.read_bytes())
                    uploaded += 1
                else:
                    conflicts.append(path.name)
            continue
        if validate(path):
            request(path.name, path.read_bytes())
            uploaded += 1
    # Re-list to include clips uploaded concurrently by another user.
    for item in json.loads(request())['files']:
        name = item['name']
        if Path(name).name != name or any(c in name for c in '/\\\\:') or Path(name).suffix.lower() not in extensions:
            continue
        path = folder / name
        if path.exists():
            continue
        content = request(name)
        if blob_sha(content) != item['sha']:
            raise ValueError('Shared clip checksum mismatch: ' + name)
        temporary = folder / ('.' + name + '.download')
        try:
            temporary.write_bytes(content)
            # Validate using the original audio extension.
            candidate = folder / ('.sync-' + name)
            temporary.replace(candidate)
            try:
                if not validate(candidate):
                    raise ValueError('Shared file is not playable audio: ' + name)
                with path.open('xb') as output:
                    output.write(content)
                downloaded += 1
            finally:
                candidate.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
    return uploaded, downloaded, conflicts
