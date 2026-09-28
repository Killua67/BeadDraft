"""合并小色块、主导色取色测试。"""

import json

import numpy as np

from app.services.cleanup import merge_small_regions
from app.services.color import srgb_to_lab
from tests.helpers import to_png

# 测试用的 4 色色卡：0 红、1 深红、2 蓝、3 黑
LAB = srgb_to_lab(np.array([[220, 30, 40], [150, 20, 30], [40, 80, 200], [0, 0, 0]]))


def test_merge_single_and_double_cells():
    idx = np.zeros((8, 8), dtype=np.int32)
    idx[2, 2] = 2              # 单颗杂点
    idx[5, 5] = idx[5, 6] = 2  # 两颗相连的小块
    changed = merge_small_regions(idx, LAB, -1, min_size=3)
    assert changed == 3 and (idx == 0).all()


def test_merge_keeps_regions_at_threshold():
    idx = np.zeros((8, 8), dtype=np.int32)
    idx[1:3, 1:3] = 3  # 2×2 的黑块（眼睛）= 4 颗
    merge_small_regions(idx, LAB, -1, min_size=3)
    assert (idx[1:3, 1:3] == 3).all()


def test_merge_keeps_diagonal_lines():
    """像素画的斜线按 8 邻域相连，不应被当成单颗杂点合并掉。"""
    idx = np.zeros((8, 8), dtype=np.int32)
    for i in range(6):
        idx[i + 1, i + 1] = 3
    merge_small_regions(idx, LAB, -1, min_size=3)
    assert all(idx[i + 1, i + 1] == 3 for i in range(6))


def test_merge_tie_prefers_similar_color():
    """票数相同时并入更接近的颜色：单颗深红夹在红色块和蓝色块之间，应并入红。"""
    idx = np.array([[0, 0, 0, 1, 2, 2, 2]], dtype=np.int32)
    merge_small_regions(idx, LAB, -1, min_size=2)
    assert idx[0, 3] == 0


def test_merge_ignores_floating_pieces():
    idx = np.full((5, 5), -1, dtype=np.int32)
    idx[2, 2] = 2  # 四周全空
    assert merge_small_regions(idx, LAB, -1, min_size=3) == 0


def _outline_art(size=240) -> bytes:
    """卡通风格测试图：白底、黄色圆脸、2 像素黑色勾线（线宽不到半格，平均取色会得到灰黄色）。"""
    yy, xx = np.mgrid[0:size, 0:size]
    arr = np.full((size, size, 3), 255)
    r = np.hypot(xx - size / 2, yy - size / 2)
    arr[r < size * 0.4] = (250, 210, 40)
    arr[np.abs(r - size * 0.4) < 1.5] = (0, 0, 0)
    return to_png(arr)


def _convert(client, **params):
    resp = client.post("/api/convert", files={"file": ("t.png", _outline_art(), "image/png")},
                       data={"params": json.dumps({"fit_mode": "width", "width": 40, "max_colors": 0,
                                                   "min_region_size": 0, **params})})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_dominant_mode_avoids_blended_colors(client):
    box = _convert(client, resample="box")
    dominant = _convert(client, resample="dominant")
    # 区域平均会在勾线处混出多种中间色；主导色只剩白、黄和少量黑
    assert dominant["color_count"] < box["color_count"]
    assert dominant["color_count"] <= 4


def test_min_region_size_reduces_fragments(client):
    raw = _convert(client, resample="box", min_region_size=0)
    merged = _convert(client, resample="box", min_region_size=4)
    assert merged["color_count"] < raw["color_count"]
    assert merged["bead_count"] == raw["bead_count"]  # 只改颜色，不增减豆子


def test_line_priority_keeps_thin_outline_connected(client):
    """细勾线（约 0.4 格宽）：开启深色线条优先后，黑色勾线连成一整圈。"""
    from app.services.cleanup import _components

    def black_parts(grid):
        dark = np.array([[c == "H7" for c in row] for row in grid])
        return len(_components(dark)), int(dark.sum())

    without = _convert(client, resample="dominant", line_priority=False)
    with_lines = _convert(client, resample="dominant", line_priority=True)
    parts_without, count_without = black_parts(without["grid"])
    parts_with, count_with = black_parts(with_lines["grid"])
    assert count_with > count_without
    assert parts_with == 1 and parts_without > 1


def test_merge_keeps_highlights():
    """黑块里的单颗白色高光保留；反差不大的杂点照常合并。"""
    lab = srgb_to_lab(np.array([[0, 0, 0], [255, 255, 255], [60, 60, 60], [128, 128, 128]]))  # 黑、白、深灰、中灰
    black_with_white = np.zeros((5, 5), dtype=np.int32)
    black_with_white[2, 2] = 1
    merge_small_regions(black_with_white, lab, -1, min_size=3)
    assert black_with_white[2, 2] == 1  # 高光保留

    black_with_gray = np.zeros((5, 5), dtype=np.int32)
    black_with_gray[2, 2] = 3
    merge_small_regions(black_with_gray, lab, -1, min_size=3)
    assert black_with_gray[2, 2] == 0  # 中灰不是黑白，照常合并

    gray_with_black = np.full((5, 5), 2, dtype=np.int32)
    gray_with_black[2, 2] = 0
    merge_small_regions(gray_with_black, lab, -1, min_size=3)
    assert gray_with_black[2, 2] == 2  # 深灰里的黑点反差小，照常合并
