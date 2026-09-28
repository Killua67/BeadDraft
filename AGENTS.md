# AGENTS.md

本文件面向 AI 编码助手（Claude Code、Codex、Cursor 等）和新加入的开发者，说明本项目的结构、约定和协作规则。
面向用户的介绍见 [README.md](README.md)。

## 项目概览

**拼豆图纸生成器**：把任意图片转换为拼豆（Perler / Hama / Artkal / MARD 等）图纸。

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0（SQLite）+ Pillow + numpy + reportlab + onnxruntime（AI 抠图）
- 前端：原生 HTML / CSS / JavaScript（无构建步骤），由 FastAPI 以静态文件方式提供
- 环境管理：uv（虚拟环境在项目内 `.venv/`）

核心流程：上传图片 → （可选）去背景：颜色识别 / AI 抠图 → 裁掉主体四周空白 → 适配一块豆板（默认）或按宽度，按主体覆盖率缩放到网格 → Lab + CIEDE2000 映射到品牌色卡 → 合并颜色 → 清理杂点/碎块/描边 → 输出色号网格、用量清单、分板打印 PDF。

## 目录结构

```
app/
  main.py                 FastAPI 入口：异常处理、路由注册、静态文件挂载
  enums.py                枚举（尺寸方式、缩放方式、抖动方式、色卡来源、导出格式、去背景方式、AI 模型、模型状态）
  models.py               ORM 模型（patterns 图纸表、custom_palettes 自定义色卡表）
  schemas.py              Pydantic 请求/响应模型
  core/
    config.py             配置（环境变量前缀 PB_）
    logger.py             日志配置（控制台 + logs/app.log，带时间）
    database.py           数据库引擎、会话、建表
    errors.py             业务异常（BadRequestError / NotFoundError）
  api/
    convert.py            POST /api/convert、POST /api/export
    palettes.py           /api/palettes 色卡增删查
    patterns.py           /api/patterns 图纸增删改查、缩略图、导出
    bg_models.py          /api/bg-models AI 抠图模型状态与后台下载
  services/
    color.py              sRGB→Lab、CIEDE2000 色差（已用 Sharma 标准数据验证）
    converter.py          ★ 核心算法：图片 → 拼豆网格
    background.py         颜色识别去背景、主体内芯
    cleanup.py            网格清理：合并小色块、去掉背景残留碎块
    segmentation.py       AI 抠图：模型登记、后台下载（MD5 校验）、ONNX 推理、蒙版缓存
    palette_service.py    色卡加载（内置 JSON + 数据库自定义）
    renderer.py           图纸渲染（PNG / PDF / 缩略图）
    export_service.py     导出格式分发
    pattern_service.py    图纸 CRUD
    grid_utils.py         网格校验、用量统计
  data/palettes/*.json    内置色卡 13 套（由 scripts/import_palettes.py 生成，sort_order 决定下拉框顺序）
web/
  index.html              页面结构
  css/app.css             样式
  js/api.js               接口封装
  js/canvas.js            画布渲染（只画可见区域，支持缩放/平移/双指）
  js/app.js               页面逻辑（参数、编辑、保存、导出、历史）
scripts/
  import_palettes.py      从上游开源数据重新生成内置色卡
  download_model.py       预先下载 AI 抠图模型（部署时用）
  start.sh / start.bat    一键启动脚本
tests/                    pytest 测试
run.py                    启动入口（统一日志格式）
```

运行时数据（不提交 Git）：`data/perler.db`（SQLite）、`logs/app.log`、AI 模型 `~/.u2net/*.onnx`（项目目录之外）。
注意 `.gitignore` 中写的是 `/data/`、`/logs/`（只忽略根目录），`app/data/palettes/` 是源码的一部分，必须提交。

## 环境与启动

所有 Python 命令都必须通过 `uv run` 使用项目内虚拟环境 `.venv/`，不要使用系统 Python 或全局 pip。

```bash
uv sync                      # 安装/同步依赖（首次或 pyproject.toml 变更后）
uv run python run.py         # 启动服务，默认 http://127.0.0.1:8520
uv run python run.py --reload  # 开发模式，代码改动自动重启
uv run pytest                # 运行测试
```

### 已验证可用的启动命令（避免每次新会话重新摸索）

| 系统 | 命令 | 说明 |
|---|---|---|
| macOS / Linux | `./scripts/start.sh` | 自动 `uv sync` 后启动，已在 macOS（Apple Silicon）验证 |
| macOS / Linux | `uv run python run.py` | 直接启动 |
| Windows | `scripts\start.bat` | 自动 `uv sync` 后启动（CMD / PowerShell 均可）；尚未在 Windows 实机验证，首次验证后请更新此处 |
| Windows | `uv run python run.py` | 直接启动 |
| 任意 | `PB_PORT=9000 uv run python run.py` | 修改端口（Windows PowerShell：`$env:PB_PORT=9000; uv run python run.py`） |

- 默认端口 **8520**（本机 8000 端口常被其他项目占用，故不使用 8000）。
- 启动成功的标志：日志出现 `拼豆图纸生成器 v0.1.0 启动完成`。
- 页面：`http://127.0.0.1:8520/`；接口文档：`http://127.0.0.1:8520/docs`。

### 环境变量（均可不设置）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PB_HOST` | `127.0.0.1` | 监听地址，局域网访问改为 `0.0.0.0` |
| `PB_PORT` | `8520` | 端口 |
| `PB_LOG_LEVEL` | `INFO` | 日志级别，排查算法问题可设为 `DEBUG` |
| `PB_LOG_DIR` | `logs/` | 日志目录 |
| `PB_DATA_DIR` | `data/` | SQLite 数据库目录 |
| `PB_MAX_UPLOAD_MB` | `15` | 上传图片大小上限 |
| `PB_MAX_GRID_SIZE` | `200` | 网格边长上限 |
| `PB_FONT_PATH` | 自动查找 | 渲染图纸用的中文字体路径 |
| `PB_MODEL_DIR` | `~/.u2net` | AI 抠图模型目录（与 rembg 共用） |

### AI 抠图模型

- 模型不随代码提交，首次使用时在页面点「下载」，或执行 `uv run python scripts/download_model.py [模型ID]`（`--list` 查看状态）。
- 可选模型：`isnet-general-use` 通用（170MB，默认）、`u2net_human_seg` 人像（168MB）、`isnet-anime` 动漫（168MB）、`u2netp` 轻量（4.4MB）。
- 本机已下载：`isnet-general-use`（2026-09-28）。

## 数据库变更

- 新增字段：在 `models.py` 中定义（必须写 `comment`，枚举类字段注明可能取值），同时在
  `app/core/database.py` 的 `ADDED_COLUMNS` 中登记，启动时会自动 `ALTER TABLE` 给旧数据库补上（SQLite 的 `create_all` 不会修改已有表）。
- 新增字段必须有默认值（`DEFAULT ...`），保证旧数据可用；`tests/test_progress.py` 中有迁移测试可参考。
- 已登记的迁移：`patterns.done_codes_json`（拼豆进度，2026-09-28）。

## 编码约定

- Python：遵循 PEP 8，行宽 120；类型注解使用 3.12 语法（`list[str]`、`X | None`）。
- 分层：`api/` 只做参数解析和调用，业务逻辑放 `services/`；service 层抛 `app.core.errors` 中的异常，不直接抛 HTTPException。
- CPU 密集型接口（图片处理、导出）用同步 `def` 定义，FastAPI 会放入线程池执行。
- 前端：原生 JS，不引入构建工具和外部 CDN（保证离线可用）；所有插入 HTML 的用户数据必须经过 `escapeHtml`。
- 图标：使用 `web/index.html` 顶部的 SVG 雪碧图（`<symbol id="i-xxx">`，24×24 线条图标），按钮里用
  `<svg class="icon"><use href="#i-xxx"/></svg>` 引用，颜色跟随 `currentColor`；不要用 emoji 做图标（各系统显示不一致）。
- 接口错误响应统一为 `{"detail": "中文错误信息"}`。

## 测试

```bash
uv run pytest            # 全部测试
uv run pytest -k api     # 只跑接口测试
```

- 测试使用临时目录中的数据库与日志（见 `tests/conftest.py`），不会影响 `data/`、`logs/`。
- 修改 `services/color.py` 或 `services/converter.py` 后必须运行测试；色差公式测试使用 Sharma 2005 论文标准数据。
- 测试把 `PB_MODEL_DIR` 指向空的临时目录，AI 抠图用假模型（monkeypatch `_run_model`）验证流程，**测试不会下载模型**。
- 调整去背景算法时，除单元测试外建议用真实照片目测对比（纯色背景、渐变背景、主体贴边、复杂背景各一张）。
- 新增接口或算法参数时，同步在 `tests/test_api.py` 中补充用例。

## 安全注意事项

- 上传文件只在内存中处理，不落盘；有大小上限与 Pillow 解压炸弹保护。
- 服务默认只监听 `127.0.0.1`。改为 `0.0.0.0` 对外开放前，需要自行增加鉴权（当前无用户体系）。
- 不要把 `data/`、`logs/`、`.venv/` 提交到 Git。

## Git 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，描述可用中文：

```
<type>(<scope>): <简要描述>

[可选正文：说明改动原因和影响]
```

- `type`：`feat` 新功能 / `fix` 修复 / `docs` 文档 / `refactor` 重构 / `perf` 性能 / `test` 测试 / `chore` 构建、依赖、杂项
- `scope`（可选）：`converter`、`renderer`、`api`、`web`、`palette` 等
- 示例：`feat(converter): 支持按用户排除的色号生成图纸`

---

## 项目协作规则（必须遵守）

1. 枚举、models、schemas 等都需要添加中文注释。
2. 代码编写过程中，项目中重要的文件和方法需要添加注释，方便阅读理解。
3. 编码过程中添加核心日志（带时间）。统一使用 `app.core.logger.get_logger(__name__)`，格式已包含时间戳。
4. 每次大模块功能迭代后，需要检查项目中的 `README.md`、`AGENTS.md` 等文档是否需要更新。
5. 数据库新增字段需要添加注释（SQLAlchemy `comment=`）；类型或枚举字段需要在注释中备注可能的取值和含义。
6. 改动较大的功能或开启新功能时，主动提醒先 Git 提交再继续，且提交日志符合上文规范。提交代码前先更新代码（`git pull --rebase`）避免冲突；如有冲突先解决，无法判断如何解决时询问用户。
7. 涉及 Python 环境时必须使用项目内的虚拟环境（`uv run ...` / `.venv/`）。
8. 成功启动后记录启动命令（见上文「已验证可用的启动命令」），区分 macOS 与 Windows，避免每个新会话重复测试启动命令或脚本。
9. 删除文件或文件夹时需要确认并仔细辨认；AI 助手只允许逐个删除单个文件。禁止批量删除文件夹等操作，涉及批量删除这类危险操作必须人工确认。
10. 如果提供了项目远程服务器部署地址或文件夹，只能进行查询操作，不能在其他范围外的文件夹执行命令或删除文件。
