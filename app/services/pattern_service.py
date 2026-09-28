"""
图纸的增删改查。数据库中网格以 JSON 文本存储，读写时在这里做序列化。
"""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logger import get_logger
from app.models import Pattern
from app.schemas import PatternCreate, PatternUpdate
from app.services.grid_utils import bead_count, build_bom, validate_grid_codes
from app.services.palette_service import get_palette

logger = get_logger(__name__)


def to_summary(p: Pattern) -> dict:
    """ORM 对象 -> PatternSummary 字典。"""
    return {
        "id": p.id, "name": p.name, "palette_id": p.palette_id, "width": p.width, "height": p.height,
        "bead_count": p.bead_count, "color_count": p.color_count, "source_filename": p.source_filename,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "thumbnail_url": f"/api/patterns/{p.id}/thumbnail.png?v={int(p.updated_at.timestamp())}",
        "done_color_count": len(json.loads(p.done_codes_json or "[]")),
    }


def to_detail(db: Session, p: Pattern) -> dict:
    """ORM 对象 -> PatternDetail 字典（含网格和用量清单）。"""
    grid = json.loads(p.grid_json)
    palette = get_palette(db, p.palette_id)
    return {**to_summary(p), "grid": grid, "colors": build_bom(grid, palette), "params": json.loads(p.params_json),
            "done_codes": json.loads(p.done_codes_json or "[]")}


def get_pattern(db: Session, pattern_id: int) -> Pattern:
    pattern = db.get(Pattern, pattern_id)
    if pattern is None:
        raise NotFoundError(f"图纸不存在：{pattern_id}")
    return pattern


def list_patterns(db: Session, limit: int, offset: int) -> tuple[int, list[Pattern]]:
    total = db.scalar(select(func.count()).select_from(Pattern)) or 0
    rows = db.scalars(select(Pattern).order_by(Pattern.updated_at.desc(), Pattern.id.desc()).limit(limit).offset(offset)).all()
    return total, list(rows)


def create_pattern(db: Session, payload: PatternCreate) -> Pattern:
    palette = get_palette(db, payload.palette_id)
    validate_grid_codes(payload.grid, palette)
    bom = build_bom(payload.grid, palette)
    pattern = Pattern(
        name=payload.name,
        palette_id=palette.id,
        width=len(payload.grid[0]),
        height=len(payload.grid),
        grid_json=json.dumps(payload.grid, ensure_ascii=False, separators=(",", ":")),
        params_json=payload.params.model_dump_json() if payload.params else "{}",
        bead_count=bead_count(payload.grid),
        color_count=len(bom),
        source_filename=payload.source_filename,
    )
    db.add(pattern)
    db.commit()
    db.refresh(pattern)
    logger.info("保存图纸 #%d「%s」：%dx%d，%d 颗，%d 色",
                pattern.id, pattern.name, pattern.width, pattern.height, pattern.bead_count, pattern.color_count)
    return pattern


def update_pattern(db: Session, pattern_id: int, payload: PatternUpdate) -> Pattern:
    pattern = get_pattern(db, pattern_id)
    if payload.name is not None:
        pattern.name = payload.name
    if payload.grid is not None:
        palette = get_palette(db, pattern.palette_id)
        validate_grid_codes(payload.grid, palette)
        pattern.grid_json = json.dumps(payload.grid, ensure_ascii=False, separators=(",", ":"))
        pattern.width, pattern.height = len(payload.grid[0]), len(payload.grid)
        pattern.bead_count = bead_count(payload.grid)
        pattern.color_count = len(build_bom(payload.grid, palette))
    if payload.grid is not None or payload.done_codes is not None:
        # 进度只保留当前网格里还存在的色号（改图后删掉的颜色不再算作「已完成」）
        present = {code for row in json.loads(pattern.grid_json) for code in row if code is not None}
        done = payload.done_codes if payload.done_codes is not None else json.loads(pattern.done_codes_json or "[]")
        pattern.done_codes_json = json.dumps([c for c in dict.fromkeys(done) if c in present], ensure_ascii=False)
    db.commit()
    db.refresh(pattern)
    logger.info("更新图纸 #%d「%s」：进度 %d/%d 色", pattern.id, pattern.name,
                len(json.loads(pattern.done_codes_json)), pattern.color_count)
    return pattern


def delete_pattern(db: Session, pattern_id: int) -> None:
    pattern = get_pattern(db, pattern_id)
    db.delete(pattern)
    db.commit()
    logger.info("删除图纸 #%d「%s」", pattern_id, pattern.name)
