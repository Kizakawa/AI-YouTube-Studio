"""
app/utils/log_config.py — Structured Logging tap trung.
[Phase 3] Thay the logging.basicConfig() phan tan:
  LOG_FORMAT=json    -> JSON NDJSON (Loki/Datadog/production)
  LOG_FORMAT=console -> mau sac human-readable (dev, mac dinh)
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Any


class JsonFormatter(logging.Formatter):
    """Moi log record -> 1 JSON object tren 1 dong (NDJSON format)."""

    _SKIP = frozenset([
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs",
        "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
    ])

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.message,
            "module": record.module,
            "line":   record.lineno,
        }
        for k, v in record.__dict__.items():
            if k not in self._SKIP and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ColorConsoleFormatter(logging.Formatter):
    """Log mau sac de doc trong terminal phat trien."""

    _C = {
        "DEBUG":    "\033[36m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    _R = "\033[0m"
    _B = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        color = self._C.get(record.levelname, "")
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        ctx_parts = []
        for field in ("channel_id", "job_id", "stage"):
            val = getattr(record, field, None)
            if val is not None:
                ctx_parts.append(field + "=" + str(val))
        ctx  = (" [" + ", ".join(ctx_parts) + "]") if ctx_parts else ""
        line = (ts + " " + color + self._B + "%-8s" % record.levelname + self._R
                + " " + color + record.name + self._R + ctx
                + " -- " + record.message)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def ensure_file_logging_attached(level: str = "INFO") -> None:
    """
    Gắn idempotent RotatingFileHandler -> storage/logs/app.log nếu tiến trình chạy qua CLI/Script/Batch
    chưa gọi configure_logging() từ run.py (không xóa stdout handler hiện có).
    """
    try:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        root = logging.getLogger()
        log_dir = Path("storage/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        target_log = (log_dir / "app.log").resolve()

        for h in list(root.handlers):
            base_fn = getattr(h, "baseFilename", None)
            if base_fn:
                try:
                    if Path(base_fn).resolve() == target_log:
                        return
                except Exception:
                    pass

        level_val = getattr(logging, (os.getenv("LOG_LEVEL") or level or "INFO").upper(), logging.INFO)
        if root.level == logging.NOTSET or root.level > level_val:
            root.setLevel(level_val)

        file_handler = RotatingFileHandler(
            target_log,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level_val)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s -- %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root.addHandler(file_handler)

        for noisy_lib in ("httpx", "httpcore", "urllib3",
                          "googleapiclient.discovery", "google.auth",
                          "google.auth.transport"):
            logging.getLogger(noisy_lib).setLevel(logging.WARNING)
    except Exception:
        pass


def configure_logging(level: str = "INFO", log_format: Optional[str] = None) -> None:
    """
    Goi 1 lan duy nhat tai run.py.
    Cau hinh toan bo logging cua ung dung.

    LOG_FORMAT=json    -> JsonFormatter  (production / container)
    LOG_FORMAT=console -> Color terminal (dev, mac dinh)
    """
    fmt       = (log_format or os.getenv("LOG_FORMAT", "console")).lower()
    level_val = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level_val)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level_val)

    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    elif sys.stdout.isatty():
        handler.setFormatter(ColorConsoleFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s -- %(message)s",
            datefmt="%H:%M:%S"
        ))

    root.addHandler(handler)

    # Ghi đồng thời toàn bộ log terminal ra file storage/logs/app.log (Rotating 10MB x 3) để theo dõi & chẩn đoán lỗi thời gian thực
    try:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path
        log_dir = Path("storage/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level_val)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s -- %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root.addHandler(file_handler)
    except Exception:
        pass

    # Tat spam log thu vien thu 3
    for noisy_lib in ("httpx", "httpcore", "urllib3",
                      "googleapiclient.discovery", "google.auth",
                      "google.auth.transport"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    logging.getLogger("AI-YouTube").info(
        "[LogConfig] Structured logging ON (format=%s, level=%s, file=storage/logs/app.log).", fmt, level
    )


class StructuredLogger:
    """
    Logger wrapper tu dong dinh kem context fields.

    Vi du:
        log = StructuredLogger("pipeline", channel_id="ch_001", job_id=42)
        log.info("Bat dau render", stage="RENDER")
        child = log.bind(stage="FFMPEG")
        child.error("FFmpeg crash", exc_info=True)
    """

    def __init__(self, name: str, **default_context: Any) -> None:
        self._logger  = logging.getLogger(name)
        self._ctx     = default_context

    def _log(self, level: int, msg: str, **kwargs: Any) -> None:
        self._logger.log(level, msg, extra={**self._ctx, **kwargs}, stacklevel=2)

    def debug(self, msg: str, **kw: Any) -> None:
        self._log(logging.DEBUG, msg, **kw)

    def info(self, msg: str, **kw: Any) -> None:
        self._log(logging.INFO, msg, **kw)

    def warning(self, msg: str, **kw: Any) -> None:
        self._log(logging.WARNING, msg, **kw)

    def error(self, msg: str, exc_info: bool = False, **kw: Any) -> None:
        self._logger.error(
            msg, extra={**self._ctx, **kw}, exc_info=exc_info, stacklevel=2
        )

    def critical(self, msg: str, exc_info: bool = False, **kw: Any) -> None:
        self._logger.critical(
            msg, extra={**self._ctx, **kw}, exc_info=exc_info, stacklevel=2
        )

    def bind(self, **kw: Any) -> "StructuredLogger":
        """Tra ve logger moi voi context bo sung (immutable pattern)."""
        return StructuredLogger(self._logger.name, **{**self._ctx, **kw})
