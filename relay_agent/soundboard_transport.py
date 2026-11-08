"""Dedicated protocol-v2 transport for local soundboard clips.

This module owns only the clip WebSocket and FFmpeg-to-PCM stream. It does
not know about Tk widgets or the music relay connection.
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect


PROTOCOL_VERSION = 2
FRAME_MAGIC = b"SBV2"
PCM_FRAME_BYTES = 3840
FRAME_HEADER = struct.Struct("!4sB16sIH")
FRAME_SECONDS = 0.020
MAX_WEBSOCKET_MESSAGE = 64 * 1024
SESSION_DRAIN_TIMEOUT = 45


def soundboard_endpoint(relay_url: str) -> str:
    """Map the existing /relay URL to the dedicated /soundboard endpoint."""
    parsed = urlsplit(relay_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/relay"):
        path = path[:-len("/relay")] + "/soundboard"
    else:
        path = path + "/soundboard"
    return urlunsplit(parsed._replace(path=path))


async def drain_stderr(stream) -> str:
    chunks = []
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > 32_000:
            chunks = chunks[-4:]
            total = sum(map(len, chunks))
    return b"".join(chunks).decode("utf-8", errors="replace")[-2000:]


class SoundboardTransport:
    def __init__(self, relay_url, token, relay_name, ssl_context):
        self.url = soundboard_endpoint(relay_url)
        self.token = token
        self.relay_name = relay_name
        self.ssl_context = ssl_context

    @staticmethod
    def _frame(session_id: str, sequence: int, pcm: bytes) -> bytes:
        if len(pcm) != PCM_FRAME_BYTES:
            raise ValueError("FFmpeg returned an incomplete PCM frame.")
        return FRAME_HEADER.pack(
            FRAME_MAGIC,
            PROTOCOL_VERSION,
            bytes.fromhex(session_id),
            sequence,
            len(pcm),
        ) + pcm

    async def play_file(
        self,
        *,
        session_id: str,
        guild_id: str | None,
        file_id: str,
        title: str,
        source_path: Path,
        ffmpeg_path: Path,
        audio_filter: str,
        transform_frame,
        on_state,
    ):
        process = None
        stderr_task = None
        receiver_task = None
        messages: asyncio.Queue = asyncio.Queue()
        websocket = None
        started = False
        terminal = False

        async def receive_messages():
            try:
                async for raw in websocket:
                    if isinstance(raw, str):
                        try:
                            payload = json.loads(raw)
                        except (TypeError, ValueError):
                            await messages.put({"type": "error", "error": "Invalid server response."})
                            return
                        await messages.put(payload)
                await messages.put({"type": "connection_closed"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await messages.put({"type": "error", "error": str(exc) or "Soundboard connection closed."})

        async def next_message(timeout=SESSION_DRAIN_TIMEOUT):
            try:
                return await asyncio.wait_for(messages.get(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError("The soundboard server stopped responding.") from exc

        def notify(state, details=None):
            event = {
                "soundboard_state": state,
                "session_id": session_id,
                "file_id": file_id,
                "title": title,
            }
            if details:
                event.update(details)
            on_state(event)

        try:
            async with connect(
                self.url,
                ssl=self.ssl_context,
                ping_interval=20,
                ping_timeout=30,
                max_size=MAX_WEBSOCKET_MESSAGE,
                max_queue=16,
            ) as websocket_context:
                websocket = websocket_context
                await websocket.send(json.dumps({
                    "type": "hello",
                    "protocol": PROTOCOL_VERSION,
                    "token": self.token,
                    "name": self.relay_name,
                }))
                hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
                if hello.get("type") != "hello_ok" or hello.get("protocol") != PROTOCOL_VERSION:
                    raise ConnectionError("The server rejected soundboard protocol v2.")

                await websocket.send(json.dumps({
                    "type": "start",
                    "session_id": session_id,
                    "guild_id": guild_id,
                    "file_id": file_id,
                    "title": title[:120],
                }))
                accepted = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
                if accepted.get("type") == "error":
                    raise RuntimeError(str(accepted.get("error") or "The server rejected this clip."))
                if accepted.get("type") != "accepted" or accepted.get("session_id") != session_id:
                    raise ConnectionError("The soundboard server did not accept this session.")
                credits = accepted.get("window")
                if not isinstance(credits, int) or not 1 <= credits <= 64:
                    raise ConnectionError("The soundboard server returned an invalid send window.")
                started = True
                notify("accepted", {"guild_id": accepted.get("guild_id")})
                receiver_task = asyncio.create_task(receive_messages())

                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = await asyncio.create_subprocess_exec(
                    str(ffmpeg_path),
                    "-hide_banner",
                    "-loglevel", "error",
                    "-threads", "1",
                    "-i", str(source_path),
                    "-vn",
                    "-af", audio_filter,
                    "-f", "s16le",
                    "-ar", "48000",
                    "-ac", "2",
                    "pipe:1",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
                stderr_task = asyncio.create_task(drain_stderr(process.stderr))
                notify("decoding")

                sequence = 0
                next_frame_at = asyncio.get_running_loop().time()
                first_frame = True
                while True:
                    try:
                        pcm = await process.stdout.readexactly(PCM_FRAME_BYTES)
                    except asyncio.IncompleteReadError as exc:
                        if not exc.partial:
                            break
                        pcm = exc.partial + bytes(PCM_FRAME_BYTES - len(exc.partial))

                    while credits <= 0:
                        message = await next_message()
                        message_type = message.get("type")
                        if message_type == "credit":
                            credits += int(message.get("frames") or 0)
                        elif message_type == "server_buffered":
                            notify("server-buffered", message)
                        elif message_type == "playing":
                            notify("playing", message)
                        elif message_type == "cancelled":
                            raise asyncio.CancelledError
                        elif message_type in {"error", "connection_closed"}:
                            raise RuntimeError(str(message.get("error") or "The soundboard server disconnected."))

                    now = asyncio.get_running_loop().time()
                    await asyncio.sleep(max(0, next_frame_at - now))
                    pcm = transform_frame(pcm)
                    if len(pcm) != PCM_FRAME_BYTES:
                        raise ValueError("The PCM volume transform changed the frame size.")
                    await websocket.send(self._frame(session_id, sequence, pcm))
                    sequence += 1
                    credits -= 1
                    if first_frame:
                        notify("streaming")
                        first_frame = False
                    next_frame_at = max(next_frame_at + FRAME_SECONDS, asyncio.get_running_loop().time())

                return_code = await process.wait()
                stderr_text = await stderr_task
                stderr_task = None
                if return_code:
                    raise RuntimeError(stderr_text or "FFmpeg could not decode this clip.")

                await websocket.send(json.dumps({"type": "end", "session_id": session_id}))
                notify("server-buffered", {"frames_sent": sequence})
                while True:
                    message = await next_message()
                    message_type = message.get("type")
                    if message_type == "credit":
                        credits += int(message.get("frames") or 0)
                    elif message_type == "server_buffered":
                        notify("server-buffered", message)
                    elif message_type == "playing":
                        notify("playing", message)
                    elif message_type == "completed":
                        terminal = True
                        notify("completed", message)
                        return message
                    elif message_type == "cancelled":
                        terminal = True
                        notify("cancelled")
                        return message
                    elif message_type in {"error", "connection_closed"}:
                        raise RuntimeError(str(message.get("error") or "The soundboard server disconnected."))

        except asyncio.CancelledError:
            if websocket is not None and started:
                try:
                    await websocket.send(json.dumps({"type": "cancel", "session_id": session_id}))
                except Exception:
                    pass
            notify("cancelled")
            raise
        except Exception as exc:
            notify("failed", {"error": str(exc)[:300]})
            raise
        finally:
            if process is not None:
                if process.returncode is None:
                    process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
            if stderr_task is not None:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            if receiver_task is not None:
                receiver_task.cancel()
                await asyncio.gather(receiver_task, return_exceptions=True)
            if websocket is not None and started and not terminal and not websocket.closed:
                try:
                    await websocket.send(json.dumps({"type": "cancel", "session_id": session_id}))
                except Exception:
                    pass
