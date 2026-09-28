"""尺寸测试：适配豆板、裁掉主体空白、描边留边。"""

import json

import numpy as np

from tests.helpers import make_image


def convert(client, **params) -> dict:
    image = make_image(params.pop("transparent", True), size=params.pop("size", (300, 200)))
    resp = client.post("/api/convert", files={"file": ("t.png", image, "image/png")},
                       data={"params": json.dumps(params)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def bbox(grid) -> tuple[int, int, int, int]:
    """非空格子的包围盒 (x0, y0, x1, y1)，含两端。"""
    filled = np.array([[c is not None for c in row] for row in grid])
    ys, xs = np.nonzero(filled)
    return xs.min(), ys.min(), xs.max(), ys.max()


def test_board_mode_is_default_and_fills_board(client):
    """默认适配 29×29 豆板：透明底的圆形主体被裁掉空白后放大到占满整块板。"""
    data = convert(client)
    assert (data["width"], data["height"]) == (29, 29)
    x0, y0, x1, y1 = bbox(data["grid"])
    assert x1 - x0 + 1 >= 27 and y1 - y0 + 1 >= 27  # 主体几乎撑满（圆形边缘按覆盖率取舍，允许差 1~2 格）


def test_board_mode_custom_size_and_centered(client):
    data = convert(client, board_size=40, transparent=False)  # 不透明、不去背景：整张 3:2 图片缩放进 40×40
    assert (data["width"], data["height"]) == (40, 40)
    x0, y0, x1, y1 = bbox(data["grid"])
    assert (x0, x1) == (0, 39)                 # 宽边占满
    assert y1 - y0 + 1 == round(40 * 200 / 300)  # 高按比例
    assert abs(y0 - (39 - y1)) <= 1            # 上下居中


def test_board_mode_outline_keeps_margin(client):
    """描边时主体缩小一圈，描边完整地落在豆板内。"""
    data = convert(client, board_size=29, outline=True, outline_code="H7")
    grid = data["grid"]
    x0, y0, x1, y1 = bbox(grid)
    assert x0 >= 0 and x1 <= 28 and y0 >= 0 and y1 <= 28
    # 左右两端都应是描边色（主体被描边包住，没有被豆板边缘截断）
    row = grid[(y0 + y1) // 2]
    filled = [c for c in row if c is not None]
    assert filled[0] == "H7" and filled[-1] == "H7"


def test_crop_to_subject_in_width_mode(client):
    """按宽度模式下，裁掉空白后主体横向占满指定宽度。"""
    cropped = convert(client, fit_mode="width", width=40)
    x0, _, x1, _ = bbox(cropped["grid"])
    assert x1 - x0 + 1 >= 38

    uncropped = convert(client, fit_mode="width", width=40, crop_to_subject=False)
    x0, _, x1, _ = bbox(uncropped["grid"])
    assert x1 - x0 + 1 < 30  # 不裁剪时圆形只占中间一部分（直径约为图宽的 56%）


def test_opaque_image_without_background_removal_not_cropped(client):
    """不透明图片且未去背景：没有主体蒙版，不裁剪、不描边。"""
    data = convert(client, fit_mode="width", width=30, transparent=False, outline=True)
    assert (data["width"], data["height"]) == (30, 20)
    assert data["bead_count"] == 30 * 20
    assert any("描边未生效" in w for w in data["warnings"])
