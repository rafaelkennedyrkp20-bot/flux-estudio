@echo off
rem Abre o Flux Estudio (janela do Windows, sem navegador).
rem Se o ambiente .venv nao existir, abre o instalador (ou Instalar.bat).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    if exist "ferramentas\Instalador.exe" (
        start "" "ferramentas\Instalador.exe"
        exit /b 0
    )
    echo Ambiente nao encontrado. Instalando...
    call "Instalar.bat"
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "FluxEstudio.py"
