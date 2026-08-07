# 鸟类高光时刻视频流水线 -- 脚本化自动生成方案

> 1.目标：给定检测模型筛出的 N 张（约 10-20 张）高清鸟类照片，无人值守地渲染出一支类似参考片《高光时刻2》的成片。
> 2.本文包含：参考片逆向拆解 → 工具选型 → 两套可直接运行的脚本 → 沙箱实测数据 → 服务端工程化与踩坑清单。
> 3.所有实测数据都是在 2 核 CPU 容器里跑出来的，没有估算。
> 4.输出：运行 make_reel.py 加载自己的 photos & bgm 就可以流水线地生成模板视频了， out.mp4 就是一个示例。

---

## 一、先把参考片（产品定义的视频模板）拆开看

任何"做一个类似这样的视频"的需求，第一步都不是选工具，而是把参考片变成一份可执行的规格（本文的参考片示例为 “高光时刻2.mp4” ）。用 `ffprobe` 和 `ffmpeg` 的 `select` 滤镜就能做完。

### 1.1 容器与编码规格

```bash
ffprobe -v error -show_format -show_streams -of json 高光时刻2.mp4{填入你要使用的模板视频名}
```

| 项 | 值 |
|---|---|
| 容器 | MP4 (isom) |
| 时长 | 28.119 s |
| 分辨率 | 1280 × 720 |
| 视频 | H.264 Main / yuv420p / 30 fps / 843 帧 |
| 视频码率 | 7,495,645 bps |
| 总码率 | 7,688,029 bps |
| 音频 | AAC LC / 190,081 bps / 1212 帧 |
| 容器 tag | `encoder: Lavf61.1.100`、`Hw: 1`、`bitrate: 10000000`、`te_is_reencode: 1` |

`te_is_reencode: 1` 和 `bitrate: 10000000` 这两个非标准 tag 是剪映/CapCut 导出时写进去的，配合画面里残留的 `hibird`、`campojp` 第三方水印，可以确认：**参考片是剪映的"年度回顾"模板套在别人素材上导出的**。

这件事对我们有两层意义：一是模板本身有版权和素材来源风险，不能直接拿来复用；二是它的视觉语言完全可以从零复刻，因为拆开之后并没有什么魔法。

### 1.2 镜头切点

```bash
ffmpeg -i 高光时刻2.mp4 -filter:v "select='gt(scene,0.2)',showinfo" -f null - 2>&1 \
  | grep -o 'pts_time:[0-9.]*'
```

切点（秒）：

```
1.167  1.433  1.667      ← 数字快速跳变
8.667  8.700             ← 甩镜转场
12.467 12.533 12.867 13.200
15.867 16.200 16.533 16.867
19.533 19.867 20.200
22.533 22.867            ← 照片墙的四组，每组内部 3~4 个小切
```

切点是**成组出现**的：8.6s 一次强切，之后每隔 3.3~3.4 秒出现一簇 3~4 个密集切点。这正是"每张主图停留 3.4 秒，期间有几张缩略图飞进来"的signature。

### 1.3 还原出来的五幕结构

| 幕 | 时间 | 内容 |
|---|---|---|
| ① 数字开场 | 0 – 2.2 s | 巨大年份数字 2016→2018→2020→2023→2026 作为**文字遮罩**，画面从字形里透出来；黑底、白色描边 |
| ② 标题段 | 2.2 – 8.6 s | 满屏素材做慢速 Ken Burns + 红色手写体 "高光时刻" + 白色手绘箭头/爱心/闪光装饰依次弹入 |
| ③ 甩镜转场 | 8.6 – 9.2 s | 强纵向运动模糊 + 白闪，把观众从"全屏"甩到"照片墙" |
| ④ 照片墙 | 9.2 – 23 s | 3D 微倾的白框拍立得卡片；主卡居中带透视旋转，3~5 张缩略图从四边飞入落位；主卡停留约 1.5~2.5 s，共 4 组 |
| ⑤ 收尾落版 | 23 – 28 s | 卡片向中心缩走 → 红色品牌落版 + 装饰 → 淡出黑场 |

全程有 BGM。

**关键结论**：①③④⑤ 四幕都不是"视频剪辑"，而是**2D 图层动画**——遮罩、位移、缩放、旋转、透视、透明度。只有 ② 是纯粹的画面运动。这个判断直接决定了选型。

---

## 二、选型：什么软件组合最适合 "10-20 张照片 → 一支成片"

### 2.1 候选全景

| 工具                                         | 许可                 | 服务端可用 | 依赖体积                                    | 对本需求的致命点                                                                        |
|--------------------------------------------|--------------------|---|-----------------------------------------|---------------------------------------------------------------------------------|
| **ffmpeg 原生 filtergraph**                  | LGPL/GPL           | ✅ | 已有                                      | 表达力止步于 zoompan/xfade/overlay/drawtext；做不了逐帧透视卡片                                 |
| **GL Transitions + ffmpeg 编码**             | GL-License/MIT     | ✅ | git hub: gl-trasitions                  | 无需手动定义转场变换，已经定义好100+种转场                                                         |
| **Pillow 逐帧 + ffmpeg 编码**                  | PIL-License/MIT    | ✅ | +20 MB                                  | 帧生成是 Python 单线程，需要靠多进程分幕并行                                                      |
| **Remotion**                               | BSL（4 人以上公司收费）     | ✅ | 无头 Chromium ~400 MB                     | 云端/程序化渲染算 Automators：$0.01/渲染，**每月低消 $100**                                     |
| **Revideo**                                | MIT                | ✅ | Node + canvas                           | Motion Canvas 的 fork，自带 `renderVideo()` 和 REST；生态小（周下载量约 3K）                    |
| **Motion Canvas**                          | MIT                | ⚠️ | Node                                    | 没有 Node API、没有批量/REST，本来就是给人手动录屏用的                                              |
| **MoviePy**                                | MIT                | ✅ | pip 即可                                  | v2.2.1（2025-05）；逐帧 NumPy 合成，高清源图下是 CPU 成本最高的一档；v1→v2 破坏性改动导致网上示例基本失效            |
| **FFCreator**                              | MIT                | ⚠️ | node-canvas + headless-gl + Xvfb + Mesa | **停更**（v6.8.1，2023-02），锁死 EOL Node，Docker 体积最差                                  |
| **editly**                                 | MIT                | ✅ | headless-gl                             | 自带 Ken Burns（`zoomDirection`/`zoomAmount`），最贴合需求，但停在 v0.15.0-rc.1（2025-01）且单人维护 |
| **Blender VSE 无头**                         | GPL                | ✅ | ~367 MB 压缩包                             | 为 10 张静图上一个 1 GB 的 3D 引擎；VSE 编码单线程、慢且难并行                                        |
| **Shotstack / Creatomate / JSON2Video**    | 商业                 | ✅ | 零                                       | 最快上线，但**每支片子都有边际成本**，且必须把用户高清原图传给第三方                                            |
| **剪映 draft_content.json**（pyJianYingDraft） | Apache-2.0         | ❌ | 需 Windows + 桌面版                         | 生成的是**工程文件不是视频**；导出要 GUI 自动化；剪映 6+ 已加密 draft 文件                                 |
| **@diffusionstudio/core / Etro / Rendley** | MPL-2.0 / MIT / 商业 | ❌ | —                                       | 浏览器端引擎，官方明确不支持 Node 服务端渲染                                                       |

### 2.2 结论

**推荐组合：方案 A 打底 + 方案 B 做高级模板。**

- **方案 A（纯 ffmpeg 或 ffmpeg + gl-transitions）** 负责 80% 的常规用户：一条 `filter_complex` 命令，零额外依赖，成本最低，很多容器里本来就有 ffmpeg。能做到 Ken Burns + 转场 + 标题 + 落版，观感已经是"像样的回顾视频"，gl-transitions则有更多预设好的转场方式，无需手动写代码。
- **方案 B（Pillow 图层 + ffmpeg 编码）** 负责真正复刻参考片那种质感：文字遮罩、3D 倾斜卡片墙、手绘装饰、甩镜。依赖只多一个 Pillow，没有浏览器、没有 GPU、没有原生 GL —— 这是它相对 Remotion/FFCreator/editly 的决定性优势，Docker 镜像不会因为它膨胀。
- **不推荐**：FFCreator（已死）、MoviePy（同样是 Python 逐帧，但比自己写 Pillow 更慢且 API 不稳）、Blender（杀鸡用牛刀）、剪映 draft（根本不是渲染后端，只能作为"给用户一个可继续编辑的草稿"这种独立功能）。
- **云 API** 只在一种情况下值得：产品要在两周内上线且量不大。按 Shotstack 最优价 $0.20/分钟算，一支 15 秒片子约 $0.05，一千个用户各出一支就是 $50——量一起来这条线永远压在毛利上。

**如果未来要自己迭代模板**，再引入 Revideo（MIT，自带 Node 渲染 API），不要引入 Remotion——BSL 的商用条款对硬件公司不友好。

---

## 三、方案 A：一条 ffmpeg 命令搞定

### 3.1 完整脚本(bash部分不支持，推荐 make_reel.py)

```python
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
```

### 3.2 实测（2 核容器 / 10 张 1920×1080 源图 / 输出 1280×720 30fps 23.6s）

| preset | 墙钟时间 | 实时倍率 |
|---|---|---|
| `medium` (crf 20) | **40.8 s** | 0.58× |
| `veryfast` | **26.0 s** | 0.91× |

2 核就能做到接近实时。生产环境 8 核跑 `veryfast` + `-threads` 调优，一支 25 秒的片子在 10 秒内出片是可以预期的。

---

## 四、方案 B：Pillow 图层 + ffmpeg 编码，完整复刻五幕

思路很简单：**Python 只负责"画"，ffmpeg 只负责"编码"**。Python 用 Pillow 逐帧合成 RGB 图像，通过管道以 rawvideo 喂给 ffmpeg。没有中间文件、没有浏览器、没有 GPU。

```python
cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
       "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
       "-c:v", "libx264", "-preset", "medium", "-crf", "19",
       "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
for frame in all_frames():
    pr.stdin.write(frame.convert("RGB").tobytes())
pr.stdin.close(); pr.wait()
```

每一幕就是一个 generator，主流程把它们串起来。完整代码见附件 `reel_engine.py`，这里只讲四个关键技术点。

### 4.1 数字开场：文字当遮罩

把年份画成一张 `L` 模式的字形图，直接当 `paste` 的 mask，画面就从字里透出来了。

```python
gly  = text_layer(YEARS[k], F(FONT_BLACK, 300), (255,255,255,255))
mask = Image.new("L", (W, H), 0)
mask.paste(gly.getchannel("A"), (W//2 - gly.width//2, H//2 - gly.height//2))
base = Image.new("RGB", (W, H), BG)          # 黑底
base.paste(photo_kenburns, (0, 0), mask)      # 只有字形区域被填上画面
# 再叠一层纯描边（fill 全透明 + stroke_width）做白边
outline = text_layer(YEARS[k], fnt, (0,0,0,0), stroke=4, stroke_fill=(255,255,255,235))
```

同样的效果在纯 ffmpeg 里要靠 `alphamerge` + 外部生成的遮罩序列才能做，得不偿失。

### 4.2 3D 倾斜卡片：透视变换

参考片里的卡片不是简单旋转，是**一侧被压扁**的透视。Pillow 的 `Image.transform(..., Image.PERSPECTIVE, coeffs)` 能做，但需要自己解 8 个系数：

```python
def perspective(im, tilt=0.10):
    w, h = im.size; d = tilt * h
    dst = [(0,0), (w,d), (w,h-d), (0,h)]     # 右边上下各内收 d
    src = [(0,0), (w,0), (w,h),   (0,h)]
    A, B = [], []
    for (x,y), (u,v) in zip(dst, src):
        A += [[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]]
        B += [u, v]
    co = np.linalg.solve(np.array(A,float), np.array(B,float))
    return im.transform((w,h), Image.PERSPECTIVE, co, Image.BICUBIC)
```

注意 PIL 要的是**目标→源的逆变换**，所以上面 `dst` 和 `src` 的位置是反着填的。填正了的症状是卡片往反方向倾斜。

卡片本体是"照片 → 白边（下边留宽，像拍立得）→ 透视 → 旋转 → 高斯模糊投影"，投影用一张纯黑 RGBA 以卡片自身为 mask 贴上去再 `GaussianBlur`。

### 4.3 甩镜转场：纵向运动模糊 + 白闪

用 NumPy 把画面沿 y 轴多次位移叠加，强度按 `sin(πt)` 走一个来回，同时整体加亮：

```python
arr = np.asarray(frame, np.float32); acc = np.zeros_like(arr); K = 9
amp = int(math.sin(t*math.pi) * 150)
for k in range(K):
    acc += np.roll(arr, round((k/(K-1) - 0.5) * 2 * amp), axis=0)
arr = acc / K + math.sin(t*math.pi) * 210     # 白闪
```

15 帧（0.5 秒）就够，参考片里也就 0.5 秒。

### 4.4 缓动函数决定"贵不贵"

这一步最容易被忽略，但它是"像模板"和"像 PPT 切页"的分水岭。全部用线性插值的动画一定廉价。至少要三条曲线：

```python
def ease_out(t):    return 1 - (1-t)**3                    # 飞入、淡入
def ease_in_out(t): return 3*t*t - 2*t*t*t                 # Ken Burns、整体缩放
def ease_back(t):   s = 1.70158                            # 卡片落位的轻微回弹
                    return 1 + (s+1)*(t-1)**3 + s*(t-1)**2
```

缩略图飞入用 `ease_out` 且**每张错开 2 帧**出发，主卡用 `ease_back` 落位并轻微超调，整组卡片再叠一个 `sin` 的缓慢漂移——参考片的"活"就是这三样加出来的。

### 4.5 实测（同一台 2 核容器 / 10 张源图 / 输出 1280×720 30fps 26.5s / 795 帧）

| 幕 | 帧数 | 帧生成耗时 | 单帧 |
|---|---|---|---|
| ① 数字开场 | 78 | 6.7 s | 86.2 ms |
| ② 标题段 | 180 | 11.7 s | 65.2 ms |
| ③ 甩镜转场 | 15 | 1.1 s | 75.3 ms |
| ④ 照片墙 | 384 | 47.4 s | 123.3 ms |
| ⑤ 收尾落版 | 138 | 1.9 s | 13.9 ms |
| **合计（含编码）** | **795** | **约 102 s** | — |

照片墙最贵，因为每帧都要重新做一次背景大虚化。**这是可以直接优化掉的**：背景虚化图在一个 hero 内是不变的，缓存后这一幕能降到 40 ms/帧以下，整片压到 40 秒左右。

再往下的加速手段，按性价比排序：

1. **缓存不变图层**（背景虚化、卡片本体）—— 最划算，代码里 `_card_cache` 已经做了卡片这一半。
2. **分幕多进程并行**：五幕互不依赖，各自渲成一段 MP4 再 `concat` demuxer 拼接。8 核机器上接近线性加速。
3. **降低中间精度**：`Image.LANCZOS` 换成 `BILINEAR` 用在缩略图上（主卡保留 LANCZOS），单帧能省 20~30%。
4. 真的还不够，再考虑 `pyvips` 替代 Pillow。

---

## 五、服务端工程化

### 五步流水线

```
检测模型打分  →  选图策略  →  预处理  →  渲染  →  分发
   (已有)       (产品要写)    (裁剪)   (plan A或B)   (CDN)
               (程序已有一版)         
```

### 5.1 选图策略：这一步比渲染重要

10 张照片决定了片子好不好看，渲染只决定它糙不糙。建议的排序信号（可直接复用行为清单里的字段）：

- **画质分**：清晰度（拉普拉斯方差）、主体框面积占比、是否过曝/欠曝。低于阈值直接淘汰，不要指望后期救。
- **行为分**：优先取行为清单里"上传吸引力=高/极高"的片段截帧——争食打架、亲鸟喂幼、洗澡戏水、起飞降落。这几类天然比"站着不动"好看。
- **多样性约束**：这是最容易做错的地方。纯按分数取 Top-10，结果往往是同一只北美红雀在同一个角度出现 8 次。必须加硬约束：**同一物种最多 3 张、同一天最多 2 张**，然后在满足约束的前提下取最高分。
- **叙事顺序**：不要按分数排序播放。按时间排序（春→夏→秋→冬）能让片子自带故事线，这也是"年度回顾"这个形式成立的前提。开场那张和落版前那张单独挑最强的两张。

### 5.2 预处理

- 统一裁到 **16:9 且主体居中**：用检测框中心作为 `fill_crop` 的 `cx/cy`，而不是图片几何中心。鸟站在画面右下角的时候，几何中心裁切会把鸟裁掉一半。
- 长边限制到输出的 2 倍（1280×720 输出就限到 2560）。再大对画质没有贡献，只增加内存和 IO。
- EXIF 方向必须先 `ImageOps.exif_transpose()` 修正，否则竖拍照片会横过来。

### 5.3 BGM

方案 A 和 B 都留了 BGM 参数。要点：

- **必须用可商用授权的曲库**，且要在成片元数据或 App 内做署名（很多 CC 授权要求署名）。这是产品上线前的法务必查项。
- 音频要 `afade` 淡入淡出，直接切进切出很刺耳。
- 用 `-shortest` 让音频跟随视频长度；如果 BGM 比视频短，先 `-stream_loop -1` 循环。
- 进阶：把镜头切点对到 BGM 的节拍上。用 `librosa.beat.beat_track()` 拿到 beat 时间戳，再让 `xfade` 的 `offset` 吸附到最近的 beat。这一步的观感提升非常明显，而且不贵。

### 5.4 并发与成本

- 渲染是**纯 CPU、无状态、可失败重试**的任务，天然适合队列 + worker 池。用户触发后异步生成，完成推送。
- 建议**限流而不是限时**：一个用户一天最多生成 N 支，避免被刷。
- 输出建议同时产出 **720p（分享用）+ 竖版 1080×1920（社媒用）** 两个版本。竖版不是简单裁切——方案 B 的图层布局改几个百分比坐标就能重排，这正是自己写渲染器相对云 API 的价值。
- 加缓存键：`hash(照片ID列表 + 模板版本 + 文案)`。用户重复点"生成"不该重复烧 CPU。

### 5.5 字体与文案

- 容器里必须显式装中文字体，不要依赖系统默认。本文用的是 `NotoSansCJK-Black.ttc`（正文/装饰）和 `NotoSerifCJK-Black.ttc`（标题）。
- `drawtext` 里的文案如果来自用户输入（比如鸟的昵称），**必须转义** `:` `'` `\` `%`，否则会破坏 filtergraph 语法，甚至成为注入点。更稳妥的做法是走 `textfile=` 参数从文件读。
- 参考片的红色手写体在开源字体里没有等价物。如果要那个味道，得单独采购一款可商用的中文手写/毛笔字体——这是唯一一处"钱能解决但代码不能"的地方。

---

## 六、踩坑清单(来自社区)

| 坑 | 症状 | 解法 |
|---|---|---|
| `zoompan` 不写 `fps` | 时长变长、转场错位 | 显式 `fps=30` |
| `zoompan` 直接对原图缩放 | 画面一格一格跳 | 先 `scale` 到输出 2 倍再进 `zoompan` |
| `xfade` 的 offset 用了相对值 | 后半段黑屏 | offset = `(DUR-XF) * i` 绝对时间 |
| 两路 SAR 不一致 | `xfade` 直接报错 | 每路都加 `setsar=1` |
| 输出尺寸有奇数边 | yuv420p 编码失败 | `crop=2*floor(iw/2):2*floor(ih/2)` |
| 静态图没给 `-t` | 第一帧就 EOF | `-loop 1 -t DUR` |
| PIL 透视系数填反 | 卡片往反方向倾斜 | `transform` 要的是目标→源的**逆**变换 |
| 逐帧重算不变图层 | 帧生成慢 3 倍 | 背景虚化、卡片本体一律缓存 |
| 全线性缓动 | 观感像 PPT 切页 | 至少上 `ease_out` / `ease_in_out` / `ease_back` |
| 按分数取 Top-10 | 同一只鸟出现 8 次 | 加物种/日期多样性硬约束 |
| 用几何中心裁切 | 鸟被裁掉一半 | 用检测框中心作为裁切中心 |
| 忘记 `exif_transpose` | 竖拍照片横过来 | 预处理第一步就修正 |
| 用户文案直接进 `drawtext` | filtergraph 语法炸掉 | 转义或走 `textfile=` |
| 想直接复用剪映模板 | 素材版权 + 模板版权双重风险 | 从零复刻视觉语言，不碰模板文件 |

---

## 附件

- `make_reel.sh` or `make_reel.py` —— 方案 A，纯 ffmpeg，可直接运行
- `reel_engine.py` —— 方案 B，五幕引擎，可直接运行
- `out.mp4` / `out_engine.mp4` —— 两套脚本在 10+ 张真实喂鸟器截图上的实际输出

---

## 参考资料

- [MoviePy · PyPI](https://pypi.org/project/moviepy/)
- [MoviePy v1 → v2 迁移说明](https://zulko.github.io/moviepy/getting_started/updating_to_v2.html)
- [FFCreator · GitHub](https://github.com/tnfe/FFCreator)
- [headless-gl · stackgl](https://github.com/stackgl/headless-gl)
- [editly · GitHub](https://github.com/mifi/editly)
- [Blender 下载与版本](https://www.blender.org/download/)
- [Blender VSE 图像片段文档](https://docs.blender.org/manual/en/latest/video_editing/edit/montage/strips/image.html)
- [Shotstack 定价](https://shotstack.io/pricing/)
- [Creatomate 定价说明](https://creatomate.com/docs/account/how-does-the-pricing-work)
- [JSON2Video 定价](https://json2video.com/pricing/)
- [2026 年视频 API 横评](https://renderly.video/blog/best-video-apis-for-developers-compared)
- [pyJianYingDraft · GitHub](https://github.com/GuanYixuan/pyJianYingDraft)
- [pyCapCut · GitHub](https://github.com/GuanYixuan/pyCapCut)
- [@diffusionstudio/core · GitHub](https://github.com/diffusionstudio/core)
- [Etro · GitHub](https://github.com/etro-js/etro)
- [Rendley SDK](https://rendley.com/products/sdk)
