"""
去背景相关算法（颜色识别方式 + 与 AI 蒙版共用的后处理）。

颜色识别的思路（color_mask）：
  1. 取图片四周一圈像素，用 k-means 聚成最多 3 种颜色，只有「占比够大且足够平滑」的才当作背景色。
     背景通常是平滑的，而碰到图片边缘的主体（羽毛、毛发、花纹）纹理明显，这样可以避免把主体误删
  2. 从四周向内填充：像素与任一背景色的 ΔE00 小于容差即视为背景；
     或者与相邻背景像素几乎同色、且离背景色不太远，也视为背景（顺着渐变继续填充）
  3. 只从四周连通地填充，主体内部与背景同色的区域（如眼白）不会被误删
  4. 开运算去掉背景里残留的小噪点

这些计算都在「中等分辨率」（每格约 4 像素）上完成，最后由 converter 按覆盖率缩放到网格，
这样主体边缘不会混入背景色，也不会留下一圈杂边。
"""

from collections import deque

import numpy as np
from PIL import Image, ImageFilter

from app.core.logger import get_logger
from app.services.color import delta_e_2000, srgb_to_lab

logger = get_logger(__name__)

MIN_CLUSTER_SHARE = 0.15   # 边缘聚类占比低于该值视为碰到边缘的主体，不当作背景色
FLATNESS_LIMIT = 3.5       # 边缘聚类纹理强度（相邻像素色差的 75 分位）超过该值视为主体，不当作背景色
GRADIENT_STEP = 1 / 3      # 渐变跟随：相邻像素色差需小于 容差 × 该系数
GRADIENT_REACH = 2.5       # 渐变跟随：离背景色的距离不能超过 容差 × 该系数


def color_mask(img: Image.Image, tolerance: float) -> Image.Image:
    """颜色识别去背景，返回前景蒙版（L 模式，255 = 主体）。img 为 RGBA。"""
    arr = np.asarray(img)
    opaque = arr[..., 3] >= 128
    lab = srgb_to_lab(arr[..., :3])
    h, w = opaque.shape

    ring = max(1, round(min(h, w) * 0.02))
    border = np.zeros_like(opaque)
    border[:ring, :] = border[-ring:, :] = border[:, :ring] = border[:, -ring:] = True
    border &= opaque
    if not border.any():
        return Image.fromarray(np.where(opaque, 255, 0).astype(np.uint8), "L")

    diff_h = np.linalg.norm(np.diff(lab, axis=1), axis=-1)  # 横向相邻色差 (h, w-1)
    diff_v = np.linalg.norm(np.diff(lab, axis=0), axis=-1)  # 纵向相邻色差 (h-1, w)
    texture = np.zeros((h, w))
    texture[:, :-1] = diff_h
    texture[:-1, :] = np.maximum(texture[:-1, :], diff_v)

    bg_colors = _border_colors(lab[border], texture[border])
    if not bg_colors:
        logger.debug("颜色去背景：四周没有平滑的背景色，跳过")
        return Image.fromarray(np.where(opaque, 255, 0).astype(np.uint8), "L")
    dist = np.min(np.stack([delta_e_2000(lab, c) for c in bg_colors]), axis=0)

    near = (dist < tolerance) & opaque
    reach = (dist < tolerance * GRADIENT_REACH) & opaque
    step = diff_h < tolerance * GRADIENT_STEP
    step_v = diff_v < tolerance * GRADIENT_STEP

    bg = border & near
    for _ in range(h + w):  # 每轮向外扩 1 像素，最多扩到整张图
        grown = bg.copy()
        d = np.zeros_like(bg)  # 4 邻域膨胀
        d[1:, :] |= bg[:-1, :]
        d[:-1, :] |= bg[1:, :]
        d[:, 1:] |= bg[:, :-1]
        d[:, :-1] |= bg[:, 1:]
        grown |= d & near
        grown[:, 1:] |= bg[:, :-1] & step & reach[:, 1:]
        grown[:, :-1] |= bg[:, 1:] & step & reach[:, :-1]
        grown[1:, :] |= bg[:-1, :] & step_v & reach[1:, :]
        grown[:-1, :] |= bg[1:, :] & step_v & reach[:-1, :]
        if np.array_equal(grown, bg):
            break
        bg = grown

    fg = opaque & ~bg
    mask = Image.fromarray(np.where(fg, 255, 0).astype(np.uint8), "L")
    # 开运算：先腐蚀再膨胀，去掉背景中孤立的小噪点
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    logger.debug("颜色去背景：背景色 %d 种，背景占比 %.0f%%", len(bg_colors), bg.mean() * 100)
    return mask


def _border_colors(samples: np.ndarray, texture: np.ndarray, k: int = 3, iters: int = 12) -> list[np.ndarray]:
    """
    对边缘像素做简易 k-means（Lab 空间），返回可以当作背景的聚类中心：
    占比 ≥ MIN_CLUSTER_SHARE 且纹理强度 ≤ FLATNESS_LIMIT。都不满足时返回空列表（不去背景）。
    """
    rng = np.random.default_rng(0)
    if len(samples) > 4000:
        pick = rng.choice(len(samples), 4000, replace=False)
        samples, texture = samples[pick], texture[pick]
    # 初始化：中位色 + 依次选离已有中心最远的点
    centers = [np.median(samples, axis=0)]
    for _ in range(k - 1):
        d = np.min([np.linalg.norm(samples - c, axis=1) for c in centers], axis=0)
        centers.append(samples[d.argmax()])
    centers = np.array(centers)
    for _ in range(iters):
        labels = np.linalg.norm(samples[:, None] - centers[None], axis=-1).argmin(axis=1)
        for i in range(k):
            if (labels == i).any():
                centers[i] = samples[labels == i].mean(axis=0)
    keep = []
    for i in range(k):
        member = labels == i
        share = member.mean()
        if share >= MIN_CLUSTER_SHARE and np.percentile(texture[member], 75) <= FLATNESS_LIMIT:
            keep.append(centers[i])
    return keep


def core_alpha(mask: Image.Image) -> Image.Image:
    """
    主体「内芯」：去掉半透明边缘并向内收缩 1 像素。
    边缘像素是主体色与背景色的混合，计算格子颜色时排除它们，避免主体外圈发白 / 发灰。
    """
    return mask.point(lambda v: 255 if v >= 230 else 0).filter(ImageFilter.MinFilter(3))


def remove_small_islands(idx: np.ndarray, empty: int, min_size: int) -> int:
    """
    去掉网格中小于 min_size 格的孤立碎块（8 邻域连通），最大的一块永远保留。
    去背景后残留的背景碎片通常就是这种小块。直接修改 idx，返回删除的格子数。
    """
    h, w = idx.shape
    filled = idx != empty
    seen = np.zeros_like(filled)
    components: list[list[tuple[int, int]]] = []
    for sy, sx in zip(*np.nonzero(filled)):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        cells, queue = [], deque([(sy, sx)])
        while queue:
            y, x = queue.popleft()
            cells.append((y, x))
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < h and 0 <= nx < w and filled[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        components.append(cells)

    if len(components) <= 1:
        return 0
    largest = max(len(c) for c in components)
    removed = 0
    for cells in components:
        if len(cells) < min_size and len(cells) < largest:
            for y, x in cells:
                idx[y, x] = empty
            removed += len(cells)
    return removed
