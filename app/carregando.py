"""Janela "Carregando o modelo": trava o programa enquanto o modelo vai para a placa, mostra a etapa,
o tempo passado e o tempo estimado NESTE computador (aprende com as cargas anteriores)."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from . import config, tema
from .i18n import tr


def _mmss(s):
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def tempo_estimado(id_modelo):
    return (config.carregar().get("tempo_carga") or {}).get(id_modelo)


def registrar_tempo(id_modelo, segundos):
    """Média móvel: a 1ª abertura depois de ligar o PC é bem mais lenta que as seguintes.
    Se ficou mais rápido, vale o tempo novo na hora (não leva várias cargas para a estimativa cair)."""
    tempos = config.carregar().get("tempo_carga") or {}
    antigo = tempos.get(id_modelo)
    novo = segundos if antigo is None or segundos < antigo else 0.6 * antigo + 0.4 * segundos
    tempos[id_modelo] = round(novo, 1)
    config.salvar({"tempo_carga": tempos})


class _Escura(QDialog):
    def showEvent(self, e):
        super().showEvent(e)
        tema.barra_titulo_escura(self)


def perguntar_ao_abrir(parent, modelo):
    """Pergunta da abertura: (carregar_agora, lembrar). Janela de verdade, no meio da tela."""
    d = _Escura(parent)
    d.setWindowTitle(tr("Ligar o modelo"))
    d.setWindowModality(Qt.ApplicationModal)
    d.setStyleSheet(f"QDialog {{ background: {tema.PAINEL}; }}")
    d.setMinimumWidth(520)
    v = QVBoxLayout(d)
    v.setContentsMargins(26, 24, 26, 22)
    v.setSpacing(12)
    titulo = QLabel(tr("Carregar o {nome} na placa de vídeo agora?", nome=modelo["nome"]))
    titulo.setObjectName("titulo")
    titulo.setWordWrap(True)
    v.addWidget(titulo)
    eta = tempo_estimado(modelo["id"])
    txt = (tr("Leva cerca de {t} neste computador. Depois disso, cada imagem sai em poucos segundos.", t=_mmss(eta))
           if eta else tr("Depois disso, cada imagem sai em poucos segundos."))
    sub = QLabel(txt)
    sub.setObjectName("subtitulo")
    sub.setWordWrap(True)
    v.addWidget(sub)
    hb = QHBoxLayout()
    lembrar = QCheckBox(tr("Lembrar escolha"))
    hb.addWidget(lembrar)
    hb.addStretch(1)
    nao = QPushButton(tr("Agora não"))
    nao.clicked.connect(d.reject)
    sim = QPushButton(tr("Carregar agora"))
    sim.setObjectName("principal")
    sim.setStyleSheet("padding: 8px 18px;")
    sim.setDefault(True)
    sim.clicked.connect(d.accept)
    hb.addWidget(nao)
    hb.addWidget(sim)
    v.addSpacing(6)
    v.addLayout(hb)
    sim.setFocus()
    return d.exec() == QDialog.Accepted, lembrar.isChecked()


class DialogoCarregando(QDialog):
    def __init__(self, parent, modelo, ao_cancelar=None):
        super().__init__(parent)
        self.modelo = modelo
        self.ao_cancelar = ao_cancelar
        self._fim = False
        self.setWindowTitle(tr("Carregando o modelo"))
        # Fechar esta janela (X ou botão Cancelar) cancela a carga: o motor é reiniciado na hora.
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setStyleSheet(f"QDialog {{ background: {tema.PAINEL}; }}")
        self.setMinimumWidth(540)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(12)
        titulo = QLabel(tr("Carregando o {nome}, aguarde…", nome=modelo["nome"]))
        titulo.setObjectName("titulo")
        titulo.setWordWrap(True)
        v.addWidget(titulo)
        self.fase = QLabel(tr("Iniciando…"))
        self.fase.setObjectName("subtitulo")
        v.addWidget(self.fase)
        self.barra = QProgressBar()
        self.barra.setObjectName("grossa")
        self.barra.setRange(0, 1000)
        v.addWidget(self.barra)
        self.tempo = QLabel("")
        self.tempo.setObjectName("valor")
        v.addWidget(self.tempo)
        nota = QLabel(tr("Depois que o modelo carregar a primeira vez, você não precisa carregá-lo de novo. "
                         "Mantenha o programa aberto para fazer suas edições."))
        nota.setObjectName("dica")
        nota.setWordWrap(True)
        v.addWidget(nota)
        hb = QHBoxLayout()
        hb.addStretch(1)
        self.bt_cancelar = QPushButton(tr("Cancelar"))
        self.bt_cancelar.clicked.connect(self.close)
        hb.addWidget(self.bt_cancelar)
        v.addLayout(hb)
        self.eta = tempo_estimado(modelo["id"])
        self.t0 = time.time()
        self._relogio = QTimer(self, interval=250)
        self._relogio.timeout.connect(self._tic)
        self._tic()
        self._relogio.start()

    def definir_fase(self, texto):
        self.fase.setText(texto)

    def _tic(self):
        passado = time.time() - self.t0
        if self.eta:
            frac = passado / self.eta
            if frac < 0.97:
                self.barra.setRange(0, 1000)
                # um pouco mais rápido no começo, sem nunca "terminar" antes da hora
                self.barra.setValue(int(1000 * min(0.97, frac ** 0.85)))
                self.tempo.setText(tr("{passado} de ~{total} (tempo medido neste computador)",
                                      passado=_mmss(passado), total=_mmss(self.eta)))
            else:
                self.barra.setRange(0, 0)
                self.tempo.setText(tr("{passado} · quase lá…", passado=_mmss(passado)))
        else:
            self.barra.setRange(0, 0)
            self.tempo.setText(tr("{passado} · primeira vez neste computador: medindo o tempo",
                                  passado=_mmss(passado)))

    def concluir(self, ok=True):
        self._fim = True
        self._relogio.stop()
        if ok:
            registrar_tempo(self.modelo["id"], time.time() - self.t0)
        self.accept()

    def closeEvent(self, e):
        if not self._fim:
            self._fim = True
            self._relogio.stop()
            if callable(self.ao_cancelar):
                self.ao_cancelar()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:  # Esc não cancela sem querer; para cancelar, o botão ou o X
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        tema.barra_titulo_escura(self)
