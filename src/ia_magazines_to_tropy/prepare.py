"""Step 2: make the images the model reads: PNGs no larger than 2048 px on the long edge.

    ia2tropy prepare byte

Reads data/<profile>/originals/*.jp2 and writes data/<profile>/model-input/*.png.
The originals are kept for the final collection. Existing PNGs are skipped.
"""
import argparse

from PIL import Image, ImageOps

from .config import load_profile

Image.MAX_IMAGE_PIXELS = None   # magazine scans can be very large


def main(argv=None):
    p = argparse.ArgumentParser(prog="ia2tropy prepare", description=__doc__.split("\n")[0])
    p.add_argument("profile")
    p.add_argument("--size", type=int, default=2048, help="long edge in pixels (default 2048)")
    a = p.parse_args(argv)
    profile = load_profile(a.profile)
    profile.model_input.mkdir(parents=True, exist_ok=True)
    sources = sorted(profile.originals.glob("*.jp2"))
    if not sources:
        raise SystemExit(f"No .jp2 files in {profile.originals}; run 'ia2tropy fetch {a.profile}' first")
    made = 0
    for n, src in enumerate(sources, 1):
        dest = profile.model_input / f"{src.stem}.png"
        if dest.exists():
            continue
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((a.size, a.size), Image.Resampling.LANCZOS)   # only shrinks
            im.save(dest, format="PNG")
        made += 1
        print(f"[{n}/{len(sources)}] {dest.name} {im.size[0]}x{im.size[1]}", flush=True)
    print(f"{made} new images in {profile.model_input} ({len(sources) - made} already there)")
