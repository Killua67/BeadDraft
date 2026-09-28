"""
网格清理：让图纸更好拼。

- merge_small_regions：同色相连不足 N 颗的小色块并入周围颜色（减少零碎的单颗、双颗杂色）
- remove_small_islands：去掉与主体不相连的小碎块（去背景后残留的背景噪点）

连通性都按 8 邻域计算：像素画里的斜线只在对角方向相连，按 4 邻域会被当成一堆单颗杂点误删。
"""

from collections import Counter, deque

import numpy as np

from app.services.color import delta_e_2000

# 高光保护：接近黑 / 白的小色块，如果与要并入的颜色差别很大（如黑眼睛里的白色高光、白眼睛里的黑瞳孔），
# 说明是有意画上去的细节，不合并
HIGHLIGHT_DARK_L = 22
HIGHLIGHT_LIGHT_L = 93
HIGHLIGHT_MIN_DELTA = 35

_NEIGHBORS_8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _components(mask: np.ndarray, same: np.ndarray | None = None) -> list[list[tuple[int, int]]]:
    """
    8 邻域连通分量。mask 为参与计算的格子；传入 same（颜色网格）时只连接同色格子。
    返回每个分量的格子坐标列表。
    """
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result = []
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        color = same[sy, sx] if same is not None else None
        cells, queue = [], deque([(sy, sx)])
        while queue:
            y, x = queue.popleft()
            cells.append((y, x))
            for dy, dx in _NEIGHBORS_8:
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx] \
                        and (same is None or same[ny, nx] == color):
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        result.append(cells)
    return result


def merge_small_regions(idx: np.ndarray, palette_lab: np.ndarray, empty: int, min_size: int,
                        max_passes: int = 4) -> int:
    """
    把同色相连不足 min_size 格的小色块改成周围最多的颜色，直接修改 idx，返回改动的格子数。

    - 「周围最多」按接触的格子计数：上下左右接触记 2，对角接触记 1
    - 票数相同时选与原色最接近的颜色（ΔE00）
    - 周围全是空位的小块（悬空的碎块）不处理，交给 remove_small_islands
    - 接近黑 / 白、且与要并入的颜色反差很大的小块视为高光 / 瞳孔等细节，保留（见 HIGHLIGHT_*）
    - 合并后可能产生新的小色块，最多重复 max_passes 轮
    """
    if min_size <= 1:
        return 0
    h, w = idx.shape
    total = 0
    for _ in range(max_passes):
        regions = [r for r in _components(idx != empty, same=idx) if len(r) < min_size]
        regions.sort(key=len)  # 先处理最小的，避免大一点的小块先把它吞掉后形状失真
        changed = 0
        for cells in regions:
            color = idx[cells[0]]
            if any(idx[c] != color for c in cells):  # 本轮已被其他合并改动过
                continue
            inside = set(cells)
            votes: Counter = Counter()
            for y, x in cells:
                for dy, dx in _NEIGHBORS_8:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and (ny, nx) not in inside:
                        c = idx[ny, nx]
                        if c != empty and c != color:
                            votes[c] += 2 if dy == 0 or dx == 0 else 1
            if not votes:
                continue
            best = max(votes.values())
            candidates = [c for c, v in votes.items() if v == best]
            target = min(candidates, key=lambda c: float(delta_e_2000(palette_lab[color], palette_lab[c])))
            if _is_highlight(palette_lab[color], palette_lab[target]):
                continue
            for cell in cells:
                idx[cell] = target
            changed += len(cells)
        total += changed
        if not changed:
            break
    return total


def _is_highlight(color_lab: np.ndarray, target_lab: np.ndarray) -> bool:
    """小色块是否为需要保留的黑 / 白细节：颜色接近黑或白，且与周围颜色反差很大。"""
    near_bw = color_lab[0] < HIGHLIGHT_DARK_L or color_lab[0] > HIGHLIGHT_LIGHT_L
    return near_bw and float(delta_e_2000(color_lab, target_lab)) > HIGHLIGHT_MIN_DELTA


def remove_small_islands(idx: np.ndarray, empty: int, min_size: int) -> int:
    """
    去掉网格中小于 min_size 格的孤立碎块（与其他豆子不相连），最大的一块永远保留。
    去背景后残留的背景碎片通常就是这种小块。直接修改 idx，返回删除的格子数。
    """
    components = _components(idx != empty)
    if len(components) <= 1:
        return 0
    largest = max(len(c) for c in components)
    removed = 0
    for cells in components:
        if len(cells) < min_size and len(cells) < largest:
            for cell in cells:
                idx[cell] = empty
            removed += len(cells)
    return removed
