@echo off
rem Compila o instalador (pequeno: so a janela e a logica de instalacao, sem PyTorch).
rem Resultado: dist\FluxEstudio-Instalador.exe  -> suba como asset de uma Release no GitHub.
cd /d "%~dp0.."
set "PY_BUILD=%PYTHON_EXE%"
if not defined PY_BUILD set "PY_BUILD=.venv\Scripts\python.exe"
if not exist "%PY_BUILD%" call "Instalar.bat"
"%PY_BUILD%" -m pip install -q pyinstaller
"%PY_BUILD%" -m PyInstaller --noconfirm --clean --workpath build\instalador instalador\instalador.spec || (echo FALHOU & exit /b 1)
echo Pronto: dist\FluxEstudio-Instalador.exe
