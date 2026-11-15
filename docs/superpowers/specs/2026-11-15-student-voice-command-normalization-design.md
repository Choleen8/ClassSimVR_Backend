# Student Voice Command Normalization Design

## Intent

Make voice commands reliable when a registered student's name appears at the beginning, middle, or end of a natural English, Tagalog, or Taglish sentence.

## Goals

- Resolve all current and future students through `StudentRegistry` names and aliases.
- Recognize polite and varied command wording for `STAND`, `SIT`, `RAISE_HAND`, and existing actions.
- Keep physical-only commands deterministic, silent, and free of TTS.
- Preserve microphone endpoints, response JSON, dynamic registry, and Unreal integration.

## Non-goals

- No Unreal Engine or Blueprint changes.
- No AI provider call for a physical-only command.
- No hardcoded Maria/Robert routing rules.

## Approach

Use one normalized text path before matching. The registry will match aliases against normalized word boundaries anywhere in the transcript, while the behavior intent classifier will recognize English, Tagalog, and Taglish phrase families after removing polite filler. The existing router will continue to use the registry result and the existing response model.

## Constraints

- Existing endpoints and JSON fields remain unchanged.
- `speak=false` and `audio_url=null` remain mandatory for physical-only commands.
- Matching must work for every registered profile, not only default students.

## Verification

Automated tests will cover name-first, command-first, polite, Tagalog/Taglish, aliases, all supported physical actions, and dynamic registered students.
