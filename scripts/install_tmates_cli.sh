#!/usr/bin/env bash

set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found on PATH. Please install Python 3." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${HOME}/bin"
TARGET="${TARGET_DIR}/tmates"

mkdir -p "${TARGET_DIR}"

cat > "${TARGET}" <<EOF
#!/usr/bin/env bash
exec python3 "${ROOT_DIR}/run.py" cli "\$@"
EOF

chmod +x "${TARGET}"

ADDED_PATH=0
ZSHRC="${HOME}/.zshrc"
PATH_LINE='export PATH="$HOME/bin:$PATH"'

if [ -f "${ZSHRC}" ]; then
    if ! grep -Fq "$PATH_LINE" "${ZSHRC}"; then
        printf '\n%s\n' "$PATH_LINE" >> "${ZSHRC}"
        ADDED_PATH=1
    fi
else
    printf '%s\n' "$PATH_LINE" > "${ZSHRC}"
    ADDED_PATH=1
fi

echo "✅ Installed tmates launcher at ${TARGET}"
if [ "${ADDED_PATH}" -eq 1 ]; then
    echo "📌 Added \"export PATH=\"\$HOME/bin:\$PATH\"\" to ~/.zshrc"
    echo "   Run 'source ~/.zshrc' or open a new shell session to pick it up."
fi

echo "Run 'tmates' from any directory to open the CLI."
