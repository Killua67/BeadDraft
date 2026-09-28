"""
ORM 数据模型。

每个字段都通过 comment 写入数据库注释，JSON 字段在注释中说明了结构。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Pattern(Base):
    """拼豆图纸（用户保存的作品）。"""

    __tablename__ = "patterns"
    __table_args__ = {"comment": "拼豆图纸表"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="图纸名称")
    palette_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="使用的色卡 ID：内置色卡如 mard / perler / hama，自定义色卡为 custom_ 开头",
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False, comment="宽度（格数，即横向豆子数）")
    height: Mapped[int] = mapped_column(Integer, nullable=False, comment="高度（格数，即纵向豆子数）")
    grid_json: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment='网格数据 JSON：二维数组 grid[行][列]，元素为色号字符串，null 表示空位不放豆，如 [["A1",null],["B2","A1"]]',
    )
    params_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}",
        comment="生成参数 JSON（ConvertParams 结构：width、max_colors、dither 等），用于复现或再次调整",
    )
    bead_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="豆子总颗数（不含空位）")
    color_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="使用的颜色种数")
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="原始图片文件名，可为空")
    done_codes_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]",
        comment='拼豆进度条目列表 JSON，空列表表示还没开始。条目格式：「色号」表示整张图纸该颜色已拼完；「板边长/板号:色号」表示按该边长分板时某块豆板上该颜色已拼完（板号从 1 开始，按行从左到右），如 A1、29/2:B3',
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间（本地时间）")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="最后修改时间（本地时间）",
    )


class CustomPalette(Base):
    """自定义色卡（用户导入，例如按自己实物色卡校准后的颜色）。"""

    __tablename__ = "custom_palettes"
    __table_args__ = {"comment": "自定义色卡表"}

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="色卡 ID，自动生成，格式 custom_<8位随机串>")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="色卡名称")
    brand: Mapped[str] = mapped_column(String(50), nullable=False, default="自定义", comment="品牌名称")
    bead_size_mm: Mapped[float] = mapped_column(
        Float, nullable=False, default=5.0,
        comment="豆子直径（毫米），常见值：5.0 标准豆 / 2.6 迷你豆，用于打印 1:1 图纸",
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="", comment="说明")
    colors_json: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment='颜色列表 JSON：[{"code": 色号, "name": 名称, "hex": "#RRGGBB"}]',
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间（本地时间）")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="最后修改时间（本地时间）",
    )
