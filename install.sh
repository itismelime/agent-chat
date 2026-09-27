#!/usr/bin/env bash
# Install agent-chat for the current user (Linux with systemd). Safe to rerun.
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove; chats stay in ~/.local/share/agent-chat
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
bin=$HOME/.local/bin
unit=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/agent-chat.service
say() { printf '  %s\n' "$*"; }

if [[ ${1:-} == --uninstall ]]; then
    systemctl --user disable --now agent-chat >/dev/null 2>&1 || true
    rm -f "$unit"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    if [[ $(readlink "$bin/chat" || true) == "$here/bin/chat" ]]; then rm "$bin/chat"; fi
    if command -v claude >/dev/null; then claude mcp remove --scope user agent-chat >/dev/null 2>&1 || true; fi
    if command -v codex >/dev/null; then codex mcp remove agent-chat >/dev/null 2>&1 || true; fi
    echo "agent-chat removed; chats kept in ${XDG_DATA_HOME:-$HOME/.local/share}/agent-chat"
    exit 0
fi
[[ -z ${1:-} ]] || { echo "usage: ./install.sh [--uninstall]" >&2; exit 2; }

echo "Installing agent-chat from $here"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
    { echo "agent-chat needs python3 3.9 or newer" >&2; exit 1; }

mkdir -p "$bin" "$(dirname "$unit")"
ln -sfn "$here/bin/chat" "$bin/chat"
say "linked $bin/chat"
case ":$PATH:" in *":$bin:"*) ;; *) say "warning: $bin is not on your PATH" ;; esac

cat >"$unit" <<EOF
[Unit]
Description=agent-chat service (127.0.0.1:8765)

[Service]
ExecStart=/usr/bin/env python3 "$here/bin/chat" serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable agent-chat
systemctl --user restart agent-chat
say "service agent-chat running on http://127.0.0.1:8765"

for tool in claude codex; do
    if ! command -v "$tool" >/dev/null; then say "$tool not found: skipped"; continue; fi
    if "$tool" mcp get agent-chat >/dev/null 2>&1; then
        say "$tool: agent-chat already registered"
    elif [[ $tool == claude ]]; then
        claude mcp add --scope user agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "claude: registered agent-chat for all your sessions"
    else
        codex mcp add agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "codex: registered agent-chat for all your sessions"
    fi
done
echo "Done. Open http://127.0.0.1:8765 and add a project, or run: chat add <folder>"
