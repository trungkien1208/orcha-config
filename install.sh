#!/bin/sh
# orcha in one line (macOS):
#   curl -fsSL https://raw.githubusercontent.com/trungkien1208/orcha/main/install.sh | sh
# Clones orcha to ~/.orcha (or $ORCHA_DIR), or updates it, then starts `orcha setup`.
set -e
DIR=${ORCHA_DIR:-$HOME/.orcha}
[ "$(uname)" = Darwin ] || { echo "orcha is macOS-only for now."; exit 1; }
command -v git >/dev/null || { echo "orcha needs git first: xcode-select --install"; exit 1; }
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only --quiet
else
  git clone --depth 1 --quiet https://github.com/trungkien1208/orcha "$DIR"
fi
# setup asks questions: read the answers from the terminal, not from this pipe
exec "$DIR/bin/orcha" setup < /dev/tty
