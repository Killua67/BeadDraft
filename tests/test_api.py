"""接口测试：色卡、转换、保存、导出全流程。"""

import json

from tests.helpers import make_image


def convert(client, **params):
    resp = client.post(
        "/api/convert",
        files={"file": ("test.png", make_image(params.pop("transparent", False)), "image/png")},
        data={"params": json.dumps(params)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_and_index(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert "拼豆" in client.get("/").text


def test_palettes(client):
    palettes = client.get("/api/palettes").json()
    ids = [p["id"] for p in palettes]
    assert ids[0] == "mard" and "perler" in ids and "hama" in ids
    mard = client.get("/api/palettes/mard").json()
    assert mard["color_count"] == len(mard["colors"]) > 200
    assert client.get("/api/palettes/not_exist").status_code == 404


def test_convert_basic(client):
    data = convert(client, width=40, max_colors=8)
    assert data["width"] == 40 and data["height"] == 27  # 300x200 按比例
    assert len(data["grid"]) == 27 and all(len(r) == 40 for r in data["grid"])
    assert data["color_count"] <= 8
    assert data["bead_count"] == 40 * 27
    assert sum(c["count"] for c in data["colors"]) == data["bead_count"]


def test_convert_keeps_small_dark_detail(client):
    """颜色合并时，面积很小但色差很大的颜色（黑眼睛）应该被保留。"""
    data = convert(client, width=60, max_colors=6)
    mard = {c["code"]: c for c in client.get("/api/palettes/mard").json()["colors"]}
    darkest = min(int(mard[c["code"]]["hex"][1:3], 16) + int(mard[c["code"]]["hex"][3:5], 16) for c in data["colors"])
    assert darkest < 120


def test_convert_transparent_and_outline(client):
    data = convert(client, transparent=True, width=40, outline=True, outline_code="H7")
    flat = [c for row in data["grid"] for c in row]
    assert None in flat  # 透明区域为空位
    assert data["grid"][0][0] is None
    assert any(c["code"] == "H7" for c in data["colors"])


def test_convert_remove_background_and_dither(client):
    data = convert(client, width=40, remove_background=True, dither="floyd_steinberg")
    assert data["grid"][0][0] is None
    assert data["bead_count"] < 40 * 27


def test_convert_excluded_codes(client):
    first = convert(client, width=30, max_colors=5)
    excluded = [c["code"] for c in first["colors"]]
    second = convert(client, width=30, max_colors=5, excluded_codes=excluded)
    assert not set(excluded) & {c["code"] for c in second["colors"]}


def test_convert_bad_inputs(client):
    resp = client.post("/api/convert", files={"file": ("a.png", b"not an image", "image/png")})
    assert resp.status_code == 400 and "图片" in resp.json()["detail"]
    resp = client.post(
        "/api/convert",
        files={"file": ("a.png", make_image(), "image/png")},
        data={"params": json.dumps({"width": 1})},
    )
    assert resp.status_code == 400


def test_pattern_crud_and_export(client):
    data = convert(client, width=60, max_colors=10)
    created = client.post("/api/patterns", json={
        "name": "测试图纸", "palette_id": "mard", "grid": data["grid"], "params": {"width": 60},
    })
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    listing = client.get("/api/patterns").json()
    assert listing["total"] >= 1 and listing["items"][0]["id"] == pid

    # 修改一格
    grid = created.json()["grid"]
    grid[0][0] = "H7"
    updated = client.put(f"/api/patterns/{pid}", json={"name": "改名", "grid": grid}).json()
    assert updated["name"] == "改名" and updated["grid"][0][0] == "H7"

    # 非法色号
    grid[0][0] = "XXX"
    assert client.put(f"/api/patterns/{pid}", json={"grid": grid}).status_code == 400

    assert client.get(f"/api/patterns/{pid}/thumbnail.png").headers["content-type"] == "image/png"
    for fmt, ctype, magic in [("png", "image/png", b"\x89PNG"), ("pdf", "application/pdf", b"%PDF"),
                              ("json", "application/json", b"{"), ("csv", "text/csv", b"\xef\xbb\xbf")]:
        resp = client.get(f"/api/patterns/{pid}/export", params={"format": fmt, "board_size": 29})
        assert resp.status_code == 200, (fmt, resp.text)
        assert resp.headers["content-type"].startswith(ctype)
        assert resp.content.startswith(magic)

    assert client.delete(f"/api/patterns/{pid}").status_code == 204
    assert client.get(f"/api/patterns/{pid}").status_code == 404


def test_export_unsaved(client):
    data = convert(client, width=30)
    resp = client.post("/api/export", json={"palette_id": "mard", "grid": data["grid"], "format": "pdf", "board_size": 29})
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    resp = client.post("/api/export", json={"palette_id": "mard", "grid": [["A1", "B2"], ["A1"]], "format": "png"})
    assert resp.status_code == 422  # 行长度不一致


def test_custom_palette(client):
    payload = {"name": "我的色卡", "bead_size_mm": 2.6, "colors": [
        {"code": "W", "name": "白", "hex": "#FFFFFF"},
        {"code": "K", "name": "黑", "hex": "#000000"},
        {"code": "R", "name": "红", "hex": "#E53935"},
    ]}
    created = client.post("/api/palettes", json=payload)
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert pid.startswith("custom_")

    data = convert(client, palette_id=pid, width=20)
    assert {c["code"] for c in data["colors"]} <= {"W", "K", "R"}

    dup = {**payload, "colors": payload["colors"] + [{"code": "W", "name": "", "hex": "#FEFEFE"}]}
    assert client.post("/api/palettes", json=dup).status_code == 422
    assert client.delete("/api/palettes/mard").status_code == 400
    assert client.delete(f"/api/palettes/{pid}").status_code == 204
