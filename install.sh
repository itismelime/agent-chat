#!/usr/bin/env bash
# Install agent-chat for the current user (Linux with systemd). Safe to rerun.
#   ./install.sh                     install or update
#   ./install.sh --ollama-url <url>  use an existing Ollama (http://127.0.0.1:<port>),
#                                    or "own" for agent-chat's own
#   ./install.sh --uninstall         remove; chats and models stay in ~/.local/share/agent-chat
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
bin=$HOME/.local/bin
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
unit=$units/agent-chat.service
ollama_unit=$units/agent-chat-ollama.service
data=${XDG_DATA_HOME:-$HOME/.local/share}/agent-chat
port=${AGENT_CHAT_PORT:-8765}
runtime=${AGENT_CHAT_RUNTIME:-$here/runtime}
ollama_version=v0.34.2
ollama_sha256=${AGENT_CHAT_OLLAMA_SHA256:-e155b83589986d2c581fdbf1381ea3ebdb16549883679cd5a0627f7cdc05b12b}
ollama_download=${AGENT_CHAT_OLLAMA_DOWNLOAD:-https://github.com/ollama/ollama/releases/download/$ollama_version/ollama-linux-amd64.tar.zst}
ollama_port=${AGENT_CHAT_OLLAMA_PORT:-11436}
say() { printf '  %s\n' "$*"; }
usage() { echo "usage: ./install.sh [--uninstall | --ollama-url <url>|own]" >&2; exit 2; }
# ~/.local/bin/chat may be replaced if it is missing, a dangling link, or ours
replaceable() {
    [[ ! -e $1 ]] && return 0
    [[ -L $1 ]] && [[ -f $(dirname "$(readlink -f "$1")")/../agentchat/store.py ]]
}
wait_for() {  # url [tries]: poll every 0.5 s
    for _ in $(seq "${2:-20}"); do
        if python3 - "$1" 2>/dev/null <<'PY'
import sys, urllib.request
urllib.request.urlopen(urllib.request.Request(sys.argv[1], headers={"X-Agent-Chat": "1"}), timeout=2)
PY
        then return 0; fi
        sleep 0.5
    done
    return 1
}

mode=install url=
case ${1:-} in
    "") ;;
    --uninstall) mode=uninstall ;;
    --ollama-url) [[ -n ${2:-} ]] || usage; url=$2 ;;
    *) usage ;;
esac

if [[ $mode == uninstall ]]; then
    for u in agent-chat agent-chat-ollama; do
        systemctl --user disable --now "$u" >/dev/null 2>&1 || true
    done
    rm -f "$unit" "$ollama_unit"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    rm -rf "$runtime/ollama" "$runtime/ollama.new"
    if [[ $(readlink "$bin/chat" || true) == "$here/bin/chat" ]]; then rm "$bin/chat"; fi
    if command -v claude >/dev/null; then claude mcp remove --scope user agent-chat >/dev/null 2>&1 || true; fi
    if command -v codex >/dev/null; then codex mcp remove agent-chat >/dev/null 2>&1 || true; fi
    echo "agent-chat removed; chats and models kept in $data"
    exit 0
fi

echo "Installing agent-chat from $here"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
    { echo "agent-chat needs python3 3.9 or newer" >&2; exit 1; }
mkdir -p "$bin" "$units"
if replaceable "$bin/chat"; then
    ln -sfn "$here/bin/chat" "$bin/chat"
    say "linked $bin/chat"
else
    say "warning: $bin/chat is another program; left it alone (run $here/bin/chat instead)"
fi
case ":$PATH:" in *":$bin:"*) ;; *) say "warning: $bin is not on your PATH" ;; esac

# Ollama: agent-chat's own, or the one the setting names
if [[ -n $url ]]; then
    AGENT_CHAT_OLLAMA_PORT=$ollama_port "$here/bin/chat" config ollama-url "$url" || exit 2
fi
in_use=$(AGENT_CHAT_OLLAMA_PORT=$ollama_port "$here/bin/chat" config ollama-url)
if [[ $in_use == "http://127.0.0.1:$ollama_port" ]]; then
    if [[ $(cat "$runtime/ollama/.version" 2>/dev/null) == "$ollama_version" ]]; then
        say "Ollama $ollama_version already installed"
    else
        for tool in curl tar zstd sha256sum; do
            command -v "$tool" >/dev/null || { echo "installing Ollama needs $tool" >&2; exit 1; }
        done
        say "downloading Ollama $ollama_version (about 1.4 GB)"
        mkdir -p "$runtime"
        tarball=$runtime/ollama.tar.zst.part
        curl -fL --progress-bar -o "$tarball" "$ollama_download"
        if [[ $(sha256sum "$tarball" | cut -d' ' -f1) != "$ollama_sha256" ]]; then
            rm -f "$tarball"
            echo "the Ollama download failed its checksum; nothing installed" >&2
            exit 1
        fi
        rm -rf "$runtime/ollama.new"
        mkdir -p "$runtime/ollama.new"
        tar --zstd -xf "$tarball" -C "$runtime/ollama.new"
        rm -f "$tarball"
        echo "$ollama_version" >"$runtime/ollama.new/.version"
        rm -rf "$runtime/ollama"
        mv "$runtime/ollama.new" "$runtime/ollama"
        say "unpacked Ollama to $runtime/ollama"
    fi
    mkdir -p "$data/ollama-models"
    cat >"$ollama_unit" <<EOF
[Unit]
Description=agent-chat's Ollama (127.0.0.1:$ollama_port)

[Service]
ExecStart="$runtime/ollama/bin/ollama" serve
Environment=OLLAMA_HOST=127.0.0.1:$ollama_port
Environment="OLLAMA_MODELS=$data/ollama-models"
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_CONTEXT_LENGTH=32768
Environment=OLLAMA_KEEP_ALIVE=5m
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=60

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable agent-chat-ollama
    systemctl --user restart agent-chat-ollama
    wait_for "http://127.0.0.1:$ollama_port/api/version" 60 ||
        { echo "agent-chat's Ollama did not start; see: journalctl --user -u agent-chat-ollama" >&2; exit 1; }
    say "Ollama running on http://127.0.0.1:$ollama_port"
else
    systemctl --user disable --now agent-chat-ollama >/dev/null 2>&1 || true
    rm -f "$ollama_unit"
    say "Ollama: using $in_use (agent-chat's own is not installed)"
fi

cat >"$unit" <<EOF
[Unit]
Description=agent-chat service (127.0.0.1:$port)

[Service]
Environment=AGENT_CHAT_PORT=$port
Environment=AGENT_CHAT_OLLAMA_PORT=$ollama_port
Environment=PATH=$PATH
ExecStart=/usr/bin/env python3 "$here/bin/chat" serve
Restart=on-failure
RestartSec=5
# agents started from the page live in tmux servers this service may start;
# a restart must stop only the service, not them
KillMode=process

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable agent-chat
systemctl --user restart agent-chat
wait_for "http://127.0.0.1:$port/api/projects" ||
    { echo "agent-chat did not start on port $port (is something else using it?);" \
           "see: journalctl --user -u agent-chat" >&2; exit 1; }
say "service agent-chat running on http://127.0.0.1:$port"

for tool in claude codex; do
    if ! command -v "$tool" >/dev/null; then say "$tool not found: skipped"; continue; fi
    # re-register every time: a moved clone leaves the old path behind
    if [[ $tool == claude ]]; then
        claude mcp remove --scope user agent-chat >/dev/null 2>&1 || true
        claude mcp add --scope user agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "claude: registered agent-chat for all your sessions"
    else
        codex mcp remove agent-chat >/dev/null 2>&1 || true
        codex mcp add agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "codex: registered agent-chat for all your sessions"
    fi
done
echo "Done. Open http://127.0.0.1:$port and add a project, or run: chat add <folder>"
