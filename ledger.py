"""Receipt ledger: records tool invocations per session/turn for the Say-Do Gate."""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Receipt:
    seq: int
    session_id: str
    turn_key: str
    tool_name: str
    args: dict
    status: str
    exit_code: int | None
    error_type: str | None
    error_message: str | None
    output_tail: str
    command: str | None
    background: bool
    files: tuple[str, ...]
    duration_ms: int


def _task_of(turn_id: str) -> str | None:
    """The task segment of a full turn id "session:task:uuid8", else None."""
    parts = turn_id.split(":")
    return parts[1] if len(parts) == 3 and parts[1] else None


class Ledger:
    def __init__(self, lookback_turns: int = 3, max_sessions: int = 256,
                 max_receipts_per_turn: int = 500) -> None:
        self._lock = threading.Lock()
        self._lookback = lookback_turns
        self._max_sessions = max_sessions
        self._max_receipts = max_receipts_per_turn
        # session_id -> OrderedDict[turn_key, list[Receipt]]
        # OrderedDict maintains insertion order for main turns (newest at end)
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._seq = 0

    def begin_turn(self, *, session_id: str, turn_id: str,
                   user_message: Any = None) -> None:
        with self._lock:
            sess = self._get_session(session_id)
            msg_str = user_message if isinstance(user_message, str) else None
            if ":" not in turn_id and turn_id:
                # Main turn: add to order if new, evict old
                order = sess.main_turn_order
                if turn_id not in order:
                    order.append(turn_id)
                    # Evict: keep only last lookback+1
                    while len(order) > self._lookback + 1:
                        old = order.pop(0)
                        sess.turns.pop(old, None)
                        sess.user_messages.pop(old, None)
                if msg_str is not None:
                    sess.user_messages[turn_id] = msg_str
            elif not turn_id:
                # Real runtime: pre_llm_call fires before the turn id is minted.
                # Open a slot (empty id) as the newest main turn so the later
                # full-id receipts/user_message bind into it.
                if (not sess.main_turn_order
                        or sess.main_turn_order[-1] != ""):
                    sess.main_turn_order.append("")
                    while len(sess.main_turn_order) > self._lookback + 1:
                        old = sess.main_turn_order.pop(0)
                        sess.turns.pop(old, None)
                        sess.user_messages.pop(old, None)
                if msg_str is not None:
                    sess.user_messages[""] = msg_str
            else:
                # Fork: just store user_message under the resolved key
                key = self._resolve_key(sess, turn_id)
                if msg_str is not None:
                    sess.user_messages[key] = msg_str
            sess.touch()

    def _get_session(self, session_id: str) -> _Session:
        """Get or create a session, evicting LRU if over max_sessions."""
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = _Session()
            self._sessions[session_id] = sess
            # Evict LRU if over limit
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        else:
            # Move to end (most recently used)
            self._sessions.move_to_end(session_id)
        return sess

    def _resolve_key(self, sess: _Session, turn_id: str) -> str:
        """Resolve a turn_id to its actual storage key within a session."""
        if not turn_id:
            # Empty: use most recent main turn, or __noturn__
            if sess.main_turn_order:
                return sess.main_turn_order[-1]
            return "__noturn__"
        if ":" in turn_id:
            # Fork turn
            if turn_id not in sess.fork_order:
                sess.fork_order.append(turn_id)
                # Evict oldest fork if over 8
                while len(sess.fork_order) > 8:
                    old = sess.fork_order.pop(0)
                    sess.turns.pop(old, None)
                    sess.user_messages.pop(old, None)
            return turn_id
        # Main turn: ensure it's in the order
        if turn_id not in sess.main_turn_order:
            sess.main_turn_order.append(turn_id)
            while len(sess.main_turn_order) > self._lookback + 1:
                old = sess.main_turn_order.pop(0)
                sess.turns.pop(old, None)
                sess.user_messages.pop(old, None)
        return turn_id

    def _slot_for_full_id(self, sess: _Session, turn_id: str) -> tuple[str, str]:
        """Return (storage_key, order_key) for a full "session:task:uuid8" id.

        In the real runtime, begin_turn fires at pre_llm_call with an EMPTY
        turn_id (the id is minted after that hook), while post_tool_call and
        transform carry the full id. The empty begin_turn registered a slot as
        the newest main turn; bind this full id to that slot when its task
        matches, so receipts, lookback, and user_message all resolve correctly.
        """
        task = _task_of(turn_id)
        if sess.main_turn_order:
            newest = sess.main_turn_order[-1]
            newest_task = _task_of(newest)
            if newest == "" or newest_task is None or newest_task == task:
                # The slot is open (empty) or belongs to the same task: adopt the full id.
                idx = sess.main_turn_order.index(newest)
                sess.main_turn_order[idx] = turn_id
                if newest != turn_id:
                    if newest in sess.turns:
                        sess.turns[turn_id] = sess.turns.pop(newest)
                    if newest in sess.user_messages:
                        sess.user_messages[turn_id] = sess.user_messages.pop(newest)
                return turn_id, turn_id
        # No matching slot: register the full id as a new main turn.
        key = self._resolve_key(sess, turn_id)
        return key, key

    def _main_key(self, sess: _Session, turn_id: str) -> str:
        """Read-only resolution of a turn_id to a main-turn storage key.

        Mirrors record()'s binding: full "session:task:uuid8" ids bind to the
        newest open/same-task slot; unknown full ids resolve to the newest slot
        when their task matches, else read empty. Never mutates.
        """
        if not turn_id:
            return sess.main_turn_order[-1] if sess.main_turn_order else "__noturn__"
        if ":" not in turn_id:
            return turn_id
        task = _task_of(turn_id)
        if turn_id in sess.main_turn_order:
            return turn_id
        if sess.main_turn_order:
            newest = sess.main_turn_order[-1]
            newest_task = _task_of(newest)
            if newest == "" or newest_task is None or newest_task == task:
                return newest
        return turn_id  # belongs to no known slot; reads empty

    def record(self, *, tool_name: str, args: Any = None, result: Any = None,
               session_id: str = "", turn_id: str = "", status: str | None = None,
               error_type: str | None = None, error_message: str | None = None,
               duration_ms: int = 0, **_ignored) -> Receipt:
        with self._lock:
            sess = self._get_session(session_id)
            if ":" in turn_id and _task_of(turn_id) is not None and turn_id not in sess.fork_order:
                key, _ = self._slot_for_full_id(sess, turn_id)
            else:
                key = self._resolve_key(sess, turn_id)

            # Parse result into a dict if possible
            result_dict = None
            if isinstance(result, dict):
                result_dict = result
            elif isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        result_dict = parsed
                except (json.JSONDecodeError, ValueError):
                    pass

            # Derive status
            if status not in ("ok", "error"):
                if result_dict and result_dict.get("error"):
                    status = "error"
                else:
                    status = "ok"
            # Exit code override
            exit_code = None
            if result_dict is not None:
                ec = result_dict.get("exit_code")
                if isinstance(ec, int):
                    exit_code = ec
                    if ec != 0:
                        status = "error"

            # Compute output_tail
            if result_dict is not None:
                raw_output = result_dict.get("output")
                if isinstance(raw_output, str):
                    output_tail = raw_output[-2000:]
                else:
                    output_tail = str(result)[-2000:]
            else:
                output_tail = str(result)[-2000:] if result is not None else ""

            # Process args
            if not isinstance(args, dict):
                args_copy = {}
            else:
                args_copy = {}
                for k, v in args.items():
                    if isinstance(v, str) and len(v) > 2000:
                        args_copy[k] = v[:2000]
                    else:
                        args_copy[k] = v

            # Compute command and background
            command = None
            background = False
            if tool_name == "terminal":
                cmd_val = args_copy.get("command")
                if isinstance(cmd_val, str):
                    command = cmd_val
                background = bool(args_copy.get("background"))

            # Compute files
            files = ()
            if result_dict is not None:
                fm = result_dict.get("files_modified")
                if isinstance(fm, list):
                    files = tuple(f for f in fm if isinstance(f, str))
                else:
                    rp = result_dict.get("resolved_path")
                    if isinstance(rp, str):
                        files = (rp,)
            if not files and tool_name in ("write_file", "patch"):
                p = args_copy.get("path")
                if isinstance(p, str):
                    files = (p,)

            # Truncate error_message
            if isinstance(error_message, str) and len(error_message) > 500:
                error_message = error_message[:500]

            self._seq += 1
            receipt = Receipt(
                seq=self._seq,
                session_id=session_id,
                turn_key=key,
                tool_name=tool_name,
                args=args_copy,
                status=status,
                exit_code=exit_code,
                error_type=error_type,
                error_message=error_message,
                output_tail=output_tail,
                command=command,
                background=background,
                files=files,
                duration_ms=duration_ms,
            )

            # Store receipt, enforcing per-turn cap
            if key not in sess.turns:
                sess.turns[key] = []
            receipts = sess.turns[key]
            receipts.append(receipt)
            while len(receipts) > self._max_receipts:
                receipts.pop(0)

            sess.touch()
            return receipt

    def window(self, *, session_id: str, turn_id: str) -> list[tuple[int, list[Receipt]]]:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return [(0, [])]
            sess.touch()
            self._sessions.move_to_end(session_id)

            if not turn_id:
                # Resolve like record()
                if sess.main_turn_order:
                    key = sess.main_turn_order[-1]
                else:
                    key = "__noturn__"
            else:
                key = turn_id

            is_full_id = ":" in key and _task_of(key) is not None
            if is_full_id:
                key = self._main_key(sess, key)
                if key not in sess.main_turn_order:
                    order = sess.main_turn_order
                    # Unknown full id: check its own receipts, then recent turns
                    return [(0, list(sess.turns.get(key, [])))] + [
                        (-off, list(sess.turns.get(order[-off], [])))
                        for off in range(1, min(self._lookback, len(order)) + 1)
                    ]
            elif key == "__noturn__" or (":" in key and not is_full_id):
                # Fork or noturn: just return its receipts
                receipts = sess.turns.get(key, [])
                return [(0, list(receipts))]

            # Main turn: build lookback window
            order = sess.main_turn_order
            if key not in order:
                # Unknown main turn (begin_turn never fired, no tool calls): treat it as the turn after
                # the newest known one, read-only, so a recap is still checked against recent turns.
                return [(0, [])] + [(-off, list(sess.turns.get(order[-off], [])))
                                    for off in range(1, min(self._lookback, len(order)) + 1)]

            idx = order.index(key)
            result = []
            # Current turn (age 0)
            result.append((0, list(sess.turns.get(key, []))))
            # Previous turns
            for offset in range(1, self._lookback + 1):
                prev_idx = idx - offset
                if prev_idx < 0:
                    break
                prev_key = order[prev_idx]
                result.append((-offset, list(sess.turns.get(prev_key, []))))
            return result

    def user_message(self, *, session_id: str, turn_id: str) -> str | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            if not turn_id:
                if sess.main_turn_order:
                    key = sess.main_turn_order[-1]
                else:
                    key = "__noturn__"
            elif ":" in turn_id and _task_of(turn_id) is not None:
                key = self._main_key(sess, turn_id)
            else:
                key = turn_id
            return sess.user_messages.get(key)


class _Session:
    def __init__(self) -> None:
        self.main_turn_order: list[str] = []
        self.fork_order: list[str] = []
        self.turns: dict[str, list[Receipt]] = {}
        self.user_messages: dict[str, str] = {}
        self._last_touched = 0.0

    def touch(self) -> None:
        import time
        self._last_touched = time.monotonic()
