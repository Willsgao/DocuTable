@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================
echo   银行年报PDF解析工具
echo ====================================
echo.
echo 正在启动...
python main.py
pause
