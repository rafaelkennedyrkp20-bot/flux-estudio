"""O motor (PyTorch + modelo) roda num PROCESSO separado da janela.

Por quê: no Python só uma thread executa por vez (GIL). Com o motor numa thread do mesmo processo,
partes do PyTorch e do carregamento seguram essa trava por segundos e a janela congela junto (medido:
52 congelamentos, o pior de 11 s, só para carregar o modelo). Em outro processo isso não acontece:
a janela nem importa o torch, abre mais rápido e fica sempre fluida. Se o driver da placa derrubar o
motor, a janela continua viva e um motor novo sobe na próxima tarefa.

MotorRemoto tem os mesmos sinais do Motor; a janela usa os dois do mesmo jeito.
"""
import logging
import multiprocessing as mp
import queue
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("processo_motor")

SINAIS = ("estado", "passo", "imagem", "concluido", "falhou", "carregado", "carregando", "liberado", "placa", "detectado")


def _processo(tarefas, eventos, cancelar):
    """Ponto de entrada do processo do motor."""
    import os
    import sys
    from logging.handlers import RotatingFileHandler

    for nome in ("stdout", "stderr"):  # pythonw: sem console, as barras do diffusers quebrariam
        if getattr(sys, nome) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))
    from . import config, dispositivo, i18n
    dispositivo.configurar_ambiente()
    logging.basicConfig(handlers=[RotatingFileHandler(os.path.join(config.BASE, "flux_estudio_motor.log"),
                                                      maxBytes=2_000_000, backupCount=1, encoding="utf-8")],
                        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    i18n.iniciar()
    from PySide6.QtCore import QCoreApplication
    _app = QCoreApplication(sys.argv[:1])  # noqa: F841 - QObject/sinais do Motor
    from .motor import Motor

    m = Motor()
    for nome in SINAIS:
        getattr(m, nome).connect(lambda *a, n=nome: eventos.put((n, a)))

    def vigiar_cancelar():
        while True:
            cancelar.wait()
            m.cancelar = True
            cancelar.clear()

    def medir_vram():
        import time
        while True:
            try:
                mem = dispositivo.memoria_info()
                if mem and mem[1] > 0:
                    eventos.put(("vram", (mem[0] / 2**30, mem[1] / 2**30)))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2.0)

    threading.Thread(target=vigiar_cancelar, daemon=True).start()
    vram_ligada = False
    while True:
        t = tarefas.get()
        if t is None:
            break
        m.executar(t)
        if t.get("tipo") == "info" and not vram_ligada and dispositivo.tem_gpu():
            vram_ligada = True
            threading.Thread(target=medir_vram, daemon=True).start()
    try:
        m.executar({"tipo": "liberar"})
    except Exception:  # noqa: BLE001
        pass


class MotorRemoto(QObject):
    estado = Signal(str)
    passo = Signal(int, int)
    imagem = Signal(str)
    concluido = Signal(dict)
    falhou = Signal(str)
    carregado = Signal(str)
    carregando = Signal(str)
    liberado = Signal()
    placa = Signal(str, str, float)
    detectado = Signal(int, object, object, str)
    vram = Signal(float, float)
    _evento = Signal(int, str, object)   # (geração do processo, sinal, argumentos), da thread leitora

    def __init__(self):
        super().__init__()
        self._geracao = 0
        self._evento.connect(self._entregar)
        self._ctx = mp.get_context("spawn")
        self._proc = None
        self._iniciar()

    def _iniciar(self):
        self._geracao += 1
        self._tarefas = self._ctx.Queue()
        self._eventos = self._ctx.Queue()
        self._cancelar = self._ctx.Event()
        self._proc = self._ctx.Process(target=_processo, args=(self._tarefas, self._eventos, self._cancelar),
                                       name="FluxEstudio-motor", daemon=True)
        # No Windows o processo novo reexecutaria o script principal (que abre a janela). Sem o __file__
        # do __main__, ele só importa app.processo_motor: sobe mais rápido e sem efeitos colaterais.
        import sys
        principal = sys.modules.get("__main__")
        arq = getattr(principal, "__file__", None)
        try:
            if arq is not None:
                del principal.__file__
            self._proc.start()
        finally:
            if arq is not None:
                principal.__file__ = arq
        threading.Thread(target=self._ler, args=(self._proc, self._eventos, self._geracao),
                         name="eventos-motor", daemon=True).start()

    def _entregar(self, geracao, nome, args):
        """Na thread da janela. Eventos de um processo já cancelado/reiniciado são jogados fora: se chegassem
        depois do Cancelar, reabririam a janela de carga."""
        if geracao != self._geracao:
            return
        try:
            getattr(self, nome).emit(*args)
        except Exception:  # noqa: BLE001
            log.exception("evento %s", nome)

    def _ler(self, proc, eventos, geracao):
        """Traz os eventos do processo do motor para a janela (sinais Qt chegam na thread da janela)."""
        while True:
            try:
                nome, args = eventos.get(timeout=1.0)
            except queue.Empty:
                if not proc.is_alive():
                    if proc is self._proc and getattr(self, "_encerrando", False) is False:
                        log.error("processo do motor terminou (código %s)", proc.exitcode)
                        self._evento.emit(geracao, "carregado", ("",))
                        from .i18n import tr
                        msg = tr("O motor de imagem fechou inesperadamente (código {c}). "
                                 "Ele será reiniciado na próxima tarefa.", c=proc.exitcode)
                        self._evento.emit(geracao, "falhou", (msg,))
                    return
                continue
            except (EOFError, OSError):
                return
            if proc is not self._proc:
                return  # processo antigo (cancelado/reiniciado): eventos atrasados dele não valem mais
            self._evento.emit(geracao, nome, tuple(args))

    # ---- API usada pela janela
    def enviar(self, tarefa):
        if self._proc is None or not self._proc.is_alive():
            self._iniciar()
            if tarefa.get("tipo") != "info":
                self._tarefas.put({"tipo": "info"})
        self._cancelar.clear()
        self._tarefas.put(tarefa)

    def reiniciar(self):
        """Mata o motor na hora e sobe outro (já importando o torch). É o único jeito de parar a carga de
        um modelo no meio; a placa de vídeo e a RAM fixada voltam para o Windows no mesmo instante."""
        velho, self._proc = self._proc, None
        if velho is not None and velho.is_alive():
            velho.terminate()
            velho.join(3)
        self._iniciar()
        self._tarefas.put({"tipo": "info"})

    @property
    def cancelar(self):
        return self._cancelar.is_set()

    @cancelar.setter
    def cancelar(self, valor):
        if valor:
            self._cancelar.set()

    def encerrar(self, espera_s=4.0):
        self._encerrando = True
        if self._proc is None:
            return
        self._cancelar.set()
        try:
            self._tarefas.put(None)
        except Exception:  # noqa: BLE001
            pass
        self._proc.join(espera_s)
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(2)
