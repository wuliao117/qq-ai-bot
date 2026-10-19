@echo off
chcp 65001 >nul
set "SCRIPTS=%~dp0"
set "ROOT=%~dp0.."
cd /d "%ROOT%"

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 找不到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)

if not exist "%ROOT%\bot\config.json" (
  echo [提示] 未找到 bot\config.json，已从 config.example.json 复制一份
  copy "%ROOT%\bot\config.example.json" "%ROOT%\bot\config.json" >nul
  echo        请编辑 bot\config.json 填入 llm.api_key 后重新启动。
)

if not exist "%ROOT%\NapCat.Shell\NapCatWinBootMain.exe" (
  echo [错误] 未找到 NapCat.Shell，请先按 README 第 1 步安装 NapCat
  pause
  exit /b 1
)

echo 启动机器人...
start "QQ-AI-Bot" /D "%ROOT%\bot" cmd /k python main.py
timeout /t 2 >nul
echo 启动 NapCat...
start "NapCat" /D "%SCRIPTS%" cmd /k start_napcat.cmd
echo.
echo 两个窗口已打开。首次使用请扫码登录:
echo   http://127.0.0.1:6099/webui
echo.
timeout /t 5 >nul
