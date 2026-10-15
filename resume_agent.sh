#!/usr/bin/env bash
# 恢复运行：删除 INTERRUPT 文件
rm -f "$(dirname "$0")/INTERRUPT"
echo "[OK] 打断已清除，恢复正常"
