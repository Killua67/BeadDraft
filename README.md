# 拼豆图纸生成器

把任意图片转换成拼豆（Perler / Hama / Artkal / MARD 等）图纸：色号网格、用量清单、可 1:1 打印的分板 PDF。
包含 Web 页面和 REST API 两部分。

## 功能

- **图片转图纸**：上传图片，按真实品牌色卡匹配最接近的豆子颜色
- **适配豆板**（默认）：自动裁掉主体四周空白，主体等比缩放后居中放在一块豆板上（29×29 / 52×52 / 14×14 / 自定义）；
  也可以切换为「按宽度」生成跨多块板的大图
- **7 套内置色卡**：MARD（291 色）、Perler、Perler Mini、Hama Midi、Hama Mini、Artkal S、Artkal C
- **颜色控制**：限制最多使用几种颜色；排除手上没有的颜色；支持导入自己校准的色卡
- **去背景**：两种方式可选
  - 颜色识别：纯色 / 渐变背景的插画、截图，无需额外下载
  - AI 抠图：照片（人像、宠物、物品），首次使用在页面上一键下载模型（通用 / 人像 / 动漫 / 轻量）
- **图像处理**：饱和度 / 对比度 / 亮度调整、主体描边、清理孤立杂点、可选抖动
- **三种预览**：拼豆效果、带色号的图纸、原图对照；支持缩放、拖动、悬停查看坐标与色号
- **手动修图**：画笔、橡皮、吸管，支持撤销 / 重做
- **导出**：PNG 整张图纸、PDF 分板打印（按豆子实际尺寸 1:1）、CSV 用量清单（含 10% 损耗建议）、JSON 原始数据
- **保存与历史**：图纸保存在本地 SQLite，可随时打开继续编辑

## 快速开始

需要先安装 [uv](https://docs.astral.sh/uv/)（Python 版本由 uv 自动管理，项目使用 Python 3.12）。

**macOS / Linux**

```bash
./scripts/start.sh
```

**Windows**

```bat
scripts\start.bat
```

也可以直接运行 `uv sync` 后执行 `uv run python run.py`。

启动后打开 <http://127.0.0.1:8520>，接口文档在 <http://127.0.0.1:8520/docs>。

## 使用建议

| 场景 | 建议参数 |
|---|---|
| 卡通头像、表情包 | 适配一块 29×29 豆板，12～24 色，「去除背景 · 颜色识别」+「主体描边」 |
| 照片（人像、宠物） | 适配一块 52×52 豆板（或按宽度 52～104 格），24～40 色，「去除背景 · AI 抠图」，饱和度调到 1.2 左右 |
| 动漫角色插画 | 「去除背景 · AI 抠图」选「动漫」模型 |
| 像素画原图 | 「按宽度」，缩放方式选「最近邻」，宽度设为原图像素宽度 |
| 渐变很多的插画 | 可尝试「误差扩散」抖动，强度 0.3～0.5 |

- 透明背景的 PNG 效果最好；主体尽量占满画面。
- PDF 的豆板页要按 **「实际大小 / 100%」** 打印，垫在透明豆板下面就能照着拼。2.6mm 迷你豆的格子太小，1:1 打印时不印色号，只印颜色；需要色号时可以把「打印豆距」改成 5 放大打印。
- 内置色卡的色值来自社区整理，与实物会有偏差。追求准确可以在固定光源下拍自己的色卡取色，通过「管理 → 导入新色卡」导入。

## 算法说明

核心代码在 [app/services/converter.py](app/services/converter.py)：

1. **去背景**（可选，在每格约 4 像素的「工作图」上进行）：
   - 颜色识别（[background.py](app/services/background.py)）：把图片四周的颜色聚成最多 3 类，只把「占比够大且平滑」的当作背景色（碰到边缘的羽毛、毛发纹理明显，不会被误删），再从四周向内填充相近颜色，并能顺着渐变继续填充
   - AI 抠图（[segmentation.py](app/services/segmentation.py)）：用 IS-Net / U²-Net 分割模型识别主体，对模型不太确定的主体内部做对比度拉伸，避免把白肚皮之类的区域挖空
   - 缩放到网格时按主体覆盖率决定是否放豆，格子颜色只取主体像素，边缘不会混出一圈背景色；最后清理残留的小碎块
2. **尺寸与缩放**：先按主体蒙版（去背景结果或透明度）裁掉四周空白；「适配豆板」时主体等比缩放到刚好放进一块板并居中，
   开描边时四周预留 1 格。照片用区域平均（每格取对应区域的平均色），像素画用最近邻。
3. **颜色匹配**：转换到 CIELAB 色彩空间，用 **CIEDE2000** 色差找色卡中最接近的颜色。RGB 距离与人眼感受差别较大，容易把肤色选成灰紫色。
4. **合并颜色**：超过颜色上限时，每轮去掉「合并后平方误差增加最少」的颜色。小面积但很关键的颜色（比如眼睛的黑色）不容易被合并掉。
5. **后处理**：8 邻域内没有同色的孤立豆改为周围最多的颜色（保留像素画中的斜线）；描边在主体外围一圈空位上放最深的颜色。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/palettes` | 色卡列表 |
| GET | `/api/palettes/{id}` | 色卡详情（全部颜色） |
| POST | `/api/palettes` | 导入自定义色卡 |
| DELETE | `/api/palettes/{id}` | 删除自定义色卡 |
| POST | `/api/convert` | 图片转网格（multipart：`file` 图片 + `params` JSON 字符串） |
| POST | `/api/export` | 导出未保存的网格（png / pdf / json / csv） |
| GET | `/api/patterns` | 图纸列表 |
| POST | `/api/patterns` | 保存图纸 |
| GET / PUT / DELETE | `/api/patterns/{id}` | 图纸详情 / 修改 / 删除 |
| GET | `/api/patterns/{id}/thumbnail.png` | 缩略图 |
| GET | `/api/patterns/{id}/export` | 导出已保存的图纸 |
| GET | `/api/bg-models` | AI 抠图模型列表与下载状态 |
| POST | `/api/bg-models/{id}/download` | 后台下载模型（进度通过 `GET /api/bg-models/{id}` 查询） |

示例：

```bash
curl -F "file=@cat.png" -F 'params={"palette_id":"mard","width":52,"max_colors":20,"remove_background":true}' \
  http://127.0.0.1:8520/api/convert
```

返回的 `grid[行][列]` 是色号，`null` 表示空位；`colors` 是按颗数排序的用量清单。完整参数说明见 `/docs`。

## 开发

```bash
uv sync                          # 安装依赖
uv run python run.py --reload    # 开发模式启动
uv run pytest                    # 运行测试
uv run python scripts/import_palettes.py   # 从上游重新生成内置色卡
uv run python scripts/download_model.py    # 预先下载 AI 抠图模型（默认通用模型，约 170MB）
```

项目结构、编码约定和协作规则见 [AGENTS.md](AGENTS.md)。

## 数据来源与许可

内置色卡数据来自 [maxcleme/beadcolors](https://github.com/maxcleme/beadcolors)（MIT License）。

AI 抠图模型使用 [rembg](https://github.com/danielgatis/rembg) 项目发布的 ONNX 文件（原始模型为 IS-Net、U²-Net 等，许可以各自项目为准），
默认保存在 `~/.u2net`（与 rembg 共用，可通过 `PB_MODEL_DIR` 修改），下载后校验 MD5。
