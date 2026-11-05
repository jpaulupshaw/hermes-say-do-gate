"""Render footer and nudge text from verdicts. Stdlib only, duck-typed on Verdict fields."""


def _visible(verdicts):
    """Return verdicts with severity 'high' or 'medium', high first, then medium, stable order."""
    if not verdicts:
        return []
    high = [v for v in verdicts if v.severity == "high"]
    medium = [v for v in verdicts if v.severity == "medium"]
    return high + medium


def render_footer(verdicts):
    """Render an annotate-mode footer. None if no high/medium verdicts."""
    visible = _visible(verdicts)
    if not visible:
        return None
    lines = []
    for i, v in enumerate(visible):
        line = f"[say-do] Claimed: {v.phrase}. Ledger: {v.evidence}."
        if i == 1 and len(visible) > 2:
            line += f" (+{len(visible) - 2} more)"
        lines.append(line)
        if i >= 1:
            break
    return "\n".join(lines)


def render_nudge(verdicts):
    """Render an observe-mode nudge. None if no high/medium verdicts."""
    visible = _visible(verdicts)
    if not visible:
        return None
    if len(visible) == 1:
        v = visible[0]
        return (f"[say-do gate] Your previous reply claimed: {v.phrase}. "
                f"Tool ledger: {v.evidence}. "
                f"Verify with a tool call and report the real result, or correct the claim.")
    # Multiple: list at most 3
    listed = visible[:3]
    parts = "; ".join(f"{v.phrase} (ledger: {v.evidence})" for v in listed)
    return (f"[say-do gate] Your previous reply made claims the tool ledger does not support: "
            f"{parts}. Verify with tool calls and report the real results, or correct the claims.")
