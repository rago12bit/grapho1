"""Mix the recorded run and the voiceover into the final MP4 with burned-in captions.

    docker run --rm -v "$PWD/data/demo:/w" -v "$PWD/demo:/demo" faultline-ffmpeg python /demo/mix.py /w

Inputs in <dir>: rec/video.webm, rec/timeline.json (scene start times from record.mjs), audio/<scene>.mp3.
Outputs: captions.srt and faultline-demo.mp4 (1920x1080, H.264 + AAC).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

OFFSET = -0.25      # the video starts ~0.25 s before the recorder's clock (measured from the title card's fade-in)
TAIL = 1.5          # seconds of picture after the last line

d = Path(sys.argv[1])
timeline = json.loads((d / "rec" / "timeline.json").read_text())


def chunks(text: str, limit: int = 78) -> list[str]:
    """Caption lines: split at sentence ends first, then commas/colons, never past `limit` characters."""
    out = []
    for sentence in re.split(r"(?<=[.?!])\s+", text.strip()):
        parts, cur = re.split(r"(?<=[,:])\s+", sentence), ""
        for p in parts:
            if cur and len(cur) + 1 + len(p) > limit:
                out.append(cur)
                cur = p
            else:
                cur = f"{cur} {p}".strip()
        while len(cur) > limit:                       # very long clause: break at a space
            cut = cur.rfind(" ", 0, limit)
            out.append(cur[:cut])
            cur = cur[cut + 1:]
        if cur:
            out.append(cur)
    return out


def ts(t: float) -> str:
    t = max(t, 0)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")


srt, n = [], 1
for sc in timeline:
    start, dur = sc["start"] + OFFSET, sc["duration"]
    lines = chunks(sc["text"])
    total = sum(len(x) for x in lines)
    t = start
    for line in lines:
        span = dur * len(line) / total
        srt.append(f"{n}\n{ts(t)} --> {ts(t + span - 0.05)}\n{line}\n")
        t += span
        n += 1
(d / "captions.srt").write_text("\n".join(srt))

end = timeline[-1]["start"] + OFFSET + timeline[-1]["duration"] + TAIL
inputs = ["-i", str(d / "rec" / "video.webm")]
delays = []
for i, sc in enumerate(timeline, start=1):
    inputs += ["-i", str(d / "audio" / f"{sc['id']}.mp3")]
    ms = int(round((sc["start"] + OFFSET) * 1000))
    delays.append(f"[{i}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
style = ("FontName=DejaVu Sans,FontSize=11,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
         "BorderStyle=4,Outline=0,Shadow=0,MarginV=22")
graph = ";".join(delays) + ";" + "".join(f"[a{i}]" for i in range(1, len(timeline) + 1)) + \
    f"amix=inputs={len(timeline)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad[aout];" + \
    f"[0:v]scale=1920:1080:flags=lanczos,subtitles={d / 'captions.srt'}:force_style='{style}',fps=30,format=yuv420p[vout]"
cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
       "-t", f"{end:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
       "-movflags", "+faststart", str(d / "faultline-demo.mp4")]
subprocess.run(cmd, check=True)
print(f"wrote {d / 'faultline-demo.mp4'} ({end:.1f} s, {n - 1} captions)")
