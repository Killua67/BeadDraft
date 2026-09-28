"""
转换与导出接口（不落库）。

- POST /api/convert：上传图片 + 参数，返回拼豆网格与用量清单
- POST /api/export：提交网格数据，直接导出 PNG / PDF / JSON / CSV

图片处理是 CPU 密集型操作，接口用同步 def 定义，FastAPI 会放到线程池执行，不阻塞事件循环。
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import BadRequestError
from app.core.logger import get_logger
from app.schemas import ConvertParams, ConvertResponse, ExportRequest
from app.services.converter import convert_image
from app.services.export_service import export_grid
from app.services.grid_utils import validate_grid_codes
from app.services.palette_service import get_palette

router = APIRouter(prefix="/api", tags=["转换与导出"])
logger = get_logger(__name__)


@router.post("/convert", response_model=ConvertResponse, summary="图片转拼豆网格")
def convert(
    file: UploadFile = File(..., description="图片文件（PNG / JPG / WEBP / GIF 等）"),
    params: str = Form("{}", description="转换参数 JSON 字符串，结构见 ConvertParams"),
    db: Session = Depends(get_db),
):
    try:
        options = ConvertParams.model_validate_json(params or "{}")
    except ValidationError as exc:
        raise BadRequestError(f"参数错误：{exc.errors()[0]['msg']}") from exc

    data = file.file.read()
    size_mb = len(data) / 1024 / 1024
    if size_mb > settings.max_upload_mb:
        raise BadRequestError(f"图片不能超过 {settings.max_upload_mb}MB（当前 {size_mb:.1f}MB）")
    logger.info("收到转换请求：文件=%s 大小=%.2fMB 参数=%s", file.filename, size_mb, options.model_dump_json())

    palette = get_palette(db, options.palette_id)
    return convert_image(data, options, palette)


@router.post("/export", summary="导出未保存的图纸")
def export(payload: ExportRequest, db: Session = Depends(get_db)):
    palette = get_palette(db, payload.palette_id)
    validate_grid_codes(payload.grid, palette)
    file = export_grid(payload.grid, palette, payload.name, payload)
    return Response(file.content, media_type=file.media_type, headers=file.headers)
