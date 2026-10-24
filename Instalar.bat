@echo off
rem Cria o ambiente Python do Flux Estudio (.venv) para placas NVIDIA (CUDA 12.8).
rem Aceita Python 3.12, 3.11 ou 3.10 instalado. Para AMD/Intel Arc/automatico, use FluxEstudio-Instalador.exe.
title Flux Estudio - instalacao (NVIDIA)
cd /d "%~dp0"
set "PY_CMD="
py -3.12 -V >nul 2>&1 && set "PY_CMD=py -3.12"
if not defined PY_CMD py -3.11 -V >nul 2>&1 && set "PY_CMD=py -3.11"
if not defined PY_CMD py -3.10 -V >nul 2>&1 && set "PY_CMD=py -3.10"
if not defined PY_CMD python -V >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    echo Instale o Python 3.12 ^(ou 3.11/3.10^) de python.org, ou use o FluxEstudio-Instalador.exe.
    pause
    exit /b 1
)
%PY_CMD% -m venv .venv || goto erro
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple || goto erro
".venv\Scripts\python.exe" -m pip install --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple -r requisitos.txt || goto erro
echo.
echo Pronto. Abra pelo "Abrir Flux Estudio.bat".
pause
exit /b 0
:erro
echo.
echo Falhou a instalacao. Veja a mensagem acima.
pause
exit /b 1
