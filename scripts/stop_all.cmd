@echo off
chcp 65001 >nul
echo 正在停止 QQ 协议端与机器人...

taskkill /f /im NapCatWinBootMain.exe >nul 2>&1
taskkill /f /im QQ.exe >nul 2>&1

rem 结束监听 3002 的机器人进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3002" ^| findstr "LISTENING"') do (
  taskkill /f /pid %%a >nul 2>&1
)

echo 已停止。
timeout /t 2 >nul
