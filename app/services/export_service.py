"""
导出服务：根据格式生成文件内容，供「导出已保存图纸」和「导出未保存图纸」两个接口共用。
"""

import csv
import io
import json
import math
from dataclasses import dataclass
from urllib.parse import quote

from app.enums import ExportFormat
from app.schemas import ExportOptions, Grid
from app.services import renderer
from app.services.grid_utils import build_bom
from app.services.palette_service import Palette

SPARE_RATIO = 0.1  # 建议购买量的损耗余量（10%）


@dataclass
class ExportFile:
    """导出文件。"""

    content: bytes
    media_type: str
    filename: str

    @property
    def headers(self) -> dict[str, str]:
        # filename* 支持中文文件名
        return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(self.filename)}"}


def export_grid(grid: Grid, palette: Palette, name: str, options: ExportOptions) -> ExportFile:
    """按 options.format 导出图纸。"""
    fmt = options.format
    safe_name = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip() or "拼豆图纸"
    pitch = options.pitch_mm or palette.bead_size_mm

    if fmt == ExportFormat.PNG:
        data = renderer.render_png(grid, palette, title=name, board_size=options.board_size)
        return ExportFile(data, "image/png", f"{safe_name}.png")

    if fmt == ExportFormat.PDF:
        data = renderer.render_pdf(grid, palette, title=name, board_size=options.board_size, pitch_mm=pitch)
        return ExportFile(data, "application/pdf", f"{safe_name}.pdf")

    bom = build_bom(grid, palette)
    if fmt == ExportFormat.CSV:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["色号", "名称", "颜色", "颗数", f"建议购买（含{int(SPARE_RATIO * 100)}%损耗）"])
        for item in bom:
            writer.writerow([item["code"], item["name"], item["hex"], item["count"],
                             math.ceil(item["count"] * (1 + SPARE_RATIO))])
        writer.writerow(["合计", "", "", sum(i["count"] for i in bom), ""])
        # utf-8-sig：带 BOM，Excel 直接打开中文不乱码
        return ExportFile(buf.getvalue().encode("utf-8-sig"), "text/csv", f"{safe_name}-用量清单.csv")

    # JSON
    payload = {
        "name": name,
        "palette_id": palette.id,
        "palette_name": palette.name,
        "width": len(grid[0]),
        "height": len(grid),
        "bead_size_mm": pitch,
        "grid": grid,
        "colors": bom,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return ExportFile(data, "application/json", f"{safe_name}.json")
