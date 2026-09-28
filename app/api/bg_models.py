"""
AI 抠图模型接口：查询模型下载状态、触发后台下载。
"""

from fastapi import APIRouter

from app.enums import SegModel
from app.schemas import SegModelStatus
from app.services import segmentation

router = APIRouter(prefix="/api/bg-models", tags=["AI 抠图模型"])


@router.get("", response_model=list[SegModelStatus], summary="模型列表与下载状态")
def list_models():
    return [segmentation.model_status(m) for m in segmentation.MODELS]


@router.get("/{model_id}", response_model=SegModelStatus, summary="单个模型状态（下载时轮询用）")
def get_model(model_id: SegModel):
    return segmentation.model_status(model_id)


@router.post("/{model_id}/download", response_model=SegModelStatus, status_code=202, summary="后台下载模型")
def download(model_id: SegModel):
    return segmentation.start_download(model_id)
