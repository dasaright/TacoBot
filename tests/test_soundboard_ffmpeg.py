import asyncio
import json
import math
import os
import struct
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

from websockets.asyncio.server import serve

from relay_agent.soundboard_transport import (
    FRAME_HEADER,
    FRAME_MAGIC,
    PCM_FRAME_BYTES,
    PROTOCOL_VERSION,
    SoundboardTransport,
)


@unittest.skipUnless(
    os.environ.get("TEST_FFMPEG_PATH") and Path(os.environ["TEST_FFMPEG_PATH"]).is_file(),
    "The build workflow supplies TEST_FFMPEG_PATH after downloading FFmpeg.",
)
class RealFFmpegSoundboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_ffmpeg_pcm_stream_uses_protocol_v2_and_drains(self):
        ffmpeg_path = Path(os.environ["TEST_FFMPEG_PATH"])
        session_id = uuid.uuid4().hex
        packets = []
        states = []

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "soundboard-fixture.wav"
            sample_rate = 48_000
            frame_count = sample_rate  # one second, enough to exercise flow control
            with wave.open(str(source_path), "wb") as fixture:
                fixture.setnchannels(2)
                fixture.setsampwidth(2)
                fixture.setframerate(sample_rate)
                pcm = bytearray()
                for index in range(frame_count):
                    value = int(7000 * math.sin(2 * math.pi * 440 * index / sample_rate))
                    pcm.extend(struct.pack("<hh", value, value))
                fixture.writeframes(pcm)

            async def server_handler(websocket):
                hello = json.loads(await websocket.recv())
                self.assertEqual(hello["type"], "hello")
                self.assertEqual(hello["protocol"], PROTOCOL_VERSION)
                await websocket.send(json.dumps({
                    "type": "hello_ok",
                    "protocol": PROTOCOL_VERSION,
                    "sample_rate": 48_000,
                    "channels": 2,
                    "frame_ms": 20,
                    "frame_bytes": PCM_FRAME_BYTES,
                }))
                start = json.loads(await websocket.recv())
                self.assertEqual(start["type"], "start")
                self.assertEqual(start["session_id"], session_id)
                await websocket.send(json.dumps({
                    "type": "accepted",
                    "protocol": PROTOCOL_VERSION,
                    "session_id": session_id,
                    "guild_id": "test",
                    "window": 12,
                    "frame_bytes": PCM_FRAME_BYTES,
                }))

                async for raw in websocket:
                    if isinstance(raw, bytes):
                        packets.append(raw)
                        if len(packets) == 3:
                            await websocket.send(json.dumps({
                                "type": "server_buffered",
                                "session_id": session_id,
                                "frames": len(packets),
                            }))
                        if len(packets) == 1 and any(raw[FRAME_HEADER.size:]):
                            await websocket.send(json.dumps({
                                "type": "playing",
                                "session_id": session_id,
                                "first_non_silent_frame": 1,
                            }))
                        if len(packets) % 4 == 0:
                            await websocket.send(json.dumps({
                                "type": "credit",
                                "session_id": session_id,
                                "frames": 4,
                            }))
                        continue

                    control = json.loads(raw)
                    if control.get("type") == "end":
                        await websocket.send(json.dumps({
                            "type": "completed",
                            "session_id": session_id,
                            "frames_received": len(packets),
                            "frames_consumed": len(packets),
                            "underruns": 0,
                            "played": any(any(packet[FRAME_HEADER.size:]) for packet in packets),
                        }))
                        break

            async with serve(server_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                transport = SoundboardTransport(
                    f"ws://127.0.0.1:{port}/relay",
                    "test-token",
                    "Windows FFmpeg test",
                    None,
                )
                result = await asyncio.wait_for(
                    transport.play_file(
                        session_id=session_id,
                        guild_id="test",
                        file_id="ffmpeg-fixture",
                        title="FFmpeg transport fixture",
                        source_path=source_path,
                        ffmpeg_path=ffmpeg_path,
                        audio_filter="volume=0dB",
                        transform_frame=lambda frame: frame,
                        on_state=states.append,
                    ),
                    timeout=20,
                )

        self.assertEqual(result["type"], "completed")
        self.assertTrue(result["played"])
        self.assertGreaterEqual(len(packets), 20)
        for sequence, packet in enumerate(packets):
            self.assertEqual(len(packet), FRAME_HEADER.size + PCM_FRAME_BYTES)
            magic, version, actual_session, actual_sequence, payload_length = FRAME_HEADER.unpack_from(packet)
            self.assertEqual(magic, FRAME_MAGIC)
            self.assertEqual(version, PROTOCOL_VERSION)
            self.assertEqual(actual_session.hex(), session_id)
            self.assertEqual(actual_sequence, sequence)
            self.assertEqual(payload_length, PCM_FRAME_BYTES)
            self.assertTrue(any(packet[FRAME_HEADER.size:]))

        names = [event["soundboard_state"] for event in states]
        self.assertIn("server-buffered", names)
        self.assertIn("playing", names)
        self.assertEqual(names[-1], "completed")


if __name__ == "__main__":
    unittest.main()
