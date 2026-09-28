"""
从开源项目 maxcleme/beadcolors（MIT 协议）拉取各品牌拼豆色卡，生成 app/data/palettes/*.json。

用法（项目根目录执行，使用项目虚拟环境）：
    uv run python scripts/import_palettes.py

说明：
- 色值是社区整理的近似值，实物受批次/光照影响会有偏差，
  对准确度要求高时建议拍摄自己的实物色卡取色后，通过「自定义色卡」接口导入。
- 生成的 JSON 已提交到仓库，一般不需要重复执行，仅在想同步上游更新时运行。
"""

import csv
import io
import json
import logging
import re
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("import_palettes")

RAW_BASE = "https://raw.githubusercontent.com/maxcleme/beadcolors/master/raw"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "palettes"

# 要导入的色卡：id -> (上游文件名, 显示名称, 品牌, 豆子直径 mm, 说明)
PALETTES = {
    "mard": ("mard.csv", "MARD 全色卡", "MARD", 2.6, "国内常用色号体系（A1、B2…），2.6mm 小豆"),
    "perler": ("perler.csv", "Perler 标准豆", "Perler", 5.0, "美国 Perler 5mm 标准豆"),
    "perler_mini": ("perler_mini.csv", "Perler Mini", "Perler", 2.6, "Perler 2.6mm 迷你豆"),
    "hama": ("hama.csv", "Hama Midi", "Hama", 5.0, "丹麦 Hama 5mm 标准豆"),
    "hama_mini": ("hama_mini.csv", "Hama Mini", "Hama", 2.5, "Hama 2.5mm 迷你豆"),
    "artkal_s": ("artkal_s.csv", "Artkal S 系列", "Artkal", 5.0, "Artkal S 系列 5mm 软豆"),
    "artkal_c": ("artkal_c.csv", "Artkal C 系列", "Artkal", 2.6, "Artkal C 系列 2.6mm 迷你豆"),
}


def natural_key(code: str) -> list:
    """自然排序：A2 排在 A10 前面。"""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", code)]


def fetch_csv(filename: str) -> list[list[str]]:
    """下载上游 raw CSV，格式：[色号, 名称, R, G, B, 贡献者]。"""
    with urllib.request.urlopen(f"{RAW_BASE}/{filename}", timeout=30) as resp:
        text = resp.read().decode("utf-8")
    return [row for row in csv.reader(io.StringIO(text)) if len(row) >= 5]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for palette_id, (filename, name, brand, bead_size, desc) in PALETTES.items():
        rows = fetch_csv(filename)
        colors, seen = [], set()
        for code, color_name, r, g, b, *_ in rows:
            code = code.strip()
            if code in seen:  # 上游偶有重复色号，保留第一条
                continue
            seen.add(code)
            colors.append({
                "code": code,
                "name": color_name.strip() or code,
                "hex": "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b)),
            })
        colors.sort(key=lambda c: natural_key(c["code"]))
        data = {
            "id": palette_id,
            "name": name,
            "brand": brand,
            "bead_size_mm": bead_size,
            "description": desc,
            "data_source": "https://github.com/maxcleme/beadcolors (MIT License)",
            "colors": colors,
        }
        out = OUTPUT_DIR / f"{palette_id}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("生成色卡 %s：%d 色 -> %s", palette_id, len(colors), out.name)


if __name__ == "__main__":
    main()
