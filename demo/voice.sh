#!/usr/bin/env bash
# The voiceover: one clip per scene of demo/narration.json, spoken by macOS's built-in Samantha voice (synthetic,
# nobody's voice is cloned), converted to MP3 in the ffmpeg image, with durations.txt for the recorder.
#   bash demo/voice.sh data/demo/audio
set -euo pipefail
OUT=${1:-data/demo/audio}
mkdir -p "$OUT"
python3 -c 'import json,sys; [print(n["id"] + "\t" + n["text"]) for n in json.load(open("demo/narration.json"))]' |
while IFS=$'\t' read -r id text; do
  say -v Samantha -r 182 -o "$OUT/$id.aiff" "$text"
done
docker run --rm -v "$PWD/$OUT:/a" --entrypoint sh faultline-ffmpeg -c '
  cd /a && : > durations.txt
  for f in *.aiff; do id=${f%.aiff}
    ffmpeg -v error -y -i "$f" -ar 48000 -b:a 160k "$id.mp3" && rm "$f"
    printf "%s %s\n" "$id" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$id.mp3")" >> durations.txt
  done'
cat "$OUT/durations.txt"
