"""Thumbnail colour grade modelled on the owner's own edit of the Part 8 thumbnail (26 Nov 2026):
roughly double saturation, punchier contrast, deeper midtones/blacks, slight teal-blue lift,
dark top band so the headline pops. Apply to the background frame BEFORE drawing text/badges."""
from PIL import Image, ImageEnhance
import numpy as np

def vibrant(img, sat=1.8, contrast=1.55, gamma=1.45, tint=(1.0, 1.06, 1.03), top_dark=0.08):
    img = ImageEnhance.Color(img.convert("RGB")).enhance(sat)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    a = np.asarray(img).astype(np.float32) / 255.0
    a = np.clip(a ** gamma * np.array(tint, np.float32), 0, 1)
    h = a.shape[0]
    if top_dark:                                   # fade to black across the top ~8 % of the frame
        band = int(h * top_dark * 2)
        ramp = np.clip(np.linspace(0.0, 2.0, band), 0, 1)[:, None, None]
        a[:band] *= ramp
    return Image.fromarray((a * 255).astype(np.uint8))


def gta6_label(img, text="GTA 6", corner="top-right", scale=1.0):
    """Generic 'GTA 6' tag (not the official logo): bold Anton text on a sunset pink->orange pill
    with a black outline and soft shadow. Sized relative to the image so it works at 1280 or 3840 wide."""
    from PIL import ImageDraw, ImageFont, ImageFilter
    img = img.convert("RGB")
    W, H = img.size
    u = W / 1280 * scale
    font = ImageFont.truetype("/usr/share/fonts/truetype/google-fonts/ofl/anton/Anton-Regular.ttf", int(96 * u))
    tw = ImageDraw.Draw(img).textlength(text, font=font)
    pw, ph = int(tw + 64 * u), int(128 * u)
    m = int(28 * u)
    x = W - pw - m if "right" in corner else m
    y = m if "top" in corner else H - ph - m
    # shadow
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).rounded_rectangle((x + 8 * u, y + 10 * u, x + pw + 8 * u, y + ph + 10 * u), radius=int(26 * u), fill=170)
    sh = sh.filter(ImageFilter.GaussianBlur(10 * u))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, sh)
    # gradient pill
    grad = Image.new("RGB", (pw, ph))
    gd = ImageDraw.Draw(grad)
    for i in range(pw):
        t = i / max(1, pw - 1)
        gd.line([(i, 0), (i, ph)], fill=(int(255), int(40 + 120 * t), int(150 - 120 * t)))   # hot pink -> orange
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=int(26 * u), fill=255)
    img.paste(grad, (x, y), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + pw, y + ph), radius=int(26 * u), outline=(0, 0, 0), width=max(2, int(7 * u)))
    bb = d.textbbox((0, 0), text, font=font)
    d.text((x + (pw - (bb[2] - bb[0])) / 2 - bb[0], y + (ph - (bb[3] - bb[1])) / 2 - bb[1]), text, font=font,
           fill=(255, 255, 255), stroke_width=max(2, int(6 * u)), stroke_fill=(0, 0, 0))
    return img
