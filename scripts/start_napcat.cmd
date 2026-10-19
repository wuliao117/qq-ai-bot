@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "SCRIPTS=%~dp0"
set "ROOT=%~dp0.."
set "NC=%ROOT%\NapCat.Shell"

if not exist "%NC%\NapCatWinBootMain.exe" (
  echo [错误] 没找到 "%NC%\NapCatWinBootMain.exe"
  echo        请把 NapCat.Shell.zip 解压到项目根目录，并命名为 NapCat.Shell
  pause
  exit /b 1
)

rem ---------- 1. 从注册表定位 QQ.exe ----------
set "UNINST="
for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ" /v UninstallString 2^>nul') do set "UNINST=%%~b"
if not defined UNINST (
  for /f "tokens=2*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ" /v UninstallString 2^>nul') do set "UNINST=%%~b"
)
if not defined UNINST (
  echo [错误] 注册表里没找到 QQ，请先安装 QQ NT 客户端
  pause
  exit /b 1
)
for %%i in ("!UNINST!") do set "QQDIR=%%~dpi"
set "QQEXE=!QQDIR!QQ.exe"
if not exist "!QQEXE!" (
  echo [错误] QQ.exe 不存在: !QQEXE!
  pause
  exit /b 1
)

rem ---------- 2. 把 loadNapCat.js 放到 QQ 的 resources\app ----------
rem NapCat 注入后以 ./loadNapCat.js 为入口，且相对 QQ 自己的 app 目录解析，缺了会报 Cannot find module
for /d %%v in ("!QQDIR!versions\*") do (
  if exist "%%v\resources\app\package.json" (
    if not exist "%%v\resources\app\loadNapCat.js" (
      copy /y "%NC%\loadNapCat.js" "%%v\resources\app\loadNapCat.js" >nul 2>&1
      if not errorlevel 1 echo [修复] 已放置 loadNapCat.js 到 %%v\resources\app
    )
  )
)

rem ---------- 3. 启动 NapCat ----------
set "NAPCAT_PATCH_PACKAGE=%NC%\qqnt.json"
set "NAPCAT_LOAD_PATH=%NC%\loadNapCat.js"
set "NAPCAT_INJECT_PATH=%NC%\NapCatWinBootHook.dll"
set "NAPCAT_LAUNCHER_PATH=%NC%\NapCatWinBootMain.exe"
set "NAPCAT_MAIN_PATH=%NC%\napcat.mjs"

echo [NapCat] QQ: !QQEXE!
echo [NapCat] 提示: 首次登录请访问 http://127.0.0.1:6099/webui 扫码, token 见 NapCat.Shell\config\webui.json
echo.
pushd "%NC%"
"%NC%\NapCatWinBootMain.exe" "!QQEXE!" "%NC%\NapCatWinBootHook.dll" %*
set "RC=!ERRORLEVEL!"
popd
echo.
echo [NapCat] 已退出, code=!RC!
endlocal & exit /b %RC%
