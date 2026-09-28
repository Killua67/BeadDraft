"""
预先下载 AI 抠图模型（部署时使用，避免用户首次使用时等待下载）。

用法（项目根目录执行）：
    uv run python scripts/download_model.py                    # 下载默认通用模型 isnet-general-use（约 170MB）
    uv run python scripts/download_model.py u2net_human_seg    # 下载指定模型
    uv run python scripts/download_model.py --list             # 查看全部模型及状态

模型保存在 PB_MODEL_DIR（默认 ~/.u2net），下载后会校验 MD5。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import setup_logging  # noqa: E402
from app.enums import ModelStatus, SegModel  # noqa: E402
from app.services import segmentation  # noqa: E402


def main() -> int:
    setup_logging()
    arg = sys.argv[1] if len(sys.argv) > 1 else SegModel.ISNET_GENERAL.value

    if arg == "--list":
        for model in segmentation.MODELS:
            st = segmentation.model_status(model)
            print(f"{model.value:20s} {st['name']:4s} {st['size_mb']:6.1f}MB  {st['status']}  {st['description']}")
        return 0

    try:
        model = SegModel(arg)
    except ValueError:
        print(f"未知模型：{arg}，可选：{', '.join(m.value for m in SegModel)}")
        return 1

    if segmentation.is_ready(model):
        print(f"模型 {model.value} 已存在：{segmentation.model_path(model)}")
        return 0
    segmentation.download_model(model)
    return 0 if segmentation.model_status(model)["status"] == ModelStatus.READY else 1


if __name__ == "__main__":
    sys.exit(main())
