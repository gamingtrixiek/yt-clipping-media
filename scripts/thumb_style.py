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
