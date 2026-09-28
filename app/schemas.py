"""
Pydantic 请求/响应模型（接口数据结构）。

每个字段都写了 description，会显示在 /docs 接口文档中。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.enums import DitherMode, ExportFormat, PaletteSource, ResampleMode

# 网格类型：grid[行][列] = 色号，None 表示空位
Grid = list[list[str | None]]


def check_grid(grid: Grid) -> Grid:
    """校验网格：非空、每行长度一致、不超过尺寸上限。"""
    if not grid or not grid[0]:
        raise ValueError("网格不能为空")
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise ValueError("网格每一行的长度必须一致")
    if width > settings.max_grid_size or len(grid) > settings.max_grid_size:
        raise ValueError(f"网格尺寸不能超过 {settings.max_grid_size}×{settings.max_grid_size}")
    return grid


# ---------------------------------------------------------------- 色卡

class PaletteColor(BaseModel):
    """色卡中的一个颜色。"""

    code: str = Field(..., min_length=1, max_length=20, description="色号，如 MARD 的 A1、Perler 的 80-19001")
    name: str = Field("", max_length=50, description="颜色名称")
    hex: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$", description="颜色值，#RRGGBB")


class PaletteSummary(BaseModel):
    """色卡概要（列表用）。"""

    id: str = Field(..., description="色卡 ID")
    name: str = Field(..., description="色卡名称")
    brand: str = Field(..., description="品牌")
    bead_size_mm: float = Field(..., description="豆子直径（毫米）：5.0 标准豆 / 2.6 迷你豆")
    description: str = Field("", description="说明")
    source: PaletteSource = Field(..., description="来源：builtin 内置（只读）/ custom 自定义（可删除）")
    color_count: int = Field(..., description="颜色数量")


class PaletteDetail(PaletteSummary):
    """色卡详情（含全部颜色）。"""

    data_source: str = Field("", description="色值数据出处")
    colors: list[PaletteColor] = Field(..., description="颜色列表")


class PaletteCreate(BaseModel):
    """导入自定义色卡。"""

    name: str = Field(..., min_length=1, max_length=100, description="色卡名称")
    brand: str = Field("自定义", max_length=50, description="品牌")
    bead_size_mm: float = Field(5.0, gt=0, le=20, description="豆子直径（毫米）")
    description: str = Field("", max_length=500, description="说明")
    colors: list[PaletteColor] = Field(..., min_length=1, max_length=1000, description="颜色列表")

    @field_validator("colors")
    @classmethod
    def _unique_codes(cls, colors: list[PaletteColor]) -> list[PaletteColor]:
        codes = [c.code for c in colors]
        dup = {c for c in codes if codes.count(c) > 1}
        if dup:
            raise ValueError(f"色号重复：{', '.join(sorted(dup))}")
        return colors


# ---------------------------------------------------------------- 转换

class ConvertParams(BaseModel):
    """图片转拼豆的参数。"""

    palette_id: str = Field("mard", description="使用的色卡 ID")
    width: int = Field(52, ge=4, le=settings.max_grid_size, description="宽度（格数 = 横向豆子数）")
    height: int | None = Field(
        None, ge=4, le=settings.max_grid_size, description="高度（格数），不填则按原图比例自动计算",
    )
    max_colors: int = Field(24, ge=0, le=300, description="最多使用的颜色种数，0 表示不限制")
    resample: ResampleMode = Field(ResampleMode.BOX, description="缩放方式：box 区域平均（照片）/ nearest 最近邻（像素画）")
    dither: DitherMode = Field(DitherMode.NONE, description="抖动方式：none 不抖动（推荐）/ floyd_steinberg 误差扩散")
    dither_strength: float = Field(0.6, ge=0, le=1, description="抖动强度 0~1，仅 floyd_steinberg 时生效")
    remove_background: bool = Field(False, description="是否自动去除背景（从图片四周向内填充相近颜色）")
    bg_tolerance: float = Field(12, ge=1, le=60, description="去背景的颜色容差（ΔE00），越大去得越多")
    clean_isolated: bool = Field(True, description="是否清理孤立杂点（周围 8 格都不同色的单颗豆）")
    outline: bool = Field(False, description="是否给主体外围加一圈描边（需要图片有透明/背景区域）")
    outline_code: str | None = Field(None, description="描边色号，不填则自动选色卡中最深的颜色")
    saturation: float = Field(1.0, ge=0, le=3, description="饱和度倍数，1 为原图，拼豆通常适当调高更好看")
    contrast: float = Field(1.0, ge=0, le=3, description="对比度倍数，1 为原图")
    brightness: float = Field(1.0, ge=0, le=3, description="亮度倍数，1 为原图")
    excluded_codes: list[str] = Field(default_factory=list, description="不使用的色号（如手上没有的颜色）")


class BomItem(BaseModel):
    """用量清单中的一项。"""

    code: str = Field(..., description="色号")
    name: str = Field(..., description="颜色名称")
    hex: str = Field(..., description="颜色值 #RRGGBB")
    count: int = Field(..., description="需要的颗数")


class ConvertResponse(BaseModel):
    """转换结果（未保存）。"""

    palette_id: str = Field(..., description="色卡 ID")
    width: int = Field(..., description="宽度（格）")
    height: int = Field(..., description="高度（格）")
    grid: Grid = Field(..., description="网格数据 grid[行][列] = 色号，null 为空位")
    colors: list[BomItem] = Field(..., description="用量清单，按颗数从多到少排序")
    bead_count: int = Field(..., description="豆子总颗数")
    color_count: int = Field(..., description="颜色种数")
    elapsed_ms: int = Field(..., description="处理耗时（毫秒）")
    warnings: list[str] = Field(default_factory=list, description="提示信息（如尺寸被调整、描边未生效等）")


# ---------------------------------------------------------------- 图纸

class PatternCreate(BaseModel):
    """保存图纸。"""

    name: str = Field(..., min_length=1, max_length=100, description="图纸名称")
    palette_id: str = Field(..., description="色卡 ID")
    grid: Grid = Field(..., description="网格数据")
    params: ConvertParams | None = Field(None, description="生成参数（可选，便于之后复现）")
    source_filename: str | None = Field(None, max_length=255, description="原始图片文件名")

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: Grid) -> Grid:
        return check_grid(v)


class PatternUpdate(BaseModel):
    """修改图纸（只传需要修改的字段）。"""

    name: str | None = Field(None, min_length=1, max_length=100, description="图纸名称")
    grid: Grid | None = Field(None, description="网格数据（手动编辑后的结果）")

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: Grid | None) -> Grid | None:
        return None if v is None else check_grid(v)


class PatternSummary(BaseModel):
    """图纸概要（列表用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="图纸 ID")
    name: str = Field(..., description="图纸名称")
    palette_id: str = Field(..., description="色卡 ID")
    width: int = Field(..., description="宽度（格）")
    height: int = Field(..., description="高度（格）")
    bead_count: int = Field(..., description="豆子总颗数")
    color_count: int = Field(..., description="颜色种数")
    source_filename: str | None = Field(None, description="原始图片文件名")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最后修改时间")
    thumbnail_url: str = Field("", description="缩略图地址")


class PatternDetail(PatternSummary):
    """图纸详情。"""

    grid: Grid = Field(..., description="网格数据")
    colors: list[BomItem] = Field(..., description="用量清单")
    params: dict = Field(default_factory=dict, description="生成参数")


class PatternListResponse(BaseModel):
    """图纸分页列表。"""

    total: int = Field(..., description="总数")
    items: list[PatternSummary] = Field(..., description="当前页数据")


# ---------------------------------------------------------------- 导出

class ExportOptions(BaseModel):
    """导出选项。"""

    format: ExportFormat = Field(ExportFormat.PNG, description="导出格式：png / pdf / json / csv")
    board_size: int = Field(29, ge=5, le=200, description="单块豆板边长（格），常见 29 或 52，用于分板和画分板线")
    pitch_mm: float | None = Field(
        None, gt=0, le=20, description="豆子间距（毫米），PDF 按此 1:1 输出；不填则使用色卡的豆子直径",
    )


class ExportRequest(ExportOptions):
    """导出未保存的图纸（直接提交网格数据）。"""

    name: str = Field("拼豆图纸", max_length=100, description="图纸名称（显示在图纸标题）")
    palette_id: str = Field(..., description="色卡 ID")
    grid: Grid = Field(..., description="网格数据")

    @field_validator("grid")
    @classmethod
    def _grid(cls, v: Grid) -> Grid:
        return check_grid(v)
