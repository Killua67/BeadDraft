"""
枚举定义。所有枚举值都是字符串，可直接用于 API 参数与数据库存储。
"""

from enum import StrEnum


class ResampleMode(StrEnum):
    """图片缩放到网格时的采样方式。"""

    BOX = "box"          # 区域平均：每格取对应区域的平均色，适合照片、插画
    NEAREST = "nearest"  # 最近邻：每格取一个像素，适合本身就是像素画的图片（不会产生混合色）


class DitherMode(StrEnum):
    """颜色量化时的抖动方式。"""

    NONE = "none"                        # 不抖动：色块干净，最适合拼豆（推荐）
    FLOYD_STEINBERG = "floyd_steinberg"  # 误差扩散抖动：渐变更细腻，但会产生大量散点，拼起来费时


class PaletteSource(StrEnum):
    """色卡来源。"""

    BUILTIN = "builtin"  # 内置色卡：来自 app/data/palettes/*.json，只读
    CUSTOM = "custom"    # 自定义色卡：用户通过接口导入，存数据库，可删除


class ExportFormat(StrEnum):
    """图纸导出格式。"""

    PNG = "png"    # 整张图纸图片（带坐标、色号、用量图例）
    PDF = "pdf"    # 打印用 PDF：首页总览 + 每块豆板一页（按实际豆子尺寸 1:1 输出）
    JSON = "json"  # 原始网格数据，便于二次开发
    CSV = "csv"    # 用量清单（色号、名称、颗数、含损耗建议购买量）
