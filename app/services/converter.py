"""
核心算法：图片 -> 拼豆网格。

处理流程（convert_image）：
  1. 读取图片（按 EXIF 自动旋转），缩小为「工作图」（每格约 4 像素）
  2. 可选去背景：颜色识别或 AI 抠图，得到工作图上的前景蒙版（见 background.py / segmentation.py）
  3. 调整饱和度 / 对比度 / 亮度，按前景覆盖率缩放到网格：覆盖 ≥50% 的格子放豆，
     格子颜色只取主体像素（排除半透明边缘），避免主体外圈混入背景色
  4. 颜色量化：Lab + CIEDE2000 找色卡中的最近色，再贪心合并到不超过 max_colors 种
  5. 可选 Floyd–Steinberg 抖动
  6. 可选清理孤立杂点、给主体加描边
  7. 输出色号网格与用量清单

算法内部用「色卡下标」的二维数组表示网格（-1 为空位），最后再转换成色号。
"""

import hashlib
import io
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.logger import get_logger
from app.enums import BackgroundMethod, DitherMode, ResampleMode
from app.schemas import ConvertParams
from app.services import background, segmentation
from app.services.color import delta_e_matrix, srgb_to_lab
from app.services.grid_utils import build_bom
from app.services.palette_service import Palette

logger = get_logger(__name__)

EMPTY = -1              # 网格中空位（不放豆）的标记
ALPHA_THRESHOLD = 128   # 缩放后 alpha 低于该值视为透明
PRE_SHRINK_SIZE = 2048  # 超大图片先缩小到该边长，加快处理速度
WORK_SCALE = 4          # 工作图每格对应的像素数（去背景、边缘处理在工作图上进行）


@dataclass
class ConvertResult:
    """转换结果，字段与 schemas.ConvertResponse 对应。"""

    palette_id: str
    width: int
    height: int
    grid: list[list[str | None]]
    colors: list[dict]
    bead_count: int
    color_count: int
    elapsed_ms: int
    warnings: list[str] = field(default_factory=list)


def convert_image(data: bytes, params: ConvertParams, palette: Palette) -> ConvertResult:
    """把图片字节流转换为拼豆网格。入口函数，API 层直接调用。"""
    t0 = time.perf_counter()
    warnings: list[str] = []

    base = _load_image(data, params)
    width, height = _target_size(base, params, warnings)
    work = _work_image(base, width, height, params.resample)
    if params.remove_background:
        work.putalpha(_background_mask(data, base, work, params, warnings))
    rgb, mask = _resize_to_grid(_enhance(work, params), width, height, params.resample)
    lab = srgb_to_lab(rgb)

    candidates = _candidate_indices(palette, params.excluded_codes)

    if not mask.any():
        warnings.append("图片全部为透明/背景，没有需要放豆的格子")
        idx = np.full((height, width), EMPTY, dtype=np.int32)
    else:
        selected = _quantize(rgb[mask], palette, candidates, params.max_colors)
        if params.dither == DitherMode.FLOYD_STEINBERG and params.dither_strength > 0:
            idx = _dither(lab, mask, palette, selected, params.dither_strength)
        else:
            idx = _nearest_grid(lab, mask, palette, selected)

        if params.clean_isolated:
            idx, changed = _clean_isolated(idx)
            logger.debug("清理孤立杂点：%d 格", changed)

        if params.remove_background:
            # 去背景后残留的小碎块（背景里的噪点），阈值随主体大小变化，最多 20 格
            total = int((idx != EMPTY).sum())
            removed = background.remove_small_islands(idx, EMPTY, min(20, max(3, int(total * 0.003))))
            logger.debug("去除小碎块：%d 格", removed)

        if params.outline:
            if (idx == EMPTY).any():
                outline_index = _outline_index(palette, candidates, params.outline_code, warnings)
                _add_outline(idx, outline_index)
            else:
                warnings.append("图片没有透明或背景区域，描边未生效（可先开启「去除背景」）")

    grid = [[palette.codes[i] if i != EMPTY else None for i in row] for row in idx.tolist()]
    bom = build_bom(grid, palette)
    if params.max_colors and len(bom) > params.max_colors:
        warnings.append(f"描边颜色额外占用了 1 种，共 {len(bom)} 色")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    beads = sum(item["count"] for item in bom)
    logger.info(
        "转换完成：色卡=%s 尺寸=%dx%d 颜色=%d 豆子=%d 耗时=%dms",
        palette.id, width, height, len(bom), beads, elapsed_ms,
    )
    return ConvertResult(
        palette_id=palette.id, width=width, height=height, grid=grid, colors=bom,
        bead_count=beads, color_count=len(bom), elapsed_ms=elapsed_ms, warnings=warnings,
    )


# ---------------------------------------------------------------- 1~3 图片预处理

def _load_image(data: bytes, params: ConvertParams) -> Image.Image:
    """读取图片，返回 RGBA 图像（超大图片先缩小）。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise BadRequestError("无法识别的图片，请上传 PNG / JPG / WEBP / GIF 等常见格式") from exc

    img = ImageOps.exif_transpose(img).convert("RGBA")
    logger.debug("读取图片 %dx%d", img.width, img.height)

    if max(img.size) > PRE_SHRINK_SIZE:
        method = Image.Resampling.NEAREST if params.resample == ResampleMode.NEAREST else Image.Resampling.LANCZOS
        img.thumbnail((PRE_SHRINK_SIZE, PRE_SHRINK_SIZE), method)
    return img


def _target_size(img: Image.Image, params: ConvertParams, warnings: list[str]) -> tuple[int, int]:
    """计算网格尺寸：高度未指定时按原图比例，超出上限时等比缩小。"""
    width = params.width
    height = params.height or max(1, round(img.height * width / img.width))
    limit = settings.max_grid_size
    if height > limit:
        width = max(1, round(width * limit / height))
        height = limit
        warnings.append(f"按比例计算的高度超过上限 {limit}，已等比缩小为 {width}×{height}")
    return width, height


def _work_image(base: Image.Image, width: int, height: int, resample: ResampleMode) -> Image.Image:
    """
    工作图：缩小到每格约 WORK_SCALE 像素。去背景、边缘处理都在这张图上做，
    既保留了足够的边缘细节，又比原图快得多。像素画（最近邻）模式保持原图不缩放。
    """
    size = (width * WORK_SCALE, height * WORK_SCALE)
    if resample == ResampleMode.NEAREST or base.width <= size[0]:
        return base.copy()
    return base.resize(size, Image.Resampling.BOX)


def _background_mask(data: bytes, base: Image.Image, work: Image.Image, params: ConvertParams,
                     warnings: list[str]) -> Image.Image:
    """计算工作图的前景蒙版（L，255 = 主体），并与图片原有的透明度合并。"""
    t0 = time.perf_counter()
    if params.bg_method == BackgroundMethod.AI:
        # AI 在原图上推理（结果按图片内容缓存），再缩放到工作图尺寸
        mask = segmentation.predict_mask(base, params.bg_model, hashlib.sha1(data).hexdigest())
        mask = mask.resize(work.size, Image.Resampling.BILINEAR)
    else:
        mask = background.color_mask(work, params.bg_tolerance)
    mask = ImageChops.darker(mask, work.getchannel("A"))

    opaque = np.asarray(work.getchannel("A")) >= 128
    fg_ratio = (np.asarray(mask) >= 128).sum() / max(1, opaque.sum())
    logger.debug("去背景（%s）：主体占比 %.0f%%，耗时 %dms",
                 params.bg_method, fg_ratio * 100, (time.perf_counter() - t0) * 1000)
    if fg_ratio > 0.98:
        warnings.append("没有检测到明显的背景" + (
            "（图片四周颜色不统一），可调大容差或改用 AI 抠图" if params.bg_method == BackgroundMethod.COLOR else ""))
    elif fg_ratio < 0.01:
        warnings.append("几乎整张图都被当成了背景" + (
            "，可调小容差" if params.bg_method == BackgroundMethod.COLOR else "，可换一个 AI 模型试试"))
    return mask


def _enhance(img: Image.Image, params: ConvertParams) -> Image.Image:
    """调整饱和度 / 对比度 / 亮度（保留透明度）。"""
    if (params.saturation, params.contrast, params.brightness) == (1.0, 1.0, 1.0):
        return img
    alpha = img.getchannel("A")
    rgb = img.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(params.saturation)
    rgb = ImageEnhance.Contrast(rgb).enhance(params.contrast)
    rgb = ImageEnhance.Brightness(rgb).enhance(params.brightness)
    rgb.putalpha(alpha)
    return rgb


def _resize_to_grid(img: Image.Image, width: int, height: int, resample: ResampleMode):
    """
    缩放到网格尺寸，返回 (rgb, mask)：
    rgb 为 (H, W, 3) uint8；mask 为 (H, W) bool，True 表示该格放豆（主体覆盖率 ≥ 50%）。

    Pillow 缩放 RGBA 时做预乘 alpha，格子颜色 = 格内主体像素的平均色，不会混入背景。
    图片有透明区域时，再用「主体内芯」（去掉半透明边缘）重新计算颜色，避免外圈发白 / 发灰。
    """
    if resample == ResampleMode.NEAREST:
        small = np.asarray(img.resize((width, height), Image.Resampling.NEAREST))
        return small[..., :3].copy(), small[..., 3] >= ALPHA_THRESHOLD

    small = np.asarray(img.resize((width, height), Image.Resampling.BOX))
    rgb, mask = small[..., :3].copy(), small[..., 3] >= ALPHA_THRESHOLD
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        core = img.copy()
        core.putalpha(background.core_alpha(alpha))
        core_small = np.asarray(core.resize((width, height), Image.Resampling.BOX))
        use = core_small[..., 3] >= 32  # 内芯至少占格子的 1/8，太少时 8 位反预乘误差大，仍用原色
        rgb[use] = core_small[use, :3]
    return rgb, mask


# ---------------------------------------------------------------- 4~5 颜色量化

def _candidate_indices(palette: Palette, excluded_codes: list[str]) -> np.ndarray:
    """可用颜色的色卡下标（排除用户不想用的颜色）。"""
    excluded = set(excluded_codes)
    candidates = np.array([i for i, code in enumerate(palette.codes) if code not in excluded], dtype=np.int32)
    if candidates.size == 0:
        raise BadRequestError("可用颜色为空，请减少排除的颜色")
    return candidates


def _quantize(pixels_rgb: np.ndarray, palette: Palette, candidates: np.ndarray, max_colors: int) -> np.ndarray:
    """
    决定最终使用哪些颜色，返回色卡下标数组。

    做法：
      1. 对像素颜色去重（同色像素只算一次，按出现次数加权）
      2. 计算每个颜色到候选色的 ΔE00 距离矩阵
      3. 先取每个像素的最近色，得到初始颜色集合
      4. 若超过 max_colors：每轮计算「去掉某颜色后，其像素改用次近色带来的平方误差增量」，
         去掉增量最小的颜色。这样面积小但很关键的颜色（如眼睛的黑色）不会被轻易去掉。
    """
    t0 = time.perf_counter()
    uniq, counts = np.unique(pixels_rgb.reshape(-1, 3), axis=0, return_counts=True)
    dist = delta_e_matrix(srgb_to_lab(uniq), palette.lab[candidates])
    weights = counts.astype(np.float64)

    selected = np.unique(dist.argmin(axis=1))  # 候选列下标
    initial = len(selected)
    while max_colors and len(selected) > max_colors:
        selected = _drop_colors(dist, weights, selected, max_colors)

    logger.debug(
        "颜色量化：唯一色 %d，初始 %d 色 -> %d 色，耗时 %dms",
        len(uniq), initial, len(selected), (time.perf_counter() - t0) * 1000,
    )
    return candidates[selected]


def _drop_colors(dist: np.ndarray, weights: np.ndarray, selected: np.ndarray, max_colors: int) -> np.ndarray:
    """一轮合并：去掉代价最小的若干颜色。远离目标时一次多去几个以加快速度。"""
    k = len(selected)
    sub = dist[:, selected]
    part = np.argpartition(sub, 1, axis=1)
    first, second = part[:, 0], part[:, 1]  # 每个像素的最近色、次近色（在 selected 中的位置）
    rows = np.arange(sub.shape[0])
    # 用平方误差：少量像素的大色差（黑眼睛被换成深蓝）比大量像素的小色差更显眼，应优先保留
    penalty = (sub[rows, second] ** 2 - sub[rows, first] ** 2) * weights
    cost = np.bincount(first, weights=penalty, minlength=k)

    # 每个颜色的像素主要会流向哪个颜色（partner），同一轮不能同时去掉一对互为替代的颜色
    flow = np.bincount(first * k + second, weights=weights, minlength=k * k).reshape(k, k)
    partner = flow.argmax(axis=1)

    n_drop = max(1, (k - max_colors) // 4)
    drop: list[int] = []
    for c in np.argsort(cost, kind="stable"):
        if len(drop) >= n_drop:
            break
        if partner[c] in drop or any(partner[d] == c for d in drop):
            continue
        drop.append(int(c))
    return np.delete(selected, drop)


def _nearest_grid(lab: np.ndarray, mask: np.ndarray, palette: Palette, selected: np.ndarray) -> np.ndarray:
    """不抖动：每格取已选颜色中 ΔE00 最近的。"""
    idx = np.full(mask.shape, EMPTY, dtype=np.int32)
    dist = delta_e_matrix(lab[mask], palette.lab[selected])
    idx[mask] = selected[dist.argmin(axis=1)]
    return idx


def _dither(lab: np.ndarray, mask: np.ndarray, palette: Palette, selected: np.ndarray, strength: float) -> np.ndarray:
    """
    Floyd–Steinberg 误差扩散（蛇形扫描，在 Lab 空间扩散误差）。
    逐像素循环，为了速度这里用 Lab 欧氏距离（ΔE76）找最近色。
    误差只扩散到需要放豆的格子，避免把颜色"漏"到背景里。
    """
    h, w = mask.shape
    work = lab.copy()
    pal = palette.lab[selected]
    idx = np.full(mask.shape, EMPTY, dtype=np.int32)
    for y in range(h):
        step = 1 if y % 2 == 0 else -1
        xs = range(w) if step == 1 else range(w - 1, -1, -1)
        for x in xs:
            if not mask[y, x]:
                continue
            old = work[y, x]
            k = int(((pal - old) ** 2).sum(axis=1).argmin())
            idx[y, x] = selected[k]
            err = (old - pal[k]) * strength
            for dx, dy, ratio in ((step, 0, 7 / 16), (-step, 1, 3 / 16), (0, 1, 5 / 16), (step, 1, 1 / 16)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and ny < h and mask[ny, nx]:
                    work[ny, nx] += err * ratio
    return idx


# ---------------------------------------------------------------- 6 后处理

def _clean_isolated(idx: np.ndarray) -> tuple[np.ndarray, int]:
    """
    清理孤立杂点：某颗豆的 8 邻域内没有同色豆，就改成邻域中最多的颜色。
    用 8 邻域而不是 4 邻域，是为了保留像素画里的斜线（斜线上的点只在对角方向相连）。
    """
    h, w = idx.shape
    out = idx.copy()
    changed = 0
    for y in range(h):
        for x in range(w):
            c = idx[y, x]
            if c == EMPTY:
                continue
            block = idx[max(0, y - 1):y + 2, max(0, x - 1):x + 2].ravel().tolist()
            block.remove(c)  # 去掉自身
            neighbors = [n for n in block if n != EMPTY]
            if not neighbors or c in neighbors:
                continue
            out[y, x] = Counter(neighbors).most_common(1)[0][0]
            changed += 1
    return out, changed


def _outline_index(palette: Palette, candidates: np.ndarray, outline_code: str | None, warnings: list[str]) -> int:
    """描边颜色：优先用户指定的色号，否则选可用颜色中最深（L 最小）的。"""
    if outline_code:
        if outline_code in palette.index:
            return palette.index[outline_code]
        warnings.append(f"描边色号 {outline_code} 不在色卡中，已自动选择最深的颜色")
    return int(candidates[palette.lab[candidates, 0].argmin()])


def _add_outline(idx: np.ndarray, color_index: int) -> None:
    """在主体外围一圈（8 邻域膨胀）的空位上放描边色，直接修改 idx。"""
    filled = idx != EMPTY
    h, w = filled.shape
    padded = np.pad(filled, 1)
    dilated = np.zeros_like(filled)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            dilated |= padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
    idx[dilated & ~filled] = color_index
