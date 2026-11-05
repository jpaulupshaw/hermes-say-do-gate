"""Gate: the Say-Do Gate logic. Coordinates ledger, claims, matcher, and render."""
import logging
import threading
from collections import OrderedDict

logger = logging.getLogger("say_do_gate")

if __package__:
    # Loaded as a package by Hermes (e.g. hermes_plugins.say_do_gate): use relative imports
    from .ledger import Ledger
    from .claims import extract_claims
    from .matcher import match
    from .render import render_footer, render_nudge
else:
    # Top-level import in unit tests (plugin root on sys.path via conftest.py)
    from ledger import Ledger
    from claims import extract_claims
    from matcher import match
    from render import render_footer, render_nudge


class Gate:
    def __init__(self, mode: str = "observe", *, max_pending: int = 256) -> None:
        self.mode = mode
        self.max_pending = max_pending
        self.ledger = Ledger()
        self._lock = threading.Lock()
        # session_id -> nudge text (ordered for eviction)
        self._pending_nudges: OrderedDict[str, str] = OrderedDict()
        # session_id -> list[Verdict] from most recent evaluated reply (LRU, bounded by max_pending)
        self._last_report: OrderedDict[str, list] = OrderedDict()
        # (session_id, turn_id) whose tool recording failed (insertion-ordered, bounded by max_pending)
        self._unreliable_turns: OrderedDict[tuple[str, str], None] = OrderedDict()

    def _bounded_put(self, store: OrderedDict, key, value) -> None:
        """Insert as newest and evict the oldest past max_pending. Caller holds self._lock."""
        store.pop(key, None)
        store[key] = value
        while len(store) > self.max_pending:
            store.popitem(last=False)

    def on_post_tool_call(self, **kw) -> None:
        """Record a tool call receipt in the ledger."""
        session_id = kw.get("session_id") or ""
        turn_id = kw.get("turn_id") or ""
        try:
            self.ledger.record(
                tool_name=kw.get("tool_name", ""),
                args=kw.get("args"),
                result=kw.get("result"),
                session_id=session_id,
                turn_id=turn_id,
                status=kw.get("status"),
                error_type=kw.get("error_type"),
                error_message=kw.get("error_message"),
                duration_ms=kw.get("duration_ms", 0),
            )
        except Exception:
            # Recording failed: mark this turn as unreliable so the transform
            # skips annotating (can't tell if claims are backed).
            with self._lock:
                self._bounded_put(self._unreliable_turns, (session_id, turn_id), None)
        return None

    def on_pre_llm_call(self, **kw) -> dict | None:
        """Pop and return a pending nudge for this session, if any."""
        session_id = kw.get("session_id") or ""
        turn_id = kw.get("turn_id") or ""

        # First: pop pending nudge for this session
        nudge = None
        with self._lock:
            if session_id in self._pending_nudges:
                nudge = self._pending_nudges.pop(session_id)

        # Then: begin turn in the ledger (unless it's a fork)
        user_message = kw.get("user_message")
        if ":" not in turn_id:
            self.ledger.begin_turn(
                session_id=session_id,
                turn_id=turn_id,
                user_message=user_message,
            )

        if nudge:
            return {"context": nudge}
        return None

    def on_transform(self, response_text=None, session_id=None, turn_id=None, **kw) -> str | None:
        """Evaluate claims in the response against the ledger. Annotate or store nudge."""
        if not isinstance(response_text, str) or not response_text:
            return None

        session_id = session_id or ""
        turn_id = turn_id or ""

        # If tool recording failed this turn, the ledger is unreliable: skip annotating.
        with self._lock:
            if (session_id, turn_id) in self._unreliable_turns:
                return None

        user = self.ledger.user_message(session_id=session_id, turn_id=turn_id)
        claims = extract_claims(response_text, user)
        if not claims:
            with self._lock:
                self._bounded_put(self._last_report, session_id, [])
            return None

        window = self.ledger.window(session_id=session_id, turn_id=turn_id)
        verdicts = match(claims, window)

        with self._lock:
            self._bounded_put(self._last_report, session_id, verdicts)

        # Log summary (never reply text, phrases, evidence, commands, or user messages)
        high = sum(1 for v in verdicts if v.severity == "high")
        medium = sum(1 for v in verdicts if v.severity == "medium")
        low = sum(1 for v in verdicts if v.severity == "low")
        backed = sum(1 for v in verdicts if v.severity is None)
        logger.info("say_do_gate turn evaluated: high=%d medium=%d low=%d backed=%d mode=%s",
                    high, medium, low, backed, self.mode)

        # Log each high/medium finding (kind/severity/label only, never phrase/evidence)
        for v in verdicts:
            if v.severity in ("high", "medium"):
                logger.info("say_do_gate finding kind=%s severity=%s label=%s",
                            getattr(v.claim, "kind", None) or v.label, v.severity, v.label)

        if self.mode == "annotate":
            footer = render_footer(verdicts)
            if footer:
                return response_text.rstrip() + "\n\n" + footer
            return None
        else:
            # observe mode: store nudge for next turn
            nudge = render_nudge(verdicts)
            if nudge:
                self._store_nudge(session_id, nudge)
            return None

    def _store_nudge(self, session_id: str, nudge: str) -> None:
        """Store a pending nudge, evicting oldest if over max_pending."""
        with self._lock:
            self._bounded_put(self._pending_nudges, session_id, nudge)

    def last_report(self, session_id: str) -> list:
        """Return verdicts from the most recent evaluated reply for this session."""
        with self._lock:
            return list(self._last_report.get(session_id, []))
