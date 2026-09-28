"""
网格相关的通用工具：校验色号、统计用量清单（BOM）。
"""

from collections import Counter

from app.core.errors import BadRequestError
from app.schemas import Grid
from app.services.palette_service import Palette


def validate_grid_codes(grid: Grid, palette: Palette) -> None:
    """确保网格中的每个色号都存在于色卡中。"""
    unknown = {code for row in grid for code in row if code is not None and code not in palette.index}
    if unknown:
        sample = ", ".join(sorted(unknown)[:10])
        raise BadRequestError(f"色卡 {palette.name} 中不存在这些色号：{sample}")


def build_bom(grid: Grid, palette: Palette) -> list[dict]:
    """
    统计每种颜色的用量，按颗数从多到少排序（颗数相同按色号排序）。
    返回 [{"code", "name", "hex", "count"}]。
    """
    counter = Counter(code for row in grid for code in row if code is not None)
    items = []
    for code, count in counter.items():
        color = palette.colors[palette.index[code]]
        items.append({"code": code, "name": color["name"], "hex": color["hex"], "count": count})
    items.sort(key=lambda it: (-it["count"], palette.index[it["code"]]))
    return items


def bead_count(grid: Grid) -> int:
    """豆子总颗数（非空格子数）。"""
    return sum(1 for row in grid for code in row if code is not None)
