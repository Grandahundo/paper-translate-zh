#!/usr/bin/env bash
# 一次性环境准备：Python 虚拟环境 + 依赖 + 中文字体抽取 + 检查 tectonic
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
echo "Python 依赖就绪：$ROOT/.venv"

if ! command -v tectonic >/dev/null 2>&1; then
  echo "未找到 tectonic，请先安装：brew install tectonic" >&2
else
  echo "tectonic: $(tectonic --version)"
fi
command -v pdftoppm >/dev/null 2>&1 || echo "（可选）未找到 poppler：brew install poppler" >&2

# 抽取中文字体（macOS 自带宋体）。使用任一 project.json 即可，字体放到其 fonts_dir。
if [ -n "${1:-}" ]; then
  "$ROOT/.venv/bin/python" "$HERE/pdftr.py" -c "$1" fonts
fi
