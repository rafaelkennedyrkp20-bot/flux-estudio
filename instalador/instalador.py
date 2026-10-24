"""Instalador do Flux Estúdio.

Detecta a placa de vídeo (NVIDIA -> CUDA, Radeon -> ROCm, Intel Arc -> XPU, nenhuma -> CPU),
baixa o programa do GitHub, instala Python + PyTorch certo + bibliotecas com o uv, baixa o modelo
e cria os atalhos. Rodando de novo na mesma pasta: atualiza, repara ou desinstala.

Compilar: instalador\\compilar.bat  ->  dist\\FluxEstudio-Instalador.exe
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback

if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from app import i18n, instalacao as inst, tema
from app.i18n import tr

VERSAO_INSTALADOR = "1.4.0"


def _rot(texto, nome="rotulo"):
    l = QLabel(texto)
    l.setObjectName(nome)
    l.setWordWrap(True)
    return l


def _cartao():
    f = QFrame()
    f.setObjectName("cartao")
    return f


def _pasta_auto_instalada():
    """Se o instalador estiver rodando de `<pasta>/ferramentas/Instalador.exe`, devolve `<pasta>`."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        pai = os.path.dirname(exe_dir)
        if os.path.basename(exe_dir).lower() == "ferramentas" and os.path.isfile(os.path.join(pai, "instalacao.json")):
            return pai
    return None


class Ponte(QObject):
    passo = Signal(int, str)            # índice, estado: rodando/ok/erro/pulado
    texto = Signal(str)                 # detalhe atual
    barra = Signal(int, int)            # feito, total (total 0 = indeterminado)
    linha = Signal(str)                 # log
    fim = Signal(bool, str)             # sucesso, mensagem
    mostrar_pular = Signal(bool)        # botão "Pular teste"
    hardware = Signal(dict, str)        # deteccao, pasta_sugerida


class Instalador(QWidget):
    def __init__(self, estado_inicial=None):
        super().__init__()
        self.setWindowTitle(tr("Instalador do Flux Estúdio"))
        self.setObjectName("raiz")
        self.setAttribute(Qt.WA_StyledBackground, True)
        tela = QGuiApplication.primaryScreen()
        disp = tela.availableGeometry() if tela else QRect(0, 0, 1440, 900)
        self.resize(min(840, disp.width() - 40), min(680, disp.height() - 50))
        self.setMinimumSize(680, 520)

        self.deteccao = (estado_inicial or {}).get("deteccao") or dict(
            backend="cuda", gfx="gfx1201", placa="", vram_gb=0, aviso="detectando"
        )
        self.pasta_modelo_existente = (estado_inicial or {}).get("pasta_modelo")
        self._pasta_editada = bool((estado_inicial or {}).get("pasta_editada", False))
        self._backend_editado = bool((estado_inicial or {}).get("backend_editado", False))
        self._instalando = False
        self._cancelar = False
        self._modo = "instalar"

        self.ponte = Ponte()
        self.ponte.passo.connect(self._ao_passo)
        self.ponte.texto.connect(lambda s: self.det.setText(s))
        self.ponte.barra.connect(self._ao_barra)
        self.ponte.linha.connect(lambda s: self.log.appendPlainText(s))
        self.ponte.fim.connect(self._ao_fim)
        self.ponte.mostrar_pular.connect(lambda v: self.bt_pular.setVisible(v))
        self.ponte.hardware.connect(self._ao_hardware)

        self._timer_pasta = QTimer(self, singleShot=True, interval=180)
        self._timer_pasta.timeout.connect(self._atualizar_modo)

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 20, 26, 20)
        v.setSpacing(14)
        topo = QHBoxLayout()
        marca = QLabel("✦")
        marca.setStyleSheet(f"font-size: 30px; color: {tema.ACENTO2};")
        topo.addWidget(marca)
        tx = QVBoxLayout()
        tx.setSpacing(0)
        tx.addWidget(_rot("Flux Estúdio", "titulo"))
        tx.addWidget(_rot(tr("Instalador · imagens locais na sua placa de vídeo"), "subtitulo"))
        topo.addLayout(tx, 1)
        self.idioma = QComboBox()
        for cod, nome in i18n.IDIOMAS.items():
            self.idioma.addItem(nome, cod)
        self.idioma.setCurrentIndex(list(i18n.IDIOMAS).index(i18n.atual()))
        self.idioma.currentIndexChanged.connect(self._trocar_idioma)
        topo.addWidget(self.idioma, 0, Qt.AlignTop)
        v.addLayout(topo)
        self.pilha = QStackedWidget()
        v.addWidget(self.pilha, 1)
        self.pilha.addWidget(self._pagina_config(estado_inicial))
        self.pilha.addWidget(self._pagina_progresso())
        self._atualizar_modo()

        if self.deteccao.get("aviso") == "detectando":
            threading.Thread(target=self._sondar_hardware, daemon=True).start()
        else:
            self._aplicar_deteccao(self.deteccao, None)

    def _sondar_hardware(self):
        try:
            det = inst.escolher_backend()
        except Exception:  # noqa: BLE001
            det = dict(backend="cpu", gfx=None, placa="", vram_gb=0, aviso="sem_placa")
        try:
            sugerida = _pasta_auto_instalada() or inst.pasta_padrao()
        except Exception:  # noqa: BLE001
            sugerida = ""
        self.ponte.hardware.emit(det, sugerida)

    def _ao_hardware(self, det, sugerida):
        self.deteccao = det
        self._aplicar_deteccao(det, sugerida)
        self.bt_principal.setEnabled(True)
        if self.bt_principal.text() == tr("Detectando a placa…"):
            self.bt_principal.setText(getattr(self, "_texto_principal", tr("Instalar")))

    def _aplicar_deteccao(self, d, sugerida):
        nome = d["placa"] or tr("nenhuma")
        if d.get("vram_gb"):
            nome += f" · {d['vram_gb']:.0f} GB"
        self.lbl_placa.setText(nome)
        if not self._backend_editado:
            codigos = ["cuda", "rocm", "xpu", "cpu"]
            bk = d["backend"] if d["backend"] in codigos else "cpu"
            self.backend.blockSignals(True)
            self.backend.setCurrentIndex(codigos.index(bk))
            self.backend.blockSignals(False)
            if d.get("gfx"):
                idx_gfx = self.combo_gfx.findData(d["gfx"])
                if idx_gfx >= 0:
                    self.combo_gfx.setCurrentIndex(idx_gfx)
        if sugerida and not self._pasta_editada:
            self.pasta.blockSignals(True)
            self.pasta.setText(sugerida)
            self.pasta.blockSignals(False)
        self._aviso_placa()
        self._atualizar_modo()

    # ------------------------------------------------------------------ página 1: configurar
    def _pagina_config(self, est=None):
        est = est or {}
        w = QWidget()
        fora = QVBoxLayout(w)
        fora.setContentsMargins(0, 0, 0, 0)
        fora.setSpacing(12)

        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rolagem.setFrameShape(QFrame.NoFrame)
        corpo = QWidget()
        v = QVBoxLayout(corpo)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(12)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 15, 18, 15)
        cv.setSpacing(8)
        cv.addWidget(_rot(tr("Placa de vídeo encontrada")))
        self.lbl_placa = _rot(tr("Verificando a placa…"), "valor")
        self.lbl_placa.setStyleSheet("font-size: 15px;")
        cv.addWidget(self.lbl_placa)

        h = QHBoxLayout()
        h.addWidget(_rot(tr("Versão a instalar"), "subtitulo"))
        self.backend = QComboBox()
        self.backend.addItem("NVIDIA (CUDA 12.8)", "cuda")
        self.backend.addItem("AMD Radeon (ROCm)", "rocm")
        self.backend.addItem("Intel Arc (XPU)", "xpu")
        self.backend.addItem(tr("Sem placa compatível (processador, muito lento)"), "cpu")
        if est.get("backend"):
            idx = self.backend.findData(est["backend"])
            if idx >= 0:
                self.backend.setCurrentIndex(idx)
        self.backend.setMinimumWidth(320)
        self.backend.currentIndexChanged.connect(self._usuario_trocou_backend)
        h.addWidget(self.backend)
        h.addStretch()
        cv.addLayout(h)

        self.linha_gfx = QWidget()
        hg = QHBoxLayout(self.linha_gfx)
        hg.setContentsMargins(0, 2, 0, 0)
        hg.addWidget(_rot(tr("Família Radeon"), "subtitulo"))
        self.combo_gfx = QComboBox()
        for gfx_cod, gfx_rot in inst.FAMILIAS_AMD:
            self.combo_gfx.addItem(gfx_rot, gfx_cod)
        if est.get("gfx"):
            idx_g = self.combo_gfx.findData(est["gfx"])
            if idx_g >= 0:
                self.combo_gfx.setCurrentIndex(idx_g)
        self.combo_gfx.setMinimumWidth(360)
        self.combo_gfx.currentIndexChanged.connect(lambda _: self._aviso_placa())
        hg.addWidget(self.combo_gfx)
        hg.addStretch()
        self.linha_gfx.setVisible(False)
        cv.addWidget(self.linha_gfx)

        self.lbl_aviso = _rot("", "dica")
        cv.addWidget(self.lbl_aviso)
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 15, 18, 15)
        cv.setSpacing(8)
        cv.addWidget(_rot(tr("Pasta de instalação")))
        h = QHBoxLayout()
        pasta_ini = est.get("pasta") or _pasta_auto_instalada() or inst.pasta_padrao_rapida()
        if _pasta_auto_instalada():
            self._pasta_editada = True
        self.pasta = QLineEdit(pasta_ini)
        self.pasta.textEdited.connect(self._ao_editar_pasta)
        h.addWidget(self.pasta, 1)
        b = QPushButton(tr("Procurar…"))
        b.clicked.connect(self._procurar)
        h.addWidget(b)
        cv.addLayout(h)
        h = QHBoxLayout()
        h.setSpacing(10)
        self.lbl_espaco = _rot("", "dica")
        self.lbl_espaco.setWordWrap(False)
        h.addWidget(self.lbl_espaco)
        self.lbl_disco = _rot("", "dica")
        self.lbl_disco.setWordWrap(False)
        h.addWidget(self.lbl_disco)
        h.addStretch()
        cv.addLayout(h)
        self.lbl_disco_aviso = _rot("", "dica")
        self.lbl_disco_aviso.setStyleSheet(f"color: {tema.AMARELO};")
        self.lbl_disco_aviso.setVisible(False)
        cv.addWidget(self.lbl_disco_aviso)
        self.cb_modelo = QCheckBox(tr("Baixar o modelo principal FLUX.2 [klein] 4B agora (~15 GB)"))
        self.cb_modelo.setChecked(est.get("cb_modelo", True))
        cv.addWidget(self.cb_modelo)
        h = QHBoxLayout()
        self.bt_modelo = QPushButton(tr("Já tenho o modelo numa pasta…"))
        self.bt_modelo.clicked.connect(self._modelo_existente)
        h.addWidget(self.bt_modelo)
        self.lbl_modelo = _rot(self.pasta_modelo_existente or "", "dica")
        h.addWidget(self.lbl_modelo, 1)
        cv.addLayout(h)
        self.cb_atalho = QCheckBox(tr("Criar atalho na área de trabalho"))
        self.cb_atalho.setChecked(est.get("cb_atalho", True))
        cv.addWidget(self.cb_atalho)
        v.addWidget(c)
        v.addStretch()

        rolagem.setWidget(corpo)
        fora.addWidget(rolagem, 1)

        self.lbl_existente = _rot("", "subtitulo")
        fora.addWidget(self.lbl_existente)
        h = QHBoxLayout()
        self.bt_desinstalar = QPushButton(tr("Desinstalar"))
        self.bt_desinstalar.clicked.connect(self._desinstalar)
        h.addWidget(self.bt_desinstalar)
        self.bt_reparar = QPushButton(tr("Reinstalar tudo"))
        self.bt_reparar.clicked.connect(lambda: self._comecar("instalar"))
        h.addWidget(self.bt_reparar)
        h.addStretch()
        self.bt_principal = QPushButton(tr("Instalar"))
        self.bt_principal.setObjectName("principal")
        self.bt_principal.setMinimumWidth(220)
        self.bt_principal.setMinimumHeight(46)
        self.bt_principal.clicked.connect(lambda: self._comecar(self._modo))
        # Só libera depois de detectar a placa: senão uma AMD poderia receber o PyTorch da NVIDIA.
        self.bt_principal.setEnabled(False)
        self._texto_principal = self.bt_principal.text()
        self.bt_principal.setText(tr("Detectando a placa…"))
        h.addWidget(self.bt_principal)
        fora.addLayout(h)
        self._aviso_placa()
        return w

    def _usuario_trocou_backend(self, _i=0):
        self._backend_editado = True
        self._aviso_placa()

    def _ao_editar_pasta(self, _txt=""):
        self._pasta_editada = True
        self._timer_pasta.start()

    def _aviso_placa(self):
        b = self.backend.currentData()
        d = self.deteccao
        self.linha_gfx.setVisible(b == "rocm")
        if b == "cpu":
            t = tr("Sem placa compatível: vai funcionar no processador, mas cada imagem pode levar vários minutos.")
            if d.get("aviso") == "amd_desconhecida":
                t += " " + tr("Se a sua Radeon suportar ROCm, você pode selecionar AMD Radeon (ROCm) acima e escolher a família.")
        elif b == "rocm":
            gfx = self.combo_gfx.currentData() or d.get("gfx") or "gfx1201"
            t = tr("PyTorch oficial da AMD (ROCm) para {gfx}. Mantenha o driver AMD Adrenalin atualizado.", gfx=gfx)
        elif b == "xpu":
            t = tr("PyTorch com Intel XPU para placas Intel Arc. Mantenha o driver Intel Graphics atualizado.")
        else:
            t = tr("PyTorch com CUDA 12.8.")
            if d.get("aviso") == "driver_antigo":
                t += " " + tr("Seu driver NVIDIA é antigo: atualize para a versão 570 ou mais nova.")
        if d.get("vram_gb") and d["vram_gb"] < 11.5 and b != "cpu":
            if d["vram_gb"] >= 5.5:
                t += " " + tr("O modo econômico automático permite usar o FLUX.2 4B sem estourar a VRAM.")
            else:
                t += " " + tr("Placa com menos de 6 GB: prefira tamanhos menores (768 px) ou modelos leves.")
        self.lbl_aviso.setText(t)

    def _procurar(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta de instalação"), self.pasta.text())
        if p:
            p = os.path.normpath(p)
            try:
                tem_arqs = bool(os.listdir(p))
            except OSError:
                tem_arqs = False
            if not os.path.basename(p).lower().startswith("flux") and tem_arqs and not os.path.isfile(os.path.join(p, "instalacao.json")):
                p = os.path.join(p, "FluxEstudio")
            self._pasta_editada = True
            self.pasta.setText(p)
            self._atualizar_modo()

    def _modelo_existente(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta do modelo FLUX.2 [klein] 4B"))
        if not p:
            return
        if not os.path.isfile(os.path.join(p, "model_index.json")):
            QMessageBox.warning(self, tr("Pasta inválida"),
                                tr("Essa pasta não tem o modelo completo no formato diffusers (model_index.json + subpastas)."))
            return
        self.pasta_modelo_existente = os.path.normpath(p)
        self.cb_modelo.setChecked(False)
        self.lbl_modelo.setText(self.pasta_modelo_existente)

    def _espaco_livre_gb(self, pasta):
        drv = os.path.splitdrive(pasta)[0] if pasta else ""
        if len(drv) == 2 and drv[1] == ":":
            try:
                return shutil.disk_usage(drv + "\\").free / 2**30
            except OSError:
                return None
        return None

    def _atualizar_modo(self):
        pasta = self.pasta.text().strip()
        info = inst.ler_info(pasta) if pasta else {}
        livre = self._espaco_livre_gb(pasta)
        if livre is not None:
            self.lbl_espaco.setText(tr("Espaço livre no disco: {gb:.0f} GB (precisa de ~25 GB com o modelo)", gb=livre))
        else:
            self.lbl_espaco.setText("")
        self._atualizar_disco(pasta)
        if info:
            self._modo = "atualizar"
            self.bt_principal.setText(tr("Atualizar"))
            self.lbl_existente.setText(tr("Já instalado aqui (versão {v}, {b}). Atualizar baixa a versão nova do programa e mantém modelos, imagens e prompts.",
                                          v=inst.versao_local(pasta), b=info.get("backend", "?").upper()))
            self.lbl_existente.setVisible(True)
        else:
            self._modo = "instalar"
            self.bt_principal.setText(tr("Instalar"))
            self.lbl_existente.setText("")
            self.lbl_existente.setVisible(False)
        for b in (self.bt_desinstalar, self.bt_reparar):
            b.setVisible(bool(info))

    def _atualizar_disco(self, pasta):
        tipo = inst.tipo_do_disco(pasta)
        aviso = ""
        if tipo == "nvme":
            texto, cor = tr("Disco NVMe ✓"), tema.VERDE
        elif tipo == "ssd":
            texto, cor = tr("Disco SSD SATA"), tema.SUAVE
        elif tipo == "hdd":
            texto, cor = tr("Disco HDD ⚠"), tema.AMARELO
            aviso = tr("Disco rígido (HDD): o modelo de ~15 GB é lido do disco toda vez que o programa abre, "
                       "então vai demorar bastante para carregar. Se puder, escolha uma pasta num SSD.")
        else:
            texto, cor = "", tema.SUAVE
        self.lbl_disco.setText(texto)
        self.lbl_disco.setStyleSheet(f"color: {cor};")
        self.lbl_disco_aviso.setText(aviso)
        self.lbl_disco_aviso.setVisible(bool(aviso))

    def _trocar_idioma(self, i):
        i18n._atual = self.idioma.itemData(i)
        pos = self.pos()
        tam = self.size()
        estado = dict(
            deteccao=self.deteccao,
            pasta=self.pasta.text(),
            pasta_editada=self._pasta_editada,
            backend=self.backend.currentData(),
            backend_editado=self._backend_editado,
            gfx=self.combo_gfx.currentData(),
            cb_modelo=self.cb_modelo.isChecked(),
            cb_atalho=self.cb_atalho.isChecked(),
            pasta_modelo=self.pasta_modelo_existente,
        )
        novo = Instalador(estado_inicial=estado)
        novo.idioma.blockSignals(True)
        novo.idioma.setCurrentIndex(i)
        novo.idioma.blockSignals(False)
        novo.resize(tam)
        novo.move(pos)
        novo.show()
        tema.barra_titulo_escura(novo)
        QApplication.instance()._janela = novo
        self.close()

    # ------------------------------------------------------------------ página 2: progresso
    def _pagina_progresso(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.titulo_prog = _rot(tr("Instalando…"), "titulo")
        v.addWidget(self.titulo_prog)
        c = _cartao()
        self.lista_passos = QVBoxLayout(c)
        self.lista_passos.setContentsMargins(18, 14, 18, 14)
        self.lista_passos.setSpacing(8)
        v.addWidget(c)
        self.det = _rot("", "info")
        v.addWidget(self.det)
        self.barra = QProgressBar()
        self.barra.setObjectName("grossa")
        v.addWidget(self.barra)
        self.bt_log = QPushButton(tr("Mostrar detalhes"))
        self.bt_log.setObjectName("secao")
        self.bt_log.setCheckable(True)
        self.bt_log.toggled.connect(lambda s: self.log.setVisible(s))
        v.addWidget(self.bt_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMaximumBlockCount(4000)
        self.log.setStyleSheet("font-family: Consolas; font-size: 11px;")
        v.addWidget(self.log, 1)
        v.addStretch()
        h = QHBoxLayout()
        h.addStretch()
        self.bt_pular = QPushButton(tr("Pular teste"))
        self.bt_pular.setToolTip(tr("O programa já está instalado; o teste só confere a placa de vídeo."))
        self.bt_pular.clicked.connect(lambda: setattr(self, "_pular_teste", True))
        self.bt_pular.setVisible(False)
        h.addWidget(self.bt_pular)
        self.bt_cancelar = QPushButton(tr("Cancelar"))
        self.bt_cancelar.clicked.connect(self._pedir_cancelar)
        h.addWidget(self.bt_cancelar)
        self.bt_fim = QPushButton(tr("Abrir o Flux Estúdio"))
        self.bt_fim.setObjectName("principal")
        self.bt_fim.setMinimumWidth(220)
        self.bt_fim.setMinimumHeight(46)
        self.bt_fim.setVisible(False)
        self.bt_fim.clicked.connect(self._abrir_programa)
        h.addWidget(self.bt_fim)
        self.bt_voltar = QPushButton(tr("Tentar de novo"))
        self.bt_voltar.setVisible(False)
        self.bt_voltar.clicked.connect(lambda: self._comecar(self._modo_atual))
        h.addWidget(self.bt_voltar)
        v.addLayout(h)
        return w

    def _montar_passos(self, nomes):
        while self.lista_passos.count():
            it = self.lista_passos.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rotulos = []
        for n in nomes:
            l = QLabel("○   " + n)
            l.setStyleSheet(f"color: {tema.APAGADO}; font-size: 13.5px;")
            self.lista_passos.addWidget(l)
            self._rotulos.append((l, n))

    def _ao_passo(self, i, estado):
        if i < 0 or i >= len(self._rotulos):
            return
        l, n = self._rotulos[i]
        simbolo, cor = {"rodando": ("◐", tema.ACENTO2), "ok": ("✓", tema.VERDE), "erro": ("✕", tema.VERMELHO),
                        "pulado": ("–", tema.APAGADO)}[estado]
        l.setText(f"{simbolo}   {n}")
        l.setStyleSheet(f"color: {cor if estado != 'ok' else tema.TEXTO}; font-size: 13.5px;"
                        + ("font-weight: 700;" if estado == "rodando" else ""))
        if estado == "rodando":
            self.barra.setRange(0, 0)

    def _ao_barra(self, feito, total):
        if total <= 0:
            self.barra.setRange(0, 0)
        else:
            self.barra.setRange(0, 1000)
            self.barra.setValue(max(0, min(1000, int(1000 * feito / total))))

    def _pedir_cancelar(self):
        if QMessageBox.question(self, tr("Cancelar"), tr("Parar agora? Dá para continuar depois rodando o instalador de novo.")) \
                == QMessageBox.Yes:
            self._cancelar = True
            self.det.setText(tr("Cancelando…"))
            return True
        return False

    # ------------------------------------------------------------------ execução
    def _verificar_programa_fechado(self, pasta):
        if inst.programa_aberto(pasta):
            QMessageBox.warning(self, tr("Flux Estúdio aberto"),
                                tr("Feche o Flux Estúdio antes de atualizar, reinstalar ou desinstalar."))
            return False
        return True

    def _comecar(self, modo):
        pasta = os.path.normpath(self.pasta.text().strip())
        drv = os.path.splitdrive(pasta)[0]
        if not pasta or len(pasta) < 4 or len(drv) != 2:
            QMessageBox.warning(self, tr("Pasta inválida"), tr("Escolha uma pasta válida (ex.: D:\\FluxEstudio)."))
            return
        if not self._verificar_programa_fechado(pasta):
            return
        if modo == "instalar":
            livre = self._espaco_livre_gb(pasta)
            minimo = 22 if (self.cb_modelo.isChecked() and not self.pasta_modelo_existente) else 7
            if livre is not None and livre < minimo:
                r = QMessageBox.question(
                    self, tr("Pouco espaço em disco"),
                    tr("Este disco tem apenas {gb:.1f} GB livres (recomendado: pelo menos {min} GB). Continuar mesmo assim?",
                       gb=livre, min=minimo),
                )
                if r != QMessageBox.Yes:
                    return

        self._modo_atual = modo
        self._cancelar = False
        self._instalando = True
        self.pasta_final = pasta
        info = inst.ler_info(pasta)
        if modo == "atualizar" and info.get("backend") and not self._backend_editado:
            self.backend_final = info["backend"]
            self.gfx_final = info.get("gfx") or self.combo_gfx.currentData() or "gfx1201"
        else:
            self.backend_final = self.backend.currentData()
            self.gfx_final = self.combo_gfx.currentData() or self.deteccao.get("gfx") or "gfx1201"
        passos = [("programa", tr("Baixar o programa"))]
        if modo == "instalar" or not os.path.isfile(inst.python_do_venv(pasta)):
            passos += [("python", tr("Preparar o Python")), ("torch", tr("Instalar o PyTorch para a sua placa (~3 GB)")),
                       ("bibliotecas", tr("Instalar as bibliotecas"))]
        else:
            passos += [("bibliotecas", tr("Atualizar as bibliotecas (se mudaram)"))]
        if self.cb_modelo.isChecked() and modo == "instalar":
            passos += [("modelo", tr("Baixar o modelo FLUX.2 [klein] 4B (~15 GB)"))]
        passos += [("atalhos", tr("Criar atalhos"))]
        if modo == "instalar":
            passos += [("teste", tr("Testar a placa de vídeo"))]
        self._passos = passos
        self._montar_passos([n for _, n in passos])
        self.titulo_prog.setText(tr("Atualizando…") if modo == "atualizar" else tr("Instalando…"))
        self.bt_fim.setVisible(False)
        self.bt_voltar.setVisible(False)
        self.bt_cancelar.setVisible(True)
        self.idioma.setEnabled(False)
        self.pilha.setCurrentIndex(1)
        threading.Thread(target=self._executar, args=(modo,), daemon=True).start()

    def _executar(self, modo):
        p = self.ponte
        pasta = self.pasta_final
        cancelado = lambda: self._cancelar
        barra = lambda f, t: p.barra.emit(f, t)
        requisitos_mudaram = True
        resultado_teste = ""
        i = 0
        try:
            os.makedirs(pasta, exist_ok=True)
            for i, (chave, nome) in enumerate(self._passos):
                if self._cancelar:
                    raise InterruptedError
                p.passo.emit(i, "rodando")
                p.linha.emit(f"== {nome}")
                if chave == "programa":
                    p.texto.emit("github.com/" + inst.REPO)
                    requisitos_mudaram = inst.instalar_programa(pasta, barra, cancelado) or modo == "instalar"
                elif chave == "python":
                    p.texto.emit(tr("Baixando o uv e o Python {v}…", v=inst.PYTHON))
                    inst.instalar_uv(pasta, barra, cancelado)
                    p.barra.emit(0, 0)
                    inst.rodar(inst.comandos_instalacao(pasta, self.backend_final, self.gfx_final)[0][1], pasta,
                               lambda l: (p.linha.emit(l), p.texto.emit(l[:120])), cancelado)
                elif chave == "torch":
                    inst.rodar(inst.comandos_instalacao(pasta, self.backend_final, self.gfx_final)[1][1], pasta,
                               lambda l: (p.linha.emit(l), p.texto.emit(l[:120])), cancelado)
                elif chave == "bibliotecas":
                    if not requisitos_mudaram and os.path.isfile(inst.python_do_venv(pasta)):
                        p.passo.emit(i, "pulado")
                        continue
                    inst.instalar_uv(pasta, barra, cancelado)
                    inst.rodar(inst.comandos_instalacao(pasta, self.backend_final, self.gfx_final)[2][1], pasta,
                               lambda l: (p.linha.emit(l), p.texto.emit(l[:120])), cancelado)
                elif chave == "modelo":
                    self._baixar_modelo(pasta, cancelado)
                elif chave == "atalhos":
                    self._finalizar(pasta)
                elif chave == "teste":
                    resultado_teste = self._testar(pasta, cancelado)
                    if resultado_teste is None:
                        p.passo.emit(i, "pulado")
                        continue
                    if not resultado_teste.startswith("OK"):
                        p.passo.emit(i, "erro")  # a instalação terminou; só o teste da placa não passou
                        continue
                p.passo.emit(i, "ok")
            p.fim.emit(True, resultado_teste or "")
        except InterruptedError:
            p.passo.emit(i, "erro")
            p.fim.emit(False, tr("Cancelado. Rode o instalador de novo para continuar de onde parou."))
        except Exception as e:  # noqa: BLE001
            p.linha.emit(traceback.format_exc())
            p.passo.emit(i, "erro")
            p.fim.emit(False, str(e)[-600:])

    def _baixar_modelo(self, pasta, cancelado):
        """Usa o baixador do próprio programa (retomável, em paralelo) e lê o progresso dele."""
        cmd = [inst.python_do_venv(pasta), "-u", "-m", "app.baixador", "flux2-klein-4b"]

        def ao_linha(l):
            m = re.match(r"\s*([\d.]+)%\s+([\d.]+)/([\d.]+) GB\s+([\d.]+) MB/s", l)
            if m:
                self.ponte.barra.emit(int(float(m.group(1)) * 10), 1000)
                self.ponte.texto.emit(f"{m.group(2)} / {m.group(3)} GB · {m.group(4)} MB/s")
            else:
                self.ponte.linha.emit(l)
        inst.rodar(cmd, pasta, ao_linha, cancelado)

    def _finalizar(self, pasta):
        if self.pasta_modelo_existente:
            import json
            arq = os.path.join(pasta, "config.json")
            dados = {}
            if os.path.isfile(arq):
                try:
                    with open(arq, encoding="utf-8") as f:
                        dados = json.load(f)
                except (OSError, ValueError):
                    dados = {}
            dados.setdefault("pastas", {})["flux2-klein-4b"] = self.pasta_modelo_existente
            with open(arq, "w", encoding="utf-8") as f:
                json.dump(dados, f, indent=2, ensure_ascii=False)
        # Uma cópia do instalador fica na pasta: serve para atualizar e desinstalar depois.
        copia = None
        if getattr(sys, "frozen", False):
            copia = os.path.join(pasta, "ferramentas", "Instalador.exe")
            os.makedirs(os.path.dirname(copia), exist_ok=True)
            if os.path.normcase(sys.executable) != os.path.normcase(copia):
                try:
                    shutil.copy2(sys.executable, copia)
                except OSError:
                    pass
        inst.criar_atalhos(pasta, self.cb_atalho.isChecked(), copia)
        inst.gravar_info(pasta, backend=self.backend_final, gfx=self.gfx_final if self.backend_final == "rocm" else None,
                         placa=self.deteccao.get("placa", ""), versao=inst.versao_local(pasta), idioma=i18n.atual())
        cfg = os.path.join(pasta, "config.json")
        if not os.path.isfile(cfg):
            import json
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"idioma": i18n.atual()}, f)
        inst.limpar_cache(pasta)

    LIMITE_TESTE_S = 15 * 60  # placas AMD/Intel compilam kernels na 1ª vez; depois disso é travamento

    def _testar(self, pasta, cancelado):
        """Roda o autoteste do programa (abre o modelo e gera uma imagem pequena) se o modelo estiver lá.
        Nunca segura a instalação: tem limite de tempo e o botão Pular teste; o resultado (ou até onde
        chegou) vem do autoteste.txt, que o teste grava a cada etapa."""
        py = inst.python_do_venv(pasta)
        tem_modelo = os.path.isfile(os.path.join(pasta, "modelos", "flux2-klein-4b", "model_index.json")) or \
            self.pasta_modelo_existente
        if not tem_modelo:
            return None
        self.ponte.texto.emit(tr("Abrindo o modelo e gerando uma imagem de teste (1–2 min)…"))
        self._pular_teste = False
        self.ponte.mostrar_pular.emit(True)
        aviso = ""
        try:
            inst.rodar([py, "FluxEstudio.py", "--autoteste"], pasta,
                       lambda l: (self.ponte.linha.emit(l), self.ponte.texto.emit(l[:140])),
                       lambda: cancelado() or self._pular_teste, limite_s=self.LIMITE_TESTE_S)
        except inst.TempoEsgotado:
            aviso = tr("O teste passou de 15 minutos e foi interrompido.")
        except InterruptedError:
            if not self._pular_teste:
                raise
            aviso = tr("Teste pulado.")
        except RuntimeError:
            pass
        finally:
            self.ponte.mostrar_pular.emit(False)
        try:
            with open(os.path.join(pasta, "autoteste.txt"), encoding="utf-8") as f:
                texto = f.read().strip()
        except OSError:
            texto = "FALHOU"
        if aviso:
            texto = "FALHOU\n" + aviso + "\n" + texto.split("\n", 1)[-1]
        return texto

    def _ao_fim(self, ok, msg):
        self._instalando = False
        self.bt_cancelar.setVisible(False)
        self.bt_pular.setVisible(False)
        self.idioma.setEnabled(True)
        self.barra.setRange(0, 1000)
        self.barra.setValue(1000 if ok else 0)
        if ok:
            self.titulo_prog.setText(tr("Pronto!"))
            if msg.startswith("OK"):
                self.det.setText(tr("Teste da placa: OK — tudo funcionando.") + "\n" + msg.split("\n", 1)[-1])
            elif msg:
                arq = os.path.join(self.pasta_final, "autoteste.txt")
                ultimas = "\n".join(msg.split("\n")[1:][-6:])
                self.det.setText(tr("O programa foi instalado, mas o teste da placa não terminou. Você já pode abrir o "
                                    "Flux Estúdio. Para diagnosticar, mande este arquivo: {arq}", arq=arq) + "\n\n" + ultimas)
                self.det.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.bt_log.setChecked(True)
            else:
                self.det.setText(tr("Instalado. Baixe o modelo na aba Modelos do programa."))
            self.bt_fim.setVisible(True)
        else:
            self.titulo_prog.setText(tr("Não deu certo"))
            self.det.setText(msg)
            self.bt_log.setChecked(True)
            self.bt_voltar.setVisible(True)

    def _abrir_programa(self):
        pasta = self.pasta_final
        subprocess.Popen([inst.python_do_venv(pasta, janela=True), os.path.join(pasta, "FluxEstudio.py")], cwd=pasta,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        self.close()

    # ------------------------------------------------------------------ desinstalar
    def _desinstalar(self):
        pasta = os.path.normpath(self.pasta.text().strip())
        if not self._verificar_programa_fechado(pasta):
            return
        caixa = QMessageBox(self)
        caixa.setWindowTitle(tr("Desinstalar"))
        caixa.setText(tr("Remover o Flux Estúdio de {p}?", p=pasta))
        manter = QCheckBox(tr("Manter modelos, imagens e prompts salvos"))
        manter.setChecked(True)
        caixa.setCheckBox(manter)
        caixa.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if caixa.exec() != QMessageBox.Yes:
            return
        inst.remover_atalhos()
        eu = os.path.normcase(sys.executable) if getattr(sys, "frozen", False) else ""
        if manter.isChecked():
            for item in list(inst.DO_PROGRAMA) + [".venv", "instalacao.json", "flux_estudio.log", "flux_estudio.log.1",
                                                   "flux_estudio.log.2", "autoteste.txt"]:
                alvo = os.path.join(pasta, item)
                shutil.rmtree(alvo, ignore_errors=True) if os.path.isdir(alvo) else _apagar(alvo)
            _apagar_arvore(os.path.join(pasta, "ferramentas"), eu)
        else:
            _apagar_arvore(pasta, eu)
        QMessageBox.information(self, tr("Desinstalar"), tr("Flux Estúdio removido."))
        if eu:
            # O instalador não pode apagar a si mesmo enquanto roda: o Windows apaga logo depois.
            subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 > nul & del /f /q "{sys.executable}" & rmdir "{os.path.dirname(sys.executable)}"',
                             shell=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.close()

    def closeEvent(self, e):
        if self._instalando and not self._cancelar:
            if not self._pedir_cancelar():
                e.ignore()
                return
        super().closeEvent(e)


def _apagar(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _apagar_arvore(raiz, exceto=""):
    if not os.path.isdir(raiz):
        return
    for atual, _pastas, arqs in os.walk(raiz, topdown=False):
        for a in arqs:
            c = os.path.join(atual, a)
            if os.path.normcase(c) != exceto:
                _apagar(c)
        try:
            os.rmdir(atual)
        except OSError:
            pass


def main():
    app = QApplication(sys.argv)
    i18n.iniciar(so_detectar=True)
    tema.aplicar(app)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ico = os.path.join(base, "icone.ico")
    if os.path.isfile(ico):
        app.setWindowIcon(QIcon(ico))
    app._janela = Instalador()
    app._janela.show()
    tema.barra_titulo_escura(app._janela)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
