#!/bin/sh
# Tell herdr which station the agent's train is at (the orcha sidebar draws it):
#   UserPromptSubmit, reading / searching   -> thinking
#   editing / writing files                 -> coding
#   running tests, typecheck, lint          -> testing
# Reported as the pane's `stage` token; anything else leaves it as it was.
# Runs on every tool call, so it stays tiny and never blocks Claude.
[ -n "$HERDR_PANE_ID" ] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
input=$(cat)
event=$(printf '%s' "$input" | jq -r '.hook_event_name // ""')
tool=$(printf '%s' "$input" | jq -r '.tool_name // ""')
stage=""
case $event in
  UserPromptSubmit) stage=thinking ;;
  PreToolUse)
    case $tool in
      Read|Grep|Glob|LS|WebFetch|WebSearch|Task|Agent|TodoWrite) stage=thinking ;;
      Edit|Write|MultiEdit|NotebookEdit) stage=coding ;;
      Bash)
        cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
        if printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_])(test|tests|jest|vitest|pytest|mocha|tsc|typecheck|lint|eslint|clippy|nextest|rspec)([^[:alnum:]_]|$)|cargo (test|check)|go (test|vet)'; then
          stage=testing
        fi ;;
    esac ;;
esac
[ -n "$stage" ] || exit 0
herdr pane report-metadata "$HERDR_PANE_ID" --source orcha-stage --token "stage=$stage" >/dev/null 2>&1 &
exit 0
