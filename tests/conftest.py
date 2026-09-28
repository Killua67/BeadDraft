"""
测试公共配置：数据库、日志、模型目录都指向临时目录，不污染项目 data/、logs/ 和 ~/.u2net。
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="perler_test_")
os.environ["PB_DATA_DIR"] = _tmp
os.environ["PB_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["PB_MODEL_DIR"] = os.path.join(_tmp, "models")  # 空目录：测试中 AI 模型均为「未下载」

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c

