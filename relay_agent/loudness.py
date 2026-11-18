"""Soundboard loudness normalisation shared by bot playback and Direct Audio.

Clips are brought to an average of -16 dB, and a limiter keeps peaks under
-1 dBFS. Up to 6 dB of limiting is allowed so punchy clips are not left much
quieter than compressed ones.
"""

NORMALIZATION_TARGET_DB = -16.0
NORMALIZATION_MAX_LIMITING_DB = 6.0
NORMALIZATION_MAX_GAIN_DB = 24.0
PEAK_CEILING_DB = -1.0
# YouTube audio streams live, so it cannot be measured up front like a clip.
# FFmpeg's single-pass loudnorm at -14 LUFS lands typical songs near the
# clips' -16 dB average (measured: a -8 LUFS pop master -> -15.2 dB mean,
# a -13 LUFS 80s track -> -17.4 dB), with the same -1 dB peak ceiling.
STREAM_LOUDNESS_TARGET_LUFS = -14.0


def normalization_filter(gain_db):
    """FFmpeg filter that applies the clip gain and keeps peaks below the ceiling."""
    ceiling = round(10 ** (PEAK_CEILING_DB / 20), 4)
    return f"volume={gain_db}dB,alimiter=limit={ceiling}:attack=5:release=50:level=false"


def stream_normalization_filter():
    """Single-pass FFmpeg filter that levels streamed (YouTube) audio."""
    return f"loudnorm=I={STREAM_LOUDNESS_TARGET_LUFS:g}:TP={PEAK_CEILING_DB:g}:LRA=11"
