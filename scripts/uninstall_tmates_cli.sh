#!/usr/bin/env bash

set -euo pipefail

TARGET="${HOME}/bin/tmates"
ZSHRC="${HOME}/.zshrc"
PATH_LINE='export PATH="$HOME/bin:$PATH"'

REMOVED_LAUNCHER=0
REMOVED_PATH_LINE=0

if [ -f "${TARGET}" ]; then
    rm -f "${TARGET}"
    echo "🗑️  Removed tmates launcher at ${TARGET}"
    REMOVED_LAUNCHER=1
else
    echo "ℹ️  No tmates launcher found at ${TARGET}"
fi

if [ -f "${ZSHRC}" ] && grep -Fq "${PATH_LINE}" "${ZSHRC}"; then
    # On macOS, use sed -i '' for in-place editing; fall back to tmp file otherwise.
    if sed --version >/dev/null 2>&1; then
        sed -i "" "s|^${PATH_LINE}$||" "${ZSHRC}"
        sed -i "" '/^$/N;/^\n$/D' "${ZSHRC}" 2>/dev/null || true
    else
        tmp_file="${ZSHRC}.tmp"
        sed "s|^${PATH_LINE}$||" "${ZSHRC}" > "${tmp_file}"
        mv "${tmp_file}" "${ZSHRC}"
    fi
    REMOVED_PATH_LINE=1
fi

if [ "${REMOVED_LAUNCHER}" -eq 0 ] && [ "${REMOVED_PATH_LINE}" -eq 0 ]; then
    echo "ℹ️  Nothing to uninstall."
fi

if [ "${REMOVED_PATH_LINE}" -eq 1 ]; then
    echo "📌 Removed \"${PATH_LINE}\" from ~/.zshrc"
    echo "   Open a new shell or run 'source ~/.zshrc' to refresh your PATH."
fi

if [ "${REMOVED_LAUNCHER}" -eq 1 ]; then
    echo "✅ tmates CLI uninstalled."
fi
