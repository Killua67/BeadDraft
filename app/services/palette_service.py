"""
色卡服务：加载内置色卡（JSON 文件）与自定义色卡（数据库），并提供统一的 Palette 对象。

Palette 对象里预先算好了每个颜色的 RGB / Lab，转换算法直接使用。
"""

import json
import secrets
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.logger import get_logger
from app.enums import PaletteSource
from app.models import CustomPalette
from app.schemas import PaletteCreate
from app.services.color import hex_to_rgb, srgb_to_lab

logger = get_logger(__name__)


@dataclass
class Palette:
    """色卡（内存对象）。colors 中每项为 {"code", "name", "hex"}。"""

    id: str
    name: str
    brand: str
    bead_size_mm: float
    description: str
    source: PaletteSource
    colors: list[dict]
    data_source: str = ""
    # 以下为派生字段，__post_init__ 中计算
    codes: list[str] = field(init=False)
    index: dict[str, int] = field(init=False)   # 色号 -> 下标
    rgb: np.ndarray = field(init=False)         # (P, 3) uint8
    lab: np.ndarray = field(init=False)         # (P, 3) float64

    def __post_init__(self) -> None:
        self.codes = [c["code"] for c in self.colors]
        self.index = {code: i for i, code in enumerate(self.codes)}
        self.rgb = np.array([hex_to_rgb(c["hex"]) for c in self.colors], dtype=np.uint8).reshape(-1, 3)
        self.lab = srgb_to_lab(self.rgb)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "brand": self.brand, "bead_size_mm": self.bead_size_mm,
            "description": self.description, "source": self.source, "color_count": len(self.colors),
        }

    def detail(self) -> dict:
        return {**self.summary(), "data_source": self.data_source, "colors": self.colors}


@lru_cache(maxsize=1)
def _builtin_palettes() -> dict[str, Palette]:
    """读取 app/data/palettes/*.json（只在首次调用时读取，之后走缓存），按 sort_order 排序（国内品牌在前）。"""
    palettes: dict[str, Palette] = {}
    items = [json.loads(p.read_text(encoding="utf-8")) for p in settings.palette_dir.glob("*.json")]
    for data in sorted(items, key=lambda d: (d.get("sort_order", 999), d["id"])):
        palettes[data["id"]] = Palette(
            id=data["id"], name=data["name"], brand=data["brand"], bead_size_mm=data["bead_size_mm"],
            description=data.get("description", ""), source=PaletteSource.BUILTIN,
            colors=data["colors"], data_source=data.get("data_source", ""),
        )
    logger.info("加载内置色卡 %d 套：%s", len(palettes), ", ".join(palettes))
    return palettes


def _from_row(row: CustomPalette) -> Palette:
    return Palette(
        id=row.id, name=row.name, brand=row.brand, bead_size_mm=row.bead_size_mm,
        description=row.description, source=PaletteSource.CUSTOM,
        colors=json.loads(row.colors_json), data_source="用户导入",
    )


def list_palettes(db: Session) -> list[Palette]:
    """全部色卡：内置在前，自定义按创建时间在后。"""
    custom = db.scalars(select(CustomPalette).order_by(CustomPalette.created_at)).all()
    return [*_builtin_palettes().values(), *(_from_row(r) for r in custom)]


def get_palette(db: Session, palette_id: str) -> Palette:
    """按 ID 获取色卡，不存在时抛 NotFoundError。"""
    builtin = _builtin_palettes().get(palette_id)
    if builtin:
        return builtin
    row = db.get(CustomPalette, palette_id)
    if row is None:
        raise NotFoundError(f"色卡不存在：{palette_id}")
    return _from_row(row)


def create_custom_palette(db: Session, payload: PaletteCreate) -> Palette:
    """导入自定义色卡。"""
    row = CustomPalette(
        id=f"custom_{secrets.token_hex(4)}",
        name=payload.name,
        brand=payload.brand,
        bead_size_mm=payload.bead_size_mm,
        description=payload.description,
        colors_json=json.dumps(
            [{"code": c.code, "name": c.name or c.code, "hex": c.hex.upper(), "group": c.group} for c in payload.colors],
            ensure_ascii=False,
        ),
    )
    db.add(row)
    db.commit()
    logger.info("导入自定义色卡 %s（%s），共 %d 色", row.id, row.name, len(payload.colors))
    return _from_row(row)


def delete_custom_palette(db: Session, palette_id: str) -> None:
    """删除自定义色卡（内置色卡不允许删除）。"""
    if palette_id in _builtin_palettes():
        raise BadRequestError("内置色卡不能删除")
    row = db.get(CustomPalette, palette_id)
    if row is None:
        raise NotFoundError(f"色卡不存在：{palette_id}")
    db.delete(row)
    db.commit()
    logger.info("删除自定义色卡 %s（%s）", palette_id, row.name)
