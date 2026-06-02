@echo off
REM 对分易自动签到 - 后台静默运行脚本
REM 使用说明：
REM 1. 双击运行此脚本
REM 2. 程序会在后台运行，不显示命令行窗口
REM 3. 首次运行需要输入对分易账号密码
REM 4. 登录后程序会自动监控课程签到

setlocal
set "SCRIPT_DIR=%~dp0"
set "MAIN_PY=%SCRIPT_DIR%main.py"
set "VENV_PYTHONW=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"

if not exist "%MAIN_PY%" (
    echo 未找到主程序: "%MAIN_PY%"
    pause
    exit /b 1
)

if exist "%VENV_PYTHONW%" (
    start "" "%VENV_PYTHONW%" "%MAIN_PY%"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%MAIN_PY%"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%MAIN_PY%"
    exit /b 0
)

echo 未找到可用的 pythonw/pyw，请先安装 Python 或创建 .venv。
pause
exit /b 1
