"""Doubao mascot cutout for the Magic Dial VA page.

Two steps:

1. Background removal. The official 1024x1024 icon is a character on a flat
   light-blue disc over white corners. Both background colours also occur
   *inside* the character (white collar, pale skin), so a plain colour threshold
   would punch holes in the face. Background-coloured pixels are therefore
   labelled into connected components and only the components touching the image
   border are erased -- the collar is enclosed by the body and survives.

2. Shirt -> scarf red. The iOS icon currently ships the plain (scarf-less)
   character, but the dial's assistant page is themed on the Doubao scarf red,
   so the near-neutral dark top is re-tinted to that red while its original
   luminance (folds, shading) is preserved. The hair is deliberately untouched:
   it is a warm brown, i.e. clearly saturated, while the top is neutral, so a
   saturation test separates them without any hand-drawn mask.
"""

from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = "tools/doubao_icon_raw.png"  # official 1024x1024 App Store artwork
OUT = "images/doubao_avatar.png"
SIZE = 112  # rendered size on the 360x360 dial, roughly an app-icon block
SCARF_RGB = (0xE8, 0x39, 0x2A)

img = Image.open(SRC).convert("RGB")
a = np.asarray(img).astype(np.int16)
h, w, _ = a.shape

blue = np.array([200, 223, 248])
white = np.array([255, 255, 255])
bg_like = (np.abs(a - blue).sum(axis=2) < 60) | (np.abs(a - white).sum(axis=2) < 30)

labels, _ = ndimage.label(bg_like)
border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
border.discard(0)
bg = np.isin(labels, list(border))

# --- shirt re-tint ---------------------------------------------------------
rgb = a.astype(np.float32)
mx = rgb.max(axis=2)
mn = rgb.min(axis=2)
sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
rows = np.arange(h)[:, None] * np.ones((1, w))

shirt = (~bg) & (mx < 110) & (sat < 0.30) & (rows > 0.70 * h)
shirt = ndimage.binary_opening(shirt, np.ones((5, 5)))
shirt = ndimage.binary_closing(shirt, np.ones((9, 9)))

hs, ss, _ = colorsys.rgb_to_hsv(*[c / 255 for c in SCARF_RGB])
lum = (mx + mn) / 2 / 255.0
# Lift the very dark top into a readable red: keep its relative shading but map
# the range onto 0.32..0.95 value so folds stay visible on the black page.
val = 0.32 + 0.63 * np.clip(lum / max(lum[shirt].max(), 1e-6), 0, 1) if shirt.any() else lum
vs = np.clip(val, 0, 1)
# Vectorised HSV->RGB for one fixed hue/saturation: the scarf red sits in the
# first hue sextant, so the channel assignment is constant.
c = vs * ss
xx = c * (1 - abs((hs * 6) % 2 - 1))
m = vs - c
rr, gg, bb = c + m, xx + m, m

out_rgb = a.astype(np.float32)
out_rgb[shirt, 0] = rr[shirt] * 255
out_rgb[shirt, 1] = gg[shirt] * 255
out_rgb[shirt, 2] = bb[shirt] * 255
img = Image.fromarray(np.clip(out_rgb, 0, 255).astype(np.uint8))

# --- alpha + crop ----------------------------------------------------------
alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))
# One pixel of erosion plus a light blur: the disc edge is anti-aliased, so the
# outermost ring of character pixels carries blue tint that would otherwise read
# as a halo against the dial's pure black page.
alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))

rgba = img.copy()
rgba.putalpha(alpha)
rgba = rgba.crop(rgba.getbbox())
ow, oh = rgba.size
scale = SIZE / max(ow, oh)
rgba = rgba.resize(
    (max(1, round(ow * scale)), max(1, round(oh * scale))), Image.LANCZOS
)

# Flatten onto pure black instead of shipping an alpha channel.
#
# WHY (2026-08-29 bug): with `type: RGB565` + `transparency: alpha_channel`,
# ESPHome tells LVGL the buffer is LV_COLOR_FORMAT_RGB565A8, but it *emits*
# 3 interleaved bytes per pixel (RGB565 + A). LVGL's RGB565A8 is planar: a
# w*h*2 colour plane followed by a separate w*h alpha plane. So LVGL reads the
# colour plane straight through the interleaved alpha bytes, and every pixel is
# progressively shifted by one byte -- which is exactly the rainbow-gradient
# face seen on the device. Nothing in the config can fix that; the format
# itself has to go.
#
# page_va's background is pinned to pure 0x000000 (and on this round LCD true
# black merges with the unlit bezel), so a black-matted opaque image is visually
# identical to a transparent one, while using the plain RGB565 path that the old
# va_arc frames already proved correct on this build.
out = Image.new("RGB", rgba.size, (0, 0, 0))
out.paste(rgba, (0, 0), rgba)
out.save(OUT)
print("shirt px", int(shirt.sum()), "-> size", out.size)

out.resize((out.size[0] * 3, out.size[1] * 3), Image.NEAREST).save(
    "tools/doubao_avatar_preview.png"
)
