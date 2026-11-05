# Say-Do Gate

Agents say they ran the tests. Sometimes they didn't. Say-Do Gate checks every final reply against what actually ran in the turn and flags the gap.

It is a Hermes plugin. Deterministic. No model call, zero tokens.

```
All tests pass.

[say-do] Claimed: tests pass. Ledger: 0 test commands run.
```

## Quickstart

```bash
# once the catalog entry lands:
hermes plugins install say-do-gate --enable
# until then, from the repo:
hermes plugins install jpaulupshaw/hermes-say-do-gate --enable
hermes config set plugins.entries.say-do-gate.settings.mode annotate
hermes
```

## What it checks

Every tool call lands in a per-session ledger: command, exit code, files written, output tail. When the agent finishes a reply, the gate pulls the claims out of it and matches each one against the ledger.

| Claim | Backed by |
|---|---|
| tests pass / N passed | a test command that exited 0, with a matching count |
| I ran `X` | a terminal command containing `X` |
| wrote `file` | a `write_file` or `patch` on that path that succeeded |
| deployed / migrated / restarted | a deploy, migration, or service command |
| verified / confirmed | any successful tool call |
| running it now / done | at least one tool call this turn |

Severity:
- **High.** The ledger contradicts the claim. Tests exited 1, the count is wrong, the write failed.
- **Medium.** Nothing in the ledger backs the claim.
- **Low.** Can't be checked deterministically. Logged, never shown.

Claims inside code blocks, quoted output, hypotheticals, instructions, questions, negations, and references to past sessions are ignored.

The ledger keeps the last 3 turns. A recap of real work from the previous turn passes. A "tests pass" after the last real run failed is High.

## Modes

| | `observe` (default) | `annotate` |
|---|---|---|
| Live streaming | Kept | Lost on every turn |
| What you see | Nothing changes | Footer under flagged replies |
| What the agent sees | A one-line correction at the start of the next turn | The footer, in its own history |

`annotate` holds the reply until the gate has checked it. Streaming and interim commentary are not shown on held turns. That is the price of catching the claim before you read it.

`observe` never touches the reply. The next turn starts with a short note to the agent: what it claimed, what the ledger shows, verify or correct. It fires once.

## Failure behavior

Every hook fails open. If the gate errors, the reply goes through unchanged and the error type is logged. The gate never logs reply text, commands, tool output, or user messages. Only claim kind, severity, and label.

## Limitations

- **Interim commentary.** Only the final reply is checked. Text between tool calls is not.
- **Prior-turn work.** Work older than 3 turns, or from another session, does not count as evidence.
- **Unsupported shell forms.** Heredocs, `tee`, and redirects that write files count as unverifiable, not backed. Test runners outside the supported list are not recognized.
- **External work.** CI runs, deploys from another machine, or anything without a tool receipt in this session cannot back a claim.
- **The gate flags; it does not force a redo.** Hermes plugins cannot make the agent re-run a turn. `observe` mode asks on the next turn.

## Results

386 tests pass. Six frozen oracles (written before the implementation) guard the claim taxonomy, ledger semantics, matcher verdicts, gate wiring, and end-to-end behavior through real upstream Hermes plugin discovery and the real reply finalizer.

Replay: 109 final replies harvested from 124 real prior sessions. 9 contradicted-claim flags; a manual audit of all 9 found 0 false positives. The corpus contained no claim the gate should have flagged but missed, so recall is reported as untested rather than claimed.

Measured on a private corpus of local Hermes sessions. No session text is published. Test fixtures in this repo are invented.

## Development

```bash
python -m pytest
```

Integration tests load the plugin through real upstream Hermes plugin discovery and the real reply finalizer.
