"""
核心算法：图片 -> 拼豆网格。

处理流程（convert_image）：
  1. 读取图片（按 EXIF 自动旋转）
  2. 主体蒙版：去背景（颜色识别 / AI 抠图，见 background.py / segmentation.py）或图片自带的透明度
  3. 裁掉主体四周的空白，计算尺寸：
     - 适配豆板：主体等比缩放到刚好放进一块板，最后居中摆放（开描边时四周留 1 格）
     - 按宽度：按指定宽度缩放
  4. 缩小为「工作图」（每格约 4 像素），调整饱和度 / 对比度 / 亮度，按主体覆盖率缩放到网格：
     覆盖 ≥50% 的格子放豆，格子颜色只取主体像素（排除半透明边缘），避免主体外圈混入背景色
  5. 颜色量化：Lab + CIEDE2000 找色卡中的最近色，再贪心合并到不超过 max_colors 种；可选抖动
  6. 合并小色块、去掉去背景残留的小碎块（见 cleanup.py），摆到豆板上，可选描边
  7. 输出色号网格与用量清单

算法内部用「色卡下标」的二维数组表示网格（-1 为空位），最后再转换成色号。
"""

import hashlib
import io
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.logger import get_logger
from app.enums import BackgroundMethod, DitherMode, FitMode, ResampleMode
from app.schemas import ConvertParams
from app.services import background, cleanup, segmentation
from app.services.color import delta_e_matrix, srgb_to_lab
from app.services.grid_utils import build_bom
from app.services.palette_service import Palette

logger = get_logger(__name__)

EMPTY = -1              # 网格中空位（不放豆）的标记
ALPHA_THRESHOLD = 128   # 缩放后 alpha 低于该值视为透明
PRE_SHRINK_SIZE = 2048  # 超大图片先缩小到该边长，加快处理速度
ANCHOR_DARK_L = 22      # 黑色锚点：L* 低于该值的格子视为「黑色」（勾线、眼睛）
ANCHOR_LIGHT_L = 93     # 白色锚点：L* 高于该值的格子视为「白色」（高光、白底）
ANCHOR_MIN_SHARE = 0.003  # 黑 / 白格子至少占这个比例（且不少于 3 格）才保护，避免个别噪点占用颜色名额
WORK_SCALE = 4          # 工作图每格对应的像素数（去背景、边缘处理在工作图上进行）
DOMINANT_SCALE = 6      # 主导色模式的工作图每格像素数（每格 36 个样本，统计出现最多的颜色更可靠）
DOMINANT_MIN_SHARE = 0.25  # 出现最多的颜色占比低于该值（纹理杂乱，如照片毛发）时退回平均色
DARK_LINE_SHARE = 0.2      # 深色线条优先：一格内接近黑色的像素占比达到该值就取黑色，避免细勾线断开


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
    subject = _subject_mask(data, base, params, warnings)
    region, subject = _crop_to_subject(base, subject, params)
    # 有空白区域又要描边时，四周留 1 格给描边，避免被图纸边缘截掉
    margin = 1 if params.outline and subject is not None else 0
    (content_w, content_h), (width, height) = _target_size(region, params, margin, warnings)

    work = _work_image(region, content_w, content_h, params.resample)
    if subject is not None:
        mask_img = subject if subject.size == work.size else subject.resize(work.size, Image.Resampling.BILINEAR)
        work.putalpha(ImageChops.darker(mask_img, work.getchannel("A")))
    rgb, mask = _resize_to_grid(_enhance(work, params), content_w, content_h, params.resample, params.line_priority)
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

        if params.min_region_size > 1:
            changed = cleanup.merge_small_regions(idx, palette.lab, EMPTY, params.min_region_size)
            logger.debug("合并小色块（< %d 颗）：%d 格", params.min_region_size, changed)

        if params.remove_background:
            # 去背景后残留的小碎块（背景里的噪点），阈值随主体大小变化，最多 20 格
            total = int((idx != EMPTY).sum())
            removed = cleanup.remove_small_islands(idx, EMPTY, min(20, max(3, int(total * 0.003))))
            logger.debug("去除小碎块：%d 格", removed)

        idx = _place_on_canvas(idx, width, height)

        if params.outline:
            if subject is not None and (idx == EMPTY).any():
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


def _subject_mask(data: bytes, base: Image.Image, params: ConvertParams, warnings: list[str]) -> Image.Image | None:
    """
    主体蒙版（L 模式，与 base 同尺寸，255 = 主体）：
    开启去背景时为去背景结果（并与原有透明度合并）；否则图片带透明区域时为其透明度；都没有返回 None。
    """
    alpha = base.getchannel("A")
    if params.remove_background:
        return ImageChops.darker(_background_mask(data, base, params, warnings), alpha)
    return alpha if alpha.getextrema()[0] < 255 else None


def _background_mask(data: bytes, base: Image.Image, params: ConvertParams, warnings: list[str]) -> Image.Image:
    """去背景，返回与 base 同尺寸的前景蒙版。"""
    t0 = time.perf_counter()
    if params.bg_method == BackgroundMethod.AI:
        # AI 在原图上推理，结果按图片内容缓存，调整其他参数时不会重复推理
        mask = segmentation.predict_mask(base, params.bg_model, hashlib.sha1(data).hexdigest())
    else:
        # 颜色识别在缩小的分析图上进行（速度快），结果再放大回原图尺寸
        wanted = params.board_size if params.fit_mode == FitMode.BOARD else max(params.width, params.height or 0)
        side = min(max(base.size), max(256, min(800, wanted * WORK_SCALE)))
        scale = side / max(base.size)
        small = base if scale >= 1 else base.resize(
            (max(1, round(base.width * scale)), max(1, round(base.height * scale))), Image.Resampling.BOX)
        mask = background.color_mask(small, params.bg_tolerance)
        if mask.size != base.size:
            mask = mask.resize(base.size, Image.Resampling.BILINEAR)

    opaque = np.asarray(base.getchannel("A")) >= 128
    fg_ratio = (np.asarray(mask)[opaque] >= 128).sum() / max(1, opaque.sum())
    logger.debug("去背景（%s）：主体占比 %.0f%%，耗时 %dms",
                 params.bg_method, fg_ratio * 100, (time.perf_counter() - t0) * 1000)
    if fg_ratio > 0.98:
        warnings.append("没有检测到明显的背景" + (
            "（图片四周颜色不统一），可调大容差或改用 AI 抠图" if params.bg_method == BackgroundMethod.COLOR else ""))
    elif fg_ratio < 0.01:
        warnings.append("几乎整张图都被当成了背景" + (
            "，可调小容差" if params.bg_method == BackgroundMethod.COLOR else "，可换一个 AI 模型试试"))
    return mask


def _crop_to_subject(base: Image.Image, subject: Image.Image | None, params: ConvertParams):
    """
    裁掉主体四周的空白，返回 (图片, 蒙版)。
    计算包围盒前先做一次开运算，避免背景里零星的噪点把包围盒撑大。
    """
    if subject is None or not params.crop_to_subject:
        return base, subject
    binary = subject.point(lambda v: 255 if v >= 128 else 0)
    k = max(3, round(max(base.size) / 200) | 1)  # 奇数核，约为图片边长的 0.5%
    bbox = binary.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k)).getbbox() or binary.getbbox()
    if bbox is None or bbox == (0, 0, base.width, base.height):
        return base, subject
    logger.debug("裁掉主体四周空白：%dx%d -> %dx%d", base.width, base.height, bbox[2] - bbox[0], bbox[3] - bbox[1])
    return base.crop(bbox), subject.crop(bbox)


def _target_size(img: Image.Image, params: ConvertParams, margin: int, warnings: list[str]):
    """
    计算尺寸，返回 ((主体宽, 主体高), (图纸宽, 图纸高))，单位为格。
    - 适配豆板：主体等比缩放到 (board_size - 2×margin) 以内，图纸为整块豆板
    - 按宽度：图纸宽 = width，主体宽 = width - 2×margin，高度按比例（或手动指定），超出上限时等比缩小
    """
    if params.fit_mode == FitMode.BOARD:
        board = params.board_size
        avail = max(1, board - 2 * margin)
        if img.width >= img.height:
            content = (avail, max(1, round(avail * img.height / img.width)))
        else:
            content = (max(1, round(avail * img.width / img.height)), avail)
        return content, (board, board)

    width = params.width
    content_w = max(1, width - 2 * margin)
    height = params.height or max(1, round(img.height * content_w / img.width)) + 2 * margin
    limit = settings.max_grid_size
    if height > limit:
        width = max(1, round(width * limit / height))
        height = limit
        content_w = max(1, width - 2 * margin)
        warnings.append(f"按比例计算的高度超过上限 {limit}，已等比缩小为 {width}×{height}")
    return (content_w, max(1, height - 2 * margin)), (width, height)


def _work_image(img: Image.Image, width: int, height: int, resample: ResampleMode) -> Image.Image:
    """
    工作图：缩小到每格约 WORK_SCALE 像素，边缘处理和颜色计算都在这张图上做，
    既保留了足够的细节，又比原图快得多。像素画（最近邻）模式保持原图不缩放。
    """
    if resample == ResampleMode.NEAREST:
        return img.copy()
    if resample == ResampleMode.DOMINANT:
        # 主导色需要尺寸恰好是格数的整数倍，便于按格切块统计；原图较小时用最近邻放大，不产生混合色
        size = (width * DOMINANT_SCALE, height * DOMINANT_SCALE)
        method = Image.Resampling.BOX if img.width >= size[0] else Image.Resampling.NEAREST
        return img.resize(size, method)
    size = (width * WORK_SCALE, height * WORK_SCALE)
    if img.width <= size[0]:
        return img.copy()
    return img.resize(size, Image.Resampling.BOX)


def _place_on_canvas(idx: np.ndarray, width: int, height: int) -> np.ndarray:
    """把主体网格居中摆到 width × height 的图纸上（四周为空位）。"""
    h, w = idx.shape
    if (w, h) == (width, height):
        return idx
    canvas = np.full((height, width), EMPTY, dtype=idx.dtype)
    y0, x0 = (height - h) // 2, (width - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = idx
    return canvas


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


def _resize_to_grid(img: Image.Image, width: int, height: int, resample: ResampleMode, line_priority: bool = False):
    """
    缩放到网格尺寸，返回 (rgb, mask)：
    rgb 为 (H, W, 3) uint8；mask 为 (H, W) bool，True 表示该格放豆（主体覆盖率 ≥ 50%）。

    Pillow 缩放 RGBA 时做预乘 alpha，格子颜色 = 格内主体像素的平均色，不会混入背景。
    图片有透明区域时，再用「主体内芯」（去掉半透明边缘）重新计算颜色，避免外圈发白 / 发灰。
    """
    if resample == ResampleMode.NEAREST:
        small = np.asarray(img.resize((width, height), Image.Resampling.NEAREST))
        return small[..., :3].copy(), small[..., 3] >= ALPHA_THRESHOLD
    if resample == ResampleMode.DOMINANT:
        return _dominant_colors(img, width, height, line_priority)

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


def _dominant_colors(img: Image.Image, width: int, height: int, line_priority: bool = False):
    """
    主导色取色：把工作图按格切成 s×s 的小块，统计每格内出现最多的颜色（RGB 各取高 5 位分桶），
    取该颜色的像素平均值作为格子颜色。卡通图里的黑色勾线、色块边界因此不会被平均成灰色 / 混合色。
    出现最多的颜色占比不足 DOMINANT_MIN_SHARE 时（照片毛发、噪点等杂乱纹理）退回全部像素的平均色。
    深色线条优先（line_priority，可选）：卡通勾线常常不到半格宽，按多数取色会断成碎段，
    开启后格内接近黑色的像素达到 DARK_LINE_SHARE 时直接取这些深色像素的平均色。照片不建议开启（暗部会发黑）。
    返回值与 _resize_to_grid 相同：(rgb, mask)。
    """
    arr = np.asarray(img).astype(np.int32)
    s = arr.shape[0] // height
    blocks = arr[:height * s, :width * s].reshape(height, s, width, s, 4).transpose(0, 2, 1, 3, 4)
    blocks = blocks.reshape(height, width, s * s, 4)
    rgb, valid = blocks[..., :3], blocks[..., 3] >= ALPHA_THRESHOLD
    mask = valid.mean(axis=-1) >= 0.5  # 主体覆盖率 ≥ 50% 的格子放豆

    key = ((rgb[..., 0] >> 3) << 10) | ((rgb[..., 1] >> 3) << 5) | (rgb[..., 2] >> 3)
    counts = (key[..., :, None] == key[..., None, :]) & valid[..., None, :]
    counts = np.where(valid, counts.sum(axis=-1), 0)
    best = counts.argmax(axis=-1)
    modal_key = np.take_along_axis(key, best[..., None], axis=-1)
    share = counts.max(axis=-1) / np.maximum(valid.sum(axis=-1), 1)

    pick = valid & ((key == modal_key) | (share < DOMINANT_MIN_SHARE)[..., None])

    if line_priority:
        # 用亮度近似判断接近黑色：相对亮度 < 0.035 约等于 L* < 22（与黑色锚点一致）
        luminance = 0.2126 * _linear(rgb[..., 0]) + 0.7152 * _linear(rgb[..., 1]) + 0.0722 * _linear(rgb[..., 2])
        dark = valid & (luminance < 0.035)
        dark_cell = dark.sum(axis=-1) >= DARK_LINE_SHARE * np.maximum(valid.sum(axis=-1), 1)
        pick = np.where(dark_cell[..., None], dark, pick)

    total = np.maximum(pick.sum(axis=-1, keepdims=True), 1)
    colors = (rgb * pick[..., None]).sum(axis=-2) / total
    return np.clip(np.round(colors), 0, 255).astype(np.uint8), mask


def _linear(channel: np.ndarray) -> np.ndarray:
    """sRGB 0~255 转线性亮度 0~1。"""
    c = channel / 255.0
    return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)


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
      5. 黑 / 白锚点：图中有一定数量的黑色（勾线、眼睛）或白色（高光）时，对应颜色始终保留。
         它们在拼豆作品里最显眼，但面积往往很小，单靠误差计算在颜色很少时仍可能被合并掉。
    """
    t0 = time.perf_counter()
    uniq, counts = np.unique(pixels_rgb.reshape(-1, 3), axis=0, return_counts=True)
    uniq_lab = srgb_to_lab(uniq)
    dist = delta_e_matrix(uniq_lab, palette.lab[candidates])
    weights = counts.astype(np.float64)

    nearest = dist.argmin(axis=1)
    selected = np.unique(nearest)  # 候选列下标
    initial = len(selected)
    protected = _anchor_colors(uniq_lab, weights, nearest, max_colors)
    while max_colors and len(selected) > max_colors:
        selected = _drop_colors(dist, weights, selected, max_colors, protected)

    logger.debug(
        "颜色量化：唯一色 %d，初始 %d 色 -> %d 色，耗时 %dms",
        len(uniq), initial, len(selected), (time.perf_counter() - t0) * 1000,
    )
    return candidates[selected]


def _anchor_colors(uniq_lab: np.ndarray, weights: np.ndarray, nearest: np.ndarray, max_colors: int) -> set[int]:
    """找出需要保护的黑 / 白锚点颜色（候选列下标）。颜色名额太少（< 3）时不保护。"""
    if not max_colors or max_colors < 3:
        return set()
    min_weight = max(3.0, weights.sum() * ANCHOR_MIN_SHARE)
    protected = set()
    for select in (uniq_lab[:, 0] < ANCHOR_DARK_L, uniq_lab[:, 0] > ANCHOR_LIGHT_L):
        if weights[select].sum() >= min_weight:
            # 这些格子最常用的那个色卡颜色就是锚点
            protected.add(int(np.bincount(nearest[select], weights=weights[select]).argmax()))
    return protected


def _drop_colors(dist: np.ndarray, weights: np.ndarray, selected: np.ndarray, max_colors: int,
                 protected: set[int] = frozenset()) -> np.ndarray:
    """一轮合并：去掉代价最小的若干颜色（锚点颜色不参与）。远离目标时一次多去几个以加快速度。"""
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
        if int(selected[c]) in protected:
            continue
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
