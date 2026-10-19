"""Flux Estúdio — gerar e editar imagens com o FLUX.2 [klein] 4B, local, numa janela de verdade.

Rodar:     Abrir Flux Estudio.bat   (ou: .venv\\Scripts\\pythonw.exe FluxEstudio.py)
Compilar:  Compilar EXE (NVIDIA).bat / Compilar EXE (AMD).bat
"""
import ctypes
import logging
import os
import sys
import traceback


def autoteste():
    """FluxEstudio.exe --autoteste: sem janela, verifica a placa e gera uma imagem pequena.
    Escreve o resultado em autoteste.txt ao lado do programa (bom para diagnosticar outro PC)."""
    import time
    from PySide6.QtCore import QCoreApplication
    from app import config
    app = QCoreApplication(sys.argv)  # noqa: F841 - os sinais Qt precisam de uma aplicação
    linhas = []
    log = lambda s: (linhas.append(s), print(s, flush=True))
    ok = False
    try:
        from app.modelos import PRINCIPAL
        from app.motor import Motor
        m = Motor()
        res = {}
        m.placa.connect(lambda n, b, t: res.update(placa=(n, b, t)))
        m.concluido.connect(lambda d: res.update(fim=d))
        m.falhou.connect(lambda e: res.update(erro=e))
        t0 = time.time()
        m.executar({"tipo": "info"})
        log(f"placa: {res.get('placa')}  (torch/diffusers em {time.time() - t0:.1f} s)")
        if not config.modelo_instalado(PRINCIPAL["id"]):
            log(f"modelo principal não encontrado em {config.pasta_modelo(PRINCIPAL['id'])}")
        else:
            t0 = time.time()
            m.executar({"tipo": "gerar", "modelo": PRINCIPAL["id"], "prompt": "a red apple on a wooden table, photo",
                        "largura": 512, "altura": 512, "passos": 4, "quantas": 1, "semente": 1, "guia": 1.0})
            if "erro" in res:
                log(f"ERRO ao gerar: {res['erro']}")
            else:
                log(f"gerou em {time.time() - t0:.1f} s (inclui abrir o modelo): {res['fim']['caminhos']}")
                ok = True
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
    with open(os.path.join(config.BASE, "autoteste.txt"), "w", encoding="utf-8") as f:
        f.write(("OK" if ok else "FALHOU") + "\n" + "\n".join(linhas) + "\n")
    sys.exit(0 if ok else 1)


def main():
    # Aberto sem console (atalho / pythonw), stdout e stderr são None e as barras de progresso
    # do diffusers quebrariam ao escrever neles.
    for nome in ("stdout", "stderr"):
        if getattr(sys, nome) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))
    if "--autoteste" in sys.argv:
        return autoteste()
    from app import config
    config.garantir_pastas()
    logging.basicConfig(filename=config.ARQ_LOG, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.excepthook = lambda t, v, tb: logging.getLogger("erro").error("".join(traceback.format_exception(t, v, tb)))
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FluxEstudio.App")
    except Exception:  # noqa: BLE001
        pass

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Flux Estúdio")
    from app import i18n, tema
    i18n.iniciar()
    tema.aplicar(app)
    icone = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icone.ico")
    if os.path.isfile(icone):
        app.setWindowIcon(QIcon(icone))

    from app.janela import JanelaPrincipal
    j = JanelaPrincipal()
    j.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
