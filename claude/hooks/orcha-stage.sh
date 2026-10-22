#!/bin/sh
# Tell herdr which station the agent's train is at (the orcha sidebar draws it).
# Claude Code and Codex both run it (same hook format):
#   UserPromptSubmit, reading / searching   -> thinking
#   editing / writing files                 -> coding
#   running tests, typecheck, lint          -> testing
# Reported as the pane's `stage` token; anything else leaves it as it was.
# On a new prompt it also reports the `model` token (the train's name, e.g.
# claude-opus-5-5), read from the session's newest assistant message.
# Runs on every tool call, so it stays tiny and never blocks Claude.
[ -n "$HERDR_PANE_ID" ] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
input=$(cat)
event=$(printf '%s' "$input" | jq -r '.hook_event_name // ""')
tool=$(printf '%s' "$input" | jq -r '.tool_name // ""')
stage=""
case $event in
  SessionStart|UserPromptSubmit)
    [ "$event" = UserPromptSubmit ] && stage=thinking
    model=$(printf '%s' "$input" | jq -r '.model // ""')
    transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
    # Codex: the model in its config when the hook does not say
    case $transcript in
      */.codex/*) [ -n "$model" ] || model=$(sed -n 's/^model *= *"\(.*\)"/\1/p' "${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null | head -1) ;;
    esac
    if [ -z "$model" ]; then
      [ -f "$transcript" ] && model=$(tail -n 400 "$transcript" | grep -o '"model":"[a-z0-9.-]*' | tail -1 | cut -d'"' -f4)
    fi
    [ -n "$model" ] && herdr pane report-metadata "$HERDR_PANE_ID" --source orcha-model --token "model=$model" >/dev/null 2>&1 &
    # a Claude session on another account comes back on that account after a
    # restart: report the resume command with its CLAUDE_CONFIG_DIR
    session=$(printf '%s' "$input" | jq -r '.session_id // ""')
    case $transcript in */.codex/*) ;; *)
      if [ "$event" = SessionStart ] && [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ -n "$session" ]; then
        # herdr keeps a resume command only from the integration that holds the
        # pane (herdr:claude), after its own session report: wait for that first
        ( sleep 3; herdr pane report-agent-session "$HERDR_PANE_ID" --source herdr:claude --agent claude \
          --agent-session-id "$session" -- env "CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR" claude --resume "$session" ) >/dev/null 2>&1 &
      fi ;;
    esac
    ;;
  PreToolUse)
    case $tool in
      Read|Grep|Glob|LS|WebFetch|WebSearch|Task|Agent|TodoWrite) stage=thinking ;;
      Edit|Write|MultiEdit|NotebookEdit|apply_patch|edit|write_file) stage=coding ;;
      Bash|exec|shell|exec_command|local_shell)
        # Claude: a string; Codex: a string or an argv array
        cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // .tool_input.cmd // "" | if type == "array" then join(" ") else . end')
        # Codex reads files through the shell too; Claude's other commands keep the stage
        [ "$tool" = Bash ] || stage=thinking
        if printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_])(test|tests|jest|vitest|pytest|mocha|tsc|typecheck|lint|eslint|clippy|nextest|rspec)([^[:alnum:]_]|$)|cargo (test|check)|go (test|vet)'; then
          stage=testing
        fi ;;
    esac ;;
esac
[ -n "$stage" ] || exit 0
herdr pane report-metadata "$HERDR_PANE_ID" --source orcha-stage --token "stage=$stage" >/dev/null 2>&1 &
exit 0
