#!/usr/bin/env python3
"""
Pre-render the Siri-style flowing ribbons used by the Magic Dial voice
assistant page (thinking + speaking phases).

Why pre-rendered frames instead of the previous procedural LVGL arcs:
the arcs were driven by a 100 ms `interval:` (10 fps) and lv_arc_set_rotation,
which is both visibly steppy and limited to "rings that spin". A Siri ribbon
needs overlapping translucent waves with per-pixel additive glow, which LVGL
cannot draw cheaply at runtime on this SoC. Rendering it offline turns the
whole effect into a flash lookup: LVGL just blits one opaque image per frame.

Design notes
------------
* Frames are rendered OPAQUE on PURE BLACK, and page_va is pinned to pure
  black with an explicit opaque fill to match. Do not "restore" this to the
  0x03070A `color_bg_canvas` token: LVGL quantises that to RGB565 (0,4,8),
  which on this panel is visibly lighter than the black the LCD actually
  shows, so the 264x88 frame read as a grey rectangle floating on the screen.
  Matching both sides on 0x000000 makes the image boundary disappear without
  paying for an alpha channel (RGB565 + alpha is 3 B/px instead of 2, +50%).
* Both a horizontal AND a vertical edge envelope taper the glow to exactly the
  background. The vertical one matters: the tallest wave peaks come within
  ~14 px of the top/bottom edge, so without it the gaussian glow is sliced off
  mid-gradient and leaves a hard horizontal seam.
* Every animated quantity is driven by `k * frame / NFRAMES` with an INTEGER
  `k`, so the sequence is seamlessly loopable - no visible snap at wraparound.
* Waves are additively blended, which produces the bright white-ish core where
  bands overlap that is characteristic of the Siri orb.

Output: images/va_flow_think/f00.png ... and images/va_flow_speak/f00.png ...
Run:    .venv/bin/python tools/gen_va_flow.py
"""

import math
import os

from PIL import Image

# Pure black, and page_va's bg_color/bg_opa in magic-dial.yaml must agree.
BG = (0x00, 0x00, 0x00)

# Width of the top/bottom feather band, in px.
VFEATHER = 16.0

W, H = 264, 88
NFRAMES = 20

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def envelope(xn: float) -> float:
    """Horizontal taper so the opaque rectangle dissolves into the page bg."""
    return math.sin(math.pi * xn) ** 0.7


def vfeather(y: int) -> float:
    """Vertical taper, forcing the top and bottom rows to exactly BG.

    Raised-cosine over the outer VFEATHER px only, so it erases the seam
    without visibly flattening the wave peaks that reach into that band.
    """
    dy = min(y, H - 1 - y)
    if dy >= VFEATHER:
        return 1.0
    return 0.5 - 0.5 * math.cos(math.pi * dy / VFEATHER)


# (r, g, b), freq, amp, phase_k, sigma, breathe_k, breathe_phase
#   freq        : sine cycles across the full width
#   amp         : peak vertical excursion in px
#   phase_k     : integer -> how many full phase turns per loop (sign = drift dir)
#   sigma       : vertical thickness (px) of the glow band
#   breathe_k   : integer -> amplitude modulation cycles per loop
THINK_WAVES = [
    ((0x8B, 0x7C, 0xFF), 1.00, 20.0, 1, 6.0, 1, 0.0),           # purple
    ((0x38, 0xDD, 0xF2), 1.50, 16.0, -1, 5.0, 2, 1.7),          # cyan
    ((0xFF, 0x5E, 0xC4), 2.00, 13.0, 2, 4.5, 1, 3.1),           # pink
    ((0x49, 0xA8, 0xFF), 0.75, 24.0, -2, 7.0, 1, 4.6),          # blue
]

# Speaking is the "answering" state: same language, higher energy - larger
# excursions, faster drift, and a cooler blue/cyan/white palette so it is
# instantly distinguishable from the calmer purple thinking ribbon.
SPEAK_WAVES = [
    ((0x49, 0xA8, 0xFF), 1.25, 26.0, 2, 6.5, 1, 0.0),           # blue
    ((0x38, 0xDD, 0xF2), 1.75, 21.0, -2, 5.5, 2, 1.2),          # cyan
    ((0xB8, 0xE8, 0xFF), 2.50, 16.0, 3, 4.5, 2, 2.9),           # pale highlight
    ((0x45, 0xD6, 0xC5), 0.90, 29.0, -3, 7.5, 1, 4.2),          # teal
]

# Listening replaces the old "请说话" text label. It has to read as "the device
# is open and waiting" WITHOUT words, so it is deliberately the calmest of the
# three: low amplitude, slow drift, and the established listening green. The
# contrast against the energetic speaking ribbon is what makes the state
# legible at a glance.
LISTEN_WAVES = [
    ((0x35, 0xD0, 0x7F), 1.00, 13.0, 1, 6.0, 1, 0.0),           # green
    ((0x45, 0xD6, 0xC5), 1.50, 10.0, -1, 5.0, 2, 1.9),          # teal
    ((0xB8, 0xFF, 0xD9), 2.00, 7.0, 1, 4.0, 2, 3.4),            # pale mint
    ((0x38, 0xDD, 0xF2), 0.75, 16.0, -1, 7.0, 1, 4.8),          # cyan
]


def render(waves, tau):
    """Render one frame at normalised loop position `tau` in [0, 1)."""
    img = Image.new("RGB", (W, H))
    px = img.load()
    yc = (H - 1) / 2.0

    # Precompute per-column wave centres + envelope: the inner y loop then only
    # does the cheap gaussian. Keeps the whole run at a few seconds in pure
    # Python, so this stays a no-dependency script (the build venv has Pillow
    # via ESPHome but no numpy).
    cols = []
    for x in range(W):
        xn = x / (W - 1)
        env = envelope(xn)
        centres = []
        for (col, freq, amp, pk, sigma, bk, bph) in waves:
            breathe = 0.72 + 0.28 * math.sin(2 * math.pi * bk * tau + bph)
            a = amp * breathe * env
            y = yc + a * math.sin(2 * math.pi * freq * xn + 2 * math.pi * pk * tau)
            centres.append((col, y, sigma, env))
        cols.append(centres)

    vf = [vfeather(y) for y in range(H)]
    for x in range(W):
        centres = cols[x]
        for y in range(H):
            fy = vf[y]
            r, g, b = BG
            for (col, ywave, sigma, env) in centres:
                d = y - ywave
                inten = math.exp(-(d * d) / (2.0 * sigma * sigma)) * env * fy
                if inten < 0.004:
                    continue
                inten *= 0.95
                r += col[0] * inten
                g += col[1] * inten
                b += col[2] * inten
            px[x, y] = (
                int(r) if r < 255 else 255,
                int(g) if g < 255 else 255,
                int(b) if b < 255 else 255,
            )
    return img


def draw_mic(d, cx, cy, col):
    """Rough mdi-microphone stand-in, only ever used in the preview mockup."""
    d.rounded_rectangle([cx - 7, cy - 16, cx + 7, cy + 2], radius=7, fill=col)
    d.arc([cx - 13, cy - 8, cx + 13, cy + 12], start=0, end=180, fill=col, width=3)
    d.line([cx, cy + 12, cx, cy + 19], fill=col, width=3)
    d.line([cx - 8, cy + 19, cx + 8, cy + 19], fill=col, width=3)


def mockup(frame, show_mic, mic_col):
    """Composite one ribbon frame onto a 360x360 round-screen preview.

    Mirrors page_va: `color_bg_canvas` fill, the 320 px / 2 px `va_frame` ring,
    the ribbon centred, and (listening only) the mic icon above it.
    """
    from PIL import ImageDraw

    S = 360
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, S - 20, S - 20], outline=(0x0E, 0x2A, 0x33), width=2)
    img.paste(frame, ((S - W) // 2, (S - H) // 2))
    if show_mic:
        draw_mic(d, S // 2, 108, mic_col)

    # Mask to the physical round panel so the preview cannot suggest corners
    # that the hardware does not have.
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    out = Image.new("RGB", (S, S), (0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def build(name, waves, show_mic=False, mic_col=(0x35, 0xD0, 0x7F)):
    outdir = os.path.join(HERE, "images", name)
    os.makedirs(outdir, exist_ok=True)
    prev = os.path.join(HERE, "tools", "preview")
    os.makedirs(prev, exist_ok=True)

    frames = []
    for f in range(NFRAMES):
        img = render(waves, f / NFRAMES)
        img.save(os.path.join(outdir, "f%02d.png" % f))
        frames.append(img)

    # Raw ribbon loop (what actually gets compiled into flash).
    frames[0].save(
        os.path.join(prev, "%s_ribbon.gif" % name),
        save_all=True, append_images=frames[1:], duration=45, loop=0)

    # Device mockup loop (what the user will actually see on the panel).
    mocks = [mockup(fr, show_mic, mic_col) for fr in frames]
    mocks[0].save(
        os.path.join(prev, "%s_device.gif" % name),
        save_all=True, append_images=mocks[1:], duration=45, loop=0)

    nbytes = W * H * 2 * NFRAMES
    print("%-16s %d frames %dx%d  -> %.0f KB flash (RGB565)"
          % (name, NFRAMES, W, H, nbytes / 1024))
    return nbytes


def emit_image_yaml(names):
    """Write the `image:` declarations consumed via `!include` from the main YAML.

    Generated rather than hand-written so the frame files and their ESPHome
    declarations can never drift apart.

    `byte_order: big_endian` is NOT optional and must not be removed. This
    display (mipi_spi) reports big-endian, so the LVGL component compiles with
    LV_COLOR_16_SWAP=1 and reads every 16-bit pixel byte-swapped. The `image:`
    component, however, defaults RGB565 to little-endian, and nothing validates
    the two against each other - the config checks out fine and the firmware
    then renders visibly wrong colours. Pin it to match LVGL.

    `transparency` stays at the default `opaque` because the frames already
    carry the page background (see module docstring).
    """
    lines = [
        "# GENERATED by tools/gen_va_flow.py -- do not edit by hand.",
        "# Siri-style voice assistant ribbons, pulled in by `image: !include`.",
        "# %d frames per state, %dx%d, RGB565 opaque = %.0f KB flash each."
        % (NFRAMES, W, H, W * H * 2 * NFRAMES / 1024),
        "# byte_order MUST stay big_endian to match LVGL's LV_COLOR_16_SWAP=1;",
        "# the image component would otherwise default to little-endian and",
        "# silently byte-swap every colour.",
        "",
    ]
    for name in names:
        short = name.replace("va_flow_", "")
        lines.append("# %s" % name)
        for f in range(NFRAMES):
            lines.append("- platform: file")
            lines.append("  id: va_%s_%02d" % (short, f))
            lines.append("  file: images/%s/f%02d.png" % (name, f))
            lines.append("  type: RGB565")
            lines.append("  byte_order: big_endian")
        lines.append("")
    path = os.path.join(HERE, "va_flow_images.yaml")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    print("wrote va_flow_images.yaml (%d images)" % (NFRAMES * len(names)))


if __name__ == "__main__":
    names = ["va_flow_listen", "va_flow_think", "va_flow_speak"]
    total = 0
    total += build("va_flow_listen", LISTEN_WAVES)
    total += build("va_flow_think", THINK_WAVES)
    total += build("va_flow_speak", SPEAK_WAVES)
    emit_image_yaml(names)
    print("total added flash: %.2f MB" % (total / 1048576))
