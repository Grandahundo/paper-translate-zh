#!/usr/bin/env bash
# 把本技能链接到 Claude Code / Codex 的 skills 目录
#   scripts/install.sh            两者都装
#   scripts/install.sh claude     只装 Claude Code（~/.claude/skills）
#   scripts/install.sh codex      只装 Codex（~/.codex/skills）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
targets=("${1:-all}")
link() { mkdir -p "$1"; ln -sfn "$ROOT" "$1/paper-translate-zh"; echo "已安装 → $1/paper-translate-zh"; }
case "${targets[0]}" in
  claude) link "$HOME/.claude/skills" ;;
  codex)  link "$HOME/.codex/skills" ;;
  all)    link "$HOME/.claude/skills"; link "$HOME/.codex/skills" ;;
  *) echo "用法：$0 [claude|codex|all]"; exit 1 ;;
esac
"$ROOT/scripts/setup.sh"
