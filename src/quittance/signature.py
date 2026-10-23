"""Préparation de l'image de signature : redimensionnement + fond blanc rendu transparent."""
from __future__ import annotations

import io

from PIL import Image

MAX_W, MAX_H = 600, 240


def process(raw: bytes, transparent: bool = True) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img.load()
    img = img.convert("RGBA")
    img.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
    if transparent:
        px = img.load()
        w, h = img.size
        for yy in range(h):
            for xx in range(w):
                r, g, b, a = px[xx, yy]
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                if lum > 215:
                    px[xx, yy] = (r, g, b, 0)
                elif lum > 150:
                    px[xx, yy] = (r, g, b, int(a * (215 - lum) / 65))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
