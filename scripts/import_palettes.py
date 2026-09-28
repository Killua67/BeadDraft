"""
从开源数据仓库拉取各品牌拼豆色卡，生成 app/data/palettes/*.json。

数据来源（均为 MIT 协议）：
- 国内品牌（MARD / COCO / 漫漫 / 盼盼 / 咪小窝 / 优肯）：HansBug/pindou-color-data
  该仓库对各来源做了核对，标注了无法辨认的色号和透明豆
- 国际品牌（Perler / Hama / Artkal S）：maxcleme/beadcolors

用法（项目根目录执行，使用项目虚拟环境）：
    uv run python scripts/import_palettes.py

说明：
- 跳过「无法辨认」的色号（UNKNOWN-*）和透明豆（透明豆无法与图片颜色匹配）
- 色值是社区整理的近似值，实物受批次 / 光照影响会有偏差，对准确度要求高时建议拍摄自己的实物色卡取色后，
  通过「自定义色卡」导入
- 生成的 JSON 已提交到仓库，一般不需要重复执行，仅在想同步上游更新时运行
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

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "palettes"
PINDOU_API = "https://api.github.com/repos/HansBug/pindou-color-data/contents"
PINDOU_SOURCE = "https://github.com/HansBug/pindou-color-data (MIT License)"
BEADCOLORS_RAW = "https://raw.githubusercontent.com/maxcleme/beadcolors/master/raw"
BEADCOLORS_SOURCE = "https://github.com/maxcleme/beadcolors (MIT License)"

# 输出顺序即页面下拉框中的顺序。每项：(色卡 ID, 数据源, 上游标识, 显示名称, 品牌, 豆子直径 mm, 说明)
PALETTES = [
    ("mard_221", "pindou", "mard-221-alfonse-doudou", "MARD 221 常用套装", "MARD", 2.6, "国内最主流的色号体系，大多数套装就是这 221 色"),
    ("mard", "pindou", "mard-291-github", "MARD 291 全色", "MARD", 2.6, "MARD 完整色号（含 P / Q / R 等扩展色）"),
    ("coco", "pindou", "coco-291", "COCO 291 色", "COCO", 2.6, "常见的性价比品牌"),
    ("manman", "pindou", "manman-278", "漫漫 278 色", "漫漫", 2.6, "老牌，图纸生态中常见"),
    ("panpan", "pindou", "panpan-289", "盼盼 285 色", "盼盼", 2.6, "图纸工具中常见的品牌（已去掉 4 个无法辨认的色号）"),
    ("mixiaowo", "pindou", "mixiaowo-290", "咪小窝 286 色", "咪小窝", 2.6, "玩家圈常见品牌（已去掉 4 个无法辨认的色号）"),
    ("artkal_c", "pindou", "artkal-c-197-official", "优肯 Artkal C 系列", "Artkal", 2.6, "优肯官方 C 系列 2.6mm（不含透明豆）"),
    ("artkal_m", "pindou", "artkal-m-221-official", "优肯 Artkal M221", "Artkal", 2.6, "优肯官方 MARD 兼容体系（不含透明豆）"),
    ("perler", "beadcolors", "perler.csv", "Perler 标准豆", "Perler", 5.0, "美国 Perler 5mm 标准豆"),
    ("perler_mini", "beadcolors", "perler_mini.csv", "Perler Mini", "Perler", 2.6, "Perler 2.6mm 迷你豆"),
    ("hama", "beadcolors", "hama.csv", "Hama Midi", "Hama", 5.0, "丹麦 Hama 5mm 标准豆"),
    ("hama_mini", "beadcolors", "hama_mini.csv", "Hama Mini", "Hama", 2.5, "Hama 2.5mm 迷你豆"),
    ("artkal_s", "beadcolors", "artkal_s.csv", "Artkal S 系列", "Artkal", 5.0, "Artkal S 系列 5mm 软豆"),
]


def natural_key(code: str) -> list:
    """自然排序：A2 排在 A10 前面。"""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", code)]


def _get(url: str, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "perler-bead", **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return resp.read()


def load_pindou(name: str) -> list[dict]:
    """读取 pindou-color-data 的 colors.json，跳过无法辨认的色号和透明豆。"""
    data = json.loads(_get(f"{PINDOU_API}/{name}/colors.json", {"Accept": "application/vnd.github.raw"}))
    colors, skipped = [], []
    for c in data["colors"]:
        if c.get("unidentified") or c.get("transparency") or len(c["hex"]) != 7:
            skipped.append(c["code"])
            continue
        colors.append({"code": c["code"], "name": c.get("name") or c["code"], "hex": c["hex"].upper(),
                       "group": c.get("group", "")})
    if skipped:
        logger.info("  %s 跳过 %d 个（无法辨认 / 透明）：%s", name, len(skipped), ", ".join(skipped))
    return colors


def load_beadcolors(filename: str) -> list[dict]:
    """读取 beadcolors 的 raw CSV：[色号, 名称, R, G, B, 贡献者]。"""
    text = _get(f"{BEADCOLORS_RAW}/{filename}").decode("utf-8")
    colors, seen = [], set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 5:
            continue
        code, name, r, g, b = (v.strip() for v in row[:5])
        if code in seen:  # 上游偶有重复色号，保留第一条
            continue
        seen.add(code)
        colors.append({"code": code, "name": name or code, "hex": "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b)),
                       "group": re.match(r"[A-Za-z]*", code).group() or ""})
    return colors


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for order, (palette_id, source, upstream, name, brand, bead_size, desc) in enumerate(PALETTES):
        if source == "pindou":
            colors, data_source = load_pindou(upstream), PINDOU_SOURCE
        else:
            colors, data_source = load_beadcolors(upstream), BEADCOLORS_SOURCE
        colors.sort(key=lambda c: natural_key(c["code"]))
        data = {
            "id": palette_id,
            "name": name,
            "brand": brand,
            "bead_size_mm": bead_size,
            "description": desc,
            "sort_order": order,
            "data_source": f"{data_source} · {upstream}",
            "colors": colors,
        }
        out = OUTPUT_DIR / f"{palette_id}.json"
        # 每个颜色一行，便于在 Git 中查看差异
        body = ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in colors)
        head = json.dumps({k: v for k, v in data.items() if k != "colors"}, ensure_ascii=False, indent=1)[:-2]
        out.write_text(f'{head},\n "colors": [\n{body}\n ]\n}}\n', encoding="utf-8")
        json.loads(out.read_text(encoding="utf-8"))  # 自检：确保写出的是合法 JSON
        logger.info("生成色卡 %-12s %3d 色  %s", palette_id, len(colors), name)


if __name__ == "__main__":
    main()
