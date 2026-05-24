#!/usr/bin/env bash
set -euo pipefail

FUNC="${1:?Usage: $0 <function-name>}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/src/${FUNC//-/_}"
STAGING="${REPO_ROOT}/dist/${FUNC}"
ZIP_OUT="${REPO_ROOT}/dist/${FUNC}.zip"

if [[ ! -d "${SRC_DIR}" ]]; then
  echo "ERROR: source directory not found: ${SRC_DIR}" >&2
  exit 1
fi

rm -rf "${STAGING}"
mkdir -p "${STAGING}"

# Copy handler
cp "${SRC_DIR}/handler.py" "${STAGING}/handler.py"

# Copy function package for intra-package imports (e.g. from fins_dispatcher.service import ...)
cp -r "${SRC_DIR}" "${STAGING}/${FUNC}"

# Vendor shared package
cp -r "${REPO_ROOT}/src/shared/shared" "${STAGING}/shared"

# Install function-specific deps (skip if group has no packages)
# Filter index-URL lines (-i ...) and blank lines; DEPS is empty when group has no packages
DEPS=$(cd "${REPO_ROOT}" && uv export --only-group "${FUNC}" --no-hashes 2>/dev/null \
  | grep -Ev '^(-i |#|[[:space:]]*$)' || true)
if [[ -n "${DEPS}" ]]; then
  echo "${DEPS}" | uv pip install --system --no-cache --target "${STAGING}" -r /dev/stdin
fi

# Remove dotfiles created by uv (e.g. .lock)
find "${STAGING}" -name ".*" -delete

# Create ZIP
rm -f "${ZIP_OUT}"
(cd "${STAGING}" && zip -qr "${ZIP_OUT}" .)

echo "Built: ${ZIP_OUT}"
