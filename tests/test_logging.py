import logging
from pathlib import Path

from app.core.logging import (
    configure_ai_content_logging,
    configure_logging,
    reset_request_id,
    set_request_id,
)


def test_warning_and_error_logs_are_written_to_single_file(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "errors.log"
    configure_logging(file_path=log_path)
    token = set_request_id("request-123")
    logger = logging.getLogger("tests.logging")

    try:
        logger.info("not written to the error file")
        logger.warning("safe validation summary")
    finally:
        reset_request_id(token)
        for handler in logging.getLogger().handlers:
            handler.flush()

    contents = log_path.read_text(encoding="utf-8")
    assert "WARNING tests.logging request_id=request-123 safe validation summary" in contents
    assert "not written to the error file" not in contents


def test_file_logging_can_be_disabled(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "errors.log"

    configure_logging(file_enabled=False, file_path=log_path)
    logging.getLogger("tests.logging").error("not persisted")

    assert not log_path.exists()


def test_ai_content_logging_is_isolated_to_its_file(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "ai.log"
    configure_ai_content_logging(enabled=True, file_path=log_path)

    logging.getLogger("ai.content").info('{"event":"request","prompt":"safe test"}')

    for handler in logging.getLogger("ai.content").handlers:
        handler.flush()
    assert '"prompt":"safe test"' in log_path.read_text(encoding="utf-8")
