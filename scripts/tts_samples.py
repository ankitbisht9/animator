"""Listen to the narration voices before choosing one (docs/TTS.md).

Usage:
    uv run python scripts/tts_samples.py                  # the provider set in .env (kokoro, edge, ...)
    uv run python scripts/tts_samples.py --provider edge
    uv run python scripts/tts_samples.py --all            # every voice (slow on a CPU Kokoro)

Speaks one English and one Hinglish sentence in each voice, through the same code lessons use, and
writes var/voice-samples/<provider>/index.html: open it in a browser and click play. The page
shows the line to put in .env for each voice. Nothing in the project changes.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from animator.config import Settings  # noqa: E402
from animator.tts import EdgeTTS, KokoroTTS, TTSProvider, build_provider  # noqa: E402

ENGLISH = "Imagine pushing a heavy box across the floor. Why does it stop, the moment you let go?"
HINGLISH = "Socho, agar hum ball ko upar phenkein, toh gravity use wapas neeche kheenchti hai."

KOKORO_LANG = {"a": "American English", "b": "British English", "h": "Hindi (reads Hinglish)"}
KOKORO_PICKS = [  # the better-graded English voices and every Hindi one
    "af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah", "af_nova", "af_alloy", "af_sky",
    "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam", "am_onyx",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_fable", "bm_daniel", "bm_lewis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
]  # fmt: skip
EDGE_PICKS = [
    "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural", "en-US-EmmaMultilingualNeural",
    "en-US-BrianMultilingualNeural", "en-US-AriaNeural", "en-US-GuyNeural", "en-GB-SoniaNeural",
    "en-GB-RyanNeural", "en-IN-NeerjaExpressiveNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural",
    "hi-IN-SwaraNeural", "hi-IN-MadhurNeural",
]  # fmt: skip


def kokoro_voices(provider: KokoroTTS, everything: bool) -> list[dict[str, str]]:
    with urllib.request.urlopen(f"{provider.base_url}/v1/audio/voices", timeout=15) as r:
        listed = json.loads(r.read())["voices"]
    out = []
    for v in listed:
        name = v["name"] if isinstance(v, dict) else str(v)
        if name[:1] not in KOKORO_LANG or "_v0" in name or (not everything and name not in KOKORO_PICKS):
            continue
        grade = v.get("overall_grade") if isinstance(v, dict) else None
        out.append(
            {
                "voice": name,
                "language": KOKORO_LANG[name[0]],
                "gender": "female" if name[1] == "f" else "male",
                "grade": str(grade or ""),
            }
        )
    order = {n: i for i, n in enumerate(KOKORO_PICKS)}
    return sorted(out, key=lambda v: (order.get(v["voice"], 999), v["voice"]))


async def edge_voices(everything: bool) -> list[dict[str, str]]:
    import edge_tts

    out = []
    for v in await edge_tts.list_voices():
        name = v["ShortName"]
        wanted = name.startswith(("en-US", "en-GB", "en-IN", "hi-IN")) if everything else name in EDGE_PICKS
        if wanted:
            out.append({"voice": name, "language": v["Locale"], "gender": v["Gender"].lower(), "grade": ""})
    order = {n: i for i, n in enumerate(EDGE_PICKS)}
    return sorted(out, key=lambda v: (order.get(v["voice"], 999), v["voice"]))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", help="edge, kokoro, azure, google, elevenlabs, openai (default: .env)")
    ap.add_argument("--all", action="store_true", help="every voice, not only the recommended ones")
    args = ap.parse_args()
    settings = Settings()
    if args.provider:
        chosen = {"animator_tts_provider": args.provider, "animator_tts_voice": ""}
        settings = settings.model_copy(update=chosen)
    provider: TTSProvider = build_provider(settings)
    if isinstance(provider, KokoroTTS):
        voices = kokoro_voices(provider, args.all)
    elif isinstance(provider, EdgeTTS):
        voices = await edge_voices(args.all)
    else:  # key-based providers: the configured voices only
        voices = [{"voice": provider.voice, "language": "", "gender": "", "grade": ""}]
        if provider.hinglish_voice != provider.voice:
            hinglish = {"voice": provider.hinglish_voice, "language": "Hinglish", "gender": "", "grade": ""}
            voices.append(hinglish)
    out = ROOT / "var" / "voice-samples" / provider.name
    out.mkdir(parents=True, exist_ok=True)
    print(f"{provider.name}: {len(voices)} voices -> {out}")
    rows = []
    for v in voices:
        files = {}
        for label, text in (("english", ENGLISH), ("hinglish", HINGLISH)):
            path = out / f"{v['voice']}-{label}.mp3"
            if not path.exists():
                try:
                    speech = await provider.synthesize(text, v["voice"])
                except Exception as e:
                    print(f"  {v['voice']} {label}: failed ({e})")
                    continue
                path.write_bytes(speech.audio)
            files[label] = path.name
        print(f"  {v['voice']}")
        rows.append((v, files))
    await provider.close()
    (out / "index.html").write_text(page(provider.name, rows), encoding="utf-8")
    print(f"open {out / 'index.html'}")


def page(provider: str, rows: list[tuple[dict[str, str], dict[str, str]]]) -> str:
    def audio(name: str | None) -> str:
        return f'<audio controls preload="none" src="{html.escape(name)}"></audio>' if name else "-"

    body = "\n".join(
        "<tr>"
        f"<td><b>{html.escape(v['voice'])}</b></td><td>{html.escape(v['language'])}</td>"
        f"<td>{html.escape(v['gender'])}</td><td>{html.escape(v['grade'])}</td>"
        f"<td>{audio(files.get('english'))}</td><td>{audio(files.get('hinglish'))}</td>"
        f"<td><code>ANIMATOR_TTS_VOICE={html.escape(v['voice'])}</code><br>"
        f"<code>ANIMATOR_TTS_VOICE_HINGLISH={html.escape(v['voice'])}</code></td>"
        "</tr>"
        for v, files in rows
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Voices: {html.escape(provider)}</title>
<style>
body {{ font: 15px system-ui, sans-serif; margin: 24px; color: #1f2937; }}
table {{ border-collapse: collapse; }}
td, th {{ padding: 6px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }}
code {{ font-size: 12px; color: #4b5563; }} audio {{ height: 32px; width: 220px; }}
</style></head><body>
<h1>Narration voices: {html.escape(provider)}</h1>
<p>English: "{html.escape(ENGLISH)}"<br>Hinglish: "{html.escape(HINGLISH)}"</p>
<p>Put the voice you like in <code>.env</code> (English lessons: <code>ANIMATOR_TTS_VOICE</code>, Hinglish
lessons: <code>ANIMATOR_TTS_VOICE_HINGLISH</code>), then restart <code>make dev</code>.</p>
<table><tr><th>Voice</th><th>Language</th><th>Gender</th><th>Grade</th><th>English</th><th>Hinglish</th>
<th>.env</th></tr>
{body}
</table></body></html>
"""


if __name__ == "__main__":
    asyncio.run(main())
