# Checkpoint Message Truncation Compatibility Design

## Problem

Checkpoint persistence applies the generic bounded sanitizer to resume messages. When a run has
more than 100 messages, the sanitizer keeps the first 100 records and appends the string
`[TRUNCATED_ITEMS]`. Resume loading requires every item to be a message mapping, so the generated
checkpoint fails with `Invalid checkpoint message record`.

The existing workspace `b95e49d72e31` demonstrates the defect with 100 valid message mappings and
one trailing truncation marker.

## Required behavior

- New checkpoints store at most 100 message mappings and never store a truncation string inside
  the typed `state.messages` list.
- Existing checkpoints ending in the exact legacy marker `[TRUNCATED_ITEMS]` remain resumable; the
  loader removes only that final marker before reconstructing messages.
- The loader continues to reject the marker in any non-final position, any different string, and
  all other invalid message records.
- Generic sanitizer behavior remains unchanged because other persisted collections use its
  truncation marker as an audit signal.
- Resume state keeps the same first-100 retention policy used by the checkpoint that already
  exists. Changing conversation-retention strategy is outside this compatibility fix.

## Implementation

`serialize_resume_state` in `src/miniclaude/core/checkpoint.py` will cap serialized message records
at `MAX_PERSISTED_ITEMS` before the generic sanitizer runs. The cap will reuse the sanitizer's
constant so both limits cannot drift.

`load_resume_inputs` will copy the stored message list, remove an exact final legacy marker when
present, and pass the remaining records through the existing strict `_resume_message` validator.
It will not silently skip arbitrary invalid items.

No existing checkpoint file is edited in place. The current failed checkpoint becomes readable by
the updated loader, while future saves produce a structurally valid message list.

## Tests

- Serialize more than 100 supported messages and assert the result contains exactly 100 mappings
  with no truncation string.
- Load a legacy checkpoint with a final marker and assert it reconstructs the valid messages.
- Place the marker before the final position and assert Resume still raises `ValueError`.
- Run checkpoint-focused tests and the complete offline suite.

## Non-goals

- Increasing checkpoint size limits.
- Changing context compression thresholds.
- Recovering messages already omitted from an old bounded checkpoint.
- Relaxing validation for unsupported message types.
