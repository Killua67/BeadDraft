"""
色卡接口：查询内置/自定义色卡，导入和删除自定义色卡。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas import PaletteCreate, PaletteDetail, PaletteSummary
from app.services import palette_service

router = APIRouter(prefix="/api/palettes", tags=["色卡"])


@router.get("", response_model=list[PaletteSummary], summary="色卡列表")
def list_palettes(db: Session = Depends(get_db)):
    return [p.summary() for p in palette_service.list_palettes(db)]


@router.get("/{palette_id}", response_model=PaletteDetail, summary="色卡详情（含全部颜色）")
def get_palette(palette_id: str, db: Session = Depends(get_db)):
    return palette_service.get_palette(db, palette_id).detail()


@router.post("", response_model=PaletteDetail, status_code=201, summary="导入自定义色卡")
def create_palette(payload: PaletteCreate, db: Session = Depends(get_db)):
    return palette_service.create_custom_palette(db, payload).detail()


@router.delete("/{palette_id}", status_code=204, summary="删除自定义色卡（内置色卡不可删除）")
def delete_palette(palette_id: str, db: Session = Depends(get_db)):
    palette_service.delete_custom_palette(db, palette_id)
