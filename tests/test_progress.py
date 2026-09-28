"""拼豆进度、数据库迁移、抖动与合并小色块的测试。"""

import json

from sqlalchemy import create_engine, inspect, text

from app.core.database import migrate
from tests.helpers import make_image


def _save_pattern(client) -> dict:
    grid = [["A1", "A1", "H7"], ["B3", None, "H7"]]
    resp = client.post("/api/patterns", json={"name": "进度测试", "palette_id": "mard_221", "grid": grid})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_progress_save_and_filter(client):
    pattern = _save_pattern(client)
    assert pattern["done_codes"] == [] and pattern["done_color_count"] == 0
    pid = pattern["id"]

    # 不在图纸中的色号、重复色号会被过滤
    updated = client.put(f"/api/patterns/{pid}", json={"done_codes": ["H7", "A1", "H7", "Z99"]}).json()
    assert updated["done_codes"] == ["H7", "A1"]
    assert client.get("/api/patterns").json()["items"][0]["done_color_count"] == 2

    # 改图后删掉的颜色不再算完成
    grid = [["A1", "A1", "B3"], ["B3", None, "B3"]]
    updated = client.put(f"/api/patterns/{pid}", json={"grid": grid}).json()
    assert updated["done_codes"] == ["A1"]
    client.delete(f"/api/patterns/{pid}")


def test_migrate_adds_missing_column(tmp_path):
    """旧版本数据库没有 done_codes_json 字段，启动时应自动补上，旧数据默认为空进度。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patterns (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        conn.execute(text("INSERT INTO patterns (id, name) VALUES (1, '旧图纸')"))
    assert migrate(engine) == ["patterns.done_codes_json"]
    assert "done_codes_json" in {c["name"] for c in inspect(engine).get_columns("patterns")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT done_codes_json FROM patterns")).scalar() == "[]"
    assert migrate(engine) == []  # 再次执行不会重复添加


def test_dither_skips_region_merge(client):
    """开启抖动时不合并小色块：抖动产生的散点应保留。"""
    def convert(**params):
        resp = client.post("/api/convert", files={"file": ("t.png", make_image(), "image/png")},
                           data={"params": json.dumps({"fit_mode": "width", "width": 50, "max_colors": 12, **params})})
        return resp.json()

    dithered = convert(dither="floyd_steinberg", dither_strength=0.8, min_region_size=6)
    dithered_no_merge = convert(dither="floyd_steinberg", dither_strength=0.8, min_region_size=0)
    assert dithered["grid"] == dithered_no_merge["grid"]
