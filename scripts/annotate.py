#!/usr/bin/env python3
"""Draw numbered callouts on a capture so a finding's "Do" can point at the exact spot.

  python annotate.py captures/orders__360__light.png --box 12,88,340,140 "Total shown before fees load" \
      --box 20,700,160,760 "Place order enabled too early" --out annotated/orders__360__light__do.png

  python annotate.py shot.png --box x1,y1,x2,y2 "label" [--box … "label"]... [--arrow x1,y1,x2,y2] [--crop x1,y1,x2,y2]
      [--out path] [--color "#e5484d"] [--scale 1.0] [--legend]

Each --box gets a number badge at its top-left and the label in a legend strip under the image
(default) or next to the box (--no-legend). --crop cuts the capture to the region of interest first
(coordinates are in the original image's pixels; boxes are too). Output defaults to
annotated/<name>__do.png next to the captures folder. Needs Pillow.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is needed: pip install pillow")


def font(size: int):
    for cand in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def parse_rect(s: str):
    parts = [int(float(p)) for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--box", nargs=2, action="append", default=[], metavar=("X1,Y1,X2,Y2", "LABEL"))
    ap.add_argument("--arrow", action="append", default=[], metavar="X1,Y1,X2,Y2", help="arrow from (x1,y1) to (x2,y2)")
    ap.add_argument("--crop", metavar="X1,Y1,X2,Y2")
    ap.add_argument("--out")
    ap.add_argument("--color", default="#e5484d")
    ap.add_argument("--scale", type=float, default=1.0, help="scale the output (e.g. 0.5 for huge full-page captures)")
    ap.add_argument("--no-legend", action="store_true", help="put labels beside boxes instead of a legend strip")
    a = ap.parse_args(argv)

    src = pathlib.Path(a.image)
    img = Image.open(src).convert("RGB")
    boxes = [(parse_rect(r), label) for r, label in a.box]
    arrows = [parse_rect(r) for r in a.arrow]
    ox = oy = 0
    if a.crop:
        cx1, cy1, cx2, cy2 = parse_rect(a.crop)
        img = img.crop((cx1, cy1, cx2, cy2))
        ox, oy = cx1, cy1

    draw = ImageDraw.Draw(img)
    w = max(2, round(min(img.size) / 250))
    f_badge, f_label = font(max(14, w * 7)), font(max(13, w * 6))
    for i, ((x1, y1, x2, y2), label) in enumerate(boxes, 1):
        x1, y1, x2, y2 = x1 - ox, y1 - oy, x2 - ox, y2 - oy
        draw.rectangle((x1, y1, x2, y2), outline=a.color, width=w)
        r = max(12, w * 6)
        bx, by = x1, y1 - r * 2 if y1 - r * 2 > 0 else y1
        draw.ellipse((bx, by, bx + 2 * r, by + 2 * r), fill=a.color)
        t = str(i)
        tw, th = draw.textbbox((0, 0), t, font=f_badge)[2:]
        draw.text((bx + r - tw / 2, by + r - th / 2 - 1), t, fill="white", font=f_badge)
        if a.no_legend:
            lx, ly = min(x2 + w * 2, img.width - 10), y1
            tw, th = draw.textbbox((0, 0), label, font=f_label)[2:]
            lx = min(lx, img.width - tw - 8)
            draw.rectangle((lx - 4, ly - 2, lx + tw + 4, ly + th + 4), fill="white", outline=a.color, width=1)
            draw.text((lx, ly), label, fill="#111", font=f_label)
    for (x1, y1, x2, y2) in arrows:
        x1, y1, x2, y2 = x1 - ox, y1 - oy, x2 - ox, y2 - oy
        draw.line((x1, y1, x2, y2), fill=a.color, width=w)
        # arrow head
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        L = w * 5
        for d in (math.pi / 7, -math.pi / 7):
            draw.line((x2, y2, x2 - L * math.cos(ang - d), y2 - L * math.sin(ang - d)), fill=a.color, width=w)

    if boxes and not a.no_legend:
        lh = (f_label.size if hasattr(f_label, "size") else 14) + 8
        r = lh // 2 - 2
        text_x = 10 + 2 * r + 10
        max_w = max(60, img.width - text_x - 8)
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

        def wrap(label: str) -> list[str]:
            words, lines, cur = label.split(), [], ""
            for wd in words:
                trial = (cur + " " + wd).strip()
                if probe.textlength(trial, font=f_label) <= max_w or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = wd
            return lines + [cur] if cur else lines

        wrapped = [wrap(label) for _, label in boxes]
        total_lines = sum(len(w) for w in wrapped)
        strip = Image.new("RGB", (img.width, 14 + lh * total_lines + 4 * len(boxes)), "white")
        d2 = ImageDraw.Draw(strip)
        y = 7
        for i, lines in enumerate(wrapped, 1):
            d2.ellipse((10, y, 10 + 2 * r, y + 2 * r), fill=a.color)
            t = str(i)
            tw, th = d2.textbbox((0, 0), t, font=f_label)[2:]
            d2.text((10 + r - tw / 2, y + r - th / 2 - 1), t, fill="white", font=f_label)
            for ln in lines:
                d2.text((text_x, y), ln, fill="#111", font=f_label)
                y += lh
            y += 4
        combined = Image.new("RGB", (img.width, img.height + strip.height), "white")
        combined.paste(img, (0, 0))
        combined.paste(strip, (0, img.height))
        img = combined

    if a.scale != 1.0:
        img = img.resize((max(1, int(img.width * a.scale)), max(1, int(img.height * a.scale))), Image.LANCZOS)

    out = pathlib.Path(a.out) if a.out else (src.parent.parent / "annotated" / f"{src.stem}__do.png" if src.parent.name else pathlib.Path("annotated") / f"{src.stem}__do.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
