#!/usr/bin/env bash
# One-line installer for the Live Stream Auto-Clipper.
#
#   curl -fsSL https://raw.githubusercontent.com/Ramakm/clipping/main/install.sh | bash
#
# Installs the tool into ~/.clipper, a `clipper` command into ~/.local/bin, the
# /clip skill for Claude Code and the /prompts:clip prompt for Codex.
# Re-run any time to update. Env overrides: CLIPPER_HOME, CLIPPER_REPO, CLIPPER_BRANCH.
set -euo pipefail

REPO="${CLIPPER_REPO:-https://github.com/Ramakm/clipping}"
BRANCH="${CLIPPER_BRANCH:-main}"
HOME_DIR="${CLIPPER_HOME:-$HOME/.clipper}"
BIN_DIR="$HOME/.local/bin"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null || die "git is required"

# --- Python 3.11+ -----------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -n "$PY" ] || die "Python 3.11+ is required (macOS: brew install python@3.12, Ubuntu: sudo apt install python3.12)"

# --- ffmpeg ----------------------------------------------------------------
FFMPEG_DIR=""
if command -v brew >/dev/null && [ -x "$(brew --prefix)/opt/ffmpeg-full/bin/ffmpeg" ]; then
  FFMPEG_DIR="$(brew --prefix)/opt/ffmpeg-full/bin"   # keg-only full build with libass
elif ! command -v ffmpeg >/dev/null; then
  warn "ffmpeg not found — install it before clipping:"
  warn "  macOS: brew install ffmpeg-full   Ubuntu: sudo apt install ffmpeg   Windows: winget install Gyan.FFmpeg"
fi
FF="${FFMPEG_DIR:+$FFMPEG_DIR/}ffmpeg"
if command -v "$FF" >/dev/null && ! "$FF" -hide_banner -filters 2>/dev/null | grep -q ' ass '; then
  warn "your ffmpeg has no libass: clips will work, captions will be saved as .ass files instead of burned in"
  command -v brew >/dev/null && warn "  fix: brew install ffmpeg-full  (then re-run this installer)"
fi

# --- code ------------------------------------------------------------------
if [ -d "$HOME_DIR/.git" ]; then
  say "updating $HOME_DIR"
  git -C "$HOME_DIR" fetch -q origin "$BRANCH"
  git -C "$HOME_DIR" checkout -q "$BRANCH"
  git -C "$HOME_DIR" reset -q --hard "origin/$BRANCH"
else
  say "downloading to $HOME_DIR"
  git clone -q --depth 1 --branch "$BRANCH" "$REPO" "$HOME_DIR"
fi

# --- python deps -------------------------------------------------------------
say "installing Python packages (first time takes a few minutes)"
if command -v uv >/dev/null; then
  uv venv -q --allow-existing -p "$PY" "$HOME_DIR/.venv"
  uv pip install -q -p "$HOME_DIR/.venv/bin/python" -r "$HOME_DIR/requirements.txt"
else
  "$PY" -m venv "$HOME_DIR/.venv"
  "$HOME_DIR/.venv/bin/python" -m pip install -q --upgrade pip
  "$HOME_DIR/.venv/bin/python" -m pip install -q -r "$HOME_DIR/requirements.txt"
fi

# --- launcher ---------------------------------------------------------------
mkdir -p "$HOME_DIR/bin" "$BIN_DIR"
cat > "$HOME_DIR/bin/clipper" <<EOF
#!/usr/bin/env bash
${FFMPEG_DIR:+export PATH="$FFMPEG_DIR:\$PATH"}
exec "$HOME_DIR/.venv/bin/python" "$HOME_DIR/clipper.py" "\$@"
EOF
chmod +x "$HOME_DIR/bin/clipper"
ln -sf "$HOME_DIR/bin/clipper" "$BIN_DIR/clipper"

# --- agent integrations -----------------------------------------------------
mkdir -p "$HOME/.claude/skills/clip"
cp "$HOME_DIR/skills/clip/SKILL.md" "$HOME/.claude/skills/clip/SKILL.md"
say "Claude Code: /clip installed"
mkdir -p "$HOME/.codex/prompts"
cp "$HOME_DIR/integrations/codex/clip.md" "$HOME/.codex/prompts/clip.md"
say "Codex: /prompts:clip installed"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH — add this to your shell profile:"
     warn "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

say "done! Try it:"
echo "    clipper https://twitch.tv/<channel> --now --clip-length 30"
echo "    or in Claude Code:  /clip https://twitch.tv/<channel>"
echo "    or in Codex:        /prompts:clip https://twitch.tv/<channel>"
