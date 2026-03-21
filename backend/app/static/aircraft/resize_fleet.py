#!/usr/bin/env python3
"""Resize all fleet aircraft images to 480x480 with white background.

Usage:
    pip install Pillow
    python resize_fleet.py

Run this in the backend/app/static/aircraft/ directory.
It will process all PNG/JPG/JPEG/WEBP files and output standardized PNGs.
"""

from pathlib import Path
from PIL import Image

TARGET_SIZE = (480, 480)
QUALITY = 95

# Map input filenames to output filenames
FILE_MAP = {
    "M4TD": "dji_m4td_official.png",
    "M30T": "dji_m30t_official.png",
    "M3P": "dji_mavic3pro_official.png",
    "Avata2": "dji_avata2_official.png",
    "FPV": "dji_fpv_official.png",
    "M5P": "dji_mini5pro_official.png",
}

here = Path(__file__).parent


def resize_image(src: Path, dest: Path):
    """Resize image to TARGET_SIZE, centered on white background."""
    img = Image.open(src)
    img = img.convert("RGBA")

    # Calculate scale to fit within target while preserving aspect ratio
    w, h = img.size
    scale = min(TARGET_SIZE[0] / w, TARGET_SIZE[1] / h) * 0.85  # 85% fill with padding
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center on white background
    bg = Image.new("RGBA", TARGET_SIZE, (255, 255, 255, 255))
    offset = ((TARGET_SIZE[0] - new_w) // 2, (TARGET_SIZE[1] - new_h) // 2)
    bg.paste(img, offset, img)  # Use alpha as mask for transparency

    # Save as PNG (keeps clean edges)
    bg = bg.convert("RGB")
    bg.save(dest, "PNG", optimize=True)
    print(f"  {src.name} -> {dest.name} ({w}x{h} -> {TARGET_SIZE[0]}x{TARGET_SIZE[1]})")


def main():
    print(f"Resizing fleet images to {TARGET_SIZE[0]}x{TARGET_SIZE[1]}...\n")
    processed = 0

    for src_file in sorted(here.glob("*")):
        if src_file.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if src_file.name == Path(__file__).name:
            continue

        # Check if filename stem matches any key in FILE_MAP
        stem = src_file.stem
        dest_name = None
        for key, mapped_name in FILE_MAP.items():
            if key.lower() in stem.lower():
                dest_name = mapped_name
                break

        if dest_name:
            dest = here / dest_name
            resize_image(src_file, dest)
            # Remove original if it had a different name
            if src_file.name != dest_name:
                src_file.unlink()
                print(f"    Removed original: {src_file.name}")
            processed += 1
        else:
            # Not in map — just resize in place with standard naming
            print(f"  Skipping {src_file.name} (not in FILE_MAP)")

    print(f"\nDone! Processed {processed} images.")
    print("You can now delete this script: rm resize_fleet.py")


if __name__ == "__main__":
    main()
