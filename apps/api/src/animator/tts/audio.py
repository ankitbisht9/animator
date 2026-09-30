"""Audio helpers without dependencies: the duration of MP3 and WAV data, and silent WAV."""

from __future__ import annotations

import io
import wave

# Layer III bitrates (kbps) for bitrate indexes 1..14: MPEG-1, and MPEG-2 / 2.5.
_KBPS_V1 = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_KBPS_V2 = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
# Sample rates by the header's version bits (3: MPEG-1, 2: MPEG-2, 0: MPEG-2.5).
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_duration(data: bytes) -> float:
    """Seconds of audio in MPEG Layer III data, counted frame by frame (CBR or VBR). An ID3v2 tag
    and a Xing/Info/VBRI header frame are skipped."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        i = 10 + size + (10 if data[5] & 0x10 else 0)
    samples = 0
    rate = 0
    first = True
    n = len(data)
    while i + 4 <= n:
        b1, b2 = data[i + 1], data[i + 2]
        if data[i] != 0xFF or (b1 & 0xE0) != 0xE0:
            i += 1
            continue
        version = (b1 >> 3) & 3
        layer = (b1 >> 1) & 3
        bitrate = (b2 >> 4) & 0xF
        rate_index = (b2 >> 2) & 3
        if version == 1 or layer != 1 or bitrate in (0, 15) or rate_index == 3:
            i += 1
            continue
        mpeg1 = version == 3
        kbps = (_KBPS_V1 if mpeg1 else _KBPS_V2)[bitrate - 1]
        sr = _RATES[version][rate_index]
        length = (144 if mpeg1 else 72) * kbps * 1000 // sr + ((b2 >> 1) & 1)
        if first:
            first = False
            head = data[i : i + min(length, 200)]
            if b"Xing" in head or b"Info" in head or b"VBRI" in head:
                i += length
                continue
        samples += 1152 if mpeg1 else 576
        rate = sr
        i += length
    return samples / rate if rate else 0.0


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return float(w.getnframes()) / float(w.getframerate())


def duration_of(data: bytes, fmt: str) -> float:
    return wav_duration(data) if fmt == "wav" else mp3_duration(data)


def silent_wav(seconds: float, rate: int = 24000) -> bytes:
    """Mono 16-bit silence (the fake provider's audio)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * round(seconds * rate))
    return buf.getvalue()
