#!/usr/bin/env python3
"""Downscale a booth photo to the SELPHY CP1300's own print grid.

Bench experiment for B-1 (FSD §17.3). Today CUPS' imagetoraster shrinks the
camera JPEG (e.g. 4509x3052) ~2.4x with plain interpolation and no low-pass
filter, which aliases fine detail. This script does the shrink once, properly,
so CUPS only has to place the pixels 1:1.

Target, from the selphy PPD (Gutenprint 5.3.4, canon-cp1300):
  PageSize Postcard = 299.52 x 449.28 pt, Resolution 300 dpi, StpBorderless True
  -> 4.16 x 6.24 in -> 1248 x 1872 px (portrait). CUPS' imagetoraster rounds the
  page height down to 449 pt and builds a 1248 x 1871 raster; an image of exactly
  that size at ppi=300 passes through bit for bit (checked with cupsfilter), while
  1872 rows make it resample the whole image again. So the target is 1248 x 1871;
  the one missing row (0.08 mm) is inside the bleed.
The paper is 100 x 148 mm (~1181 x 1748 px); the extra ~2.7-3.3 % per edge is
the borderless bleed the printer prints past the paper edge. The image is
therefore cropped to fill the whole grid (never letterboxed), so no white edge
can appear; the outer ~30 px per edge will not be on the paper.

Steps: EXIF orientation -> sRGB (if an ICC profile is embedded) -> rotate to
portrait -> centre crop to 2:3 -> Lanczos resample (Pillow scales the kernel
with the reduction factor, i.e. a proper anti-alias filter) -> light unsharp
mask for the dye-sub softness -> lossless PNG.

Send the result with:  lp -d selphy -o ppi=300 out.png
(no fit-to-page / landscape: the image is already portrait at 300 dpi).

Usage: prescale-for-selphy.py <in.jpg> <out.png> [--no-sharpen]
"""
import io
import sys
import time

from PIL import Image, ImageCms, ImageFilter, ImageOps

PAGE_W, PAGE_H = 1248, 1871          # portrait, CUPS raster size at 300 dpi (see above)
SHARPEN = ImageFilter.UnsharpMask(radius=0.8, percent=60, threshold=2)


def prescale(src, dst, sharpen=True):
    t0 = time.monotonic()
    im = Image.open(src)
    in_size = im.size
    im = ImageOps.exif_transpose(im)

    icc = im.info.get("icc_profile")
    if icc:
        im = ImageCms.profileToProfile(
            im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
            ImageCms.createProfile("sRGB"), outputMode="RGB")
    im = im.convert("RGB")

    if im.width > im.height:                 # booth photos are landscape
        im = im.transpose(Image.Transpose.ROTATE_90)

    # Centre crop to the page aspect, in source pixels.
    target = PAGE_W / PAGE_H
    if im.width / im.height > target:
        w = round(im.height * target)
        box = ((im.width - w) // 2, 0, (im.width - w) // 2 + w, im.height)
    else:
        h = round(im.width / target)
        box = (0, (im.height - h) // 2, im.width, (im.height - h) // 2 + h)

    im = im.resize((PAGE_W, PAGE_H), Image.Resampling.LANCZOS, box=box)
    if sharpen:
        im = im.filter(SHARPEN)

    im.save(dst, "PNG", dpi=(300, 300))
    ms = (time.monotonic() - t0) * 1000
    print(f"prescale: {src} {in_size[0]}x{in_size[1]} crop {box} -> {dst} "
          f"{PAGE_W}x{PAGE_H} @300dpi lanczos{' +unsharp' if sharpen else ''}"
          f"{' icc->sRGB' if icc else ''} in {ms:.0f} ms")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    prescale(args[0], args[1], sharpen="--no-sharpen" not in sys.argv)
