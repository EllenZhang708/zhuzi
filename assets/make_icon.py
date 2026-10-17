"""Build the app icon from AppIcon.png (the artwork, with transparent corners).

Writes icon.ico (all Windows sizes), icon.png (for the window header) and
icon-preview.png next to this script. Small sizes get a light sharpen so the
artwork doesn't go soft when shrunk.
"""
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "AppIcon.png"


def render(art: Image.Image, size: int) -> Image.Image:
    out = art.resize((size, size), Image.LANCZOS, reducing_gap=3.0)
    if size <= 48:
        # Sharpen colour only; sharpening alpha would ring around the rounded edge.
        rgb = out.convert("RGB").filter(ImageFilter.UnsharpMask(radius=0.6, percent=70, threshold=0))
        rgb.putalpha(out.getchannel("A"))
        out = rgb
    return out


def main():
    art = Image.open(SOURCE).convert("RGBA")
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [render(art, n) for n in sizes]
    images[-1].save(HERE / "icon.ico", sizes=[(n, n) for n in sizes], append_images=images[:-1])
    render(art, 512).save(HERE / "icon.png")
    preview = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 276), (244, 241, 234, 255))
    x = 10
    for img in images:
        preview.alpha_composite(img, (x, 266 - img.height))
        x += img.width + 20
    preview.save(HERE / "icon-preview.png")
    print("wrote", HERE / "icon.ico")


if __name__ == "__main__":
    main()
