"""
测试公共配置：数据库和日志写到临时目录，不污染项目 data/、logs/。
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="perler_test_")
os.environ["PB_DATA_DIR"] = _tmp
os.environ["PB_LOG_DIR"] = os.path.join(_tmp, "logs")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c

