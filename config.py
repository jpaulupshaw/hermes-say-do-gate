"""Config validation: resolve the plugin's mode setting."""
import logging

logger = logging.getLogger("say_do_gate")

VALID_MODES = ("observe", "annotate")
DEFAULT_MODE = "observe"


def resolve_mode(raw):
    """Normalize a mode value to 'observe' or 'annotate'.

    Handles: None -> observe; whitespace/mixed-case -> normalized; unknown -> observe with warning;
    non-str -> observe with warning.
    """
    if raw is None:
        return DEFAULT_MODE
    if not isinstance(raw, str):
        logger.warning("say_do_gate mode=%r is not a string; using %s", raw, DEFAULT_MODE)
        return DEFAULT_MODE
    normalized = raw.strip().lower()
    if normalized not in VALID_MODES:
        logger.warning("say_do_gate unknown mode=%r; using %s", raw, DEFAULT_MODE)
        return DEFAULT_MODE
    return normalized
