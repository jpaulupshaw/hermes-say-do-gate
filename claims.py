"""Claim extractor for agent replies.

Parses natural-language agent replies into structured claims about what the
agent says it has done. Used by the Say-Do Gate to detect hallucinated claims.

Pipeline:
1. Strip tool output (fenced code blocks, quoted lines, indented blocks)
2. Split into sentences
3. Exclude non-claim sentences (questions, hypotheticals, future intent, past
   session references, instructions, negations, failure reports)
4. Detect claim kinds in surviving sentences
5. Apply subsumption rules
6. Extract count and target where applicable
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = (
    "tests_pass", "tests_ran", "ran_command", "file_written",
    "deployed", "verified", "underway", "done", "vague",
)

@dataclass(frozen=True)
class Claim:
    kind: str
    text: str
    count: int | None = None
    target: str | None = None
    low: bool = False


# --- Regex patterns (all compiled once) ---

# Fenced code blocks: ``` ... ``` (terminated) and ```...$ (unterminated trailing)
_RE_FENCED = re.compile(r'```[^\n]*\n.*?\n```', re.DOTALL)
# An opening fence with no closing fence: everything after it is output, to the end of the reply
_RE_FENCED_UNTERM = re.compile(r'^```.*\Z', re.MULTILINE | re.DOTALL)

# Quoted lines (start with >)
_RE_QUOTED = re.compile(r'^\s*>\s?.*$', re.MULTILINE)

# Indented tool output (4+ spaces or tab)
_RE_INDENTED = re.compile(r'^[ \t]{4,}.*$', re.MULTILINE)

# Bullet markers
_RE_BULLET = re.compile(r'^\s*[-*]\s+')
_RE_NUMBERED = re.compile(r'^\s*\d+\.\s+')

# Sentence splitting: punctuation followed by whitespace or end
_RE_SENT_END = re.compile(r'([.!?])(?:\s+|$)')

# Exclusion: question (sentence ends with ?)
# Checked directly on the sentence string, not regex

# Exclusion: hypothetical/conditional
# "if " at start or as clause start, "once you", "when you", "after you", "unless", "To " at start
_RE_IF_CLAUSE = re.compile(r'\bif\s', re.IGNORECASE)
_RE_ONCE_YOU = re.compile(r'\bonce\s+you\b', re.IGNORECASE)
_RE_WHEN_YOU = re.compile(r'\bwhen\s+you\b', re.IGNORECASE)
_RE_AFTER_YOU = re.compile(r'\bafter\s+you\b', re.IGNORECASE)
_RE_UNLESS = re.compile(r'\bunless\b', re.IGNORECASE)
_RE_TO_START = re.compile(r'^To\s', re.IGNORECASE)

# Exclusion: future intent
_RE_I_WILL = re.compile(r"\bI'?ll\b|\bI\s+will\b|\bwe'?ll\b|\bgoing\s+to\b", re.IGNORECASE)
_RE_NEXT_END = re.compile(r'\bnext\b(?:\s*[.!])?\s*$', re.IGNORECASE)

# Exclusion: past session
_RE_PAST = re.compile(
    r'\byesterday\b|\blast\s+week\b|\blast\s+night\b|\blast\s+time\b'
    r'|\bearlier\s+this\s+week\b|\bprevious\s+session\b|\bthe\s+other\s+day\b',
    re.IGNORECASE
)

# Exclusion: instruction (bare imperative at start of sentence)
_IMPERATIVES = (
    'run', 'rerun', 're-run', 'execute', 'try', 'restart', 'deploy', 'verify',
    'check', 'install', 'build', 'test', 'update', 'write', 'create', 'apply', 'push'
)
_RE_IMPERATIVE = re.compile(
    r'^(?:' + '|'.join(re.escape(v) for v in _IMPERATIVES) + r')\s',
    re.IGNORECASE
)
_RE_YOU_INSTRUCTION = re.compile(
    r'\byou\s+(should|can|need\s+to|must)\b|\bplease\s',
    re.IGNORECASE
)

# Exclusion: negation
_RE_NEGATION = re.compile(
    r"\b(don'?t|doesn'?t|didn'?t|haven'?t|hasn'?t|isn'?t|aren'?t|not|never|no\s+longer)\b",
    re.IGNORECASE
)
# Failure words (checked separately for "0 " or "zero " prefix)
_RE_FAILURE = re.compile(r'\b(failed|failing|fails|errors?)\b', re.IGNORECASE)

# Claim detection: tests_pass
_RE_TESTS_PASS_VERBS = re.compile(r'\btests?\s+(pass|passed|passing)\b', re.IGNORECASE)
_RE_N_PASSED = re.compile(r'\b(\d+)\s+passed\b', re.IGNORECASE)
_RE_N_OF_N = re.compile(r'\b(\d+)/(\d+)\s+tests?\s+pass', re.IGNORECASE)
_RE_ALL_N_TESTS = re.compile(r'\ball\s+(\d+)\s+tests?\s+pass', re.IGNORECASE)
_RE_N_TESTS_PASS = re.compile(r'\b(\d+)\s+tests?\s+pass', re.IGNORECASE)
_RE_THEY_PASS = re.compile(
    r'\b(?:tests?|suite)\b[^.]*?\b(?:they|all|everything)\s+(?:all\s+)?(?:pass|passed|passing)\b',
    re.IGNORECASE)
_RE_SUITE_GREEN = re.compile(r'\bsuite\s+is\s+green\b', re.IGNORECASE)
_RE_PYTEST_PASSES = re.compile(r'\bpytest\s+passes?\b', re.IGNORECASE)
_RE_ALL_GREEN = re.compile(r'\ball\s+green\b', re.IGNORECASE)

# Claim detection: tests_ran (ran the tests/suite without pass assertion)
_RE_TESTS_RAN = re.compile(
    r'\b(ran|re-ran|re?ran|executed)\s+the\s+(tests?|test\s+suite|suite)\b',
    re.IGNORECASE
)

# Claim detection: ran_command (not about tests/migration)
# "I", "I've", "I have" (curly apostrophes are normalized before matching)
_I_SUBJ = r"\bI(?:'ve|\s+have)?\s+"
_RE_RAN_COMMAND = re.compile(
    _I_SUBJ + r'(ran|re-ran|reran|run|re-run|rerun|executed)\s+'
    r'|^\s*(?:ran|re-ran|reran|executed)\s+',   # subjectless past tense at sentence start: "Re-ran the linter."
    re.IGNORECASE
)

# Claim detection: file_written
_RE_FILE_WRITTEN_I = re.compile(
    _I_SUBJ + r'(wrote|written|created|saved|updated)\s+',
    re.IGNORECASE
)
_RE_FILE_WRITTEN_INITIAL = re.compile(
    r'^(?:Created|Wrote|Saved|Updated)\s+',
    re.IGNORECASE
)
_RE_FILE_WRITTEN_PASSIVE = re.compile(
    r'\b(config|config\s+file|script|file)\s+(is|was|has\s+been)\s+(written|created|saved|updated)\b',
    re.IGNORECASE
)

# Claim detection: deployed
_RE_DEPLOYED = re.compile(
    r'\b(?:re)?deployed\b|\bro(lled|lls)\s+out\b|\bpushed\s+to\s+prod(?:uction)?\b'
    r'|\bmigration\s+(applied|ran)\b|\b(ran|re-ran|re?ran)\s+the\s+migration\b'
    r'|\brestarted\s+(the\s+)?service\b|\bservice\s+has\s+been\s+restarted\b',
    re.IGNORECASE
)

# Claim detection: verified
_RE_VERIFIED = re.compile(
    _I_SUBJ + r"(verified|confirmed)\b|\bconfirmed\s+working\b|\bverified\s+that\b",
    re.IGNORECASE
)

# Claim detection: underway
_UNDERWAY_IGNORE = {'nothing', 'something', 'anything', 'everything'}
_RE_UNDERWAY = re.compile(r'\b(\w+)ing(?:\s+(?:it|that|this|them))?(?:\s+off)?\s+now\b', re.IGNORECASE)
_RE_ON_IT_NOW = re.compile(r'\bon\s+it\s+now\b', re.IGNORECASE)
# "X is running now" / "it's working now" describe state, not work the agent just started
_RE_STATE_PREFIX = re.compile(r"(?:\b(?:is|are|was|were|be|been)|'s)\s+$", re.IGNORECASE)

# Claim detection: vague (only if no other kind matched)
_RE_VAGUE = re.compile(
    r"\bthat'?s\s+fixed\b|\bit\s+works\s+now\b|\bit\s+works\b|\bit(?:'s|\s+is)\s+working\b"
    r"|\ball\s+set\b|\beverything\s+is\s+in\s+place\b",
    re.IGNORECASE
)

# Done: whole reply is one of the done phrases
_DONE_PHRASES = re.compile(r'(?:all\s+)?(?:done|complete|completed|finished)', re.IGNORECASE)  # fullmatch only
_DONE_ACTION_VERBS = (
    'run', 'rerun', 'deploy', 'fix', 'write', 'create', 'install', 'restart',
    'build', 'test', 'update', 'delete', 'remove', 'migrate', 'execute',
    'apply', 'push', 'start', 'stop', 'generate'
)
_RE_DONE_ACTION = re.compile(
    r'\b(?:' + '|'.join(re.escape(v) for v in _DONE_ACTION_VERBS) + r')\b',
    re.IGNORECASE
)

# Backtick content extraction
_RE_BACKTICKS = re.compile(r'`([^`]+)`')

# Path-like token: contains "/" or has a dotted extension starting with a letter ("3.11s", "v2.1" are not paths).
# The lookbehind (instead of \b) keeps the leading "/" of absolute paths.
_RE_PATH_TOKEN = re.compile(
    r'(?<![\w~./-])(?:~?/?(?:[\w.-]+/)+[\w.-]+|[\w-][\w.-]*\.[A-Za-z]\w{0,9})(?![\w/-])')


def _strip_tool_output(text: str) -> str:
    """Step 1: remove fenced code, quoted lines, indented tool output."""
    text = _RE_FENCED.sub('', text)
    text = _RE_FENCED_UNTERM.sub('', text)
    text = _RE_QUOTED.sub('', text)
    text = _RE_INDENTED.sub('', text)
    return text


def _protect_backticks(text: str) -> tuple[str, list[str]]:
    """Replace backtick content with placeholders to protect dots inside."""
    contents = []

    def _protect(match):
        contents.append(match.group(1))
        return f'\x00BT{len(contents) - 1}\x00'

    protected = _RE_BACKTICKS.sub(_protect, text)
    return protected, contents


def _restore_backticks(text: str, contents: list[str]) -> str:
    for i, content in enumerate(contents):
        text = text.replace(f'\x00BT{i}\x00', f'`{content}`')
    return text


def _split_sentences(text: str) -> list[str]:
    """Step 2: split into sentences."""
    # Protect backtick content
    protected, bt_contents = _protect_backticks(text)

    sentences = []
    # Split on newlines first
    lines = protected.split('\n')
    for line in lines:
        # Strip bullet markers
        line = _RE_BULLET.sub(' ', line)
        line = _RE_NUMBERED.sub(' ', line)
        line = line.strip()
        if not line:
            continue

        # Now split on sentence-ending punctuation followed by whitespace
        # But avoid splitting on dots in filenames (dot followed by lowercase letter)
        parts = []
        current_start = 0
        for m in _RE_SENT_END.finditer(line):
            punct = m.group(1)
            end = m.end()

            # Check if this is a filename dot: dot followed by lowercase letter
            # (skip backtick placeholders - they don't contain dots)
            if punct == '.':
                # Look at what's after the dot (skip whitespace)
                after = line[end:].lstrip()
                if after and after[0].islower():
                    # Likely a filename extension (e.g., "app.py was updated")
                    # Not a sentence boundary
                    continue

            # This is a real sentence boundary
            part = line[current_start:end].strip()
            if part:
                parts.append(part)
            current_start = end

        # Remaining text after last boundary
        remaining = line[current_start:].strip()
        if remaining:
            parts.append(remaining)

        sentences.extend(parts)

    # Restore backtick content
    for i, content in enumerate(bt_contents):
        sentences = [s.replace(f'\x00BT{i}\x00', f'`{content}`') for s in sentences]

    return [s for s in sentences if s]


def _is_excluded(sentence: str) -> bool:
    """Step 3: check if sentence should be excluded."""
    s = sentence.strip()
    if not s:
        return True

    # Question
    if s.endswith('?'):
        return True

    # Hypothetical/conditional
    if _RE_IF_CLAUSE.search(s) or _RE_ONCE_YOU.search(s) or _RE_WHEN_YOU.search(s):
        return True
    if _RE_AFTER_YOU.search(s) or _RE_UNLESS.search(s):
        return True
    if _RE_TO_START.match(s):
        return True

    # Future intent
    if _RE_I_WILL.search(s) or _RE_NEXT_END.search(s):
        return True

    # Past session
    if _RE_PAST.search(s):
        return True

    # Instruction: bare imperative at start
    if _RE_IMPERATIVE.match(s):
        return True
    if _RE_YOU_INSTRUCTION.search(s):
        return True

    # Negation
    if _RE_NEGATION.search(s):
        return True

    # Failure words (unless preceded by "0 " or "zero ")
    for m in _RE_FAILURE.finditer(s):
        word = m.group(1)
        start = m.start()
        # Check if preceded by "0 " or "zero "
        if start >= 2 and s[start - 2:start] == '0 ':
            continue
        if start >= 5 and s[start - 5:start].lower() == 'zero ':
            continue
        # Not preceded by zero -> this is a failure report, exclude
        return True

    return False


def _extract_target_file(sentence: str) -> str | None:
    """Extract file target: backtick content first, then path-like token."""
    # Backtick content
    bt_match = _RE_BACKTICKS.search(sentence)
    if bt_match:
        target = bt_match.group(1).strip()
        # Strip trailing punctuation
        target = target.rstrip('.,;:)!')
        return target[:200] if target else None

    # Path-like token: prefer one containing "/" (a real path) over a bare dotted name
    tokens = [m.group(0).strip().rstrip('.,;:)!') for m in _RE_PATH_TOKEN.finditer(sentence)]
    tokens = [t for t in tokens if t]
    if tokens:
        target = next((t for t in tokens if "/" in t), tokens[0])
        return target[:200]

    return None


def _extract_target_command(sentence: str) -> str | None:
    """ran_command target: the command in backticks only (a bare path-like word is too weak a signal)."""
    bt_match = _RE_BACKTICKS.search(sentence)
    if bt_match:
        target = bt_match.group(1).strip()
        return target[:200] if target else None
    return None


def _extract_count(sentence: str) -> int | None:
    """Extract test count from sentence."""
    # "17/17 tests pass"
    m = _RE_N_OF_N.search(sentence)
    if m:
        return int(m.group(1))

    # "all 42 tests pass"
    m = _RE_ALL_N_TESTS.search(sentence)
    if m:
        return int(m.group(1))

    # "120 passed"
    m = _RE_N_PASSED.search(sentence)
    if m:
        return int(m.group(1))

    # "17 tests pass"
    m = _RE_N_TESTS_PASS.search(sentence)
    if m:
        return int(m.group(1))

    return None


def _detect_kinds(sentence: str) -> list[str]:
    """Step 4: detect which claim kinds match this sentence."""
    kinds = []

    # tests_pass
    if (_RE_TESTS_PASS_VERBS.search(sentence) or _RE_N_PASSED.search(sentence)
            or _RE_N_OF_N.search(sentence) or _RE_ALL_N_TESTS.search(sentence)
            or _RE_N_TESTS_PASS.search(sentence) or _RE_SUITE_GREEN.search(sentence)
            or _RE_THEY_PASS.search(sentence)
            or _RE_PYTEST_PASSES.search(sentence) or _RE_ALL_GREEN.search(sentence)):
        kinds.append('tests_pass')

    # tests_ran
    if _RE_TESTS_RAN.search(sentence):
        kinds.append('tests_ran')

    # ran_command. When the sentence is a tests/migration claim, subsumption drops ran_command in favor of
    # tests_ran / tests_pass / deployed. Never drop it here: "I ran `make test`" must stay a claim.
    if _RE_RAN_COMMAND.search(sentence):
        kinds.append('ran_command')

    # file_written
    if (_RE_FILE_WRITTEN_I.search(sentence) or _RE_FILE_WRITTEN_INITIAL.match(sentence)
            or _RE_FILE_WRITTEN_PASSIVE.search(sentence)):
        kinds.append('file_written')

    # deployed
    if _RE_DEPLOYED.search(sentence):
        kinds.append('deployed')

    # verified
    if _RE_VERIFIED.search(sentence):
        kinds.append('verified')

    # underway
    for m in _RE_UNDERWAY.finditer(sentence):
        word = (m.group(1) + 'ing').lower()
        if word in _UNDERWAY_IGNORE or _RE_STATE_PREFIX.search(sentence[:m.start()]):
            continue
        kinds.append('underway')
        break
    if 'underway' not in kinds and _RE_ON_IT_NOW.search(sentence):
        kinds.append('underway')

    # vague (only if no other kind matched)
    if not kinds and _RE_VAGUE.search(sentence):
        kinds.append('vague')

    return kinds


def _apply_subsumption(kinds: list[str]) -> list[str]:
    """Step 5: apply subsumption rules within a sentence."""
    result = list(kinds)

    if 'tests_pass' in result:
        # tests_pass removes tests_ran and ran_command
        if 'tests_ran' in result:
            result.remove('tests_ran')
        if 'ran_command' in result:
            result.remove('ran_command')
    elif 'tests_ran' in result:
        # tests_ran removes ran_command
        if 'ran_command' in result:
            result.remove('ran_command')

    if 'deployed' in result and 'ran_command' in result:
        # deployed removes ran_command
        result.remove('ran_command')

    return result


def _check_done(reply: str, user_message: str | None) -> bool:
    """Check if the whole reply is a "done" claim."""
    if not user_message or not isinstance(user_message, str):
        return False

    # user_message must contain an action verb and not end with "?"
    if user_message.rstrip().endswith('?'):
        return False
    if not _RE_DONE_ACTION.search(user_message):
        return False

    # Whole reply (after preprocessing) must BE a done phrase, nothing more
    cleaned = reply.strip().rstrip('.!').strip()
    return bool(_DONE_PHRASES.fullmatch(cleaned))


def extract_claims(reply: str, user_message: str | None = None) -> list[Claim]:
    """Extract claims from an agent reply.

    Args:
        reply: The agent's text reply.
        user_message: The user's message that prompted the reply (for "done" detection).

    Returns:
        List of Claim objects (possibly empty). Low-confidence (vague) claims
        have low=True.
    """
    if not reply or not isinstance(reply, str):
        return []

    # Models often emit curly apostrophes ("I’ve"); every pattern is written for ASCII.
    reply = reply.replace('\u2019', "'").replace('\u2018', "'")

    # Step 1: strip tool output
    cleaned = _strip_tool_output(reply)

    # Check for "done" before sentence splitting (whole-reply check)
    if _check_done(cleaned, user_message):
        text = cleaned.strip().rstrip('.!').strip()
        return [Claim(kind='done', text=text[:200], low=False)]

    # Step 2: split into sentences
    sentences = _split_sentences(cleaned)

    claims: list[Claim] = []
    for sentence in sentences:
        # Step 3: exclusion
        if _is_excluded(sentence):
            continue

        # Step 4: detect kinds
        kinds = _detect_kinds(sentence)

        # Step 5: subsumption
        kinds = _apply_subsumption(kinds)

        # Create claims
        for kind in kinds:
            text = sentence.strip()[:200]
            if kind == 'tests_pass':
                count = _extract_count(sentence)
                claims.append(Claim(kind=kind, text=text, count=count))
            elif kind == 'file_written':
                target = _extract_target_file(sentence)
                claims.append(Claim(kind=kind, text=text, target=target))
            elif kind == 'ran_command':
                claims.append(Claim(kind=kind, text=text, target=_extract_target_command(sentence)))
            elif kind == 'vague':
                claims.append(Claim(kind=kind, text=text, low=True))
            else:
                claims.append(Claim(kind=kind, text=text))

    return claims
