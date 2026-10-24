# PyInstaller: instalador em UM .exe pequeno (só PySide6 + biblioteca padrão; nada de PyTorch).
import os

raiz = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "instalador.py")],
    pathex=[raiz],
    datas=[(os.path.join(raiz, "icone.ico"), ".")],
    hiddenimports=["app.i18n", "app.instalacao", "app.tema", "app.traducoes", "app.config"],
    excludes=[
        "torch", "torchvision", "torchaudio", "diffusers", "transformers", "accelerate", "peft",
        "safetensors", "onnxruntime", "huggingface_hub", "numpy", "scipy", "PIL", "tkinter",
        "matplotlib", "unittest", "sqlite3", "pygments", "rich", "jinja2", "fsspec", "psutil",
        "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore",
        "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.Qt3DCore", "PySide6.QtOpenGL",
        "PySide6.QtSql", "PySide6.QtTest",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="FluxEstudio-Instalador",
    icon=os.path.join(raiz, "icone.ico"),
    console=False,
    upx=False,
)
