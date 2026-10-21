"""Flux Estúdio — gerar e editar imagens com o FLUX.2 [klein] 4B, local, numa janela de verdade.

Rodar:     Abrir Flux Estudio.bat   (ou: .venv\\Scripts\\pythonw.exe FluxEstudio.py)
Compilar:  Compilar EXE (NVIDIA).bat / Compilar EXE (AMD).bat
"""
import ctypes
import logging
import os
import sys
import traceback


def ajustes_amd():
    """Placas AMD (PyTorch ROCm). Precisa vir antes de importar o torch; nas NVIDIA não muda nada.
    - MIOPEN_FIND_MODE=FAST: sem isso a MIOpen faz uma busca exaustiva de kernels na 1ª convolução
      (VAE), que pode levar muitos minutos e parece travamento;
    - TORCH_BLAS_PREFER_HIPBLASLT=0: contorno da própria AMD para travamentos da RX 9070."""
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    os.environ.setdefault("TORCH_BLAS_PREFER_HIPBLASLT", "0")


def autoteste():
    """FluxEstudio.py --autoteste: sem janela, verifica a placa e gera uma imagem pequena.
    Cada etapa vai para a saída e para autoteste.txt na hora: se travar, dá para ver onde."""
    import time
    from PySide6.QtCore import QCoreApplication
    from app import config
    app = QCoreApplication(sys.argv)  # noqa: F841 - os sinais Qt precisam de uma aplicação
    arq = os.path.join(config.BASE, "autoteste.txt")
    linhas = []
    t_ini = time.time()

    def log(s, estado="RODANDO"):
        linhas.append(f"[{time.time() - t_ini:5.0f}s] {s}")
        print(linhas[-1], flush=True)
        with open(arq, "w", encoding="utf-8") as f:
            f.write(estado + "\n" + "\n".join(linhas) + "\n")

    ok = False
    try:
        log("importando o PyTorch…")
        import torch
        log(f"PyTorch {torch.__version__} · {'ROCm ' + torch.version.hip if getattr(torch.version, 'hip', None) else 'CUDA ' + str(torch.version.cuda)}")
        if not torch.cuda.is_available():
            log("o PyTorch NÃO encontrou a placa de vídeo (ficaria no processador): teste interrompido. "
                "Atualize o driver da placa (AMD Adrenalin / NVIDIA) e reinicie o PC.", "FALHOU")
            sys.exit(1)
        p = torch.cuda.get_device_properties(0)
        log(f"placa: {p.name} · {p.total_memory / 2**30:.1f} GB")
        x = torch.randn(1024, 1024, device="cuda")
        torch.cuda.synchronize()
        log(f"conta simples na placa OK ({float((x @ x).sum()):.0f})")
        from app.modelos import PRINCIPAL
        from app.motor import Motor
        if not config.modelo_instalado(PRINCIPAL["id"]):
            log(f"modelo principal não encontrado em {config.pasta_modelo(PRINCIPAL['id'])}", "FALHOU")
            sys.exit(1)
        m = Motor()
        res = {}
        m.estado.connect(lambda e: log(e))
        m.passo.connect(lambda a, b: log(f"passo {a}/{b}"))
        m.concluido.connect(lambda d: res.update(fim=d))
        m.falhou.connect(lambda e: res.update(erro=e))
        t0 = time.time()
        m.executar({"tipo": "gerar", "modelo": PRINCIPAL["id"], "prompt": "a red apple on a wooden table, photo",
                    "largura": 512, "altura": 512, "passos": 4, "quantas": 1, "semente": 1, "guia": 1.0})
        if "erro" in res:
            log(f"ERRO ao gerar: {res['erro']}")
        else:
            log(f"gerou em {time.time() - t0:.1f} s (inclui abrir o modelo): {res['fim']['caminhos']}")
            ok = True
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
    log("pronto", "OK" if ok else "FALHOU")
    sys.exit(0 if ok else 1)


def main():
    # Aberto sem console (atalho / pythonw), stdout e stderr são None e as barras de progresso
    # do diffusers quebrariam ao escrever neles.
    for nome in ("stdout", "stderr"):
        if getattr(sys, nome) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))
    ajustes_amd()
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
