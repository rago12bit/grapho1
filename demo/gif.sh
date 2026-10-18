#!/usr/bin/env bash
# clip.webm -> docs/img/hypocenter.gif: 960 px wide, 10 fps, one shared palette (ffmpeg in the faultline-ffmpeg image).
set -euo pipefail
IN=${1:-data/demo/gif}
START=$(cat "$IN/start.txt")
docker run --rm -v "$PWD/$IN:/i" -v "$PWD/docs/img:/o" --entrypoint sh faultline-ffmpeg -c "
  ffmpeg -v error -y -ss $START -i /i/clip.webm -vf 'fps=10,scale=960:-1:flags=lanczos,palettegen=max_colors=128:stats_mode=diff' /i/palette.png &&
  ffmpeg -v error -y -ss $START -i /i/clip.webm -i /i/palette.png -lavfi 'fps=10,scale=960:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle' /o/hypocenter.gif"
ls -la docs/img/hypocenter.gif
