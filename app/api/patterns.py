"""
图纸接口：保存、列表、详情、修改、删除、缩略图、导出。
"""

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.enums import ExportFormat
from app.schemas import ExportOptions, PatternCreate, PatternDetail, PatternListResponse, PatternUpdate
from app.services import pattern_service
from app.services.export_service import export_grid
from app.services.palette_service import get_palette
from app.services.renderer import render_thumbnail

router = APIRouter(prefix="/api/patterns", tags=["图纸"])


@router.get("", response_model=PatternListResponse, summary="图纸列表（按修改时间倒序）")
def list_patterns(
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db),
):
    total, rows = pattern_service.list_patterns(db, limit, offset)
    return {"total": total, "items": [pattern_service.to_summary(p) for p in rows]}


@router.post("", response_model=PatternDetail, status_code=201, summary="保存图纸")
def create_pattern(payload: PatternCreate, db: Session = Depends(get_db)):
    pattern = pattern_service.create_pattern(db, payload)
    return pattern_service.to_detail(db, pattern)


@router.get("/{pattern_id}", response_model=PatternDetail, summary="图纸详情")
def get_pattern(pattern_id: int, db: Session = Depends(get_db)):
    return pattern_service.to_detail(db, pattern_service.get_pattern(db, pattern_id))


@router.put("/{pattern_id}", response_model=PatternDetail, summary="修改图纸（名称或网格）")
def update_pattern(pattern_id: int, payload: PatternUpdate, db: Session = Depends(get_db)):
    pattern = pattern_service.update_pattern(db, pattern_id, payload)
    return pattern_service.to_detail(db, pattern)


@router.delete("/{pattern_id}", status_code=204, summary="删除图纸")
def delete_pattern(pattern_id: int, db: Session = Depends(get_db)):
    pattern_service.delete_pattern(db, pattern_id)


@router.get("/{pattern_id}/thumbnail.png", summary="图纸缩略图", response_class=Response)
def thumbnail(pattern_id: int, db: Session = Depends(get_db)):
    pattern = pattern_service.get_pattern(db, pattern_id)
    data = render_thumbnail(json.loads(pattern.grid_json), get_palette(db, pattern.palette_id))
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@router.get("/{pattern_id}/export", summary="导出已保存的图纸")
def export_pattern(
    pattern_id: int,
    format: ExportFormat = Query(ExportFormat.PNG, description="导出格式：png / pdf / json / csv"),
    board_size: int = Query(29, ge=5, le=200, description="单块豆板边长（格）"),
    pitch_mm: float | None = Query(None, gt=0, le=20, description="豆子间距（毫米），不填用色卡默认值"),
    db: Session = Depends(get_db),
):
    pattern = pattern_service.get_pattern(db, pattern_id)
    options = ExportOptions(format=format, board_size=board_size, pitch_mm=pitch_mm)
    file = export_grid(json.loads(pattern.grid_json), get_palette(db, pattern.palette_id), pattern.name, options)
    return Response(file.content, media_type=file.media_type, headers=file.headers)
