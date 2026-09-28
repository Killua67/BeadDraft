"""色彩算法测试：CIEDE2000 使用 Sharma 2005 论文公布的标准测试数据。"""

import numpy as np
import pytest

from app.services.color import delta_e_2000, hex_to_rgb, rgb_to_hex, srgb_to_lab

SHARMA_PAIRS = [
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 3.1571, -77.2803), (50, 0, -82.7485), 2.8615),
    ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0000),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((50, 2.5, 0), (61, -5, 29), 22.8977),
    ((50, 2.5, 0), (56, -27, -3), 31.9030),
    ((50, 2.5, 0), (58, 24, 15), 19.4535),
]


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA_PAIRS)
def test_ciede2000_reference(lab1, lab2, expected):
    assert float(delta_e_2000(np.array(lab1), np.array(lab2))) == pytest.approx(expected, abs=1e-4)


def test_lab_white_black():
    lab = srgb_to_lab(np.array([[255, 255, 255], [0, 0, 0]]))
    assert lab[0] == pytest.approx([100, 0, 0], abs=0.01)
    assert lab[1] == pytest.approx([0, 0, 0], abs=0.01)


def test_hex_roundtrip():
    assert hex_to_rgb("#1A2B3C") == (26, 43, 60)
    assert rgb_to_hex((26, 43, 60)) == "#1A2B3C"
