@echo off
rem 恢复运行：删除 INTERRUPT 文件
del /q "%~dp0INTERRUPT" 2>nul
echo [OK] 打断已清除，恢复正常
pause
