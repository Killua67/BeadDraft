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


def to_png(arr: np.ndarray) -> bytes:
    """numpy 数组（H, W, 3 或 4）转 PNG 字节。"""
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def make_gradient_bg_image(size=(240, 240)) -> bytes:
    """渐变背景（上深蓝 -> 下浅蓝）+ 中间一个红色圆形主体。"""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    t = yy / h
    arr = np.stack([40 + 150 * t, 80 + 140 * t, np.full_like(t, 200.0)], axis=-1)
    circle = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (w * 0.3) ** 2
    arr[circle] = (220, 30, 40)
    return to_png(arr)


def make_border_subject_image(size=(240, 240)) -> bytes:
    """白底 + 一个碰到下边缘、带条纹纹理的主体（模拟照片中主体贴边的情况）。"""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.full((h, w, 3), 255.0)
    body = (np.abs(xx - w / 2) < w * 0.35) & (yy > h * 0.3)
    stripes = ((xx // 3 + yy // 3) % 2).astype(float)
    arr[body] = np.stack([120 + 60 * stripes, 80 + 40 * stripes, 40 + 20 * stripes], axis=-1)[body]
    return to_png(arr)
