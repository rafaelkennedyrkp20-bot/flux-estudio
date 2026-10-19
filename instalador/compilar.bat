@echo off
rem Compila o instalador (pequeno: so a janela e a logica de instalacao, sem PyTorch).
rem Resultado: dist\FluxEstudio-Instalador.exe  -> suba como asset de uma Release no GitHub.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" call "Instalar.bat"
".venv\Scripts\python.exe" -m pip install -q pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --workpath build\instalador instalador\instalador.spec || (echo FALHOU & pause & exit /b 1)
echo Pronto: dist\FluxEstudio-Instalador.exe
