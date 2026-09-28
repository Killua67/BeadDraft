"""
色彩工具：sRGB -> CIELAB 转换、CIEDE2000 色差。

为什么不用 RGB 距离：RGB 空间和人眼感知不均匀，直接算欧氏距离找最近色，
肤色容易偏灰偏紫、暗部颜色容易选错。CIEDE2000（ΔE00）是目前最接近人眼感受的色差公式。
ΔE00 约 1 表示刚好能察觉的差异，大于 5 属于明显不同。

所有函数都支持 numpy 广播，可以一次计算「N 个像素 × P 个色卡颜色」的距离矩阵。
"""

import numpy as np

# D65 白点
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])
# 线性 sRGB -> XYZ 转换矩阵
_RGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """'#FFAA00' -> (255, 170, 0)。"""
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb) -> str:
    """(255, 170, 0) -> '#FFAA00'。"""
    r, g, b = (int(round(v)) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB（0~255，形状 [..., 3]）转 CIELAB（D65）。"""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = linear @ _RGB_TO_XYZ.T / _WHITE_D65
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def relative_luminance(rgb) -> float:
    """相对亮度（0~1），用于决定色块上的文字用黑色还是白色。"""
    c = np.asarray(rgb[:3], dtype=np.float64) / 255.0
    linear = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    return float(linear @ np.array([0.2126, 0.7152, 0.0722]))


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """
    CIEDE2000 色差（kL = kC = kH = 1），参考 Sharma 2005 论文实现。
    lab1、lab2 形状 [..., 3]，按 numpy 规则广播。
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    # 1. 修正 a*，计算 C'、h'
    c_bar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    c_bar7 = c_bar ** 7
    g = 0.5 * (1 - np.sqrt(c_bar7 / (c_bar7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    chroma_zero = (c1p * c2p) == 0

    # 2. ΔL'、ΔC'、ΔH'
    dlp = L2 - L1
    dcp = c2p - c1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(chroma_zero, 0.0, dhp)
    d_hp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp) / 2)

    # 3. 平均值与加权函数
    lp_bar = (L1 + L2) / 2
    cp_bar = (c1p + c2p) / 2
    h_sum = h1p + h2p
    hp_bar = np.where(
        np.abs(h1p - h2p) <= 180,
        h_sum / 2,
        np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2),
    )
    hp_bar = np.where(chroma_zero, h_sum, hp_bar)

    t = (
        1
        - 0.17 * np.cos(np.radians(hp_bar - 30))
        + 0.24 * np.cos(np.radians(2 * hp_bar))
        + 0.32 * np.cos(np.radians(3 * hp_bar + 6))
        - 0.20 * np.cos(np.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * np.exp(-(((hp_bar - 275) / 25) ** 2))
    cp_bar7 = cp_bar ** 7
    rc = 2 * np.sqrt(cp_bar7 / (cp_bar7 + 25.0 ** 7))
    sl = 1 + 0.015 * (lp_bar - 50) ** 2 / np.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc

    return np.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (d_hp / sh) ** 2 + rt * (dcp / sc) * (d_hp / sh)
    )


def delta_e_matrix(lab_pixels: np.ndarray, lab_palette: np.ndarray, chunk: int = 4096) -> np.ndarray:
    """
    计算 N 个像素到 P 个色卡颜色的 ΔE00 距离矩阵（N×P，float32）。
    分块计算，避免大图时中间数组占用过多内存。
    """
    n = lab_pixels.shape[0]
    out = np.empty((n, lab_palette.shape[0]), dtype=np.float32)
    for start in range(0, n, chunk):
        block = lab_pixels[start:start + chunk, None, :]
        out[start:start + chunk] = delta_e_2000(block, lab_palette[None, :, :])
    return out
