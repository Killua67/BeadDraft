"""
AI 抠图：用 ONNX 分割模型识别照片主体，输出前景蒙版。

- 模型来自 rembg 项目公开发布的 ONNX 文件，预处理 / 后处理参数与 rembg 源码一致
- 只依赖 onnxruntime，不引入 rembg 包本身（它会连带安装 opencv、scipy、numba 等大量依赖）
- 模型文件较大（约 170MB），首次使用前通过接口在后台下载，下载完成校验 MD5
- 推理结果按「图片内容 + 模型」缓存，调整其他参数时不会重复推理
"""

import hashlib
import threading
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.logger import get_logger
from app.enums import ModelStatus, SegModel

logger = get_logger(__name__)

_RELEASE = "https://github.com/danielgatis/rembg/releases/download/v0.0.0"
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class SegModelInfo:
    """模型元数据。mean / std / input_size 为模型要求的输入归一化参数和尺寸。"""

    id: SegModel
    name: str
    description: str
    size_mb: float
    md5: str
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    input_size: int

    @property
    def filename(self) -> str:
        return f"{self.id.value}.onnx"

    @property
    def url(self) -> str:
        return f"{_RELEASE}/{self.filename}"


MODELS: dict[SegModel, SegModelInfo] = {
    m.id: m for m in [
        SegModelInfo(SegModel.ISNET_GENERAL, "通用", "照片中的各类主体（动物、物品、人物），推荐", 170.4,
                     "fc16ebd8b0c10d971d3513d564d01e29", (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), 1024),
        SegModelInfo(SegModel.U2NET_HUMAN, "人像", "专门针对人物照片", 167.8,
                     "c09ddc2e0104f800e3e1bb4652583d1f", _IMAGENET_MEAN, _IMAGENET_STD, 320),
        SegModelInfo(SegModel.ISNET_ANIME, "动漫", "二次元 / 动漫角色插画", 167.9,
                     "6f184e756bb3bd901c8849220a83e38e", _IMAGENET_MEAN, (1.0, 1.0, 1.0), 1024),
        SegModelInfo(SegModel.U2NETP, "轻量", "体积小、速度快，边缘精度较差", 4.4,
                     "8e83ca70e441ab06c318d82300c84806", _IMAGENET_MEAN, _IMAGENET_STD, 320),
    ]
}


# ---------------------------------------------------------------- 下载管理

@dataclass
class _Download:
    """一次下载任务的进度。"""

    status: ModelStatus
    downloaded: int = 0
    total: int = 0
    error: str | None = None


_lock = threading.Lock()
_downloads: dict[SegModel, _Download] = {}


def model_path(model: SegModel):
    return settings.model_dir / MODELS[model].filename


def is_ready(model: SegModel) -> bool:
    return model_path(model).exists() and _downloads.get(model, _Download(ModelStatus.READY)).status != ModelStatus.DOWNLOADING


def model_status(model: SegModel) -> dict:
    """模型状态，结构对应 schemas.SegModelStatus。"""
    info = MODELS[model]
    with _lock:
        task = _downloads.get(model)
    if task and task.status in (ModelStatus.DOWNLOADING, ModelStatus.FAILED):
        status, progress, error = task.status, (task.downloaded / task.total if task.total else 0.0), task.error
    elif model_path(model).exists():
        status, progress, error = ModelStatus.READY, 1.0, None
    else:
        status, progress, error = ModelStatus.NOT_DOWNLOADED, 0.0, None
    return {
        "id": info.id, "name": info.name, "description": info.description, "size_mb": info.size_mb,
        "status": status, "progress": round(progress, 4), "error": error,
    }


def start_download(model: SegModel) -> dict:
    """在后台线程下载模型；已下载或正在下载时直接返回当前状态。"""
    with _lock:
        task = _downloads.get(model)
        busy = task is not None and task.status == ModelStatus.DOWNLOADING
        if not busy and not model_path(model).exists():
            _downloads[model] = _Download(ModelStatus.DOWNLOADING)
            threading.Thread(target=download_model, args=(model,), name=f"download-{model}", daemon=True).start()
    return model_status(model)


def download_model(model: SegModel) -> None:
    """
    下载模型（同步执行，后台线程或 scripts/download_model.py 调用）。
    先写入 .part 临时文件，校验 MD5 通过后再改名为正式文件，避免留下不完整的模型。
    """
    info = MODELS[model]
    target = model_path(model)
    part = target.with_suffix(".onnx.part")
    with _lock:
        task = _downloads.setdefault(model, _Download(ModelStatus.DOWNLOADING))
        task.status, task.error = ModelStatus.DOWNLOADING, None
    t0 = time.perf_counter()
    logger.info("开始下载模型 %s（约 %.1fMB）：%s -> %s", model, info.size_mb, info.url, target)
    try:
        settings.model_dir.mkdir(parents=True, exist_ok=True)
        md5 = hashlib.md5()
        request = urllib.request.Request(info.url, headers={"User-Agent": "perler-bead"})
        with urllib.request.urlopen(request, timeout=60) as resp, open(part, "wb") as fh:
            task.total = int(resp.headers.get("Content-Length") or 0)
            last_log = 0.0
            while chunk := resp.read(1024 * 256):
                fh.write(chunk)
                md5.update(chunk)
                task.downloaded += len(chunk)
                if time.perf_counter() - last_log > 5:
                    last_log = time.perf_counter()
                    logger.info("下载模型 %s：%.1f / %.1f MB", model, task.downloaded / 2**20, task.total / 2**20)
        if md5.hexdigest() != info.md5:
            raise ValueError(f"MD5 校验失败（{md5.hexdigest()}），文件可能不完整，请重试")
        part.replace(target)
        with _lock:
            task.status = ModelStatus.READY
        logger.info("模型 %s 下载完成，耗时 %.0fs", model, time.perf_counter() - t0)
    except Exception as exc:  # noqa: BLE001  网络错误类型很多，统一记录为失败，允许重试
        with _lock:
            task.status, task.error = ModelStatus.FAILED, str(exc)
        part.unlink(missing_ok=True)  # 只删除本次下载产生的单个临时文件
        logger.error("模型 %s 下载失败：%s", model, exc)


# ---------------------------------------------------------------- 推理

_sessions: dict[SegModel, object] = {}
_session_lock = threading.Lock()
_mask_cache: OrderedDict[tuple[str, SegModel], Image.Image] = OrderedDict()
_MASK_CACHE_SIZE = 16
# 蒙版对比度拉伸：模型对主体内部（如企鹅的白肚皮）常常只给出 0.3~0.5 的概率，
# 直接按 0.5 判断会把主体内部挖空。低于 LOW 视为背景，高于 HIGH 视为主体，中间线性过渡。
_MASK_LOW = 0.05
_MASK_HIGH = 0.3


def _session(model: SegModel):
    """懒加载 onnxruntime 会话（每个模型只加载一次）。"""
    with _session_lock:
        if model not in _sessions:
            import onnxruntime as ort  # 延迟导入：不用 AI 抠图时不加载

            t0 = time.perf_counter()
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _sessions[model] = ort.InferenceSession(
                str(model_path(model)), sess_options=opts, providers=["CPUExecutionProvider"],
            )
            logger.info("加载模型 %s，耗时 %.1fs", model, time.perf_counter() - t0)
        return _sessions[model]


def _run_model(img: Image.Image, model: SegModel) -> np.ndarray:
    """执行模型推理，返回 input_size×input_size 的前景概率图（0~1）。与 rembg 的预处理保持一致。"""
    info = MODELS[model]
    size = (info.input_size, info.input_size)
    arr = np.asarray(img.convert("RGB").resize(size, Image.Resampling.LANCZOS), dtype=np.float64)
    arr = arr / max(arr.max(), 1e-6)
    arr = (arr - np.array(info.mean)) / np.array(info.std)
    tensor = arr.transpose(2, 0, 1)[None].astype(np.float32)

    session = _session(model)
    pred = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    lo, hi = float(pred.min()), float(pred.max())
    return (pred - lo) / (hi - lo) if hi > lo else np.zeros_like(pred)


def predict_mask(img: Image.Image, model: SegModel, cache_key: str) -> Image.Image:
    """
    返回与 img 同尺寸的前景蒙版（L 模式，255 = 主体）。
    cache_key 为原始图片内容的哈希，同一张图调整其他参数时直接复用结果。
    """
    if not is_ready(model):
        info = MODELS[model]
        raise BadRequestError(f"AI 抠图模型「{info.name}」尚未下载（约 {info.size_mb:.0f}MB），请先在页面上点击下载")

    key = (cache_key, model)
    cached = _mask_cache.get(key)
    if cached is not None and cached.size == img.size:
        _mask_cache.move_to_end(key)
        return cached

    t0 = time.perf_counter()
    prob = np.clip((_run_model(img, model) - _MASK_LOW) / (_MASK_HIGH - _MASK_LOW), 0, 1)
    mask = Image.fromarray((prob * 255).astype(np.uint8), "L").resize(img.size, Image.Resampling.LANCZOS)
    _mask_cache[key] = mask
    while len(_mask_cache) > _MASK_CACHE_SIZE:
        _mask_cache.popitem(last=False)
    logger.info("AI 抠图完成：模型=%s 图片=%dx%d 耗时=%dms", model, img.width, img.height, (time.perf_counter() - t0) * 1000)
    return mask
