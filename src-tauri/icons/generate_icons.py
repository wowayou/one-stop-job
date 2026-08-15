#!/usr/bin/env python3
"""Generate Tauri app icons from a source PNG.

Usage: python3 generate_icons.py source.png

Requires: pip install Pillow
"""
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Install Pillow first: pip install Pillow")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python3 generate_icons.py source.png")
    sys.exit(1)

src = Image.open(sys.argv[1]).convert("RGBA")
out_dir = Path(__file__).parent

# PNG icons
for size in [32, 128]:
    img = src.resize((size, size), Image.Resampling.LANCZOS)
    img.save(out_dir / f"{size}x{size}.png")

# 128x128@2x
img = src.resize((256, 256), Image.Resampling.LANCZOS)
img.save(out_dir / "128x128@2x.png")

# Windows .ico
img = src.resize((256, 256), Image.Resampling.LANCZOS)
img.save(out_dir / "icon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# macOS .icns
img.save(out_dir / "icon.icns")

print(f"Icons generated in {out_dir}")
