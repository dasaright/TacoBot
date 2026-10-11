"""Public GitHub release updates for the portable Windows relay."""
import hashlib
import json
import ctypes
import sys
import time
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

import certifi

REPOSITORY = 'dasaright/TacoBot'
RELEASES_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30'
BUNDLE_NAME = 'TacoBot-Windows.zip'
MANIFEST_NAME = 'relay-update.json'
UPDATE_FILES = ('TacoBot.exe', 'README.md', 'Files/ffmpeg.exe', 'Files/yt-dlp.exe')
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
# Qt and audio runtimes expand beyond the compressed download budget.
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024


def open_url(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'TacoBot-Updater'})
    return urllib.request.urlopen(request, timeout=60,
        context=ssl.create_default_context(cafile=certifi.where()))


def read_json(url):
    with open_url(url) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Update metadata is too large.')
    return json.loads(data.decode("utf-8-sig"))


def trusted_asset_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com'
            or not parsed.path.startswith(f'/{REPOSITORY}/releases/download/')
            or parsed.username or parsed.password):
        raise ValueError('Update asset is not from the relay repository.')
    return url


def find_update(current_build):
    releases = read_json(RELEASES_URL)
    candidates = []
    for release in releases:
        tag = str(release.get('tag_name', ''))
        if release.get('draft') or release.get('prerelease') or not tag.startswith('relay-'):
            continue
        try:
            build = int(tag[6:])
        except ValueError:
            continue
        assets = {asset['name']: asset['browser_download_url'] for asset in release.get('assets', [])}
        if build > current_build and BUNDLE_NAME in assets and MANIFEST_NAME in assets:
            candidates.append((build, assets))
    if not candidates:
        return None
    build, assets = max(candidates, key=lambda item: item[0])
    manifest = read_json(trusted_asset_url(assets[MANIFEST_NAME]))
    digest = str(manifest.get('sha256', ''))
    if (manifest.get('schema') != 1 or manifest.get('build') != build
            or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
        raise ValueError('Invalid relay update manifest.')
    return {'build': build, 'sha256': digest, 'url': trusted_asset_url(assets[BUNDLE_NAME])}


def extract_verified_bundle(archive, destination, expected_digest):
    archive = Path(archive)
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise ValueError('Relay update checksum does not match.')
    with zipfile.ZipFile(archive) as bundle:
        files = [entry for entry in bundle.infolist() if not entry.is_dir()]
        if {entry.filename for entry in files} != set(UPDATE_FILES) or len(files) != len(UPDATE_FILES):
            raise ValueError('Unexpected files in relay update.')
        if any(entry.file_size == 0 for entry in files) or sum(entry.file_size for entry in files) > MAX_EXTRACTED_BYTES:
            raise ValueError('Invalid relay update size.')
        # Extract only the fixed allowlist; configuration and clips cannot be overwritten.
        for name in UPDATE_FILES:
            target = Path(destination) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)


def prepare_update(update):
    stage = Path(tempfile.mkdtemp(prefix='TacoBot-update-'))
    try:
        archive = stage / 'download.zip'
        downloaded = 0
        with open_url(update['url']) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise ValueError('Relay update download is too large.')
                output.write(chunk)
        extract_verified_bundle(archive, stage / 'payload', update['sha256'])
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


INSTALL_SCRIPT = r'''param(
    [string]$Stage,
    [string]$Target,
    [int]$ProcessToWait = 0,
    [int]$ParentProcessToWait = 0,
    [switch]$NoRestart,
    [string]$ExecutableName = "TacoBot.exe",
    [string]$RestartArgument = "",
    [ValidateRange(1,30)][int]$MaxAttempts = 30
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Join-Path $Target 'Files') | Out-Null
Start-Transcript -Path (Join-Path $Target 'Files/update-install.log') -Force | Out-Null
'Relay installer started.' | Set-Content -LiteralPath (Join-Path $Stage 'installer-ready')
if ($ProcessToWait -gt 0) { Wait-Process -Id $ProcessToWait -Timeout 60 -ErrorAction SilentlyContinue }
if ($ParentProcessToWait -gt 0) { Wait-Process -Id $ParentProcessToWait -Timeout 60 -ErrorAction SilentlyContinue }
# Restart must unpack a fresh one-file runtime after the old runtime is removed.
Get-ChildItem Env: | Where-Object { $_.Name -like '_PYI*' -or $_.Name -eq '_MEIPASS2' } | ForEach-Object { Remove-Item "Env:$($_.Name)" }
$env:PYINSTALLER_RESET_ENVIRONMENT = '1'
function Destination($file) {
    if ($file -eq 'TacoBot.exe') { return (Join-Path $Target $ExecutableName) }
    return (Join-Path $Target $file)
}
$files = @('TacoBot.exe', 'README.md', 'Files/ffmpeg.exe', 'Files/yt-dlp.exe')
$installed = $false
try {
$backup = Join-Path $Stage 'backup'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Target 'Files') | Out-Null
$existed = @{}
foreach ($file in $files) {
    $old = Destination $file
    $saved = Join-Path $backup $file
    $existed[$file] = Test-Path -LiteralPath $old -PathType Leaf
    if ($existed[$file]) {
        New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
        Copy-Item -LiteralPath $old -Destination $saved -Force
    }
}
$installed = $false
for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
    try {
        foreach ($file in $files) {
            Copy-Item -LiteralPath (Join-Path (Join-Path $Stage 'payload') $file) -Destination (Destination $file) -Force
        }
        $installed = $true
        break
    } catch {
        Write-Output $_
        foreach ($file in $files) {
            $old = Destination $file
            try {
                if ($existed[$file]) { Copy-Item -LiteralPath (Join-Path $backup $file) -Destination $old -Force }
                else { Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue }
            } catch { Write-Output "Rollback retry: $_" }
        }
        if ($attempt -lt ($MaxAttempts - 1)) { Start-Sleep -Seconds 1 }
    }
}
} catch { Write-Output $_; $installed = $false }
try {
if ($installed) {
    # Clean up obsolete dependency locations after the new bundle is installed.
    foreach ($name in @('ffmpeg.exe', 'yt-dlp.exe')) {
        Remove-Item -LiteralPath (Join-Path $Target $name) -Force -ErrorAction SilentlyContinue
    }
    'Relay update installed successfully.' | Set-Content -LiteralPath (Join-Path $Target 'Files/update-status.txt')
} else {
    'Relay update failed; previous version restored.' | Set-Content -LiteralPath (Join-Path $Target 'Files/update-status.txt')
}
} catch {}
if (-not $NoRestart) {
    try {
        $options = @{ FilePath = (Join-Path $Target $ExecutableName); WorkingDirectory = $Target; PassThru = $true }
        if ($RestartArgument) { $options.ArgumentList = $RestartArgument }
        $restarted = Start-Process @options
        Write-Output "Restarted relay process $($restarted.Id)"
    } catch {
        Write-Output $_
        'Update finished but restart failed. Open the relay manually. See update-install.log.' | Set-Content -LiteralPath (Join-Path $Target 'Files/update-status.txt')
    }
    if ($installed) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
}
Stop-Transcript | Out-Null
if (-not $installed) { exit 1 }
'''


def write_install_script(stage):
    script = Path(stage) / 'install.ps1'
    script.write_text(INSTALL_SCRIPT, encoding='utf-8-sig')
    return script


def installer_environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('_PYI') and key != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    bundle = str(getattr(sys, '_MEIPASS', ''))
    if bundle:
        env['PATH'] = os.pathsep.join(part for part in env.get('PATH', '').split(os.pathsep)
                                    if not part.startswith(bundle))
    return env


def launch_installer(stage, target, restart_argument=''):
    stage, target = Path(stage), Path(target)
    script = write_install_script(stage)
    ready = stage / 'installer-ready'
    ready.unlink(missing_ok=True)
    executable = Path(sys.executable).name if getattr(sys, 'frozen', False) else 'TacoBot.exe'
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    log_path = target / 'Files/update-launch.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        with log_path.open('wb') as log:
            process = subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(script), '-Stage', str(stage), '-Target', str(target),
                '-ExecutableName', executable, '-RestartArgument', restart_argument,
                '-ProcessToWait', str(os.getpid()),
                '-ParentProcessToWait', str(os.getppid() if frozen else 0)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=installer_environment(), cwd=str(target), close_fds=True,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
    deadline = time.monotonic() + 15
    while not ready.is_file():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('Installer did not start. Relay remains open; see Files/update-launch.log.')
        time.sleep(0.05)
    return process
