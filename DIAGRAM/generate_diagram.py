"""Render DIAGRAM/architecture.gif for Day 36 (evidence-gated schema evolution).

    pip install pillow>=10
    python DIAGRAM/generate_diagram.py

Layout: drift chips on the left, model proposer and evidence gate in the middle,
four decision bins on the right, expand/contract timeline along the bottom.
Palette (new for this post): oxblood background, mint, apricot, periwinkle.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
OUT = Path(__file__).with_name("architecture.gif")

BG = (30, 15, 20)
PANEL = (46, 25, 33)
EDGE = (84, 52, 62)
TEXT = (244, 237, 230)
MUTED = (178, 156, 160)
MINT = (111, 224, 190)
APRICOT = (255, 176, 122)
PERI = (159, 168, 255)
RED = (255, 98, 112)

FONT_DIR = Path("/usr/share/fonts/opentype/inter")
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for p in (FONT_DIR / name, Path(MONO)):
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = font("Inter-Bold.otf", 30)
F_SUB = font("Inter-Regular.otf", 17)
F_H = font("Inter-SemiBold.otf", 17)
F_B = font("Inter-Regular.otf", 15)
F_S = font("Inter-Medium.otf", 13)
F_MONO = ImageFont.truetype(MONO, 15)
F_MONO_S = ImageFont.truetype(MONO, 11)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


def mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


# ---------- static layout ----------
LEFT = (30, 92, 330, 528)
MID = (360, 92, 820, 528)
RIGHT = (850, 92, 1170, 528)
BINS = [  # name, color, caption
    ("AUTO_APPLY", MINT, "additive, safe widening"),
    ("EXPAND_CONTRACT", PERI, "verified rename"),
    ("FILL_NULL / RESCUE", APRICOT, "kept, or held for review"),
    ("BLOCK", RED, "quarantine batch"),
]
BIN_H = 90


def bin_box(i):
    x0, y0 = RIGHT[0] + 14, RIGHT[1] + 42 + i * (BIN_H + 6)
    return (x0, y0, RIGHT[2] - 14, y0 + BIN_H)


def bin_slot(i, k):
    x0, y0, _, _ = bin_box(i)
    return (x0 + 10 + k * 94, y0 + 56)


CHIPS = [  # label, slot, destination bin, small label, route
    ("+ promo_code", 0, 0, "promo_code", "direct"),
    ("loyalty_points int>long", 1, 0, "int>long", "direct"),
    ("amount float>double", 2, 0, "float>dbl", "direct"),
    ("+ customer_email", 3, 2, "email PII", "direct"),
    ("cust_id missing", 4, 1, "cust_id>", "model"),
    ("region missing", 5, 2, "region", "model"),
]


def chip_home(slot):
    return (LEFT[0] + 14, LEFT[1] + 52 + slot * 60)


CHIP_W, CHIP_H = 272, 42
SMALL_W, SMALL_H = 88, 26
PROPOSER = (MID[0] + 18, MID[1] + 44, MID[2] - 18, MID[1] + 168)
GATE_Y = MID[1] + 232
GATE = (MID[0] + 150, GATE_Y, MID[0] + 310, GATE_Y + 150)
LAMPS = ["values", "shape", "range", "nulls"]
REJECT = (MID[0] + 18, MID[1] + 410, MID[2] - 18, MID[1] + 424 + 0)

TL_Y = 618
TL_NODES = [("expand", 120), ("backfill", 390), ("switch view", 660), ("contract", 950)]

# ---------- timeline (frame indices) ----------
N = 96
T_APPEAR = (0, 14)
T_DIRECT = (16, 30)
T_TO_MODEL = (32, 40)
T_PROPOSE = (40, 46)
T_TOK1 = (46, 58)
T_TOK2 = (58, 70)
T_REGION = (70, 76)
T_TIMELINE = (76, 94)


def phase(f, rng):
    a, b = rng
    return (f - a) / (b - a) if b > a else 1.0


def rr(d, box, fill=None, outline=None, r=12, w=2):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=w)


def static_layer() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((30, 22), "Schema drift, decided by evidence", font=F_TITLE, fill=TEXT)
    d.text((560, 34), "model proposes  /  data verifies  /  view keeps readers alive",
           font=F_SUB, fill=MUTED)
    for box, title in ((LEFT, "Incoming batch v2"), (MID, "Rename proposals"), (RIGHT, "Gate decisions")):
        rr(d, box, fill=PANEL, outline=EDGE, r=16)
        d.text((box[0] + 16, box[1] + 12), title, font=F_H, fill=TEXT)
    # proposer box
    rr(d, PROPOSER, fill=BG, outline=EDGE, r=10)
    d.text((PROPOSER[0] + 12, PROPOSER[1] + 8), "LLM proposer  (metadata only, no values)",
           font=F_S, fill=MUTED)
    # gate
    gx0, gy0, gx1, gy1 = GATE
    rr(d, GATE, fill=BG, outline=EDGE, r=10)
    d.text((gx0 + 14, gy0 - 24), "evidence gate", font=F_S, fill=MUTED)
    d.text((MID[0] + 18, gy0 + 60), "proposal", font=F_S, fill=MUTED)
    d.text((gx1 + 40, gy0 + 60), "accepted", font=F_S, fill=MUTED)
    # reject chute
    d.line((MID[0] + 18, REJECT[1], MID[2] - 18, REJECT[1]), fill=EDGE, width=1)
    d.text((MID[0] + 18, REJECT[1] + 8), "rejected proposals", font=F_S, fill=MUTED)
    # bins
    for i, (name, col, cap) in enumerate(BINS):
        b = bin_box(i)
        rr(d, b, fill=BG, outline=mix(col, BG, 0.45), r=10)
        d.rectangle((b[0], b[1] + 8, b[0] + 4, b[3] - 8), fill=col)
        d.text((b[0] + 14, b[1] + 10), name, font=F_H, fill=col)
        d.text((b[0] + 14, b[1] + 32), cap, font=F_S, fill=MUTED)
    # timeline
    rr(d, (30, 548, 1170, 660), fill=PANEL, outline=EDGE, r=16)
    d.text((46, 558), "Zero-downtime migration of the accepted rename", font=F_H, fill=TEXT)
    return im


def draw_chip(d, xy, label, color, alpha=1.0, small=False, fill=None):
    x, y = xy
    w, h = (SMALL_W, SMALL_H) if small else (CHIP_W, CHIP_H)
    f = F_MONO_S if small else F_MONO
    if small:
        w = max(w, int(d.textlength(label, font=f)) + 20)
    c = mix(BG, color, alpha)
    rr(d, (x, y, x + w, y + h), fill=fill or mix(BG, PANEL, 1.0), outline=c, r=8 if small else 10, w=2)
    tb = d.textbbox((0, 0), label, font=f)
    d.text((x + 10, y + (h - (tb[3] - tb[1])) / 2 - tb[1]), label, font=f, fill=mix(BG, TEXT, alpha))


def frame(f: int, base: Image.Image) -> Image.Image:
    im = base.copy()
    d = ImageDraw.Draw(im)

    # ---- chips ----
    for idx, (label, slot, dest, small, route) in enumerate(CHIPS):
        home = chip_home(slot)
        appear = ease(phase(f, (T_APPEAR[0] + idx * 2, T_APPEAR[0] + idx * 2 + 4)))
        if appear <= 0:
            continue
        color = {0: MINT, 1: PERI, 2: APRICOT}[dest] if route == "direct" else MUTED
        left_at = T_DIRECT[0] + idx * 2 if route == "direct" else T_TO_MODEL[0] + (0 if label.startswith("cust") else 2)
        if f > left_at:
            rr(d, (home[0], home[1], home[0] + CHIP_W, home[1] + CHIP_H), outline=EDGE, r=10, w=1)
            d.text((home[0] + 10, home[1] + 12), label, font=F_MONO, fill=mix(BG, MUTED, 0.55))
        if route == "direct":
            k = [c for c in CHIPS if c[2] == dest and c[4] == "direct"].index(CHIPS[idx])
            target = bin_slot(dest, k)
            t = ease(phase(f, (T_DIRECT[0] + idx * 2, T_DIRECT[0] + idx * 2 + 7)))
            if t < 1:
                pos = lerp(home, target, t)
                draw_chip(d, pos, label if t < 0.5 else small, color, appear, small=t >= 0.5)
            else:
                draw_chip(d, target, small, color, 1.0, small=True)
        else:
            mi = 0 if label.startswith("cust") else 1
            t = ease(phase(f, (T_TO_MODEL[0] + mi * 2, T_TO_MODEL[0] + mi * 2 + 6)))
            target = (PROPOSER[0] + 12 + mi * 214, PROPOSER[1] + 32)
            if t < 1:
                draw_chip(d, lerp(home, target, t), label if t < 0.5 else label.split()[0],
                          color, appear, small=t >= 0.5)
            elif f < T_PROPOSE[1]:
                draw_chip(d, target, label.split()[0], color, 1.0, small=True)

    # ---- proposals text ----
    pa = ease(phase(f, T_PROPOSE))
    if pa > 0:
        lines = [("cust_id = customer_id", "0.93"), ("region  = promo_code", "0.81")]
        for i, (txt, conf) in enumerate(lines):
            y = PROPOSER[1] + 66 + i * 30
            used = (i == 0 and f >= T_TOK1[0] + 2) or (i == 1 and f >= T_TOK2[0] + 2)
            col = mix(BG, MUTED if used else TEXT, pa)
            d.text((PROPOSER[0] + 14, y), txt, font=F_MONO, fill=col)
            d.text((PROPOSER[2] - 120, y), f"conf {conf}", font=F_MONO, fill=mix(BG, APRICOT, pa))

    # ---- gate lamps ----
    gx0, gy0, gx1, gy1 = GATE
    lamp_state = [None] * 4
    t1, t2 = phase(f, T_TOK1), phase(f, T_TOK2)
    if 0 < t1 <= 1.3 and f < T_TOK2[0]:
        for i in range(4):
            lamp_state[i] = MINT if t1 > 0.35 + i * 0.08 else None
    if 0 < t2:
        res = [RED, RED, None, MINT]
        for i in range(4):
            if t2 > 0.35 + i * 0.08 and res[i]:
                lamp_state[i] = res[i]
    for i, name in enumerate(LAMPS):
        cy = gy0 + 22 + i * 32
        st = lamp_state[i]
        d.ellipse((gx0 + 14, cy - 8, gx0 + 30, cy + 8), fill=st or EDGE)
        d.text((gx0 + 40, cy - 9), name, font=F_S, fill=TEXT if st else MUTED)

    # ---- token 1 (accepted) ----
    start = (MID[0] + 18, gy0 + 82)
    after = (gx1 + 30, gy0 + 82)
    if 0 < t1 < 1:
        if t1 < 0.35:
            pos = lerp(start, (gx0 - 10, gy0 + 82), ease(t1 / 0.35))
        elif t1 < 0.7:
            pos = (gx0 - 10, gy0 + 82)
        else:
            dest = bin_slot(1, 0)
            u = ease((t1 - 0.7) / 0.3)
            pos = lerp(after, dest, u) if u > 0 else after
        draw_chip(d, pos, "cust_id>", PERI, 1.0, small=True)
    if t1 >= 1:
        draw_chip(d, bin_slot(1, 0), "cust_id>", PERI, 1.0, small=True)
        d.text((bin_slot(1, 0)[0] + 94, bin_slot(1, 0)[1] + 6), "customer_id ok",
               font=F_MONO_S, fill=MUTED)

    # ---- token 2 (rejected) ----
    if 0 < t2 < 1:
        if t2 < 0.35:
            pos = lerp(start, (gx0 - 10, gy0 + 82), ease(t2 / 0.35))
        elif t2 < 0.7:
            pos = (gx0 - 10 + 4 * math.sin(t2 * 60), gy0 + 82)
        else:
            u = ease((t2 - 0.7) / 0.3)
            pos = lerp((gx0 - 10, gy0 + 82), (MID[0] + 200, REJECT[1] + 4), u)
        draw_chip(d, pos, "region>", RED, 1.0, small=True)
    if t2 >= 1:
        draw_chip(d, (MID[0] + 160, REJECT[1] + 4), "region>promo", RED, 1.0, small=True)
        d.text((MID[0] + 272, REJECT[1] + 10), "value containment 0.00", font=F_MONO_S, fill=RED)

    # ---- region falls back to FILL_NULL ----
    tr = ease(phase(f, T_REGION))
    if tr > 0:
        src = (PROPOSER[0] + 226, PROPOSER[1] + 32)
        dest = bin_slot(2, 1)
        draw_chip(d, lerp(src, dest, tr) if tr < 1 else dest, "region null", APRICOT, 1.0, small=True)

    # ---- empty BLOCK bin note ----
    if f >= T_REGION[1]:
        b = bin_box(3)
        d.text((b[0] + 14, b[1] + 60), "0 in this batch", font=F_MONO_S, fill=MUTED)

    # ---- timeline ----
    tl = phase(f, T_TIMELINE)
    x_start, x_end = TL_NODES[0][1], TL_NODES[2][1]
    d.line((x_start, TL_Y, TL_NODES[3][1], TL_Y), fill=EDGE, width=3)
    # dashed to contract
    for x in range(x_end, TL_NODES[3][1], 14):
        d.line((x, TL_Y, x + 7, TL_Y), fill=MUTED, width=3)
    prog = x_start + (x_end - x_start) * ease(tl)
    if tl > 0:
        d.line((x_start, TL_Y, prog, TL_Y), fill=PERI, width=5)
    for i, (name, x) in enumerate(TL_NODES):
        reached = tl > 0 and prog >= x - 1 and i < 3
        if i == 3:
            d.ellipse((x - 10, TL_Y - 10, x + 10, TL_Y + 10), outline=MUTED, width=2, fill=BG)
        else:
            d.ellipse((x - 10, TL_Y - 10, x + 10, TL_Y + 10), fill=PERI if reached else EDGE)
        d.text((x - 30, TL_Y + 16), name, font=F_S, fill=TEXT if reached or i == 3 else MUTED)
    notes = {0: "ADD COLUMNS customer_id", 1: "UPDATE ... WHERE new IS NULL",
             2: "orders_v serves both names"}
    for i, (name, x) in enumerate(TL_NODES[:3]):
        if tl > 0 and prog >= x - 1:
            d.text((x + 16, TL_Y - 26), notes[i], font=F_MONO_S, fill=MUTED)
    d.text((TL_NODES[3][1] + 18, TL_Y - 26), "deferred until 2 readers", font=F_MONO_S, fill=MUTED)
    d.text((TL_NODES[3][1] + 18, TL_Y - 10), "move to customer_id", font=F_MONO_S, fill=MUTED)
    # reader health
    health = "readers on orders_v  3 of 3 resolving" if f >= T_DIRECT[0] else ""
    if health:
        d.ellipse((800, 564, 812, 576), fill=MINT)
        d.text((820, 559), health, font=F_S, fill=MINT)
    return im


def main() -> None:
    base = static_layer()
    frames, durations = [], []
    for f in range(N):
        frames.append(frame(f, base))
        durations.append(110)
    for i in (T_PROPOSE[1] - 1, T_TOK1[1], T_TOK2[1]):
        durations[i] = 1100
    durations[-1] = 3500
    pal = frames[-1].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    q = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    q[0].save(OUT, save_all=True, append_images=q[1:], duration=durations, loop=0,
              optimize=True, disposal=1)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(q)} frames)")


if __name__ == "__main__":
    main()
