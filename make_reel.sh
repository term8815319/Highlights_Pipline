#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# 10张以内用
# SiFar 鸟类高光时刻 · 方案A（跨平台版）<部分需显式调用bash>
#   用法： ./make_reel.sh [照片文件夹] [输出文件] [背景音乐]
#   例：   ./make_reel.sh photos out.mp4
#          ./make_reel.sh photos out.mp4 bgm.mp3
#   显示调用：& "C:\Program Files\Git\bin\bash.exe" D:\xxx\Video_Pipline\make_reel.sh bird_images ffmpeg_test.mp4 bgm/Lupinus_H.ogg
#
#   相比原版的改动（为了 macOS / Git Bash 也能跑）：
#     · 不用 mapfile   —— macOS 自带的 bash 3.2 没有这个内建命令
#     · 不用 bc        —— 改用 awk，Windows 的 Git Bash 里没有 bc
#     · 字体自动探测   —— Linux / macOS / Windows 各试一遍
# ─────────────────────────────────────────────────────────────
set -euo pipefail

DIR="${1:-photos}"
OUT="${2:-out_ffmpeg.mp4}"
BGM="${3:-}"

W=1280; H=720; FPS=30
DUR=3.0                 # 每张照片停留时长（秒，含转场）
XF=0.7                  # 转场时长（秒）
TITLE="SiFar 高光时刻"
SUB="2026 · 你的年度后院访客"
ENDCARD="SiFar 2026"

# ---------- 0. 环境自检 ----------
command -v ffmpeg >/dev/null || { echo "❌ 找不到 ffmpeg，请先安装"; exit 1; }
[ -d "$DIR" ] || { echo "❌ 找不到照片文件夹：$DIR"; exit 1; }

# 小工具：用 awk 做浮点运算（替代 bc）
calc() { awk "BEGIN{printf \"%.3f\", $1}"; }

# ---------- 1. 找中文字体 ----------
FONT=""
for f in \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
  /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
  /System/Library/Fonts/PingFang.ttc \
  /System/Library/Fonts/STHeiti\ Medium.ttc \
  /Library/Fonts/Arial\ Unicode.ttf \
  /c/Windows/Fonts/msyhbd.ttc \
  /c/Windows/Fonts/msyh.ttc \
  "C:/Windows/Fonts/msyh.ttc" ; do
  [ -f "$f" ] && { FONT="$f"; break; }
done
if [ -z "$FONT" ] && command -v fc-match >/dev/null 2>&1; then
  FONT="$(fc-match -f '%{file}' 'sans-serif:lang=zh' 2>/dev/null || true)"
fi
[ -n "$FONT" ] || { echo "❌ 没找到中文字体，请手动把 FONT= 改成本机字体路径"; exit 1; }
echo "字体：$FONT"
# ffmpeg 的 drawtext 里，Windows 盘符的冒号要转义
FONT_ESC="$(printf '%s' "$FONT" | sed 's/\\/\//g; s/:/\\\\:/g')"

# ---------- 2. 收集照片（不用 mapfile） ----------
PICS=()
while IFS= read -r line; do PICS+=("$line"); done < <(
  find "$DIR" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | sort
)
N=${#PICS[@]}
[ "$N" -ge 2 ] || { echo "❌ $DIR 里至少要放 2 张图，现在只有 $N 张"; exit 1; }
echo "照片：$N 张"

# ---------- 3. 拼 filter_complex ----------
INPUTS=()
for p in "${PICS[@]}"; do INPUTS+=(-loop 1 -t "$DUR" -i "$p"); done

FRAMES="$(awk "BEGIN{printf \"%d\", $DUR*$FPS}")"
FC=""
for ((i=0;i<N;i++)); do
  if (( i % 2 == 0 )); then          # 偶数张：慢慢推近
    Z="min(zoom+0.0009,1.22)"; X="iw/2-(iw/zoom/2)"; Y="ih/2-(ih/zoom/2)"
  else                               # 奇数张：慢慢拉远 + 轻微横移
    Z="if(eq(on,0),1.22,max(zoom-0.0009,1.0))"
    X="iw/2-(iw/zoom/2)+(on-45)*0.6"; Y="ih/2-(ih/zoom/2)"
  fi
  FC+="[${i}:v]scale=$((W*2)):$((H*2)):force_original_aspect_ratio=increase,"
  FC+="crop=$((W*2)):$((H*2)),setsar=1,"
  FC+="zoompan=z='${Z}':x='${X}':y='${Y}':d=${FRAMES}:s=${W}x${H}:fps=${FPS},"
  FC+="format=yuv420p,trim=duration=${DUR},setpts=PTS-STARTPTS[v${i}];"
done

STEP="$(calc "$DUR - $XF")"
TRANS=(fade wipeleft smoothup circleopen fadewhite slideleft dissolve wiperight fadeblack)
PREV="v0"
for ((i=1;i<N;i++)); do
  OFF="$(calc "$STEP * $i")"
  T=${TRANS[$(( (i-1) % ${#TRANS[@]} ))]}
  FC+="[${PREV}][v${i}]xfade=transition=${T}:duration=${XF}:offset=${OFF}[x${i}];"
  PREV="x${i}"
done

TOTAL="$(calc "$STEP * ($N - 1) + $DUR")"
END_IN="$(calc "$TOTAL - 2.2")"
FADE_OUT="$(calc "$TOTAL - 1.0")"

FC+="[${PREV}]"
FC+="drawtext=fontfile='${FONT_ESC}':text='${TITLE}':fontcolor=white:fontsize=64:"
FC+="borderw=3:bordercolor=black@0.45:x=(w-tw)/2:y=h*0.36:"
FC+="alpha='if(lt(t,0.4),t/0.4,if(lt(t,2.4),1,if(lt(t,2.9),(2.9-t)/0.5,0)))',"
FC+="drawtext=fontfile='${FONT_ESC}':text='${SUB}':fontcolor=white:fontsize=30:"
FC+="borderw=2:bordercolor=black@0.45:x=(w-tw)/2:y=h*0.36+86:"
FC+="alpha='if(lt(t,0.7),max(0,(t-0.3)/0.4),if(lt(t,2.4),1,if(lt(t,2.9),(2.9-t)/0.5,0)))',"
FC+="drawtext=fontfile='${FONT_ESC}':text='${ENDCARD}':fontcolor=white:fontsize=56:"
FC+="borderw=3:bordercolor=black@0.45:x=(w-tw)/2:y=(h-th)/2:"
FC+="alpha='if(lt(t,${END_IN}),0,min(1,(t-${END_IN})/0.5))',"
FC+="fade=t=in:st=0:d=0.6,fade=t=out:st=${FADE_OUT}:d=1.0[vout]"

# ---------- 4. 组命令并执行 ----------
CMD=(ffmpeg -y "${INPUTS[@]}")
if [ -n "$BGM" ]; then CMD+=(-stream_loop -1 -i "$BGM"); fi
CMD+=(-filter_complex "$FC" -map "[vout]")
if [ -n "$BGM" ]; then
  AF_OUT="$(calc "$TOTAL - 1.5")"
  CMD+=(-map "${N}:a" -af "afade=t=in:d=1,afade=t=out:st=${AF_OUT}:d=1.5"
        -c:a aac -b:a 160k)
fi
CMD+=(-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -r "$FPS"
      -movflags +faststart -t "$TOTAL" "$OUT")

echo "输出：$OUT   总时长 ${TOTAL}s"
"${CMD[@]}" -hide_banner -loglevel warning -stats
echo "✅ 完成：$OUT"