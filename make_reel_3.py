#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SiFar 鸟类高光时刻 v3 —— 照着 template.mp4 / template_01.mp4 重做

    python make_reel_v3.py --selftest
    python make_reel_v3.py bird_images out.mp4 --intro video_inputs/video_01.mp4
    python make_reel_v3.py bird_images out.mp4 --intro video_inputs/video_01.mp4 \\
           --bgm bgm/Lupinus_H.ogg --limit 24 --vertical

片子结构（两份模板各取所长）：

    ① 开场 —— 学 template.mp4 / template_01.mp4
       年份数字当遮罩，开场视频从字里透出来，数字缓慢放大
       → 对角展开成全画面视频
    ② 视频 → 图片 —— 学 template_01.mp4
       模糊过渡（xfade hblur），画面糊掉再从糊里浮出第一张图
    ③ 图片之间 —— 两份模板都有的：方格擦除、方向模糊滑动、溶解……
       本脚本给了 50 种，每次切换随机抽一种
    ④ 结尾 —— 学 template.mp4
       最后一张渐进模糊 + 标题淡入，停住，再淡出到黑

★ 关于「渐进模糊」：ffmpeg 里没法让 gblur 的 sigma 随时间连续变化
════════════════════════════════════════════════════════════════
`gblur` / `boxblur` / `avgblur` 的 sigma 和半径都是**静态选项**，不吃 `t` 表达式
（只有 `enable=` 支持时间轴，那是开关不是渐变）。想连续变化得走 sendcmd，
而 sendcmd 只能在指定时刻**跳变**，插值不了。

所以这里用另一条路：**把「模糊」做成一个目标画面，用 xfade 的 hblur 过去。**

    [清晰的最后一张] --xfade=hblur--> [同一张的静态模糊版 + 标题]

xfade 自己会在过渡期间把两端糊在一起，视觉上就是渐进模糊。
视频转图片那处同理。这条写在这里，免得以后有人再花半天去查 gblur 的表达式。

★ 50 种转场：34 个内置 + 16 个自定义表达式，**开跑前逐个验一遍**
════════════════════════════════════════════════════════════════
ffmpeg 6.1 的 xfade 有 58 个内置转场，8.x 只多不少。但**版本不同支持的名字不同**，
自定义表达式还可能用到某个版本没有的函数。所以启动时拿 64×36 的假画面
把 50 种全渲一遍，**失败的直接剔除并打印**，绝不让它在最终渲染跑到一半才炸。

这也是为什么转场表里自定义那批写的是表达式而不是硬编码滤镜串 ——
表达式能被单独验证，滤镜串只能整条跑。
"""
from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

FPS = 30
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf", r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
# 结尾标题用的手写/衬线体（template.mp4 的 "Birds Highlights Video" 是手写体）
SCRIPT_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoesc.ttf", r"C:\Windows\Fonts\Gabriola.ttf",
    r"C:\Windows\Fonts\BRUSHSCI.TTF", r"C:\Windows\Fonts\ITCKRIST.TTF",
    "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc",
    "/usr/share/fonts/truetype/crosextra/Caladea-BoldItalic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
]

# ─────────────────── 50 种图片转场 ───────────────────
# 内置（名字要在运行时核对，不同 ffmpeg 版本不一样）
BUILTIN = [
    "fade", "fadeblack", "fadewhite", "fadegrays", "fadefast", "fadeslow",
    "dissolve", "pixelize", "distance", "radial",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "wipetl", "wipetr", "wipebl", "wipebr",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "circlecrop", "rectcrop",
    "vertopen", "vertclose", "horzopen", "horzclose",
]
# 自定义表达式。xfade custom 里可用的变量：X Y W H A B P
#
# ★★ 2026-09-21 修的大 bug：**xfade 的 P 是从 1 走到 0，不是 0 走到 1。**
# 实测（红=A旧图、蓝=B新图，表达式 `if(lt(X/W,P),B,A)`）：转场第一帧整屏就是蓝的，
# 然后蓝色从左边缩回去，最后一帧全红 —— 新图先整张亮出来，再被旧图擦回去。
# Ruby 看到的「百叶窗之前新图已经出现了」「四角是从大缩到小」，全是这一条。
#
# 所以下面统一写成 `{p}`，表示「0→1 的正向进度」，build_transitions 里替换成 `(1-P)`。
# **只在一个地方做这个翻转**，16 条表达式各写各的迟早有人漏掉一条。
# 想验证方向对不对不能靠读代码，得真渲两帧看首尾 —— selftest 里就是这么做的。
CUSTOM = [
    ("百叶窗·横", "if(lt(mod(Y,H/12)/(H/12),{p}),B,A)"),
    ("百叶窗·竖", "if(lt(mod(X,W/16)/(W/16),{p}),B,A)"),
    ("棋盘格", "if(lt(mod(floor(X/(W/16))+floor(Y/(H/9)),2)*0.35,{p}*1.35-0.35),B,A)"),
    ("斜纹推进", "if(lt(mod(X+Y,W/10)/(W/10),{p}),B,A)"),
    ("中心横开", "if(lt(abs(Y-H/2)/(H/2),{p}),B,A)"),
    ("中心竖开", "if(lt(abs(X-W/2)/(W/2),{p}),B,A)"),
    ("四角向内", "if(gt(min(min(X,W-X),min(Y,H-Y))/(min(W,H)/2),1-{p}),B,A)"),
    ("四角向外", "if(lt(min(min(X,W-X),min(Y,H-Y))/(min(W,H)/2),{p}),B,A)"),
    ("菱形开", "if(lt((abs(X-W/2)/(W/2)+abs(Y-H/2)/(H/2))/2,{p}),B,A)"),
    ("波浪横扫", "if(lt(X/W+0.08*sin(Y/H*18),{p}*1.16-0.08),B,A)"),
    ("波浪竖扫", "if(lt(Y/H+0.08*sin(X/W*18),{p}*1.16-0.08),B,A)"),
    ("圆环扩散", "if(lt(hypot(X-W/2,Y-H/2)/hypot(W/2,H/2),{p}),B,A)"),
    ("圆环收拢", "if(gt(hypot(X-W/2,Y-H/2)/hypot(W/2,H/2),1-{p}),B,A)"),
    ("左上放射", "if(lt(hypot(X,Y)/hypot(W,H),{p}),B,A)"),
    ("右下放射", "if(lt(hypot(W-X,H-Y)/hypot(W,H),{p}),B,A)"),
    ("条带交错", "if(lt(mod(Y,H/10)/(H/10),if(lt(mod(floor(Y/(H/10)),2),1),{p},1-{p})),B,A)"),
]

# 正向进度：xfade 的 P 倒着走，这里统一翻过来。改动只此一处。
FWD = "(1-P)"


def die(msg):
    sys.exit(f"\n❌ {msg}")


def run(cmd, graph=None, quiet=True, check=True):
    if graph is not None:
        cmd = cmd + ["-filter_complex", graph]
    cmd = cmd + (["-hide_banner", "-loglevel", "error"] if quiet
                 else ["-hide_banner", "-loglevel", "warning", "-stats"])
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL if quiet else None,
                       stderr=subprocess.PIPE if quiet else None)
    if check and r.returncode != 0:
        code = r.returncode if r.returncode < 2**31 else r.returncode - 2**32
        err = (r.stderr or b"").decode("utf-8", "replace")[-1200:]
        die(f"ffmpeg 退出码 {code}\n{err}")
    return r.returncode == 0


def ffmpeg_ver():
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                             text=True, timeout=15).stdout
        m = re.search(r"ffmpeg version (\S+)", out)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def supported_xfades():
    """问当前这个 ffmpeg 它到底认哪些内置转场。

    ★ 不能照抄文档 —— 6.1 有 58 个，8.x 又不一样，写死名字早晚在别人机器上炸。
    """
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
                             capture_output=True, text=True, timeout=20)
        txt = (out.stdout or "") + (out.stderr or "")
    except Exception:
        return set()
    names = set()
    for ln in txt.splitlines():
        m = re.match(r"\s{5,}(\w+)\s+-?\d+\s+\.\.FV", ln)
        if m:
            names.add(m.group(1))
    return names


def build_transitions(validate=True, verbose=False):
    """→ [(显示名, xfade参数串)]，只保留这台机器真能跑的。"""
    avail = supported_xfades()
    out = []
    for n in BUILTIN:
        if avail and n not in avail:
            continue
        out.append((n, f"transition={n}"))
    for name, expr in CUSTOM:
        out.append((name, f"transition=custom:expr='{expr.replace('{p}', FWD)}'"))
    if not validate:
        return out

    ok, bad, rev = [], [], []
    for name, arg in out:
        r = check_transition(arg)
        if r == "ok":
            ok.append((name, arg))
        elif r == "reversed":
            rev.append((name, arg))
        else:
            bad.append((name, arg))
    if bad:
        print(f"⚠ {len(bad)} 种转场在这台 ffmpeg 上跑不起来，已剔除："
              f"{', '.join(n for n, _ in bad[:8])}"
              + (" …" if len(bad) > 8 else ""))
    if rev:
        print(f"★★ {len(rev)} 种转场【方向反了】（开头就显示新图），已剔除："
              f"{', '.join(n for n, _ in rev[:8])}"
              + (" …" if len(rev) > 8 else ""))
    if verbose:
        print(f"   可用转场 {len(ok)} 种")
    return ok


def check_transition(arg, tmpdir=None):
    """→ 'ok' / 'reversed' / 'fail'

    ★ 不只是「能不能跑」，还要验**方向对不对**。
    拿红(A=旧) → 蓝(B=新) 渲一小段：
        第一帧应该基本全红，最后一帧应该基本全蓝。
        第一帧就发蓝 = 这条表达式把 P 的方向搞反了（2026-09-21 踩过）。
    光看代码看不出方向，必须真渲。
    """
    d = tmpdir or tempfile.mkdtemp(prefix="xfchk_")
    own = tmpdir is None
    try:
        pat = os.path.join(d, "f%02d.png")
        g = ("color=red:s=64x36:d=0.6:r=20[a];color=blue:s=64x36:d=0.6:r=20[b];"
             f"[a][b]xfade={arg}:duration=0.4:offset=0.0,format=rgb24[v]")
        if not run(["ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1",
                    "-map", "[v]", "-frames:v", "8", pat], graph=g, check=False):
            return "fail"
        fs = sorted(f for f in os.listdir(d) if f.endswith(".png"))
        if len(fs) < 3:
            return "fail"

        def blue_ratio(p):
            """这一帧里「偏蓝」的像素占比"""
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", p, "-vf",
                 "format=rgb24,scale=16:9", "-f", "rawvideo", "-"],
                capture_output=True).stdout
            if not out:
                return -1.0
            n = len(out) // 3
            b = sum(1 for i in range(n) if out[i*3+2] > out[i*3] + 40)
            return b / max(n, 1)
        first = blue_ratio(os.path.join(d, fs[0]))
        last = blue_ratio(os.path.join(d, fs[-1]))
        if first < 0 or last < 0:
            return "fail"
        if first > 0.5:
            return "reversed"
        if last < 0.5:
            return "reversed"
        return "ok"
    finally:
        if own:
            shutil.rmtree(d, ignore_errors=True)


def find_font(user, cands):
    if user:
        if not os.path.isfile(user):
            die(f"字体不存在：{user}")
        return user
    for f in cands:
        if os.path.isfile(f):
            return f
    return None


def esc_font(p):
    return os.path.abspath(p).replace("\\", "/").replace(":", r"\:")


def esc_text(s):
    for a, b in (("\\", r"\\"), (":", r"\:"), ("'", r"\'"), ("%", r"\%")):
        s = s.replace(a, b)
    return s


def sample(items, k):
    n = len(items)
    if k <= 0 or k >= n:
        return items
    if k == 1:
        return [items[0]]
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


def probe_dur(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


# ─────────────── ① 开场：年份跳动的文字遮罩 ───────────────
def year_counter(y0, y1, dur):
    """drawtext 的 text 串：年份从 y0 跳到 y1，三次方缓出，到点停住。

    ★ 用 `%{eif:表达式:d}` —— drawtext 的 text 里能塞表达式，这是关键。
    两条路都试过：
      A. 单个 drawtext + %{eif}     ← 用这条。一行搞定，落点精确，改年份只改两个数
      B. 每个年份一个 drawtext，靠 enable='between(t,a,b)' 切
         能做，但 N 个年份要写 N 条滤镜，节奏是手填的，改一次全得重排

    缓出用 1-(1-p)³：开头跳得快、越接近目标越慢，最后几帧停在 y1 上不抖。
    template_01 的 2016→2018→2020→2022→2023→2025→2026 就是这个节奏。

    转义：text 里 `:` 要写成 `\\:`、`,` 要写成 `\\,`，否则会被当成滤镜参数分隔符。
    """
    span = int(y1) - int(y0)
    return (f"%{{eif\\:{int(y0)}+floor({span}*"
            f"(1-pow(max(0\\,1-t/{dur:.3f})\\,3))+0.001)\\:d}}")


def render_intro(video, out, w, h, font, year, year_from, count_dur,
                 mask_dur, full_dur, xf, preset, tmp):
    """年份跳动的文字遮罩段 → 对角展开 → 全画面段。

    遮罩做法：黑底白字当 luma 蒙版，alphamerge 到视频上，再叠回黑底。
    数字本身在跳，所以蒙版形状每帧都在变，视频透出来的形状跟着变 —— 就是模板那个效果。

    ★ 这里【不再】套 zoompan。数字跳动本身已经够动了，再叠一层缓慢放大
    会显得画面一直在飘（Ruby 2026-09-21 反馈）。
    """
    fe = esc_font(font)
    fs = int(h * 0.61)                      # 数字高度约占画面 6 成
    a = os.path.join(tmp, "intro_a.mp4")
    ytext = year_counter(year_from, year, count_dur)
    g = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
         f"crop={w}:{h},setsar=1,fps={FPS}[v];"
         f"color=black:s={w}x{h}:r={FPS},"
         f"drawtext=fontfile='{fe}':text='{ytext}':fontcolor=white:"
         f"fontsize={fs}:x=(w-tw)/2:y=(h-th)/2,format=gray[m];"
         f"[v][m]alphamerge[fg];"
         f"color=black:s={w}x{h}:r={FPS}[bg];"
         f"[bg][fg]overlay=shortest=1,format=yuv420p[o]")
    run(["ffmpeg", "-y", "-t", f"{mask_dur}", "-i", video,
         "-map", "[o]", "-t", f"{mask_dur}", "-c:v", "libx264",
         "-preset", preset, "-crf", "19", "-pix_fmt", "yuv420p",
         "-r", str(FPS), a], graph=g)

    b = os.path.join(tmp, "intro_b.mp4")
    g2 = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},setsar=1,fps={FPS},format=yuv420p[o]")
    run(["ffmpeg", "-y", "-ss", f"{max(0.0, mask_dur - xf):.3f}",
         "-t", f"{full_dur}", "-i", video, "-map", "[o]",
         "-t", f"{full_dur}", "-c:v", "libx264", "-preset", preset,
         "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), b], graph=g2)

    # 对角展开：数字段化开成全画面
    total = mask_dur + full_dur - xf
    g3 = (f"[0:v]setsar=1[a];[1:v]setsar=1[b];"
          f"[a][b]xfade=transition=diagtl:duration={xf}:"
          f"offset={mask_dur - xf:.3f},format=yuv420p[o]")
    run(["ffmpeg", "-y", "-i", a, "-i", b, "-map", "[o]",
         "-c:v", "libx264", "-preset", preset, "-crf", "19",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.3f}", out],
        graph=g3)
    return total


# ─────────────── ② 图片：静止，不做 Ken Burns ───────────────
def render_clip(pic, idx, out, dur, w, h, preset):
    """一张图渲成一小段，**画面完全静止**。

    ★ 2026-09-21 改：去掉 zoompan（原来每张都在缓慢推近/拉远）。
    Ruby 反馈「每帧都在动」看着累 —— 转场本身已经提供了动感，
    再让每张图各自飘会让整片没有一刻是静的。

    顺带把 v2 那个 segfault 的根子也拔了：当初 50 张图爆内存，是因为
    zoompan 要在超采样画布（1.75×）上逐帧重算，每路都占着一大块内存。
    静止图只有一次 scale+crop，内存几乎为零，`--super` 这个参数因此作废。
    """
    g = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
         f"crop={w}:{h},setsar=1,fps={FPS},format=yuv420p[o]")
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", pic,
         "-map", "[o]", "-c:v", "libx264", "-preset", preset, "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", str(dur), out], graph=g)
    return out


# ─────────────── ④ 结尾：模糊 + 标题 ───────────────
def blur_cmds(path, sigma, ramp, hold_from=0.0):
    """写一个 sendcmd 命令文件：逐帧把 gblur 的 sigma 从 0 推到 sigma。

    ★★ 2026-09-21：我先前说「ffmpeg 没法让模糊随时间连续变化」，**那是错的**。
    `ffmpeg -h filter=gblur` 里 sigma 后面有个 `T` 标志 = 支持运行时命令，
    配 sendcmd **一帧发一条命令**就是真正的逐帧渐变模糊，而且画面完全不动。
    （Ruby 提的就是这条思路，对的。）

    原来用 `xfade=hblur` 是「横向模糊 + 扫动」，所以画面会左右飘 —— 那不是渐变模糊。

    用文件而不是 `c='...'` 内联：命令之间用 `;` 分隔，而 `;` 在 filter_complex 里
    是滤镜链的分隔符，内联要多绕一层转义；文件形式没这个问题。

    曲线用 smoothstep（3p²−2p³）：起步和收尾都缓，中间快，比线性自然。
    """
    n = max(1, int(round(ramp * FPS)))
    lines = []
    for i in range(n + 1):
        t = hold_from + i / FPS
        p = i / n
        s = sigma * (p * p * (3 - 2 * p))
        lines.append(f"{t:.3f} gblur sigma {s:.3f};")
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
    return path


def render_endcard(pic, out, dur, w, h, font, title, sigma, preset,
                   ramp, delay, tmp):
    """结尾卡：同一张图【原地】从清晰渐变到模糊，然后标题浮现。画面不动。

    ★ 2026-09-21 改了两处：
      · 去掉 zoompan（画面不再漂移）
      · 「清晰→模糊」改成 sendcmd 逐帧推 sigma，**不再用 xfade=hblur**
        —— hblur 是横向模糊加扫动，Ruby 看到的「先往左再往右」就是它

    delay：标题等模糊走完再浮现，两个动作别叠在一起。
    """
    cf = blur_cmds(os.path.join(tmp, "blur.cmd"), sigma, ramp)
    tail = ""
    if font and title:
        fe = esc_font(font)
        t0 = max(0.0, delay)
        tail = (f",drawtext=fontfile='{fe}':text='{esc_text(title)}':"
                f"fontcolor=white:fontsize={int(h*0.088)}:"
                f"borderw=3:bordercolor=black@0.35:"
                f"x=(w-tw)/2:y=(h-th)/2:"
                f"alpha='min(1,max(0,(t-{t0:.2f})/0.9))'")
    g = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
         f"crop={w}:{h},setsar=1,fps={FPS},"
         f"sendcmd=f='{esc_font(cf)}',gblur=sigma=0{tail},format=yuv420p[o]")
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", pic,
         "-map", "[o]", "-c:v", "libx264", "-preset", preset, "-crf", "19",
         "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", str(dur), out], graph=g)
    return out


# ─────────────── ③ 串联 ───────────────
def xfade_chain(clips, durs, trans, xfs, out, preset, tail=None):
    """clips 依次 xfade。

    trans[i] / xfs[i] 是第 i 个接缝的转场参数串和**时长**。

    ★ 时长必须逐个接缝给，不能整条链共用一个。
    模糊过渡（视频→图片、图片→结尾卡）要 1.3s，图片之间只要 0.75s；
    早先版本整条链共用一个 xf，结果那两处模糊只在「恰好排在组内第一个」
    时才有正确时长，其余时候被悄悄缩短 —— 出片能看，但和设计不符，
    而且不报错。
    """
    n = len(clips)
    if n == 1 and tail is None:
        shutil.copyfile(clips[0], out)
        return durs[0]
    fc = [f"[{i}:v]setsar=1,format=yuv420p[c{i}];" for i in range(n)]
    prev, total = "c0", durs[0]
    for i in range(1, n):
        xf = xfs[i - 1]
        off = total - xf
        fc.append(f"[{prev}][c{i}]xfade={trans[i-1]}:duration={xf}:"
                  f"offset={off:.3f}[x{i}];")
        prev = f"x{i}"
        total = off + durs[i]
    fc.append(f"[{prev}]{tail}[vout]" if tail else f"[{prev}]null[vout]")
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-map", "[vout]", "-c:v", "libx264", "-preset", preset,
            "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-t", f"{total:.3f}", out]
    run(cmd, graph="".join(fc))
    return total


def selftest():
    ok = True

    def chk(n, c, g=""):
        nonlocal ok
        ok = ok and bool(c)
        print(("  ok   " if c else "  FAIL ") + n + (f"  {g}" if g else ""))

    print("环境")
    chk("有 ffmpeg", bool(shutil.which("ffmpeg")), ffmpeg_ver())
    chk("有 ffprobe", bool(shutil.which("ffprobe")))
    av = supported_xfades()
    chk("★ 能问出这台 ffmpeg 支持哪些转场（不照抄文档）", len(av) >= 20, len(av))
    chk("  常见几个都在", {"fade", "dissolve", "wipeleft", "hblur"} <= av)

    print("转场表")
    chk("内置 34 条", len(BUILTIN) == 34, len(BUILTIN))
    chk("自定义 16 条", len(CUSTOM) == 16, len(CUSTOM))
    chk("★ 合计 50 种", len(BUILTIN) + len(CUSTOM) == 50)
    chk("内置名字不重复", len(set(BUILTIN)) == len(BUILTIN))
    chk("自定义名字不重复", len({n for n, _ in CUSTOM}) == len(CUSTOM))
    chk("★ 自定义表达式一律写 {p}，不许直接写 P（方向由 FWD 统一翻）",
        all("{p}" in e for _, e in CUSTOM)
        and not any(re.search(r"(?<![{])P(?![}])", e) for _, e in CUSTOM))
    chk("自定义表达式都引用了 A 和 B",
        all("A" in e and "B" in e for _, e in CUSTOM))

    print("★★ xfade 的 P 是倒着走的（1→0）—— 这是 2026-09-21 的大 bug")
    chk("表达式里统一写 {p}，不直接写 P",
        all("{p}" in e for _, e in CUSTOM))
    chk("翻转只在一个地方做", FWD == "(1-P)")
    chk("★ 方向检查器能认出【反的】那种",
        check_transition("transition=custom:expr='if(lt(X/W,P),B,A)'") == "reversed")
    chk("★ 也能认出【对的】那种",
        check_transition("transition=custom:expr='if(lt(X/W,1-P),B,A)'") == "ok")
    chk("  跑不起来的报 fail",
        check_transition("transition=custom:expr='nosuchfunc(X)'") == "fail")
    chk("  内置 fade 是对的", check_transition("transition=fade") == "ok")
    chk("  内置 wipeleft 是对的", check_transition("transition=wipeleft") == "ok")

    print("★ 逐个真渲一遍（坏表达式、反方向都不许进最终渲染）")
    t = build_transitions(validate=True)
    chk(f"验完剩 {len(t)} 种，且 ≥40", len(t) >= 40, len(t))
    names = [n for n, _ in t]
    for want in ("百叶窗·横", "棋盘格", "圆环扩散", "波浪横扫",
                 "四角向外", "斜纹推进"):
        chk(f"  「{want}」方向正确并通过", want in names)

    print("★ 年份跳动（%{eif} 计数器）")
    s = year_counter(2016, 2026, 2.2)
    chk("用了 eif 而不是写死文本", "%{eif" in s and "2016" in s, s[:44])
    chk("跨度算对（2026−2016=10）", "10*" in s)
    chk("★ 冒号转义成 \\:（不转义会被当成滤镜参数分隔）", r"\:" in s)
    chk("★ 逗号转义成 \\,（同理）", r"\," in s)
    chk("大括号成对", s.count("{") == s.count("}"))

    def ease(y0, y1, T, t):
        return y0 + int((y1 - y0) * (1 - max(0.0, 1 - t / T) ** 3) + 0.001)
    chk("t=0 是起始年", ease(2016, 2026, 2.2, 0) == 2016)
    chk("t=T 落在目标年", ease(2016, 2026, 2.2, 2.2) == 2026)
    chk("★ 超过 T 之后停住不乱跳", ease(2016, 2026, 2.2, 9.9) == 2026)
    chk("★ 单调不回退",
        all(ease(2016, 2026, 2.2, i / 40) <= ease(2016, 2026, 2.2, (i + 1) / 40)
            for i in range(120)))
    mid = ease(2016, 2026, 2.2, 1.1)
    chk("★ 缓出：走一半时间已跳过一半年份（前快后慢）", mid >= 2024, mid)
    # 真渲一帧，确认这条表达式 ffmpeg 真的认
    fnt = find_font(None, FONT_CANDIDATES)
    if fnt:
        g = (f"color=black:s=160x90:d=0.3:r=10,"
             f"drawtext=fontfile='{esc_font(fnt)}':text='{s}':fontcolor=white:"
             f"fontsize=30:x=10:y=30,format=yuv420p[v]")
        chk("★★ ffmpeg 真的能跑这条表达式（不是纸上谈兵）",
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1",
                 "-map", "[v]", "-frames:v", "2", "-f", "null", "-"],
                graph=g, check=False))

    print("★ 结尾：逐帧推 sigma 做渐变模糊（不是 hblur 扫动）")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        cf = blur_cmds(os.path.join(td, "b.cmd"), 24.0, 1.0)
        ls = [l for l in open(cf).read().splitlines() if l.strip()]
        chk("1.0s @30fps 生成 31 条命令（逐帧）", len(ls) == 31, len(ls))
        v = [float(l.split()[-1].rstrip(";")) for l in ls]
        chk("从 0 开始", abs(v[0]) < 1e-6, v[0])
        chk("到 24 结束", abs(v[-1] - 24.0) < 1e-6, v[-1])
        chk("★ 单调递增（不能忽大忽小）",
            all(v[i] <= v[i+1] + 1e-9 for i in range(len(v)-1)))
        chk("★ smoothstep：中点在一半上（起收都缓）",
            abs(v[len(v)//2] - 12.0) < 1.5, v[len(v)//2])
        chk("  两端增量小于中段（缓入缓出）",
            (v[1]-v[0]) < (v[len(v)//2]-v[len(v)//2-1]) and
            (v[-1]-v[-2]) < (v[len(v)//2]-v[len(v)//2-1]))
        # 真渲一遍，确认 gblur 吃 sendcmd
        g = (f"color=white:s=64x36:d=0.6:r=30,"
             f"sendcmd=f='{esc_font(cf)}',gblur=sigma=0,format=yuv420p[v]")
        chk("★★ ffmpeg 真能靠 sendcmd 改 gblur 的 sigma",
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1",
                 "-map", "[v]", "-frames:v", "10", "-f", "null", "-"],
                graph=g, check=False))
    src0 = open(__file__, encoding="utf-8").read().split("def selftest")[0]
    chk("★ 结尾不再用 hblur（那是横向模糊+扫动，画面会左右飘）",
        "transition=hblur" not in
        src0[src0.index("def render_endcard"):] .split("def main")[0])

    print("★ 静止：图片和结尾卡都不该再有 zoompan")
    src = open(__file__, encoding="utf-8").read().split("def selftest")[0]
    body = src[src.index("def render_clip"):src.index("def xfade_chain")]
    # 查的是滤镜用法 `zoompan=`，不是注释里提到的这个词
    chk("render_clip / render_endcard 里没有 zoompan= 滤镜",
        "zoompan=" not in body)
    chk("  开场遮罩里也没有",
        "zoompan=" not in src[src.index("def render_intro"):
                              src.index("def render_clip")])
    chk("  而注释里仍然记着为什么去掉（别把理由也删了）",
        "zoompan" in body and "Ruby" in body)

    print("★ 接缝时长逐个走（修过的 bug，别让它回来）")

    def chain_total(durs, xfs):
        """单链总时长 = 各段之和 − 各接缝之和"""
        t = durs[0]
        for i in range(1, len(durs)):
            t = (t - xfs[i - 1]) + durs[i]
        return t

    def grouped_total(durs, xfs, g):
        """分组后再串，总时长必须和单链一样"""
        N = len(durs)
        segd = []
        for st in range(0, N, g):
            en = min(st + g, N)
            segd.append(chain_total(durs[st:en], xfs[st:en - 1]))
        bnd = [(k + 1) * g - 1 for k in range(len(segd) - 1)]
        return chain_total(segd, [xfs[j] for j in bnd])

    D = [4.2] + [2.6] * 18 + [3.6]           # 开场 + 18 图 + 结尾卡
    X = [1.3] + [0.75] * 17 + [1.3]          # 两头模糊过渡 1.3s，中间 0.75s
    chk("段数比接缝数多 1", len(D) == len(X) + 1)
    chk("单链总时长 = Σ段 − Σ接缝",
        abs(chain_total(D, X) - (sum(D) - sum(X))) < 1e-9,
        f"{chain_total(D, X):.2f}")
    for g in (4, 6, 7, 8, 20, 100):
        chk(f"  ★ 分组 g={g} 和单链总时长一致",
            abs(grouped_total(D, X, g) - chain_total(D, X)) < 1e-9,
            f"{grouped_total(D, X, g):.3f} vs {chain_total(D, X):.3f}")
    bad = grouped_total(D, [X[0]] * len(X), 6)   # 整条链共用一个时长（旧 bug）
    chk("★ 整条链共用一个接缝时长会算出不同的总长 —— 证明这条断言有分辨力",
        abs(bad - chain_total(D, X)) > 1.0, f"{bad:.2f}")
    chk("组边界取的是全局接缝下标",
        [(k + 1) * 6 - 1 for k in range(3)] == [5, 11, 17])

    print("抽样 / 张数限制")
    chk("均匀抽样保首尾", sample(list(range(100)), 3) == [0, 50, 99])
    chk("要的比有的多就全给", sample([1, 2], 9) == [1, 2])
    chk("k=1 给第一个", sample([5, 6, 7], 1) == [5])

    print("转义")
    chk("盘符冒号被转义", r"\:" in esc_font(r"C:\Windows\Fonts\msyh.ttc")
        if os.name == "nt" else True)
    chk("单引号被转义", esc_text("it's") == r"it\'s")
    chk("百分号被转义", esc_text("50%") == r"50\%")

    print("\n" + ("全部通过" if ok else "★ 有失败项"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="鸟类高光时刻 v3（带开场视频 + 50 种转场）")
    ap.add_argument("photos", nargs="?", default="bird_images")
    ap.add_argument("out", nargs="?", default="out.mp4")
    ap.add_argument("--intro", default=None, help="开场视频")
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--limit", type=int, default=0, help="用几张，0=全部（会夹到 10~50）")
    ap.add_argument("--min-pics", type=int, default=10)
    ap.add_argument("--max-pics", type=int, default=50)
    ap.add_argument("--dur", type=float, default=2.6, help="每张停留秒数")
    ap.add_argument("--xf", type=float, default=0.75, help="图片间转场秒数")
    ap.add_argument("--year", type=int, default=2026, help="年份动画落到哪一年")
    ap.add_argument("--year-from", type=int, default=0,
                    help="从哪一年开始跳，0 = 目标年份 −10")
    ap.add_argument("--year-count", type=float, default=0.0,
                    help="跳完用几秒，0 = 遮罩时长的 65%%（剩下的停在目标年份上）")
    ap.add_argument("--mask-dur", type=float, default=2.6, help="文字遮罩段时长")
    ap.add_argument("--intro-full", type=float, default=3.4, help="展开后全画面时长")
    ap.add_argument("--intro-xf", type=float, default=0.8, help="遮罩→全画面 展开时长")
    ap.add_argument("--video-xf", type=float, default=1.3,
                    help="★ 开场视频→第一张图 的模糊过渡时长")
    ap.add_argument("--end-dur", type=float, default=3.6, help="结尾卡时长")
    ap.add_argument("--end-blur", type=float, default=26, help="结尾模糊的最终强度")
    ap.add_argument("--end-ramp", type=float, default=1.4,
                    help="结尾从清晰糊到底用几秒（逐帧推 sigma，画面不动）")
    ap.add_argument("--end-seam", type=float, default=0.35,
                    help="末图→结尾卡的接缝时长。两边是同一张清晰图，短一点即可")
    ap.add_argument("--title", default="Birds Highlights Video")
    # --super 保留只为兼容旧命令行，现在图片是静止的，没有超采样这回事
    ap.add_argument("--super", dest="sup", type=float, default=1.75,
                    help=argparse.SUPPRESS)
    ap.add_argument("--group", type=int, default=0,
                    help="0=一条链串到底（每个接缝时长精确，首选）。"
                         "内存不够时设 6~8 分组渲染")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("--font", default=None)
    ap.add_argument("--title-font", default=None)
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--seed", type=int, default=0, help="转场随机种子，同种子出片一致")
    ap.add_argument("--skip-validate", action="store_true", help="跳过 50 种转场的预检")
    ap.add_argument("--keep-temp", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not shutil.which("ffmpeg"):
        die("找不到 ffmpeg")
    if not os.path.isdir(a.photos):
        die(f"找不到照片文件夹：{os.path.abspath(a.photos)}")

    w, h = (1080, 1920) if a.vertical else (1280, 720)
    allpics = sorted(os.path.join(a.photos, f) for f in os.listdir(a.photos)
                     if f.lower().endswith(EXTS))
    if len(allpics) < a.min_pics:
        die(f"至少要 {a.min_pics} 张图，{a.photos} 里只有 {len(allpics)} 张")
    want = a.limit if a.limit > 0 else len(allpics)
    want = max(a.min_pics, min(want, a.max_pics, len(allpics)))
    pics = sample(allpics, want)
    n = len(pics)

    font = find_font(a.font, FONT_CANDIDATES)
    tfont = find_font(a.title_font, SCRIPT_FONT_CANDIDATES) or font
    if not font:
        die("没找到可用字体（开场遮罩必须有字体）。用 --font 指定一个 .ttf/.ttc")

    rnd = random.Random(a.seed)
    print(f"ffmpeg {ffmpeg_ver()}")
    trans = build_transitions(validate=not a.skip_validate, verbose=True)
    if not trans:
        die("一种转场都不可用，检查 ffmpeg 安装")

    print(f"照片   {len(allpics)} 张 → 用 {n} 张（限制 {a.min_pics}~{a.max_pics}）")
    print(f"字体   {font}")
    if tfont != font:
        print(f"标题体 {tfont}")
    jobs = a.jobs or min(4, os.cpu_count() or 2)
    tmp = tempfile.mkdtemp(prefix="reel3_")
    try:
        parts, pdurs, pnames = [], [], []

        # ① 开场
        if a.intro:
            if not os.path.isfile(a.intro):
                die(f"找不到开场视频：{a.intro}")
            vd = probe_dur(a.intro)
            md = min(a.mask_dur, max(1.0, vd * 0.45))
            fd = min(a.intro_full, max(1.2, vd - md + a.intro_xf))
            y0 = a.year_from or (a.year - 10)
            cd = a.year_count or max(0.8, md * 0.65)
            print(f"\n[1/4] 开场：{y0}→{a.year} 跳动 {cd:.1f}s（缓出），"
                  f"遮罩共 {md:.1f}s → 展开 → 全画面 {fd:.1f}s")
            ip = os.path.join(tmp, "intro.mp4")
            idur = render_intro(a.intro, ip, w, h, font, a.year, y0, cd,
                                md, fd, a.intro_xf, a.preset, tmp)
            parts.append(ip); pdurs.append(idur); pnames.append("开场")

        # ② 图片
        print(f"\n[2/4] 渲染 {n} 张图 …")

        def job(t):
            i, p = t
            r = render_clip(p, i, os.path.join(tmp, f"c{i:03d}.mp4"),
                            a.dur, w, h, a.preset)
            print(f"      {i+1:>3}/{n}  {os.path.basename(p)}")
            return r
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            clips = list(ex.map(job, enumerate(pics)))

        # ④ 结尾卡（用最后一张）
        ec = os.path.join(tmp, "end.mp4")
        # 标题等模糊过渡走完再浮现，两个动作别叠在一起
        # 结尾卡自己完成「清晰→模糊」，所以标题只要等模糊走完
        render_endcard(pics[-1], ec, a.end_dur, w, h, tfont, a.title,
                       a.end_blur, a.preset, ramp=a.end_ramp,
                       delay=a.end_ramp + 0.15, tmp=tmp)

        # ③ 串联：开场 --hblur--> 图1 --随机--> 图2 … --hblur--> 结尾卡
        seq = parts + clips + [ec]
        durs = pdurs + [a.dur] * n + [a.end_dur]
        joints = len(seq) - 1
        picks = []
        for j in range(joints):
            if a.intro and j == 0:
                picks.append(("模糊过渡", "transition=hblur"))       # 视频→图片
            elif j == joints - 1:
                # ★ 最后一张图 → 结尾卡：两边是【同一张图、同样清晰】，
                #   用普通 fade 接上，观众看不出接缝，然后结尾卡自己糊起来。
                #   原来这里用 hblur，那是「横向模糊+扫动」，画面会左右飘。
                picks.append(("无缝接", "transition=fade"))
            else:
                picks.append(rnd.choice(trans))
        xfs = [a.video_xf if p[0] == "模糊过渡"
               else (a.end_seam if p[0] == "无缝接" else a.xf) for p in picks]

        print(f"\n[3/4] {joints} 个接缝，随机转场：")
        body = [p[0] for p in picks[1 if a.intro else 0:-1]]
        print("      " + " → ".join(body[:14]) + (" …" if len(body) > 14 else ""))

        print(f"\n[4/4] 合成 …")
        N, g = len(seq), a.group
        # 先算总时长，尾部淡出要用
        total_est = sum(durs) - sum(xfs)
        tailstr = (f"fade=t=in:st=0:d=0.7,"
                   f"fade=t=out:st={max(0.1, total_est-1.2):.3f}:d=1.2")
        silent = os.path.join(tmp, "video.mp4")

        if g <= 0 or N <= g:
            # 单条链：每个接缝的转场和时长都精确，首选这条
            total = xfade_chain(seq, durs, [p[1] for p in picks], xfs,
                                silent, a.preset, tail=tailstr)
        else:
            # 分组：几十路 xfade 同时开着内存吃不消时用。
            # ★ 组边界那个接缝也要用它原本该用的转场和时长，不能另给一个默认值
            segs, segd = [], []
            for k, st in enumerate(range(0, N, g)):
                en = min(st + g, N)
                gc, gd = seq[st:en], durs[st:en]
                gt = [picks[j][1] for j in range(st, en - 1)]
                gx = [xfs[j] for j in range(st, en - 1)]
                sp = os.path.join(tmp, f"s{k:03d}.mp4")
                if len(gc) == 1:
                    shutil.copyfile(gc[0], sp)
                    d = gd[0]
                else:
                    d = xfade_chain(gc, gd, gt, gx, sp, a.preset)
                segs.append(sp)
                segd.append(d)
            bnd = [(k + 1) * g - 1 for k in range(len(segs) - 1)]
            total = xfade_chain(segs, segd, [picks[j][1] for j in bnd],
                                [xfs[j] for j in bnd], silent, a.preset,
                                tail=tailstr)

        if a.bgm:
            if not os.path.isfile(a.bgm):
                die(f"找不到背景音乐：{a.bgm}")
            run(["ffmpeg", "-y", "-i", silent, "-stream_loop", "-1", "-i", a.bgm,
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                 "-af", f"afade=t=in:d=1.2,afade=t=out:st={total-1.8:.3f}:d=1.8",
                 "-c:a", "aac", "-b:a", "160k", "-shortest",
                 "-movflags", "+faststart", a.out], quiet=False)
        else:
            run(["ffmpeg", "-y", "-i", silent, "-c", "copy",
                 "-movflags", "+faststart", a.out], quiet=False)
    finally:
        if a.keep_temp:
            print(f"临时文件保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n✅ 完成：{os.path.abspath(a.out)}  约 {probe_dur(a.out):.1f}s")


if __name__ == "__main__":
    main()