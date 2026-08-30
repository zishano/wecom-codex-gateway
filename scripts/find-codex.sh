#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${CODEX_BIN:-}" ]]; then
  if [[ -x "${CODEX_BIN}" ]]; then
    printf '%s\n' "${CODEX_BIN}"
    exit 0
  fi
  if resolved_bin="$(command -v "${CODEX_BIN}" 2>/dev/null)"; then
    printf '%s\n' "${resolved_bin}"
    exit 0
  fi
fi

if resolved_bin="$(command -v codex 2>/dev/null)"; then
  printf '%s\n' "${resolved_bin}"
  exit 0
fi

extension_roots=(
  "${HOME}/.vscode-server/extensions"
  "${HOME}/.vscode/extensions"
  "${HOME}/.cursor-server/extensions"
  "${HOME}/.cursor/extensions"
)
existing_roots=()
for root in "${extension_roots[@]}"; do
  [[ -d "${root}" ]] && existing_roots+=("${root}")
done

if ((${#existing_roots[@]})); then
  mapfile -t plugin_bins < <(
    find "${existing_roots[@]}" \
      -type f \
      -path '*/openai.chatgpt-*/bin/linux-*/codex' \
      -perm -u+x \
      -print 2>/dev/null | sort -V
  )
  if ((${#plugin_bins[@]})); then
    printf '%s\n' "${plugin_bins[-1]}"
    exit 0
  fi
fi

cat >&2 <<'EOF'
Codex CLI was not found in PATH or a supported editor extension directory.
Install it on Linux with:
  curl -fsSL https://chatgpt.com/codex/install.sh | sh
Then open a new terminal, run `codex`, and sign in with ChatGPT.
EOF
exit 1
