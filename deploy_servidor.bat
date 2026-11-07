@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0server"

rem Publica el servidor de Escuchar juntos en Cloudflare (Workers + Durable Objects).

where node >nul 2>&1 || (echo Falta Node.js 20 o superior: https://nodejs.org & pause & exit /b 1)
for /f "tokens=1 delims=." %%v in ('node -p "process.versions.node"') do set NODEMAJ=%%v
if %NODEMAJ% LSS 20 (echo Node.js %NODEMAJ% es antiguo: hace falta 20 o superior. & pause & exit /b 1)

echo Instalando dependencias...
call npm ci || (echo Fallo npm ci. & pause & exit /b 1)

call npx wrangler whoami | findstr /C:"not authenticated" >nul
if not errorlevel 1 (
  echo Inicia sesion en Cloudflare en el navegador...
  call npx wrangler login || (echo No se pudo iniciar sesion. & pause & exit /b 1)
)

set "WRANGLER_OUTPUT_FILE_PATH=%TEMP%\estela_deploy.json"
del "%WRANGLER_OUTPUT_FILE_PATH%" >nul 2>&1
echo Publicando...
call npx wrangler deploy || (echo Fallo la publicacion. & pause & exit /b 1)

echo.
node -e "const l=require('fs').readFileSync(process.argv[1],'utf8').split('\n').filter(Boolean).map(JSON.parse).find(e=>e.type==='deploy');const u=(l&&l.targets||[])[0];console.log(u?'URL del servidor (pegala en Estela): '+u.replace(/^https:/,'wss:')+'/ws':'No se encontro la URL: mira la salida de arriba (https://...workers.dev, cambia https por wss y anade /ws).')" "%WRANGLER_OUTPUT_FILE_PATH%"
echo.
pause
