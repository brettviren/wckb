"""Logging: where kb's log records go, and from what level.

By default records of level warning and above are appended to a file of the
day, ``$XDG_CACHE_HOME/kb/logs/kb-YYYY-MM-DD.log`` (``~/.cache/kb/logs/``).
``-l/--log-level`` and ``-L/--log-sink`` (a file, ``stdout`` or ``stderr``)
change that; ``--debug`` is ``-l debug -L stdout``.  Each record has its time,
level, the store context's name and the logger's name.

Levels: ``debug`` for what went right but helps explain a surprise (which
config files applied, which store was opened); ``info`` for high-level
success; ``warning`` for something unusual that kb got over; ``error`` for
more about a failure being reported.  A command's output (its data) is not
logged: it goes to stdout.
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path

LEVELS = ("debug", "info", "warning", "error", "critical")
DEFAULT_LEVEL = "warning"
STREAMS = ("stdout", "stderr")
FORMAT = "%(asctime)s %(levelname)s [%(context)s] %(name)s: %(message)s"

_context = "-"
_handler: logging.Handler | None = None
_stream: str | None = None     # the sink, when it is stdout or stderr

log = logging.getLogger("kb")


def log_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "kb" / "logs"


def default_sink() -> Path:
    return log_dir() / f"kb-{date.today().isoformat()}.log"


def set_context(name: str) -> None:
    """The store context's name, for the records from here on."""
    global _context
    _context = name


class _WithContext(logging.Filter):
    def filter(self, record):
        record.context = _context
        return True


def setup(level: str = DEFAULT_LEVEL, sink: str | None = None) -> None:
    """Send kb's records at ``level`` and above to ``sink``: a file (appended to), stdout or stderr."""
    global _handler, _stream
    level = level.lower()
    if level not in LEVELS:
        raise ValueError(f"log level {level!r}: one of {', '.join(LEVELS)}")
    reset()
    problem = None
    if sink in STREAMS:
        handler, _stream = logging.StreamHandler(getattr(sys, sink)), sink
    else:
        path = Path(sink).expanduser() if sink else default_sink()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler, _stream = logging.FileHandler(path, mode="a", encoding="utf-8"), None
        except OSError as exc:
            handler, _stream = logging.StreamHandler(sys.stderr), "stderr"
            problem = f"cannot log to {path} ({exc}); logging to stderr"
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(_WithContext())
    log.addHandler(handler)
    log.setLevel(level.upper())
    log.propagate = False
    _handler = handler
    if problem:
        log.warning(problem)


def reset() -> None:
    """Back to no handler of kb's own (records of warning and above go to stderr, Python's default)."""
    global _handler, _stream
    if _handler is not None:
        log.removeHandler(_handler)
        _handler.close()
    _handler = _stream = None
    log.setLevel(logging.NOTSET)
    log.propagate = True


def shown(level: int) -> bool:
    """Whether a record of ``level`` already reaches the terminal (the sink is stdout or stderr)."""
    return _stream is not None and log.isEnabledFor(level)


def tell(logger: logging.Logger, level: int, message: str) -> None:
    """Log ``message``, and also tell the person at the terminal (on stderr) unless the log does."""
    logger.log(level, "%s", message)
    if not shown(level):
        prefix = "kb: warning: " if level == logging.WARNING else ""
        print(prefix + message, file=sys.stderr)
