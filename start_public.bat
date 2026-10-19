@echo off
chcp 65001 >nul
REM KINS 일일 언론동향 검색 - 이 PC에서 앱을 실행하고 HTTPS 임시 주소(trycloudflare.com)로 공개합니다.
REM 창 두 개가 열립니다. 두 창을 닫으면 공개가 종료됩니다. 실행할 때마다 주소가 바뀝니다.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [오류] .venv 가 없습니다. README 의 설치 방법을 먼저 진행하세요.
  pause & exit /b 1
)
findstr /b /c:"APP_PASSWORD=" .env | findstr /v /b /c:"APP_PASSWORD=$" >nul
if errorlevel 1 (
  echo [경고] .env 에 APP_PASSWORD 가 없습니다. 외부 공개 전에 접속 비밀번호를 설정하세요.
  pause & exit /b 1
)
if not exist "tools\cloudflared.exe" (
  echo cloudflared 를 내려받습니다...
  if not exist tools mkdir tools
  curl -sSL -o tools\cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)

start "KINS 앱 서버" cmd /k ".venv\Scripts\python.exe -m streamlit run app.py --server.port 8501 --server.headless true"
timeout /t 5 >nul
echo.
echo 아래 창에 표시되는 https://....trycloudflare.com 주소를 동료에게 전달하세요.
start "KINS HTTPS 터널" cmd /k "tools\cloudflared.exe tunnel --no-autoupdate --url http://localhost:8501"
