"""
枚举定义。所有枚举值都是字符串，可直接用于 API 参数与数据库存储。
"""

from enum import StrEnum


class ResampleMode(StrEnum):
    """图片缩放到网格时的采样方式。"""

    BOX = "box"          # 区域平均：每格取对应区域的平均色，适合照片、插画
    NEAREST = "nearest"  # 最近邻：每格取一个像素，适合本身就是像素画的图片（不会产生混合色）


class FitMode(StrEnum):
    """图纸尺寸的确定方式。"""

    BOARD = "board"  # 适配豆板：主体等比缩放到刚好放进一块 board_size × board_size 的豆板，居中摆放（推荐）
    WIDTH = "width"  # 按宽度：按指定的宽度（格数）缩放，高度按比例或手动指定，适合跨多块板的大图


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


class BackgroundMethod(StrEnum):
    """去除背景的方式。"""

    COLOR = "color"  # 颜色识别：从图片四周向内填充与背景相近的颜色，适合纯色 / 渐变背景的插画、截图
    AI = "ai"        # AI 抠图：用分割模型识别主体，适合照片（人像、宠物、物品），需先下载模型


class SegModel(StrEnum):
    """AI 抠图模型（ONNX 格式，来自 rembg 项目公开发布的模型）。"""

    ISNET_GENERAL = "isnet-general-use"  # 通用模型（约 170MB）：照片中的各类主体，推荐默认使用
    U2NET_HUMAN = "u2net_human_seg"      # 人像模型（约 168MB）：专门针对人物
    ISNET_ANIME = "isnet-anime"          # 动漫模型（约 168MB）：二次元 / 动漫角色
    U2NETP = "u2netp"                    # 轻量模型（约 4.4MB）：速度快、体积小，边缘精度较差


class ModelStatus(StrEnum):
    """模型文件的下载状态。"""

    NOT_DOWNLOADED = "not_downloaded"  # 未下载
    DOWNLOADING = "downloading"        # 下载中
    READY = "ready"                    # 已就绪，可以使用
    FAILED = "failed"                  # 下载失败（可重试）
