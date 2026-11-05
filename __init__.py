"""Say-Do Gate plugin: deterministic claim checking against the turn's tool receipts."""
import logging

logger = logging.getLogger("say_do_gate")


def register(ctx):
    """Register the plugin's hooks. Never raises."""
    try:
        _register_impl(ctx)
    except Exception as exc:
        # Type only: exception messages can carry paths, tokens, or user text
        logger.warning("say_do_gate registration failed: %s", type(exc).__name__)


def _register_impl(ctx):
    """Internal registration logic."""
    from .config import resolve_mode
    from ._failopen import fail_open
    from .gate import Gate

    raw_mode = ctx.get_config("mode", "observe")
    mode = resolve_mode(raw_mode)

    # Create the Gate instance (module-level for diagnostics)
    global _GATE
    _GATE = Gate(mode)

    ctx.register_hook("post_tool_call", fail_open(_GATE.on_post_tool_call))
    ctx.register_hook("pre_llm_call", fail_open(_GATE.on_pre_llm_call))
    ctx.register_hook(
        "transform_llm_output", fail_open(_GATE.on_transform),
        hold_stream=(mode == "annotate"),
    )

    logger.info("say_do_gate registered in %s mode", mode)


# Module-level Gate instance for diagnostics (set by _register_impl)
_GATE = None
