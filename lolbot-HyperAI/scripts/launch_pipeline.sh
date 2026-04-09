#!/usr/bin/env bash
# =============================================================================
# lolbot-HyperAI Pipeline Launch Script
# =============================================================================
#
# 查看 Apollo ``scripts/bootstrap.sh`` 和 ``docker/scripts/dev_start.sh``
# 上现有的启动脚本模式。从 Apollo 的 ``cyber_launch start`` 命令这个好例子
# 开始。然后遵循该模式实现一个 Shell 启动脚本, 让运维人员可以一条命令启动
# 系统并配置运行模式。
#
# Usage:
#   ./scripts/launch_pipeline.sh              # default full pipeline
#   ./scripts/launch_pipeline.sh --mock       # mock mode
#   ./scripts/launch_pipeline.sh --replay f   # replay mode
#   ./scripts/launch_pipeline.sh --diag       # with diagnostics
#
# 位置: lolbot-HyperAI/scripts/launch_pipeline.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  lolbot-HyperAI — Apollo-style Pipeline Launcher${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

cd "${PROJECT_ROOT}"

# ── Ensure log directory ─────────────────────────────────────────────────────
mkdir -p logs/canbus logs/perception logs/prediction logs/planning logs/control \
         logs/monitor logs/metrics

# ── Parse shorthand args → forward to run.py ─────────────────────────────────
ARGS=()

for arg in "$@"; do
    case "$arg" in
        --diag|--diagnostics)
            ARGS+=("--diagnostics")
            ;;
        --debug)
            ARGS+=("--log-level" "DEBUG")
            ;;
        --quiet)
            ARGS+=("--log-level" "WARNING" "--no-console-log")
            ;;
        --dry)
            ARGS+=("--dry-run")
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

# ── Check Python ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: python3 not found${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "  Python: ${GREEN}${PYTHON_VERSION}${NC}"
echo -e "  CWD:    ${PROJECT_ROOT}"
echo -e "  PID:    $$"
echo -e "  Args:   ${ARGS[*]:-<default>}"
echo ""

# ── Launch ───────────────────────────────────────────────────────────────────
exec python3 run.py "${ARGS[@]}"
