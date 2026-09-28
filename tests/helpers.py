"""测试辅助函数。"""

import io

import numpy as np
from PIL import Image, ImageDraw


def make_image(transparent_bg: bool = False, size=(300, 200)) -> bytes:
    """测试图：白底（或透明底）+ 渐变圆 + 两个黑色眼睛。"""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., :3] = 255
    arr[..., 3] = 0 if transparent_bg else 255
    circle = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (h * 0.42) ** 2
    arr[circle, 0] = (xx[circle] * 255 / w).astype(np.uint8)
    arr[circle, 1] = (yy[circle] * 255 / h).astype(np.uint8)
    arr[circle, 2] = 180
    arr[circle, 3] = 255
    img = Image.fromarray(arr, "RGBA")
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.38, h * 0.35, w * 0.44, h * 0.44), fill=(0, 0, 0, 255))
    d.ellipse((w * 0.56, h * 0.35, w * 0.62, h * 0.44), fill=(0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
