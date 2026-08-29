"""Doubao mascot cutout for the Magic Dial VA page.

One step:

1. Background removal. The official 1024x1024 icon is a character on a flat
   light-blue disc over white corners. Both background colours also occur
   *inside* the character (white collar, pale skin), so a plain colour threshold
   would punch holes in the face. Background-coloured pixels are therefore
   labelled into connected components and only the components touching the image
   border are erased -- the collar is enclosed by the body and survives.

2. Nothing else. The artwork is shipped in the icon's own colours -- an earlier
   revision re-tinted the character's top to a "scarf red", which was wrong: the
   current Doubao icon has no scarf at all. The page accent instead follows the
   icon's own light blue (#CAE4FF, its most common non-white pixel).
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = "tools/doubao_icon_raw.png"  # official 1024x1024 App Store artwork
OUT = "images/doubao_avatar.png"
SIZE = 112  # rendered size on the 360x360 dial, roughly an app-icon block

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
print("size", out.size)

out.resize((out.size[0] * 3, out.size[1] * 3), Image.NEAREST).save(
    "tools/doubao_avatar_preview.png"
)
