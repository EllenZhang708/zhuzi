"""Build the app icon from AppIcon.png (the artwork, with transparent corners).

Writes icon.ico (all Windows sizes), icon.icns (macOS), icon.png (for the
window header) and icon-preview.png next to this script. Small sizes get a
light sharpen so the artwork doesn't go soft when shrunk.
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


def mac_icon(art: Image.Image) -> Image.Image:
    """Apple's grid: the tile is 824 of 1024 px, centred, over a soft shadow,
    so the icon sits at the same size as the system's own in the Dock."""
    canvas, tile, top = 1024, 824, 100
    body = art.crop(art.getchannel("A").getbbox()).resize((tile, tile), Image.LANCZOS)
    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    shade = body.getchannel("A").point(lambda a: a * 0.3)
    shadow.paste((0, 0, 0, 255), (top, top + 12), shade)
    out = shadow.filter(ImageFilter.GaussianBlur(14))
    out.alpha_composite(body, (top, top))
    return out


def main():
    art = Image.open(SOURCE).convert("RGBA")
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [render(art, n) for n in sizes]
    images[-1].save(HERE / "icon.ico", sizes=[(n, n) for n in sizes], append_images=images[:-1])
    mac = mac_icon(art)
    mac.save(HERE / "icon.icns", append_images=[render(mac, n) for n in (16, 32, 64, 128, 256, 512)])
    render(art, 512).save(HERE / "icon.png")
    preview = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 276), (244, 241, 234, 255))
    x = 10
    for img in images:
        preview.alpha_composite(img, (x, 266 - img.height))
        x += img.width + 20
    preview.save(HERE / "icon-preview.png")
    print("wrote", HERE / "icon.ico", "and", HERE / "icon.icns")


if __name__ == "__main__":
    main()
