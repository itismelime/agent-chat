#!/usr/bin/env bash
# Install agent-chat for the current user (Linux with systemd). Safe to rerun.
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove; chats stay in ~/.local/share/agent-chat
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
bin=$HOME/.local/bin
unit=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/agent-chat.service
port=${AGENT_CHAT_PORT:-8765}
say() { printf '  %s\n' "$*"; }
# ~/.local/bin/chat may be replaced if it is missing, a dangling link, or ours
replaceable() {
    [[ ! -e $1 ]] && return 0
    [[ -L $1 ]] && [[ -f $(dirname "$(readlink -f "$1")")/../agentchat/store.py ]]
}

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
if replaceable "$bin/chat"; then
    ln -sfn "$here/bin/chat" "$bin/chat"
    say "linked $bin/chat"
else
    say "warning: $bin/chat is another program; left it alone (run $here/bin/chat instead)"
fi
case ":$PATH:" in *":$bin:"*) ;; *) say "warning: $bin is not on your PATH" ;; esac

cat >"$unit" <<EOF
[Unit]
Description=agent-chat service (127.0.0.1:$port)

[Service]
Environment=AGENT_CHAT_PORT=$port
ExecStart=/usr/bin/env python3 "$here/bin/chat" serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable agent-chat
systemctl --user restart agent-chat
up=
for _ in $(seq 20); do
    if python3 - "$port" 2>/dev/null <<'PY'
import sys, urllib.request
urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/projects" % sys.argv[1], headers={"X-Agent-Chat": "1"}), timeout=2)
PY
    then up=1; break; fi
    sleep 0.5
done
if [[ -z $up ]]; then
    echo "agent-chat did not start on port $port (is something else using it?);" \
         "see: journalctl --user -u agent-chat" >&2
    exit 1
fi
say "service agent-chat running on http://127.0.0.1:$port"

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
if command -v codex >/dev/null &&
    ! grep -qs '^network_access *= *true' "${CODEX_HOME:-$HOME/.codex}/config.toml"; then
    say "note: Codex can only run 'chat wait' with network access in its sandbox."
    say "      Add to ${CODEX_HOME:-$HOME/.codex}/config.toml:"
    say "        [sandbox_workspace_write]"
    say "        network_access = true"
fi
echo "Done. Open http://127.0.0.1:$port and add a project, or run: chat add <folder>"
