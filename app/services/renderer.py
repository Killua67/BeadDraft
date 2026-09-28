"""
图纸渲染：网格 -> 图片 / PDF / 缩略图。

- render_pattern：绘制一张图纸（标题、坐标、色块+色号、分板线、用量图例），PNG 导出和 PDF 各页都用它
- render_pdf：首页为总览，之后每块豆板一页，豆板页按豆子实际尺寸 1:1 输出，垫在透明豆板下即可照着拼
- render_thumbnail：历史列表用的小图

尺寸约定：cell 为每格像素；unit 为文字/边距的缩放系数（1 unit ≈ 1px @ PNG 导出），
这样 PDF 按毫米排版时，文字大小不会跟着格子一起变得过小。
"""

import io
import math
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.core.logger import get_logger
from app.schemas import Grid
from app.services.color import hex_to_rgb, relative_luminance
from app.services.grid_utils import build_bom
from app.services.palette_service import Palette

logger = get_logger(__name__)

# 常见系统中文字体（按优先级查找），都找不到时退回 Pillow 内置字体（不支持中文）
_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

# 配色
_BG = (255, 255, 255)
_TEXT = (33, 33, 33)
_MUTED = (110, 110, 110)
_LINE_THIN = (205, 205, 205)
_LINE_TEN = (80, 80, 80)
_LINE_BOARD = (229, 57, 53)

# PDF 豆板页每格像素（决定打印清晰度：5mm 豆约 160dpi，2.6mm 豆约 310dpi）
_PDF_CELL_PX = 32


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    for path in ([settings.font_path] if settings.font_path else []) + _FONT_CANDIDATES:
        if path and Path(path).exists():
            logger.info("图纸字体：%s", path)
            return path
    logger.warning("未找到中文字体，图纸中的中文可能无法显示，可设置环境变量 PB_FONT_PATH")
    return None


@lru_cache(maxsize=128)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(6, int(size))
    path = _font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def _text_width(text: str, font) -> float:
    return font.getlength(text)


def make_labels(grid: Grid, palette: Palette) -> dict[str, str]:
    """
    格子里显示的标记：色号都不超过 3 个字符时（如 MARD 的 A1、ZG8）直接显示色号；
    否则（如 Perler 的 80-19001）按用量顺序编号 1、2、3…，在图例中对应色号。
    """
    bom = build_bom(grid, palette)
    if all(len(item["code"]) <= 3 for item in bom):
        return {item["code"]: item["code"] for item in bom}
    return {item["code"]: str(i + 1) for i, item in enumerate(bom)}


def render_pattern(
    grid: Grid,
    palette: Palette,
    *,
    title: str = "",
    subtitle: str = "",
    cell: int = 28,
    unit: float = 1.0,
    board_size: int = 0,
    origin: tuple[int, int] = (0, 0),
    show_codes: bool = True,
    labels: dict[str, str] | None = None,
    legend: bool = True,
) -> Image.Image:
    """
    绘制图纸。
    board_size > 0 时画红色分板线；origin 为左上角格子的全局坐标（分板页用来显示正确的行列号）。
    """
    rows, cols = len(grid), len(grid[0])
    labels = labels or make_labels(grid, palette)
    bom = build_bom(grid, palette)

    u = lambda v: int(round(v * unit))  # noqa: E731  文字/边距尺寸换算
    f_title, f_sub, f_axis, f_legend = _font(u(22)), _font(u(13)), _font(u(11)), _font(u(13))
    label_ratio = 0.42 if max((len(v) for v in labels.values()), default=1) <= 2 else 0.32  # 3 位色号字小一点
    code_font = _font(cell * label_ratio)

    # ---- 布局
    margin = u(20)
    title_h = (u(30) if title else 0) + (u(22) if subtitle else 0) + (u(8) if title or subtitle else 0)
    max_row_label = str(origin[1] + rows)
    axis_w = int(_text_width(max_row_label, f_axis)) + u(8)
    axis_h = u(18)
    grid_w, grid_h = cols * cell, rows * cell

    legend_item_w, legend_item_h, swatch = u(190), u(30), u(22)
    content_w = max(axis_w + grid_w, u(520))
    legend_cols = max(1, content_w // legend_item_w)
    legend_h = 0
    if legend and bom:
        legend_h = u(40) + math.ceil(len(bom) / legend_cols) * legend_item_h

    width = margin * 2 + content_w
    height = margin * 2 + title_h + axis_h + grid_h + legend_h
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    # ---- 标题
    y = margin
    if title:
        draw.text((margin, y), title, fill=_TEXT, font=f_title)
        y += u(30)
    if subtitle:
        draw.text((margin, y), subtitle, fill=_MUTED, font=f_sub)
        y += u(22)
    if title or subtitle:
        y += u(8)

    gx, gy = margin + axis_w, y + axis_h  # 网格左上角

    # ---- 坐标轴：数字放不下时每 5 / 10 格标一次
    widest = _text_width(str(origin[0] + cols), f_axis) + u(2)
    step = 1 if widest <= cell else (5 if widest <= cell * 5 else 10)
    for c in range(cols):
        n = origin[0] + c + 1
        if n % step == 0 or step == 1:
            draw.text((gx + c * cell + cell / 2, gy - u(4)), str(n), fill=_MUTED, font=f_axis, anchor="ms")
    step_y = 1 if f_axis.size <= cell else (5 if f_axis.size <= cell * 5 else 10)
    for r in range(rows):
        n = origin[1] + r + 1
        if n % step_y == 0 or step_y == 1:
            draw.text((gx - u(4), gy + r * cell + cell / 2), str(n), fill=_MUTED, font=f_axis, anchor="rm")

    # ---- 色块与色号
    colors = {code: hex_to_rgb(palette.colors[palette.index[code]]["hex"]) for code in labels}
    text_colors = {code: ((255, 255, 255) if relative_luminance(rgb) < 0.35 else _TEXT) for code, rgb in colors.items()}
    for r, row in enumerate(grid):
        for c, code in enumerate(row):
            if code is None:
                continue
            x0, y0 = gx + c * cell, gy + r * cell
            draw.rectangle((x0, y0, x0 + cell, y0 + cell), fill=colors[code])
            if show_codes and cell >= 14:
                draw.text((x0 + cell / 2, y0 + cell / 2), labels[code], fill=text_colors[code], font=code_font, anchor="mm")

    # ---- 网格线：细线每格、深线每 10 格、红线为分板
    if cell >= 6:
        for c in range(cols + 1):
            draw.line((gx + c * cell, gy, gx + c * cell, gy + grid_h), fill=_LINE_THIN, width=1)
        for r in range(rows + 1):
            draw.line((gx, gy + r * cell, gx + grid_w, gy + r * cell), fill=_LINE_THIN, width=1)
    thick = max(2, cell // 12)
    for c in range(cols + 1):
        if (origin[0] + c) % 10 == 0 or c in (0, cols):
            draw.line((gx + c * cell, gy, gx + c * cell, gy + grid_h), fill=_LINE_TEN, width=thick)
    for r in range(rows + 1):
        if (origin[1] + r) % 10 == 0 or r in (0, rows):
            draw.line((gx, gy + r * cell, gx + grid_w, gy + r * cell), fill=_LINE_TEN, width=thick)
    if board_size and (cols > board_size or rows > board_size):
        for c in range(board_size, cols, board_size):
            draw.line((gx + c * cell, gy, gx + c * cell, gy + grid_h), fill=_LINE_BOARD, width=thick + 1)
        for r in range(board_size, rows, board_size):
            draw.line((gx, gy + r * cell, gx + grid_w, gy + r * cell), fill=_LINE_BOARD, width=thick + 1)

    # ---- 图例（用量清单）
    if legend and bom:
        ly = gy + grid_h + u(16)
        total = sum(item["count"] for item in bom)
        draw.text((margin, ly), f"用量清单 · 共 {total} 颗 · {len(bom)} 色", fill=_TEXT, font=f_legend)
        ly += u(26)
        swatch_font = _font(swatch * label_ratio)
        for i, item in enumerate(bom):
            lx = margin + (i % legend_cols) * legend_item_w
            iy = ly + (i // legend_cols) * legend_item_h
            rgb = colors[item["code"]]
            draw.rectangle((lx, iy, lx + swatch, iy + swatch), fill=rgb, outline=_LINE_THIN)
            draw.text((lx + swatch / 2, iy + swatch / 2), labels[item["code"]],
                      fill=text_colors[item["code"]], font=swatch_font, anchor="mm")
            name = item["code"] if item["name"] == item["code"] else f"{item['code']} {item['name']}"
            text = f"{name}  ×{item['count']}"
            max_w = legend_item_w - swatch - u(14)
            while len(text) > 4 and _text_width(text, f_legend) > max_w:  # 名称过长时截断
                name = name[:-1]
                text = f"{name}…  ×{item['count']}"
            draw.text((lx + swatch + u(6), iy + swatch / 2), text, fill=_TEXT, font=f_legend, anchor="lm")

    return img


def render_png(grid: Grid, palette: Palette, *, title: str, board_size: int) -> bytes:
    """导出整张图纸 PNG。大图自动缩小格子，避免图片过大。"""
    t0 = time.perf_counter()
    cols = len(grid[0])
    cell = max(12, min(28, 4000 // max(cols, 1)))
    img = render_pattern(
        grid, palette, title=title, subtitle=_subtitle(grid, palette, board_size),
        cell=cell, board_size=board_size, show_codes=cell >= 14,
    )
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    logger.info("导出 PNG：%dx%d 格，图片 %dx%d，耗时 %dms",
                cols, len(grid), img.width, img.height, (time.perf_counter() - t0) * 1000)
    return buf.getvalue()


def render_pdf(grid: Grid, palette: Palette, *, title: str, board_size: int, pitch_mm: float) -> bytes:
    """
    导出打印用 PDF：
      第 1 页：整体总览（缩放到 A4）
      之后：每块豆板一页（空板跳过），格子按 pitch_mm 1:1 输出。
    打印时需选择「实际大小 / 100%」，不要选「适合页面」。
    """
    t0 = time.perf_counter()
    rows, cols = len(grid), len(grid[0])
    labels = make_labels(grid, palette)
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(title)

    # ---- 总览页
    cell = max(8, min(28, 2400 // max(cols, rows)))
    overview = render_pattern(
        grid, palette, title=title, subtitle=_subtitle(grid, palette, board_size),
        cell=cell, board_size=board_size, show_codes=cell >= 14, labels=labels,
    )
    _draw_fit(pdf, overview, A4)

    # ---- 分板页
    px_per_mm = _PDF_CELL_PX / pitch_mm
    unit = 0.25 * px_per_mm  # 1 unit ≈ 0.25mm，图例文字约 3.2mm 高
    show_codes = pitch_mm >= 4  # 迷你豆 1:1 时格子太小，色号印出来看不清
    boards_x, boards_y = math.ceil(cols / board_size), math.ceil(rows / board_size)
    total_boards = boards_x * boards_y
    pages = 0
    for by in range(boards_y):
        for bx in range(boards_x):
            x0, y0 = bx * board_size, by * board_size
            sub = [row[x0:x0 + board_size] for row in grid[y0:y0 + board_size]]
            if all(code is None for row in sub for code in row):
                continue
            no = by * boards_x + bx + 1
            hint = "" if show_codes else " · 格子较小未印色号，请对照颜色"
            img = render_pattern(
                sub, palette,
                title=f"{title} · 第 {no}/{total_boards} 块（第 {by + 1} 行第 {bx + 1} 列）",
                subtitle=f"列 {x0 + 1}–{x0 + len(sub[0])} · 行 {y0 + 1}–{y0 + len(sub)} · "
                         f"豆距 {pitch_mm:g}mm，请按「实际大小」打印{hint}",
                cell=_PDF_CELL_PX, unit=unit, origin=(x0, y0), show_codes=show_codes, labels=labels,
            )
            w_mm, h_mm = img.width / px_per_mm, img.height / px_per_mm
            page_w, page_h = max(210, w_mm + 16) * mm, max(297, h_mm + 16) * mm
            pdf.setPageSize((page_w, page_h))
            pdf.drawImage(ImageReader(img), (page_w - w_mm * mm) / 2, page_h - 8 * mm - h_mm * mm,
                          width=w_mm * mm, height=h_mm * mm)
            pdf.showPage()
            pages += 1

    pdf.save()
    logger.info("导出 PDF：%dx%d 格，总览 1 页 + 豆板 %d 页，耗时 %dms",
                cols, rows, pages, (time.perf_counter() - t0) * 1000)
    return buf.getvalue()


def _draw_fit(pdf: canvas.Canvas, img: Image.Image, page: tuple[float, float]) -> None:
    """把图片等比缩放放进页面（留 10mm 边距），并结束当前页。"""
    pdf.setPageSize(page)
    avail_w, avail_h = page[0] - 20 * mm, page[1] - 20 * mm
    scale = min(avail_w / img.width, avail_h / img.height)
    w, h = img.width * scale, img.height * scale
    pdf.drawImage(ImageReader(img), (page[0] - w) / 2, page[1] - 10 * mm - h, width=w, height=h)
    pdf.showPage()


def _subtitle(grid: Grid, palette: Palette, board_size: int) -> str:
    rows, cols = len(grid), len(grid[0])
    beads = sum(1 for row in grid for code in row if code is not None)
    boards = math.ceil(cols / board_size) * math.ceil(rows / board_size)
    return f"{cols}×{rows} 格 · {beads} 颗 · 色卡 {palette.name} · {board_size}×{board_size} 豆板 {boards} 块"


def render_thumbnail(grid: Grid, palette: Palette, max_px: int = 160) -> bytes:
    """缩略图：每格一个像素再放大，空位透明。"""
    rows, cols = len(grid), len(grid[0])
    img = Image.new("RGBA", (cols, rows), (0, 0, 0, 0))
    pixels = img.load()
    colors = {c["code"]: (*hex_to_rgb(c["hex"]), 255) for c in palette.colors}
    for r, row in enumerate(grid):
        for c, code in enumerate(row):
            if code is not None:
                pixels[c, r] = colors[code]
    scale = max(1, max_px // max(rows, cols))
    img = img.resize((cols * scale, rows * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
