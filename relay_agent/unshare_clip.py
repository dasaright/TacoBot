"""Keep a complete local copy before asking the authenticated server to unshare."""
from pathlib import Path
import os
import shutil


def copy_local(source, folder):
    source=Path(source); folder=Path(folder)
    if not folder.is_dir(): raise ValueError('Choose an existing local clips folder.')
    if source.parent.resolve()==folder.resolve(): raise ValueError('Choose a local folder separate from sharedclips.')
    number=1
    while True:
        target=folder/(source.name if number==1 else f'{source.stem} ({number}){source.suffix}')
        try: output=target.open('xb'); break
        except FileExistsError: number+=1
    try:
        with output,source.open('rb') as incoming:
            shutil.copyfileobj(incoming,output); output.flush(); os.fsync(output.fileno())
        return target
    except Exception:
        target.unlink(missing_ok=True); raise
