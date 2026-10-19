@echo off
rem Cria o ambiente Python do Flux Estudio (.venv) para placas NVIDIA (CUDA 12.8).
rem Precisa do Python 3.10 instalado (py -3.10). Baixa ~4 GB de bibliotecas uma vez so.
title Flux Estudio - instalacao (NVIDIA)
cd /d "%~dp0"
py -3.10 -m venv .venv || (echo Instale o Python 3.10 de python.org e tente de novo. & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple || goto erro
".venv\Scripts\python.exe" -m pip install -r requisitos.txt || goto erro
echo.
echo Pronto. Abra pelo "Abrir Flux Estudio.bat".
pause
exit /b 0
:erro
echo.
echo Falhou a instalacao. Veja a mensagem acima.
pause
exit /b 1
