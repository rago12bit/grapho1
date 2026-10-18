"""Mix demo/film.mjs's recording with the voice, an ambient bed, sound cues and burned-in captions.

    docker run --rm -v "$PWD/data/demo/v2:/w" -v "$PWD/demo:/demo" --entrypoint python3 faultline-ffmpeg /demo/mix2.py /w

Inputs in <dir>: rec/video.webm, rec/timeline.json, audio/<scene>.mp3, raw/sfx_{bed,whoosh,impact,riser}.mp3.
Output: hypocenter-demo.mp4 (1920x1080, H.264 + AAC) and captions.srt.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

d = Path(sys.argv[1])
t = json.loads((d / "rec" / "timeline.json").read_text())
scenes, cues = t["scenes"], t["cues"]
END = t["end"]


def chunks(text, limit=64):
    out = []
    for sentence in re.split(r"(?<=[.?!:])\s+", text.strip()):
        cur = ""
        for word in sentence.split():
            if cur and len(cur) + 1 + len(word) > limit:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            out.append(cur)
    return out


def ts(x):
    x = max(x, 0)
    return f"{int(x // 3600):02}:{int(x % 3600 // 60):02}:{x % 60:06.3f}".replace(".", ",")


srt, n = [], 1
for sc in scenes:
    lines = chunks(sc["text"])
    total = sum(len(x) for x in lines)
    at = sc["start"]
    for line in lines:
        span = sc["duration"] * len(line) / total
        srt.append(f"{n}\n{ts(at)} --> {ts(at + span - 0.04)}\n{line}\n")
        at += span
        n += 1
(d / "captions.srt").write_text("\n".join(srt))

inputs = ["-i", str(d / "rec" / "video.webm"), "-stream_loop", "-1", "-i", str(d / "raw" / "sfx_bed.mp3")]
parts, labels = [], []
k = 2
for sc in scenes:
    inputs += ["-i", str(d / "audio" / f"{sc['id']}.mp3")]
    ms = int(sc["start"] * 1000)
    parts.append(f"[{k}:a]adelay={ms}|{ms},aresample=48000,volume=1.0[v{k}]")
    labels.append(f"[v{k}]")
    k += 1
vol = {"whoosh": 0.55, "impact": 0.9, "riser": 0.6}
for c in cues:
    inputs += ["-i", str(d / "raw" / f"sfx_{c['name']}.mp3")]
    ms = int(max(c["at"], 0) * 1000)
    parts.append(f"[{k}:a]adelay={ms}|{ms},aresample=48000,volume={vol[c['name']]}[s{k}]")
    labels.append(f"[s{k}]")
    k += 1
bed = f"[1:a]atrim=0:{END:.2f},aresample=48000,volume=0.16,afade=t=in:d=2,afade=t=out:st={END - 3:.2f}:d=3[bed]"
style = ("FontName=DejaVu Sans,Bold=1,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&HB0000000,"
         "BorderStyle=4,Outline=0,Shadow=0,MarginV=26")
graph = ";".join(parts + [bed]) + ";" + "".join(labels) + "[bed]" + \
    f"amix=inputs={len(labels) + 1}:normalize=0,loudnorm=I=-15:TP=-1.5:LRA=11,aresample=48000[aout];" + \
    f"[0:v]scale=1920:1080:flags=lanczos,subtitles={d / 'captions.srt'}:force_style='{style}',fps=30,format=yuv420p[vout]"
cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
       "-t", f"{END:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "aac", "-b:a", "192k",
       "-movflags", "+faststart", str(d / "hypocenter-demo.mp4")]
subprocess.run(cmd, check=True)
print(f"wrote {d / 'hypocenter-demo.mp4'} ({END:.1f} s, {n - 1} captions, {len(cues)} sound cues)")
