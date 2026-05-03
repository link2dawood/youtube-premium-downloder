#!/usr/bin/env python3
"""
Render the PixelCatch icon set.

Produces, into the same directory as this script:
  icon-16.png, icon-32.png, icon-48.png, icon-128.png  -- for manifest.json
  store-icon-128.png                                    -- Chrome Web Store icon
  promo-tile-440x280.png                                -- Web Store small promo

Design:
  - Rounded-square badge with a diagonal blue gradient (#2563eb -> #0ea5e9)
  - Centered solid rounded play triangle (▶) in white — instantly readable
    at every size from 16px up
  - Small "pixel trail" of three squares behind the triangle, riffing on the
    PixelCatch name without breaking the play silhouette
  - All assets share the same render pipeline, so a tweak here updates them
    all consistently

Re-run after any tweak:
    python3 icons/render_icons.py
"""
import os
from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Brand palette (sampled from popup.css gradients).
GRADIENT_TOP_LEFT = (37, 99, 235)       # #2563eb royal blue
GRADIENT_BOTTOM_RIGHT = (14, 165, 233)  # #0ea5e9 sky blue
WHITE = (255, 255, 255, 255)

# Render scale for crisp anti-aliasing — render at SCALE*size, downsample.
SCALE = 4


# ---------- Primitives ----------

def diagonal_gradient(size, color_a, color_b):
    grad = Image.new("RGB", (size, size), color_a)
    px = grad.load()
    inv = 1.0 / max(1, (size - 1) * 2)
    for y in range(size):
        for x in range(size):
            t = (x + y) * inv
            r = round(color_a[0] * (1 - t) + color_b[0] * t)
            g = round(color_a[1] * (1 - t) + color_b[1] * t)
            b = round(color_a[2] * (1 - t) + color_b[2] * t)
            px[x, y] = (r, g, b)
    return grad


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    return mask


# ---------- Icon ----------

def draw_play_triangle(draw, size, color, *, with_pixels=True):
    """
    Draw a solid play triangle (▶) and an optional 3-pixel decorative
    trail. `size` is the canvas edge.
    """
    # Triangle bounding box.
    tw = size * 0.42     # width
    th = size * 0.50     # height
    # Slight rightward optical shift — a play triangle's visual mass sits
    # left of its geometric center.
    cx = size * 0.555
    cy = size * 0.5

    left = cx - tw / 2
    right = cx + tw / 2
    top = cy - th / 2
    bottom = cy + th / 2
    mid_y = cy

    # Clean sharp triangle — standard for play icons.
    draw.polygon(
        [(left, top), (left, bottom), (right, mid_y)],
        fill=color,
    )

    if not with_pixels:
        return

    # Three small "pixel" squares trailing behind the triangle's left edge.
    # Pure decorative accent that keeps the name "Pixel"-relevant without
    # interfering with the play silhouette.
    pixel = size * 0.075
    gap = size * 0.030
    trail_x = left - pixel - size * 0.045
    pixel_r = max(1, round(pixel * 0.16))
    total_h = 3 * pixel + 2 * gap
    start_y = mid_y - total_h / 2
    for i in range(3):
        py = start_y + i * (pixel + gap)
        draw.rounded_rectangle(
            (trail_x, py, trail_x + pixel, py + pixel),
            radius=pixel_r,
            fill=color,
        )


def render_icon(size):
    big = size * SCALE
    radius = round(big * 0.22)

    # Background: gradient masked to a rounded square.
    bg_rgb = diagonal_gradient(big, GRADIENT_TOP_LEFT, GRADIENT_BOTTOM_RIGHT)
    bg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    bg.paste(bg_rgb, (0, 0), rounded_mask(big, radius))

    # Subtle drop-shadow under the foreground so the white doesn't look flat.
    shadow_layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw_play_triangle(
        ImageDraw.Draw(shadow_layer),
        big,
        (0, 30, 80, 90),
        with_pixels=size >= 32,  # skip pixel trail on tiny icons
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(big * 0.012))
    offset = max(1, round(big * 0.008))
    bg.alpha_composite(shadow_layer, (offset, offset))

    # Foreground.
    fg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw_play_triangle(
        ImageDraw.Draw(fg),
        big,
        WHITE,
        with_pixels=size >= 32,
    )
    bg.alpha_composite(fg)

    return bg.resize((size, size), Image.LANCZOS)


# ---------- Promo tile ----------

def _load_font(size):
    from PIL import ImageFont
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_promo_tile(width=440, height=280):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Full-bleed gradient background.
    bg = diagonal_gradient(max(width, height), GRADIENT_TOP_LEFT, GRADIENT_BOTTOM_RIGHT)
    bg = bg.resize((width, height), Image.LANCZOS).convert("RGBA")
    img.alpha_composite(bg)

    # Icon on the left, vertically centered.
    badge_size = 140
    badge = render_icon(badge_size)
    badge_x = 22
    img.alpha_composite(badge, (badge_x, (height - badge_size) // 2))

    # Text block — measure with textbbox and shrink to fit.
    text_x = badge_x + badge_size + 22
    available_w = width - text_x - 14

    draw = ImageDraw.Draw(img)

    # Pick the largest title size that fits the available width.
    title_text = "PixelCatch"
    title_size = 56
    while title_size > 18:
        f = _load_font(title_size)
        bbox = draw.textbbox((0, 0), title_text, font=f)
        if (bbox[2] - bbox[0]) <= available_w:
            title_font = f
            break
        title_size -= 2
    else:
        title_font = _load_font(20)

    sub_font = _load_font(16)
    sub1 = "Premium & members-only"
    sub2 = "videos, up to 4K."

    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_h = title_bbox[3] - title_bbox[1]
    sub_h = draw.textbbox((0, 0), sub1, font=sub_font)[3]

    block_h = title_h + 14 + sub_h * 2 + 4
    title_y = (height - block_h) // 2
    sub1_y = title_y + title_h + 14
    sub2_y = sub1_y + sub_h + 4

    draw.text((text_x, title_y), title_text, font=title_font, fill=WHITE)
    draw.text((text_x, sub1_y), sub1, font=sub_font, fill=(225, 240, 255, 255))
    draw.text((text_x, sub2_y), sub2, font=sub_font, fill=(225, 240, 255, 255))
    return img


def main():
    for size in (16, 32, 48, 128):
        path = os.path.join(OUT_DIR, f"icon-{size}.png")
        render_icon(size).save(path, "PNG", optimize=True)
        print(f"  wrote {path}")

    store = os.path.join(OUT_DIR, "store-icon-128.png")
    render_icon(128).save(store, "PNG", optimize=True)
    print(f"  wrote {store}")

    promo = os.path.join(OUT_DIR, "promo-tile-440x280.png")
    render_promo_tile().save(promo, "PNG", optimize=True)
    print(f"  wrote {promo}")


if __name__ == "__main__":
    main()
