"""Build deterministic HANGAR icon sizes from the supplied NIGHTFALL emblem."""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "nightfall-emblem-crop.png"
ICO = ROOT / "assets" / "HANGAR.ico"
WEB_ICO = ROOT / "web" / "favicon.ico"
WEB_PNG = ROOT / "web" / "icon-256.png"

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BG = (24, 24, 24, 255)
PLATE = (32, 32, 32, 255)
STEEL = (176, 176, 176, 255)
STEEL_DARK = (124, 124, 124, 255)
ACCENT = (150, 168, 178, 255)


def _rounded_mask(size: int, radius: float) -> Image.Image:
    scale = 4
    big = Image.new("L", (size * scale, size * scale), 0)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        (0, 0, size * scale - 1, size * scale - 1),
        radius=max(1, int(size * scale * radius)),
        fill=255,
    )
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _small_icon(size: int) -> Image.Image:
    scale = 8
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(s * 0.16)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=PLATE, outline=(72, 72, 72, 255), width=max(1, scale))
    # At 16 px, the original feather strokes turn to mud. Keep the shield and
    # the two strongest wing planes instead, so the emblem stays identifiable.
    w = max(scale, int(s * 0.035))
    shield = [
        (int(s * 0.50), int(s * 0.14)),
        (int(s * 0.77), int(s * 0.20)),
        (int(s * 0.75), int(s * 0.58)),
        (int(s * 0.50), int(s * 0.86)),
        (int(s * 0.25), int(s * 0.58)),
        (int(s * 0.23), int(s * 0.20)),
    ]
    draw.polygon(shield, outline=STEEL_DARK, width=w)
    draw.polygon(
        [
            (int(s * 0.48), int(s * 0.23)),
            (int(s * 0.36), int(s * 0.46)),
            (int(s * 0.46), int(s * 0.72)),
            (int(s * 0.52), int(s * 0.58)),
            (int(s * 0.49), int(s * 0.38)),
        ],
        fill=STEEL,
    )
    draw.polygon(
        [
            (int(s * 0.53), int(s * 0.34)),
            (int(s * 0.72), int(s * 0.43)),
            (int(s * 0.66), int(s * 0.55)),
            (int(s * 0.54), int(s * 0.51)),
        ],
        fill=STEEL_DARK,
    )
    draw.polygon(
        [
            (int(s * 0.35), int(s * 0.73)),
            (int(s * 0.64), int(s * 0.74)),
            (int(s * 0.50), int(s * 0.81)),
        ],
        fill=STEEL,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _large_icon(size: int) -> Image.Image:
    source = Image.open(SOURCE).convert("RGBA")
    # The supplied decal is a vertical card. Fit the emblem into a square
    # Windows icon rather than squeezing the whole poster.
    width, height = source.size
    pad = max(1, int(size * 0.06))
    inner = Image.new("RGBA", (size, size), BG)
    emblem = source.resize((size - 2 * pad, size - 2 * pad), Image.Resampling.LANCZOS)
    inner.paste(emblem, (pad, pad), emblem)
    mask = _rounded_mask(size, 0.16)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(inner, (0, 0), mask)
    return output


def write_ico(images: list[Image.Image], path: Path) -> None:
    """Write PNG-compressed ICO entries; Vista and later support this format."""
    payloads = []
    for image in images:
        stream = io.BytesIO()
        image.save(stream, format="PNG", optimize=True)
        payloads.append(stream.getvalue())
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = []
    for image, payload in zip(images, payloads):
        width = 0 if image.width == 256 else image.width
        height = 0 if image.height == 256 else image.height
        entries.append(
            struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(payload), offset)
        )
        offset += len(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(entries) + b"".join(payloads))


def main() -> None:
    images = []
    for size in SIZES:
        images.append(_small_icon(size) if size <= 32 else _large_icon(size))
    write_ico(images, ICO)
    write_ico(images, WEB_ICO)
    images[-1].save(WEB_PNG, format="PNG", optimize=True)
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes), {WEB_ICO}, {WEB_PNG}")


if __name__ == "__main__":
    main()
