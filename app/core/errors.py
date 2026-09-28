"""
业务异常。service 层抛出，由 main.py 中注册的处理器统一转换为 JSON 响应：{"detail": "错误信息"}。
"""


class AppError(Exception):
    """业务异常基类，默认 HTTP 400。"""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BadRequestError(AppError):
    """请求参数或数据不合法。"""

    status_code = 400


class NotFoundError(AppError):
    """资源不存在。"""

    status_code = 404
