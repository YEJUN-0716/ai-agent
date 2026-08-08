@echo off
REM 작업 스케줄러가 매시간 부르는 진입점.
REM
REM .cmd 로 감싸는 이유: schtasks /TR 은 공백과 리다이렉션이 섞인 명령을
REM 제대로 못 받는다("AI AGENT" 의 공백에서 매번 깨진다). 래퍼 하나면
REM 스케줄러는 파일 하나만 알면 되고, 명령을 고칠 때 작업을 다시 만들 필요도 없다.
setlocal
set "PROJ=%~dp0.."
set "LOG=%PROJ%\.tmp\obsidian_sync.log"
if not exist "%PROJ%\.tmp" mkdir "%PROJ%\.tmp"
cd /d "%PROJ%"
python -X utf8 "%PROJ%\tools\obsidian_bridge.py" sync >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
