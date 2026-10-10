#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方案 B：Pillow 逐帧合成图层 + ffmpeg 编码 v1 版本
复刻参考片《高光时刻》的五幕结构：
  1 数字开场（年份字形当遮罩，画面从字里透出来）
  2 标题段（Ken Burns + 主标题 + 手绘装饰）
  3 甩镜转场（纵向运动模糊 + 白闪）
  4 照片墙（3D 倾斜白框卡片 + 缩略图飞入）
  5 收尾落版（卡片缩走 + 红色品牌字 + 淡出）

用法：
  python3 reel_engine.py photos/ out_engine.mp4 [bgm.mp3]

依赖：pillow numpy ffmpeg —— 没有浏览器、没有 GPU、没有原生 GL。
"""
import os, sys, math, glob, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ───────────────────────── 全局参数 ─────────────────────────
W, H, FPS = 1280, 720, 30
BRAND      = "SiFar"
TITLE_CN   = "高光时刻"
YEARS      = ["2016", "2020", "2023", "2026"]
RED        = (216, 42, 48)
BG         = (10, 10, 12)

FONT_BLACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Black.ttc"
def F(path, size):   return ImageFont.truetype(path, size)

# ───────────────────────── 缓动函数 ─────────────────────────
def clamp(v, a=0.0, b=1.0): return max(a, min(b, v))
def ease_out(t):     t = clamp(t); return 1 - (1 - t) ** 3
def ease_in_out(t):  t = clamp(t); return 3*t*t - 2*t*t*t
def ease_back(t):    # 轻微回弹，卡片落位用
    t = clamp(t); s = 1.70158
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2

# ───────────────────────── 图像工具 ─────────────────────────
def load(p):
    im = Image.open(p).convert("RGB")
    # 统一到 2 倍工作分辨率，既省内存又保证放大时不糊
    if im.width > W * 2:
        im = im.resize((W * 2, round(im.height * W * 2 / im.width)), Image.LANCZOS)
    return im

def fill_crop(im, w, h, zoom=1.0, cx=0.5, cy=0.5):
    """等比填充裁切 + 中心点偏移 + 缩放（Ken Burns 的单帧实现）"""
    sw, sh = im.size
    scale = max(w / sw, h / sh) * zoom
    nw, nh = math.ceil(sw * scale), math.ceil(sh * scale)
    im2 = im.resize((nw, nh), Image.LANCZOS)
    x = clamp(cx * nw - w / 2, 0, max(0, nw - w))
    y = clamp(cy * nh - h / 2, 0, max(0, nh - h))
    return im2.crop((round(x), round(y), round(x) + w, round(y) + h))

def perspective(im, tilt=0.10, roll=0.0):
    """把 RGBA 图做一个轻微的 3D 侧倾（左右边高度不同），模拟参考片里的卡片透视"""
    w, h = im.size
    d = tilt * h
    if tilt >= 0:   # 右边压扁
        dst = [(0, 0), (w, d), (w, h - d), (0, h)]
    else:
        dst = [(0, -d), (w, 0), (w, h), (0, h + d)]
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A += [[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]]
        B += [u, v]
    co = np.linalg.solve(np.array(A, float), np.array(B, float))
    out = im.transform((w, h), Image.PERSPECTIVE, co, Image.BICUBIC)
    if roll:
        out = out.rotate(roll, resample=Image.BICUBIC, expand=True)
    return out

_card_cache = {}
def card(im, w, h, border=14, tilt=0.0, roll=0.0, shadow=26):
    """白框拍立得卡片：照片 → 白边 → 投影 → 透视/旋转，返回 RGBA"""
    key = (id(im), w, h, border, round(tilt, 3), round(roll, 2), shadow)
    if key in _card_cache: return _card_cache[key]
    ph = fill_crop(im, w, h)
    base = Image.new("RGBA", (w + border*2, h + border*2 + border*2), (255, 255, 255, 255))
    base.paste(ph, (border, border))                      # 下边留宽一点，像拍立得
    base = perspective(base, tilt) if tilt else base
    if roll: base = base.rotate(roll, resample=Image.BICUBIC, expand=True)
    pad = shadow * 2
    out = Image.new("RGBA", (base.width + pad, base.height + pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", base.size, (0, 0, 0, 120)), (pad//2, pad//2 + 6), base)
    sh = sh.filter(ImageFilter.GaussianBlur(shadow / 2.2))
    out = Image.alpha_composite(out, sh)
    out.paste(base, (pad//2, pad//2), base)
    if len(_card_cache) < 400: _card_cache[key] = out
    return out

def paste_c(base, layer, cx, cy, scale=1.0, alpha=1.0):
    """按中心点贴 RGBA 图层，支持整体缩放与透明度"""
    if scale <= 0 or alpha <= 0: return
    if abs(scale - 1) > 1e-3:
        layer = layer.resize((max(1, round(layer.width*scale)),
                              max(1, round(layer.height*scale))), Image.LANCZOS)
    if alpha < 1:
        a = layer.getchannel("A").point(lambda v: round(v * alpha))
        layer = layer.copy(); layer.putalpha(a)
    base.alpha_composite(layer, (round(cx - layer.width/2), round(cy - layer.height/2)))

def text_layer(txt, font, fill, stroke=0, stroke_fill=(255,255,255,255)):
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = d0.textbbox((0, 0), txt, font=font, stroke_width=stroke)
    im = Image.new("RGBA", (x1-x0+8, y1-y0+8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4-x0, 4-y0), txt, font=font, fill=fill,
                            stroke_width=stroke, stroke_fill=stroke_fill)
    return im

# ───────────────────────── 手绘装饰 ─────────────────────────
def deco_sparkle(r=26, wdt=7, color=(255,255,255,255)):
    """四角星：长轴到 r，腰部收到 r*0.18，做出剪映常见的那种尖角闪光"""
    s = round(r*2.4); im = Image.new("RGBA",(s,s),(0,0,0,0)); d = ImageDraw.Draw(im)
    c = s/2; w = r*0.20
    pts = []
    for k in range(4):
        a = math.radians(90*k)
        pts.append((c + math.cos(a)*r,        c + math.sin(a)*r))
        pts.append((c + math.cos(a+math.pi/4)*w, c + math.sin(a+math.pi/4)*w))
    d.polygon(pts, fill=color)
    return im

def deco_arrow(w=190, h=110, color=(255,255,255,255), wdt=8):
    im = Image.new("RGBA",(w,h),(0,0,0,0)); d = ImageDraw.Draw(im)
    pts = [(8, h-12)]
    for i in range(1, 41):
        t = i/40; pts.append((8+(w-40)*t, (h-12) - (h-40)*math.sin(t*1.35)))
    d.line(pts, fill=color, width=wdt, joint="curve")
    ex, ey = pts[-1]; px, py = pts[-6]
    ang = math.atan2(ey-py, ex-px)
    for s in (2.5, -2.5):
        d.line([ex, ey, ex-28*math.cos(ang+s*0.35+0), ey-28*math.sin(ang+s*0.35)],
               fill=color, width=wdt)
    return im

def deco_heart(r=34, color=(255,255,255,255), wdt=7):
    s = r*3; im = Image.new("RGBA",(s,s),(0,0,0,0)); d = ImageDraw.Draw(im)
    pts = []
    for i in range(101):
        t = i/100*2*math.pi
        x = 16*math.sin(t)**3
        y = -(13*math.cos(t)-5*math.cos(2*t)-2*math.cos(3*t)-math.cos(4*t))
        pts.append((s/2 + x*r/18, s/2 + y*r/18))
    d.line(pts, fill=color, width=wdt, joint="curve")
    return im

# ═════════════════════ 第 1 幕：数字开场 ═════════════════════
def act_intro(pics, dur=2.6):
    n = round(dur * FPS); per = n // len(YEARS)
    fnt = F(FONT_BLACK, 300)
    for i in range(n):
        k    = min(i // per, len(YEARS)-1)
        loc  = (i - k*per) / per                     # 该数字内部的进度
        base = Image.new("RGB", (W, H), BG)
        photo = fill_crop(load_cached(pics[k % len(pics)]), W, H,
                          zoom=1.12 + 0.06*loc, cx=0.5, cy=0.45)
        gly = text_layer(YEARS[k], fnt, (255,255,255,255))
        m   = Image.new("L", (W, H), 0)
        sc  = 1.0 + 0.05*ease_out(loc)               # 字形本身也在微微放大
        g2  = gly.resize((round(gly.width*sc), round(gly.height*sc)), Image.LANCZOS)
        m.paste(g2.getchannel("A"), (round(W/2-g2.width/2), round(H/2-g2.height/2)))
        base.paste(photo, (0, 0), m)
        # 字形白色描边
        out = base.convert("RGBA")
        ol  = text_layer(YEARS[k], fnt, (0,0,0,0), stroke=4, stroke_fill=(255,255,255,235))
        ol  = ol.resize((round(ol.width*sc), round(ol.height*sc)), Image.LANCZOS)
        paste_c(out, ol, W/2, H/2, alpha=0.85)
        if i > n-8: out = fade_to(out, BG, (i-(n-8))/8)
        yield out

# ═════════════════════ 第 2 幕：标题段 ═════════════════════
def act_title(pics, dur=6.0):
    n = round(dur * FPS)
    hero = load_cached(pics[0])
    f_en = F(FONT_SERIF, 92); f_cn = F(FONT_SERIF, 92); f_sub = F(FONT_BLACK, 30)
    t_en = text_layer(BRAND,    f_en, (*RED, 255), stroke=3, stroke_fill=(255,255,255,255))
    t_cn = text_layer(TITLE_CN, f_cn, (255,255,255,255), stroke=3, stroke_fill=(0,0,0,90))
    t_sb = text_layer("2026 年 · 你的后院访客", f_sub, (255,255,255,235), stroke=2, stroke_fill=(0,0,0,110))
    spk, arw, hrt = deco_sparkle(), deco_arrow(), deco_heart()
    for i in range(n):
        t  = i / n
        bg = fill_crop(hero, W, H, zoom=1.20 - 0.12*ease_in_out(t), cx=0.5, cy=0.48).convert("RGBA")
        bg.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 46)))   # 压暗保证字可读
        a1 = ease_out((i - 4) / 14)
        paste_c(bg, t_en, W/2 - t_cn.width/2 - 14, H*0.42 - (1-a1)*30, alpha=a1)
        a2 = ease_out((i - 12) / 14)
        paste_c(bg, t_cn, W/2 + t_en.width/2 + 14, H*0.42 + (1-a2)*30, alpha=a2)
        a3 = ease_out((i - 26) / 16)
        paste_c(bg, t_sb, W/2, H*0.42 + 84, alpha=a3)
        # 装饰按节奏依次弹出，并做极轻微的呼吸
        br = 1 + 0.04*math.sin(i/6.0)
        paste_c(bg, arw, W*0.24, H*0.30, scale=ease_back((i-34)/18)*0.95, alpha=clamp((i-34)/10))
        paste_c(bg, spk, W*0.80, H*0.26, scale=ease_back((i-44)/18)*br,  alpha=clamp((i-44)/10))
        paste_c(bg, hrt, W*0.78, H*0.62, scale=ease_back((i-54)/18)*br,  alpha=clamp((i-54)/10))
        if i < 6: bg = fade_to(bg, BG, 1 - i/6)
        yield bg

# ═════════════════════ 第 3 幕：甩镜转场 ═════════════════════
def act_whip(pics, dur=0.5):
    n = round(dur * FPS)
    src = fill_crop(load_cached(pics[0]), W, H, zoom=1.08)
    nxt = fill_crop(load_cached(pics[1]), W, H, zoom=1.08)
    for i in range(n):
        t = i / (n - 1)
        a = math.sin(t * math.pi)                 # 中间最强
        base = (src if t < 0.5 else nxt).copy()
        # 纵向运动模糊：把图按 y 位移多次叠加
        arr = np.asarray(base, np.float32); acc = np.zeros_like(arr); K = 9
        amp = int(a * 150)
        for k in range(K):
            sft = round((k / (K - 1) - 0.5) * 2 * amp)
            acc += np.roll(arr, sft, axis=0)
        arr = acc / K
        arr = arr + a * 210                        # 白闪
        yield Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

# ═════════════════════ 第 4 幕：照片墙 ═════════════════════
def act_wall(pics, per_hero=3.2, n_hero=4):
    """每个 hero：缩略图从四边飞入 → hero 卡片落位 → 停留 → 整体轻微漂移"""
    n = round(per_hero * FPS)
    HW, HH = 620, 400
    TW, TH = 210, 140
    slots = [(-0.16, 0.20, -9), (1.16, 0.24, 8), (-0.14, 0.80, 7), (1.14, 0.78, -8),
             (0.50, -0.22, 4), (0.50, 1.22, -5)]
    dest  = [(0.13, 0.19, -9), (0.87, 0.21, 8), (0.14, 0.82, 7), (0.86, 0.80, -8),
             (0.50, 0.06, 4), (0.50, 0.95, -5)]
    for hidx in range(n_hero):
        hero = load_cached(pics[hidx % len(pics)])
        thumbs = [load_cached(pics[(hidx*3 + k + 1) % len(pics)]) for k in range(6)]
        tilt   = 0.09 if hidx % 2 == 0 else -0.09
        hcard  = card(hero, HW, HH, border=16, tilt=tilt, roll=(-2.5 if hidx % 2 else 2.5))
        tcards = [card(im, TW, TH, border=9, roll=dest[k][2]*0.5, shadow=16)
                  for k, im in enumerate(thumbs)]
        for i in range(n):
            t  = i / n
            bg = Image.new("RGBA", (W, H), (243, 243, 240, 255))
            # 背景：上一张照片的大虚化，避免死白
            blur = fill_crop(hero, W, H, zoom=1.35).filter(ImageFilter.GaussianBlur(26))
            bg.alpha_composite(Image.blend(Image.new("RGB",(W,H),(248,248,246)), blur, 0.30).convert("RGBA"))
            drift = math.sin(t * math.pi) * 8
            for k, tc in enumerate(tcards):
                p  = ease_out((i - 2 - k*2) / 16)
                sx, sy, _ = slots[k]; dx, dy, _ = dest[k]
                cx = (sx + (dx - sx) * p) * W
                cy = (sy + (dy - sy) * p) * H + drift * (0.4 if k % 2 else -0.4)
                paste_c(bg, tc, cx, cy, scale=0.86 + 0.14*p, alpha=clamp(p*1.6))
            ph = ease_back((i - 6) / 20)
            paste_c(bg, hcard, W/2, H*0.50 + drift*0.5,
                    scale=0.82 + 0.18*clamp(ph), alpha=clamp((i-6)/10))
            if i < 4:      bg = fade_to(bg, (255,255,255), 1 - i/4)
            if i > n - 4:  bg = fade_to(bg, (255,255,255), (i-(n-4))/4)
            yield bg

# ═════════════════════ 第 5 幕：收尾落版 ═════════════════════
def act_end(pics, dur=4.6):
    n = round(dur * FPS)
    cards = [card(load_cached(pics[k % len(pics)]), 300, 200, border=11,
                  roll=(k*37 % 17) - 8, shadow=18) for k in range(6)]
    pos = [(0.20,0.24),(0.50,0.18),(0.80,0.26),(0.22,0.76),(0.52,0.84),(0.80,0.74)]
    f_b = F(FONT_SERIF, 96); f_s = F(FONT_BLACK, 30)
    t_b = text_layer(f"{BRAND} 2026", f_b, (*RED, 255), stroke=3, stroke_fill=(255,255,255,255))
    t_s = text_layer("感谢这一年来过的每一位小客人", f_s, (60,60,64,255))
    spk = deco_sparkle(20, 6, (*RED, 255))
    for i in range(n):
        t  = i / n
        bg = Image.new("RGBA", (W, H), (248, 248, 246, 255))
        shrink = ease_in_out(clamp((i - 6) / 34))
        for k, c in enumerate(cards):
            px, py = pos[k]
            cx = (px + (0.5 - px) * shrink) * W
            cy = (py + (0.5 - py) * shrink) * H
            paste_c(bg, c, cx, cy, scale=1 - 0.75*shrink, alpha=1 - shrink)
        a = ease_out((i - 34) / 20)
        paste_c(bg, t_b, W/2, H*0.46 + (1-a)*24, alpha=a)
        paste_c(bg, t_s, W/2, H*0.46 + 84, alpha=ease_out((i-46)/20))
        paste_c(bg, spk, W*0.5 - t_b.width/2 - 40, H*0.40, scale=ease_back((i-52)/16), alpha=clamp((i-52)/10))
        paste_c(bg, spk, W*0.5 + t_b.width/2 + 40, H*0.52, scale=ease_back((i-58)/16), alpha=clamp((i-58)/10))
        if i > n - 24: bg = fade_to(bg, BG, (i-(n-24))/24)
        yield bg

# ───────────────────────── 辅助 ─────────────────────────
def fade_to(im, color, k):
    k = clamp(k)
    if k <= 0: return im
    ov = Image.new("RGBA", im.size, (*color[:3], round(255*k)))
    return Image.alpha_composite(im.convert("RGBA"), ov)

_cache = {}
def load_cached(p):
    if p not in _cache: _cache[p] = load(p)
    return _cache[p]

# ───────────────────────── 主流程 ─────────────────────────
def main():
    d   = sys.argv[1] if len(sys.argv) > 1 else "photos"
    out = sys.argv[2] if len(sys.argv) > 2 else "out_engine.mp4"
    bgm = sys.argv[3] if len(sys.argv) > 3 else None
    pics = sorted(sum([glob.glob(os.path.join(d, e)) for e in ("*.jpg","*.jpeg","*.png")], []))
    assert len(pics) >= 4, "至少需要 4 张照片"
    print(f"输入 {len(pics)} 张照片")

    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if bgm: cmd += ["-i", bgm]
    cmd += ["-map", "0:v"]
    if bgm: cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest",
                    "-af", "afade=t=in:d=1.2"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    acts = [("1 数字开场", act_intro(pics)),
            ("2 标题段",   act_title(pics)),
            ("3 甩镜转场", act_whip(pics)),
            ("4 照片墙",   act_wall(pics)),
            ("5 收尾落版", act_end(pics))]
    total = 0
    for name, gen in acts:
        c = 0
        for fr in gen:
            pr.stdin.write(fr.convert("RGB").tobytes()); c += 1
        total += c
        print(f"  {name}: {c} 帧 / {c/FPS:.1f}s")
    pr.stdin.close(); pr.wait()
    print(f"✅ {out}  共 {total} 帧 / {total/FPS:.1f}s")

if __name__ == "__main__":
    main()