#!/usr/bin/env python3
"""
Pre-render the Magic Dial voice assistant animations: style v2, "interwoven
pure-colour arcs" (HUD / radar / particle-orbit language).

Replaces the v1 Siri ribbons (tools/gen_va_flow.py). Each state is several
independent arc groups; every group is a SINGLE pure colour with no gradient
mixing inside it, riding its own tilted elliptical track. Groups counter-rotate
at INTEGER revolutions per loop, so the 30-frame sequence closes
seamlessly and the crossings drift instead of beating against each other.

Why 192x56 and not the 264x88 of v1
-----------------------------------
LVGL's `animimg` invalidates the ENTIRE image object every frame, so the
repainted area equals the declared widget size regardless of how many pixels
actually light up. Shrinking the canvas is therefore the only lever on repaint
cost, and it buys three separate things on this board:

* 192*56*2 = 21.0 KB per frame fits inside the 31.1 KB LVGL draw buffer
  (`buffer_size: 12%` of 360*360*2), so a frame flushes in ONE
  set_addr_window + DMA round trip. The old 46.5 KB frame needed two, and that
  per-flush overhead is fixed cost, not area-proportional.
* 21.0 KB also fits comfortably in the 64 KB data cache
  (CONFIG_ESP32S3_DATA_CACHE_64KB). At 46.5 KB every frame evicted ~73% of the
  cache, which dragged down `mic_task` and the I2S DMA descriptor accesses
  sharing that memory.
* Frame bytes drop 54% (2722 KB -> 1260 KB for 3x20). That headroom is what
  lets all three states run the full frame sequence; v1 had to decimate
  listening to 10 frames (~11 fps), visibly steppier than the other two.

Invariants that must not be "cleaned up"
---------------------------------------
* Frames are OPAQUE on PURE BLACK and page_va is pinned to 0x000000 with an
  opaque fill. Do not swap in `color_bg_canvas` (0x03070A): LVGL quantises it to
  RGB565 (0,4,8), which this panel renders visibly lighter than its true black,
  so the frame reads as a grey rectangle floating on the page. Matching both
  sides on black hides the boundary without paying for an alpha channel.
* Horizontal AND vertical edge envelopes taper the glow to exactly the
  background. The vertical one is load-bearing: the outer tracks peak within a
  few px of the top/bottom edge, and without it the gaussian glow is sliced
  mid-gradient and leaves a hard horizontal seam. The four corners are asserted
  to be exactly (0, 0, 0) on every build.
* Every animated quantity is `k * frame / NFRAMES` with an INTEGER `k`, so the
  loop has no visible snap at wraparound.

Output: images/va_arc_listen/f00.png ...  plus va_arc_images.yaml
Run:    .venv/bin/python tools/gen_va_arcs.py
"""

import math
import os

from PIL import Image, ImageDraw

# Design canvas the arc specs below are authored against; the real output is
# scaled down from it so the numbers stay readable next to the v1 script.
DESIGN_W, DESIGN_H = 264, 88

# Shipping size. See the module docstring before changing this: it is chosen
# against the LVGL draw buffer and the S3 data cache, not for looks.
W, H = 192, 56

# 30 frames over a 2000 ms loop (~15 fps). The first cut ran 20 frames / 900 ms
# and every reviewer read it as far too fast. Rotation speed is spin_k/duration
# and spin_k must stay an INTEGER for the loop to close seamlessly, so ±1 is a
# hard floor -- past that the only lever is a longer period, and a longer period
# at a fixed frame count just drops the frame rate (20 frames / 2 s = 10 fps,
# visibly steppy). Hence more frames. The slower motion also makes 15 fps look
# smoother than the old 22 fps did: per-frame travel fell from ~54° to ~12°.
NFRAMES = 30

# Pure black. page_va's bg_color/bg_opa in magic-dial.yaml must agree.
BG = (0, 0, 0)

# Width of the top/bottom feather band, in px (scaled with the canvas).
VFEATHER = 11.0

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- envelopes

def h_env(x):
    """Horizontal taper so the opaque rectangle dissolves into the page bg."""
    return math.sin(math.pi * (x / (W - 1))) ** 0.7


def v_env(y):
    """Vertical raised-cosine taper over the outer VFEATHER px only.

    Confined to the edge band so it erases the seam without visibly flattening
    the arc peaks that reach into it.
    """
    dy = min(y, H - 1 - y)
    if dy >= VFEATHER:
        return 1.0
    return 0.5 - 0.5 * math.cos(math.pi * dy / VFEATHER)


HE = [h_env(x) for x in range(W)]
VE = [v_env(y) for y in range(H)]


# ---------------------------------------------------------------- arc specs
# Per arc group:
#   col    : pure RGB, used alone for this entire arc (no intra-arc gradient)
#   rx, ry : elliptical track radii, in DESIGN_W/DESIGN_H px
#   tilt   : track tilt, degrees
#   span   : angular length of the visible arc, degrees
#   spin_k : INTEGER revolutions per loop; sign selects direction.
#            Keep outer tracks at +-1; a single +-2 inner accent supplies the
#            parallax that stops every arc from looking rigidly co-rotating.
#   phase  : starting angle, degrees
#   thick  : gaussian stroke sigma, in DESIGN_H px
#   gain   : peak brightness, 0..1
#   taper  : 0 = uniform stroke, 1 = full comet trail (bright head, faded tail)

# Cold blues/cyans, thin strokes, calm +-1 rates. Reads as "open and waiting"
# without words, and is deliberately the lowest-energy of the three so the
# state is legible at a glance against the fast answering arcs.
LISTEN = [
    dict(col=(0x2E, 0x9B, 0xFF), rx=112, ry=30, tilt=0,   span=150, spin_k=1,  phase=0,   thick=1.15, gain=0.90, taper=0.85),
    dict(col=(0x2E, 0x9B, 0xFF), rx=112, ry=30, tilt=0,   span=150, spin_k=1,  phase=180, thick=1.15, gain=0.90, taper=0.85),
    dict(col=(0x22, 0xE0, 0xF0), rx=84,  ry=22, tilt=-14, span=115, spin_k=-1, phase=40,  thick=1.00, gain=0.85, taper=0.80),
    dict(col=(0x22, 0xE0, 0xF0), rx=84,  ry=22, tilt=-14, span=115, spin_k=-1, phase=220, thick=1.00, gain=0.85, taper=0.80),
    dict(col=(0x6F, 0xC8, 0xFF), rx=54,  ry=14, tilt=16,  span=200, spin_k=1,  phase=90,  thick=0.90, gain=0.70, taper=0.70),
    dict(col=(0x14, 0x5E, 0xC8), rx=128, ry=38, tilt=6,   span=70,  spin_k=-1, phase=300, thick=1.30, gain=0.60, taper=0.90),
]

# Warm purples/magentas, medium strokes. Same +-1 rates as listening; the
# state reads as higher energy through thicker strokes and the warm palette
# rather than through speed.
THINK = [
    dict(col=(0x9B, 0x5C, 0xFF), rx=110, ry=30, tilt=0,   span=135, spin_k=1,  phase=0,   thick=1.60, gain=0.95, taper=0.85),
    dict(col=(0x9B, 0x5C, 0xFF), rx=110, ry=30, tilt=0,   span=135, spin_k=1,  phase=180, thick=1.60, gain=0.95, taper=0.85),
    dict(col=(0xFF, 0x3E, 0xB5), rx=80,  ry=24, tilt=-18, span=110, spin_k=-1, phase=55,  thick=1.45, gain=0.95, taper=0.80),
    dict(col=(0xFF, 0x3E, 0xB5), rx=80,  ry=24, tilt=-18, span=110, spin_k=-1, phase=235, thick=1.45, gain=0.95, taper=0.80),
    dict(col=(0xC8, 0x7C, 0xFF), rx=52,  ry=15, tilt=22,  span=190, spin_k=1,  phase=100, thick=1.25, gain=0.80, taper=0.70),
    dict(col=(0x6A, 0x2A, 0xC8), rx=126, ry=38, tilt=8,   span=80,  spin_k=-1, phase=290, thick=1.75, gain=0.65, taper=0.90),
    dict(col=(0xFF, 0x8A, 0xE0), rx=96,  ry=12, tilt=0,   span=45,  spin_k=1,  phase=150, thick=1.10, gain=0.85, taper=0.95),
]

# Bright white/cyan/green, thickest strokes, and the one +-2 inner accent:
# the high-energy end, but still slow enough to read as deliberate.
ANSWER = [
    dict(col=(0xFF, 0xFF, 0xFF), rx=108, ry=30, tilt=0,   span=120, spin_k=1,  phase=0,   thick=2.10, gain=1.00, taper=0.85),
    dict(col=(0x1F, 0xF0, 0xFF), rx=108, ry=30, tilt=0,   span=120, spin_k=1,  phase=180, thick=2.10, gain=1.00, taper=0.85),
    dict(col=(0x2C, 0xF7, 0x8C), rx=78,  ry=24, tilt=-20, span=100, spin_k=-1, phase=60,  thick=1.90, gain=1.00, taper=0.80),
    dict(col=(0x2C, 0xF7, 0x8C), rx=78,  ry=24, tilt=-20, span=100, spin_k=-1, phase=240, thick=1.90, gain=1.00, taper=0.80),
    dict(col=(0x1F, 0xF0, 0xFF), rx=50,  ry=15, tilt=25,  span=170, spin_k=2,  phase=110, thick=1.60, gain=0.90, taper=0.70),
    dict(col=(0xFF, 0xFF, 0xFF), rx=124, ry=38, tilt=7,   span=60,  spin_k=-1, phase=280, thick=2.20, gain=0.80, taper=0.95),
    dict(col=(0x0E, 0xB8, 0xA0), rx=132, ry=20, tilt=0,   span=200, spin_k=1,  phase=20,  thick=1.50, gain=0.60, taper=0.60),
]

SX, SY = W / float(DESIGN_W), H / float(DESIGN_H)


def scaled(arcs):
    """Map the design-canvas specs onto the shipping canvas."""
    out = []
    for a in arcs:
        b = dict(a)
        b["rx"] = a["rx"] * SX
        b["ry"] = a["ry"] * SY
        # Floor the sigma: below ~0.85 px the gaussian degenerates into a
        # single aliased pixel row and the arc stops reading as a stroke.
        b["thick"] = max(0.85, a["thick"] * SY)
        out.append(b)
    return out


# ---------------------------------------------------------------- rendering

def stamp(buf, cx, cy, col, inten, sigma):
    """Additively composite one gaussian dot into the float RGB buffer.

    Additive (rather than painted) blending is what produces the bright cores
    where two arcs cross, which is most of the "interwoven" read.
    """
    if inten <= 0.004:
        return
    rad = int(sigma * 2.6) + 1
    x0, x1 = max(0, int(cx) - rad), min(W - 1, int(cx) + rad)
    y0, y1 = max(0, int(cy) - rad), min(H - 1, int(cy) + rad)
    two_s2 = 2.0 * sigma * sigma
    r, g, b = col
    for y in range(y0, y1 + 1):
        dy = y - cy
        row = buf[y]
        ve = VE[y]
        for x in range(x0, x1 + 1):
            dx = x - cx
            w = math.exp(-(dx * dx + dy * dy) / two_s2) * inten * ve * HE[x]
            if w <= 0.002:
                continue
            i = x * 3
            row[i] += r * w
            row[i + 1] += g * w
            row[i + 2] += b * w


def render(arcs, tau):
    """Render one frame at normalised loop position `tau` in [0, 1)."""
    buf = [[0.0] * (W * 3) for _ in range(H)]
    cx, cy = (W - 1) / 2.0, (H - 1) / 2.0

    for a in arcs:
        rx, ry = a["rx"], a["ry"]
        tilt = math.radians(a["tilt"])
        ct, st = math.cos(tilt), math.sin(tilt)
        span = math.radians(a["span"])
        base = math.radians(a["phase"]) + 2 * math.pi * a["spin_k"] * tau
        # ~1 sample per 0.7 px of the major axis: dense enough that the stroke
        # is continuous, sparse enough that the whole run stays a few seconds
        # in pure Python (the build venv has Pillow via ESPHome, but no numpy).
        n = max(24, int(span * max(rx, ry) / 0.7))
        for s in range(n + 1):
            u = s / n
            ang = base + span * (u - 0.5)
            ex, ey = rx * math.cos(ang), ry * math.sin(ang)
            px = cx + ex * ct - ey * st
            py = cy + ex * st + ey * ct
            # Brightness along the arc: comet falloff towards the tail, times a
            # soft fade at BOTH ends so a stroke never terminates abruptly.
            head = u ** (1.0 + 2.5 * a["taper"])
            ends = math.sin(math.pi * u) ** 0.45
            inten = a["gain"] * (0.25 + 0.75 * head) * ends
            stamp(buf, px, py, a["col"], inten * 0.32, a["thick"])

    img = Image.new("RGB", (W, H), BG)
    px = img.load()
    for y in range(H):
        row = buf[y]
        for x in range(W):
            i = x * 3
            r, g, b = row[i], row[i + 1], row[i + 2]
            px[x, y] = (255 if r > 255 else int(r),
                        255 if g > 255 else int(g),
                        255 if b > 255 else int(b))
    return img


def mockup(frame):
    """Composite one frame onto a 360x360 round-panel preview."""
    S = 360
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, S - 20, S - 20], outline=(0x0E, 0x2A, 0x33), width=2)
    img.paste(frame, ((S - W) // 2, (S - H) // 2))
    # Mask to the physical round panel so the preview cannot imply corners the
    # hardware does not have.
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    out = Image.new("RGB", (S, S), BG)
    out.paste(img, (0, 0), mask)
    return out


def build(name, arcs):
    outdir = os.path.join(HERE, "images", name)
    os.makedirs(outdir, exist_ok=True)
    prev = os.path.join(HERE, "tools", "preview")
    os.makedirs(prev, exist_ok=True)

    frames = []
    for f in range(NFRAMES):
        img = render(arcs, f / NFRAMES)
        img.save(os.path.join(outdir, "f%02d.png" % f))
        frames.append(img)

    # The corner check is the regression guard for the feather envelopes: if
    # either envelope is weakened the corners stop being exactly BG and a hard
    # rectangle edge appears on the panel.
    p = frames[0].load()
    corners = {p[0, 0], p[W - 1, 0], p[0, H - 1], p[W - 1, H - 1]}
    assert corners == {BG}, "%s: corners not pure black: %s" % (name, corners)

    # Raw loop (what gets compiled in) and a device mockup, for review only.
    frames[0].save(os.path.join(prev, "%s_strip.gif" % name), save_all=True,
                   append_images=frames[1:], duration=45, loop=0)
    mocks = [mockup(fr) for fr in frames]
    mocks[0].save(os.path.join(prev, "%s_device.gif" % name), save_all=True,
                  append_images=mocks[1:], duration=45, loop=0)

    nbytes = W * H * 2 * NFRAMES
    print("%-16s %d frames %dx%d  -> %.0f KB (RGB565)  corners OK"
          % (name, NFRAMES, W, H, nbytes / 1024))
    return nbytes


def emit_image_yaml(names):
    """Write the `image:` declarations pulled in via `image: !include`.

    Generated rather than hand-maintained so the PNG files and their ESPHome
    declarations cannot drift apart.

    `byte_order: big_endian` is NOT optional and must never be dropped. This
    panel (mipi_spi) reports big-endian, so the LVGL component compiles with
    LV_COLOR_16_SWAP=1 and reads every 16-bit pixel byte-swapped. The `image:`
    component independently defaults RGB565 to little-endian, and nothing
    cross-validates the two: `esphome config` passes cleanly and the firmware
    then renders every colour byte-swapped on the device. Pin it to match LVGL.

    `transparency` stays at the default `opaque` because the frames already
    carry the page background themselves (see the module docstring).
    """
    lines = [
        "# GENERATED by tools/gen_va_arcs.py -- do not edit by hand.",
        "# Voice assistant interwoven-arc frames, pulled in by `image: !include`.",
        "# %d frames per state, %dx%d, RGB565 opaque = %.0f KB each, %.0f KB total."
        % (NFRAMES, W, H, W * H * 2 * NFRAMES / 1024,
           W * H * 2 * NFRAMES * len(names) / 1024),
        "# byte_order MUST stay big_endian to match LVGL's LV_COLOR_16_SWAP=1;",
        "# the image component would otherwise default to little-endian and",
        "# silently byte-swap every colour with no config error.",
        "",
    ]
    for name in names:
        short = name.replace("va_arc_", "")
        lines.append("# %s" % name)
        for f in range(NFRAMES):
            lines.append("- platform: file")
            lines.append("  id: va_%s_%02d" % (short, f))
            lines.append("  file: images/%s/f%02d.png" % (name, f))
            lines.append("  type: RGB565")
            lines.append("  byte_order: big_endian")
        lines.append("")
    path = os.path.join(HERE, "va_arc_images.yaml")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    print("wrote va_arc_images.yaml (%d images)" % (NFRAMES * len(names)))


if __name__ == "__main__":
    names = ["va_arc_listen", "va_arc_think", "va_arc_answer"]
    total = 0
    total += build("va_arc_listen", scaled(LISTEN))
    total += build("va_arc_think", scaled(THINK))
    total += build("va_arc_answer", scaled(ANSWER))
    emit_image_yaml(names)
    print("total: %.2f MB  (%d px repainted per frame, %d frames/state)"
          % (total / 1048576, W * H, NFRAMES))
    print("animimg `duration:` in magic-dial.yaml must be 2000ms to match.")
