"""
图纸的增删改查。数据库中网格以 JSON 文本存储，读写时在这里做序列化。

拼豆进度（done_codes）条目格式：
  - "A1"        整张图纸的 A1 都已拼完
  - "29/2:A1"   按 29×29 分板时，第 2 块豆板上的 A1 已拼完（板号从 1 开始，按行从左到右）
条目自带板子边长，前端切换豆板规格后旧条目不会错位。
"""

import json
import math
import re
from collections import Counter

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
        **dict(zip(("done_color_count", "progress"),
                   progress_stats(json.loads(p.grid_json), json.loads(p.done_codes_json or "[]")))),
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
        # 进度只保留格式正确、且色号仍在当前网格中的条目（改图后删掉的颜色不再算作「已完成」）
        done = payload.done_codes if payload.done_codes is not None else json.loads(pattern.done_codes_json or "[]")
        pattern.done_codes_json = json.dumps(clean_progress(done, json.loads(pattern.grid_json)), ensure_ascii=False)
    db.commit()
    db.refresh(pattern)
    done_colors, progress = progress_stats(json.loads(pattern.grid_json), json.loads(pattern.done_codes_json))
    logger.info("更新图纸 #%d「%s」：进度 %d/%d 色，%.0f%%", pattern.id, pattern.name,
                done_colors, pattern.color_count, progress * 100)
    return pattern


def delete_pattern(db: Session, pattern_id: int) -> None:
    pattern = get_pattern(db, pattern_id)
    db.delete(pattern)
    db.commit()
    logger.info("删除图纸 #%d「%s」", pattern_id, pattern.name)


# ---------------------------------------------------------------- 拼豆进度

_ENTRY = re.compile(r"^(?:(\d+)/(\d+):)?(.+)$")


def _parse_entry(entry: str) -> tuple[int, int, str] | None:
    """解析进度条目，返回 (板边长, 板号, 色号)；整图条目的板边长和板号为 0。格式不对返回 None。"""
    m = _ENTRY.match(entry)
    if not m:
        return None
    size, board, code = int(m.group(1) or 0), int(m.group(2) or 0), m.group(3)
    if (m.group(1) is not None) and (size < 1 or board < 1):
        return None
    return size, board, code


def board_index(r: int, c: int, width: int, board_size: int) -> int:
    """格子 (r, c) 所在的豆板编号（从 1 开始，按行从左到右）。"""
    return (r // board_size) * math.ceil(width / board_size) + c // board_size + 1


def clean_progress(entries: list[str], grid: list) -> list[str]:
    """去掉格式错误、重复、以及色号已不在网格中的条目。"""
    present = {code for row in grid for code in row if code is not None}
    result = []
    for entry in dict.fromkeys(entries):
        parsed = _parse_entry(entry)
        if parsed and parsed[2] in present:
            result.append(entry)
    return result


def progress_stats(grid: list, entries: list[str]) -> tuple[int, float]:
    """根据进度条目统计：(已全部拼完的颜色数, 已拼豆子占比)。"""
    if not grid or not entries:
        return 0, 0.0
    plain: set[str] = set()
    boards: dict[int, set[tuple[int, str]]] = {}
    for entry in entries:
        parsed = _parse_entry(entry)
        if not parsed:
            continue
        size, board, code = parsed
        if size:
            boards.setdefault(size, set()).add((board, code))
        else:
            plain.add(code)

    width = len(grid[0])
    total, done = Counter(), Counter()
    for r, row in enumerate(grid):
        for c, code in enumerate(row):
            if code is None:
                continue
            total[code] += 1
            if code in plain or any((board_index(r, c, width, size), code) in keys for size, keys in boards.items()):
                done[code] += 1
    done_colors = sum(1 for code, n in total.items() if done[code] == n)
    return done_colors, round(sum(done.values()) / max(1, sum(total.values())), 4)
