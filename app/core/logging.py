import json
import logging
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(
    level: str = "INFO",
    log_format: str = "console",
    *,
    file_enabled: bool = True,
    file_path: Path = Path("logs/errors.log"),
) -> None:
    request_filter = RequestIdFilter()
    handler = logging.StreamHandler()
    handler.addFilter(request_filter)

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(request_id)s:%(message)s"))

    root_logger = logging.getLogger()
    for existing in root_logger.handlers:
        existing.close()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    if file_enabled:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(logging.WARNING)
        file_handler.addFilter(request_filter)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
            )
        )
        root_logger.addHandler(file_handler)
    root_logger.setLevel(level)


def configure_ai_content_logging(*, enabled: bool, file_path: Path) -> None:
    logger = logging.getLogger("ai.content")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.disabled = not enabled
    if enabled:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(file_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)


def set_request_id(request_id: str | None) -> Token[str | None]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    request_id_context.reset(token)
