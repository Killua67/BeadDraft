"""去背景测试：颜色识别、AI 抠图（用假模型代替真实推理）、小碎块清理、模型接口。"""

import json

import numpy as np
import pytest

from app.enums import SegModel
from app.services import segmentation
from app.services.cleanup import remove_small_islands
from tests.helpers import make_border_subject_image, make_gradient_bg_image, make_image


def convert(client, image: bytes, **params):
    params.setdefault("fit_mode", "width")
    params.setdefault("crop_to_subject", False)  # 本文件验证去背景本身，不裁剪，便于按原图位置断言
    resp = client.post("/api/convert", files={"file": ("t.png", image, "image/png")},
                       data={"params": json.dumps(params)})
    return resp


def filled(grid) -> np.ndarray:
    return np.array([[c is not None for c in row] for row in grid])


def test_color_method_gradient_background(client):
    """渐变背景应被完整去除，只留下中间的圆形主体。"""
    data = convert(client, make_gradient_bg_image(), width=40, remove_background=True).json()
    mask = filled(data["grid"])
    assert not mask[0].any() and not mask[-1].any()          # 上下边缘（深蓝 / 浅蓝）都去掉了
    assert mask[20, 20]                                        # 中心保留
    ratio = mask.mean()
    assert 0.2 < ratio < 0.35                                  # 圆面积占比约 π·0.3² ≈ 0.28


def test_color_method_keeps_textured_subject_touching_border(client):
    """主体贴着图片边缘且有纹理时，不能被当成背景删掉。"""
    data = convert(client, make_border_subject_image(), width=40, remove_background=True).json()
    mask = filled(data["grid"])
    assert mask[-1, 20]                    # 贴下边缘的主体保留
    assert not mask[0, 0] and not mask[5, 2]   # 白色背景去掉


def test_edge_cells_have_no_background_tint(client):
    """白底红圆：去背景后边缘格子不应出现偏粉的混合色。"""
    img = np.full((200, 200, 3), 255)
    yy, xx = np.mgrid[0:200, 0:200]
    img[(xx - 100) ** 2 + (yy - 100) ** 2 < 70 ** 2] = (210, 20, 30)
    from tests.helpers import to_png
    data = convert(client, to_png(img), width=30, remove_background=True, min_region_size=0).json()
    reds = [c for c in data["colors"]]
    assert len(reds) <= 2, reds  # 只有红色（可能相近的两种红），没有粉色过渡


def test_ai_method_requires_model(client):
    resp = convert(client, make_image(), width=30, remove_background=True, bg_method="ai")
    assert resp.status_code == 400
    assert "尚未下载" in resp.json()["detail"]


def test_ai_method_with_fake_model(client, monkeypatch):
    """用假模型（返回圆形概率图）验证 AI 抠图流程：蒙版拉伸、缩放、合并。"""
    def fake_run(img, model):
        n = segmentation.MODELS[model].input_size
        yy, xx = np.mgrid[0:n, 0:n]
        prob = np.zeros((n, n))
        prob[(xx - n / 2) ** 2 + (yy - n / 2) ** 2 < (n * 0.3) ** 2] = 0.35  # 模型对主体内部不太确定
        return prob

    monkeypatch.setattr(segmentation, "is_ready", lambda model: True)
    monkeypatch.setattr(segmentation, "_run_model", fake_run)
    resp = convert(client, make_image(size=(240, 240)), width=40, remove_background=True, bg_method="ai",
                   bg_model="u2netp")
    assert resp.status_code == 200, resp.text
    mask = filled(resp.json()["grid"])
    assert not mask[0, 0] and not mask[-1, -1]
    assert mask[20, 20]  # 概率只有 0.35 的主体内部也保留（对比度拉伸生效）


def test_bg_models_api(client):
    models = client.get("/api/bg-models").json()
    assert {m["id"] for m in models} == {m.value for m in SegModel}
    assert all(m["status"] == "not_downloaded" for m in models)
    assert client.get("/api/bg-models/isnet-general-use").json()["size_mb"] > 100
    assert client.get("/api/bg-models/not-a-model").status_code == 422


@pytest.mark.parametrize("min_size,expected_removed", [(3, 2), (1, 0)])
def test_remove_small_islands(min_size, expected_removed):
    idx = np.full((10, 10), -1)
    idx[2:7, 2:7] = 1      # 主体 25 格
    idx[9, 9] = 2          # 单格碎块
    idx[0, 9] = 3          # 单格碎块
    removed = remove_small_islands(idx, -1, min_size)
    assert removed == expected_removed
    assert (idx[2:7, 2:7] == 1).all()
