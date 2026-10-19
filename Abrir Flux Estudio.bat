@echo off
rem Abre o Flux Estudio (janela do Windows, sem navegador).
rem Se o ambiente .venv nao existir, roda a instalacao primeiro.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Ambiente nao encontrado. Instalando...
    call "Instalar.bat"
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "FluxEstudio.py"
