#!/usr/bin/env bash
# 人为打断：创建 INTERRUPT 文件，批量任务/MCP 操作会在下一步停下
echo "$(date '+%F %T') manual stop" > "$(dirname "$0")/INTERRUPT"
echo "[OK] 打断开关已设置，任务将在下一步停止"
