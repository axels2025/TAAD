"""Logging configuration using loguru.

This module sets up comprehensive logging for the trading system with
structured logging, multiple log levels, and file rotation.
"""

import logging
import sys
from pathlib import Path

from loguru import logger

# Remove default handler
logger.remove()


def setup_logging(
    log_level: str = "INFO",
    log_file: str = "logs/app.log",
    enable_console: bool = True,
    console_level: str = None,
) -> None:
    """Configure logging with loguru.

    Args:
        log_level: Logging level for file (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file
        enable_console: Whether to enable console logging
        console_level: Console logging level (defaults to log_level if not specified)

    Example:
        >>> setup_logging(log_level="INFO", log_file="logs/app.log")
        >>> setup_logging(log_level="INFO", console_level="WARNING", log_file="logs/app.log")
    """
    # Ensure logs directory exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Use separate console level if specified, otherwise same as file
    if console_level is None:
        console_level = log_level

    # Add console handler if enabled
    if enable_console:
        logger.add(
            sys.stdout,
            level=console_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            colorize=True,
        )

    # Add file handler with rotation
    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="100 MB",  # Rotate when file reaches 100 MB
        retention="30 days",  # Keep logs for 30 days
        compression="zip",  # Compress rotated logs
        enqueue=True,  # Thread-safe logging
    )

    # Add separate file for errors only
    error_log_file = str(log_path.parent / "errors.log")
    logger.add(
        error_log_file,
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="50 MB",
        retention="60 days",
        compression="zip",
        enqueue=True,
        backtrace=True,  # Include full traceback
        diagnose=True,  # Include variable values in traceback
    )

    # Add separate file for trades
    trades_log_file = str(log_path.parent / "trades.log")
    logger.add(
        trades_log_file,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="50 MB",
        retention="1 year",  # Keep trade logs for 1 year
        compression="zip",
        enqueue=True,
        filter=lambda record: record["extra"].get("type") == "trade",
    )

    # Add separate file for learning events
    learning_log_file = str(log_path.parent / "learning.log")
    logger.add(
        learning_log_file,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="50 MB",
        retention="1 year",
        compression="zip",
        enqueue=True,
        filter=lambda record: record["extra"].get("type") == "learning",
    )

    # Silence noisy third-party loggers that use stdlib logging
    for noisy_logger in ("yfinance", "peewee", "urllib3", "curl_cffi"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logger.info(
        f"Logging initialized: level={log_level}, file={log_file}, console={enable_console}"
    )


def get_logger(name: str | None = None):
    """Get a logger instance with optional name binding.

    Args:
        name: Optional name to bind to the logger

    Returns:
        Logger instance

    Example:
        >>> log = get_logger("trading")
        >>> log.info("Trade executed")
    """
    if name:
        return logger.bind(name=name)
    return logger


def log_trade(message: str, **kwargs) -> None:
    """Log a trade event to the trades log file.

    Args:
        message: Trade message to log
        **kwargs: Additional context to include

    Example:
        >>> log_trade(
        ...     "Trade opened",
        ...     symbol="AAPL",
        ...     strike=150.0,
        ...     premium=0.45
        ... )
    """
    logger.bind(type="trade").info(message, **kwargs)


def log_learning(message: str, **kwargs) -> None:
    """Log a learning event to the learning log file.

    Args:
        message: Learning message to log
        **kwargs: Additional context to include

    Example:
        >>> log_learning(
        ...     "Pattern detected",
        ...     pattern_name="18-20% OTM outperforms",
        ...     confidence=0.95
        ... )
    """
    logger.bind(type="learning").info(message, **kwargs)
