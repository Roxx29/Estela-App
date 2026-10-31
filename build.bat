@echo off
rem Genera dist\Estela\Estela.exe. Requiere Python 3.12 de 64 bits.
setlocal
cd /d "%~dp0"

py -3.12 --version >nul 2>&1 || (echo Falta Python 3.12: https://www.python.org/downloads/ & pause & exit /b 1)

if not exist .venv (
    echo Creando entorno virtual...
    py -3.12 -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

echo Instalando dependencias...
python -m pip install --upgrade pip -q || goto :error
python -m pip install -r requirements.txt pyinstaller -q || goto :error

echo Ejecutando tests...
python -m unittest discover -s tests || goto :error

echo Generando icono...
python tools\make_icon.py || goto :error

echo Generando el ejecutable...
pyinstaller Estela.spec --noconfirm --clean || goto :error
rem build\ es temporal: su .exe no arranca (falta python312.dll), se borra para no confundir
rmdir /s /q build

echo.
echo Listo: dist\Estela\Estela.exe (lleva siempre la carpeta entera)
explorer "dist\Estela"
pause
exit /b 0

:error
echo.
echo ERROR: el build ha fallado. Revisa los mensajes de arriba.
pause
exit /b 1
