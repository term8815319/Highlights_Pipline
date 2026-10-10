#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SiFar 鸟类高光时刻 · 方案A（Python 版，Windows / macOS / Linux 通用）

v2 改动（解决 50 张图 Segmentation fault）：
  · 分段渲染：不再把 N 路 zoompan 同时塞进一个 ffmpeg。
      第一步  每张图单独渲成一小段 mp4（内存只等于 1 路）
      第二步  分组做 xfade 串联，组数多时再串一次
    内存峰值只跟「每组几张」有关，跟总张数无关。
  · --limit    从一堆图里均匀抽 N 张（默认 12，高光片不该超过 15 张）
  · --super    超采样倍率（默认 1.75，越小越省内存）
  · --no-text  跳过所有 drawtext，字体出问题时先出片
  · --jobs     第一步并行渲染，多核机器明显更快

v2 改动：
  · 去掉 -filter_complex_script（该选项在 ffmpeg 8.0 已移除，新版会报
    Unrecognized option）。改成直接用 -filter_complex，subprocess 列表传参
    不经过 shell，长滤镜串本来就不需要走文件。
  · 退出码按有符号数打印，不再出现 2880417800 这种 Windows 无符号数

用法：
  python make_reel.py bird_images out.mp4 bgm/Lupinus_H.ogg
  python make_reel.py bird_images out.mp4 --limit 0          # 全部用上
  python make_reel.py bird_images out.mp4 --no-text          # 不画字
  python make_reel.py bird_images out.mp4 --vertical         # 1080x1920 竖版
  python make_reel.py --help                                 # 帮助
"""
import argparse, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

FPS  = 30
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",   r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
TRANS = ["fade", "wipeleft", "smoothup", "circleopen", "fadewhite",
         "slideleft", "dissolve", "wiperight", "fadeblack"]


# ───────────────────── 小工具 ─────────────────────
def die(msg): sys.exit(f"\n❌ {msg}")

def find_font(user):
    if user:
        if not os.path.isfile(user): die(f"指定的字体不存在：{user}")
        return user
    for f in FONT_CANDIDATES:
        if os.path.isfile(f): return f
    return None

def esc_font(p):
    """drawtext 的 fontfile：反斜杠→正斜杠，盘符冒号要转义"""
    return os.path.abspath(p).replace("\\", "/").replace(":", r"\:")

def esc_text(s):
    for a, b in (("\\", r"\\"), (":", r"\:"), ("'", r"\'"), ("%", r"\%")):
        s = s.replace(a, b)
    return s

def run(cmd, graph=None, quiet=True):
    """
    graph 直接用 -filter_complex 传。
    注意：不要用 -filter_complex_script —— 该选项在 ffmpeg 8.0 已被移除。
    Python 的 subprocess 用列表传参不经过 shell，滤镜串再长、再多引号也不会被解析，
    所以完全没必要绕道临时文件。
    """
    if graph is not None:
        cmd = cmd + ["-filter_complex", graph]
    cmd = cmd + (["-hide_banner", "-loglevel", "error"] if quiet
                 else ["-hide_banner", "-loglevel", "warning", "-stats"])
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL if quiet else None)
    if r.returncode != 0:
        code = r.returncode if r.returncode < 2**31 else r.returncode - 2**32
        die(f"ffmpeg 退出码 {code}（上面是它的报错）")


def ffmpeg_version():
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                             text=True, timeout=15).stdout
        m = re.search(r"ffmpeg version (\S+)", out)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def sample(items, k):
    """从 items 里均匀抽 k 个，保证首尾都在、时间跨度铺满"""
    n = len(items)
    if k <= 0 or k >= n: return items
    if k == 1: return [items[0]]
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


# ───────────── 第一步：每张图单独渲一小段 ─────────────
def render_clip(pic, idx, out, dur, w, h, sup, preset):
    sw, sh = int(w * sup) // 2 * 2, int(h * sup) // 2 * 2
    if idx % 2 == 0:                                    # 偶数张：慢慢推近
        z = "min(zoom+0.0009,1.22)"
        x = "iw/2-(iw/zoom/2)"
    else:                                               # 奇数张：拉远 + 轻微横移
        z = "if(eq(on,0),1.22,max(zoom-0.0009,1.0))"
        x = "iw/2-(iw/zoom/2)+(on-45)*0.6"
    graph = (f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
             f"crop={sw}:{sh},setsar=1,"
             f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':"
             f"d={int(round(dur*FPS))}:s={w}x{h}:fps={FPS},"
             f"format=yuv420p,trim=duration={dur},setpts=PTS-STARTPTS[vout]")
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", pic,
         "-map", "[vout]", "-c:v", "libx264", "-preset", preset, "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(FPS), out], graph=graph)
    return out


# ───────────── 第二步：一组片段做 xfade 串联 ─────────────
def xfade_group(clips, durs, out, xf, preset, tail=None):
    """clips: mp4 路径列表；durs: 各自时长。tail: 追加到末尾的滤镜串（字幕/淡入淡出）"""
    n = len(clips)
    if n == 1 and tail is None:
        shutil.copyfile(clips[0], out); return durs[0]
    fc, prev, acc = [], None, 0.0
    for i in range(n):
        fc.append(f"[{i}:v]setsar=1,format=yuv420p[c{i}];")
    prev, total = "c0", durs[0]
    for i in range(1, n):
        off = total - xf
        fc.append(f"[{prev}][c{i}]xfade=transition={TRANS[(i-1)%len(TRANS)]}:"
                  f"duration={xf}:offset={off:.3f}[x{i}];")
        prev = f"x{i}"
        total = off + durs[i]
    fc.append(f"[{prev}]{tail}[vout]" if tail else f"[{prev}]null[vout]")
    cmd = ["ffmpeg", "-y"]
    for c in clips: cmd += ["-i", c]
    cmd += ["-map", "[vout]", "-c:v", "libx264", "-preset", preset, "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.3f}", out]
    run(cmd, graph="".join(fc))
    return total


# ───────────────────── 主流程 ─────────────────────
def main():
    ap = argparse.ArgumentParser(description="一堆鸟类照片 → 一支高光时刻视频")
    ap.add_argument("photos", nargs="?", default="photos")
    ap.add_argument("out",    nargs="?", default="out.mp4")
    ap.add_argument("bgm",    nargs="?", default=None)
    ap.add_argument("--limit", type=int,   default=12, help="用几张图，0=全部，默认 12")
    ap.add_argument("--dur",   type=float, default=3.0, help="每张停留秒数")
    ap.add_argument("--xf",    type=float, default=0.7, help="转场秒数")
    ap.add_argument("--super", dest="sup", type=float, default=1.75,
                    help="超采样倍率，越小越省内存，默认 1.75")
    ap.add_argument("--group", type=int,   default=6,  help="每组几段，默认 6")
    ap.add_argument("--jobs",  type=int,   default=0,  help="并行数，0=自动")
    ap.add_argument("--vertical", action="store_true", help="1080x1920 竖版")
    ap.add_argument("--no-text",  action="store_true", help="跳过所有文字")
    ap.add_argument("--title",   default="SiFar 高光时刻")
    ap.add_argument("--sub",     default="2026 · 你的后院访客")
    ap.add_argument("--endcard", default="SiFar 2026")
    ap.add_argument("--font",    default=None)
    ap.add_argument("--preset",  default="veryfast")
    ap.add_argument("--keep-temp", action="store_true")
    a = ap.parse_args()

    w, h = (1080, 1920) if a.vertical else (1280, 720)

    if not shutil.which("ffmpeg"):
        die("找不到 ffmpeg。Windows: winget install ffmpeg（装完要重开终端）")
    if not os.path.isdir(a.photos):
        die(f"找不到照片文件夹：{os.path.abspath(a.photos)}")

    allpics = sorted(os.path.join(a.photos, f) for f in os.listdir(a.photos)
                     if f.lower().endswith(EXTS))
    if len(allpics) < 2:
        die(f"{a.photos} 里至少要 2 张图，现在只有 {len(allpics)} 张")
    pics = sample(allpics, a.limit)
    n = len(pics)

    font = None if a.no_text else find_font(a.font)
    if not a.no_text and not font:
        print("⚠️  没找到中文字体，自动切到 --no-text 模式（先出片，字以后再加）")
        a.no_text = True

    dur, xf, step = a.dur, a.xf, a.dur - a.xf
    total = step * (n - 1) + dur
    jobs  = a.jobs or min(4, os.cpu_count() or 2)

    print(f"ffmpeg {ffmpeg_version()}")
    print(f"照片   {len(allpics)} 张 → 用 {n} 张")
    if font: print(f"字体   {font}")
    print(f"输出   {a.out}   {w}x{h}   约 {total:.1f}s   并行 {jobs}")

    tmp = tempfile.mkdtemp(prefix="reel_")
    try:
        # ── 第一步：逐张渲染（内存只等于 1 路） ──
        print(f"\n[1/3] 渲染 {n} 个片段 …")
        def job(t):
            i, p = t
            r = render_clip(p, i, os.path.join(tmp, f"c{i:03d}.mp4"),
                            dur, w, h, a.sup, a.preset)
            print(f"      {i+1:>3}/{n}  {os.path.basename(p)}")
            return r
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            clips = list(ex.map(job, enumerate(pics)))

        # ── 第二步：分组 xfade ──
        groups = [clips[i:i+a.group] for i in range(0, len(clips), a.group)]
        print(f"\n[2/3] 分 {len(groups)} 组做转场 …")
        segs, segdurs = [], []
        for gi, g in enumerate(groups):
            sp = os.path.join(tmp, f"s{gi:03d}.mp4")
            d  = xfade_group(g, [dur]*len(g), sp, xf, a.preset)
            segs.append(sp); segdurs.append(d)
            print(f"      组 {gi+1}/{len(groups)}  {len(g)} 段  {d:.1f}s")

        # ── 第三步：拼接 + 字幕 + 首尾淡入淡出 ──
        print(f"\n[3/3] 合成成片 …")
        tail = []
        if not a.no_text:
            fe = esc_font(font)
            ei = total - 2.2
            tail.append(
                f"drawtext=fontfile='{fe}':text='{esc_text(a.title)}':fontcolor=white:"
                f"fontsize={int(h*0.089)}:borderw=3:bordercolor=black@0.45:"
                f"x=(w-tw)/2:y=h*0.36:"
                f"alpha='if(lt(t,0.4),t/0.4,if(lt(t,2.4),1,if(lt(t,2.9),(2.9-t)/0.5,0)))'")
            tail.append(
                f"drawtext=fontfile='{fe}':text='{esc_text(a.sub)}':fontcolor=white:"
                f"fontsize={int(h*0.042)}:borderw=2:bordercolor=black@0.45:"
                f"x=(w-tw)/2:y=h*0.36+{int(h*0.12)}:"
                f"alpha='if(lt(t,0.7),max(0,(t-0.3)/0.4),if(lt(t,2.4),1,if(lt(t,2.9),(2.9-t)/0.5,0)))'")
            tail.append(
                f"drawtext=fontfile='{fe}':text='{esc_text(a.endcard)}':fontcolor=white:"
                f"fontsize={int(h*0.078)}:borderw=3:bordercolor=black@0.45:"
                f"x=(w-tw)/2:y=(h-th)/2:"
                f"alpha='if(lt(t,{ei:.3f}),0,min(1,(t-{ei:.3f})/0.5))'")
        tail.append("fade=t=in:st=0:d=0.6")
        tail.append(f"fade=t=out:st={total-1.0:.3f}:d=1.0")
        tailstr = ",".join(tail)

        if a.bgm:
            if not os.path.isfile(a.bgm):
                die(f"找不到背景音乐：{os.path.abspath(a.bgm)}")
            silent = os.path.join(tmp, "video.mp4")
            xfade_group(segs, segdurs, silent, xf, a.preset, tail=tailstr)
            run(["ffmpeg", "-y", "-i", silent, "-stream_loop", "-1", "-i", a.bgm,
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                 "-af", f"afade=t=in:d=1,afade=t=out:st={total-1.5:.3f}:d=1.5",
                 "-c:a", "aac", "-b:a", "160k", "-shortest",
                 "-movflags", "+faststart", a.out], quiet=False)
        else:
            xfade_group(segs, segdurs, os.path.join(tmp, "video.mp4"),
                        xf, a.preset, tail=tailstr)
            run(["ffmpeg", "-y", "-i", os.path.join(tmp, "video.mp4"),
                 "-c", "copy", "-movflags", "+faststart", a.out], quiet=False)
    finally:
        if a.keep_temp: print(f"临时文件保留在 {tmp}")
        else: shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n✅ 完成：{os.path.abspath(a.out)}")


if __name__ == "__main__":
    main()