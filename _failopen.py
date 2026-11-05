"""Fail-open decorator: swallow exceptions, log, return None. KeyboardInterrupt passes through."""
import functools
import logging

logger = logging.getLogger("say_do_gate")


def fail_open(fn):
    """Wrap a hook callback so any exception is logged and the callback returns None.

    Preserves the original function's name. KeyboardInterrupt is not caught (allow the
    user to cancel).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            hook_name = kwargs.get("hook_name") or getattr(fn, "__name__", "unknown")
            # Type only: exception messages can carry reply/tool text, which must not reach logs.
            logger.warning("say_do_gate %s failed open: %s", hook_name, type(exc).__name__)
            return None
    return wrapper
