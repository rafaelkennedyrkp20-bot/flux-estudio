"""Instalador do Flux Estúdio.

Detecta a placa de vídeo (NVIDIA -> CUDA, Radeon -> ROCm, nenhuma -> CPU), baixa o programa do
GitHub, instala Python + PyTorch certo + bibliotecas com o uv, baixa o modelo e cria os atalhos.
Rodando de novo na mesma pasta: atualiza, repara ou desinstala.

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

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from app import i18n, instalacao as inst, tema
from app.i18n import tr

VERSAO_INSTALADOR = "1.0.0"


def _rot(texto, nome="rotulo"):
    l = QLabel(texto)
    l.setObjectName(nome)
    l.setWordWrap(True)
    return l


def _cartao():
    f = QFrame()
    f.setObjectName("cartao")
    return f


class Ponte(QObject):
    passo = Signal(int, str)            # índice, estado: rodando/ok/erro/pulado
    texto = Signal(str)                 # detalhe atual
    barra = Signal(int, int)            # feito, total (total 0 = indeterminado)
    linha = Signal(str)                 # log
    fim = Signal(bool, str)             # sucesso, mensagem


class Instalador(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("Instalador do Flux Estúdio"))
        self.setObjectName("raiz")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.resize(820, 640)
        self.deteccao = inst.escolher_backend()
        self.pasta_modelo_existente = None
        self.ponte = Ponte()
        self._cancelar = False
        self.ponte.passo.connect(self._ao_passo)
        self.ponte.texto.connect(lambda s: self.det.setText(s))
        self.ponte.barra.connect(self._ao_barra)
        self.ponte.linha.connect(lambda s: self.log.appendPlainText(s))
        self.ponte.fim.connect(self._ao_fim)

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
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
        self.pilha.addWidget(self._pagina_config())
        self.pilha.addWidget(self._pagina_progresso())
        self._atualizar_modo()

    # ------------------------------------------------------------------ página 1: configurar
    def _pagina_config(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(_rot(tr("Placa de vídeo encontrada")))
        d = self.deteccao
        nome = d["placa"] or tr("nenhuma")
        if d["vram_gb"]:
            nome += f" · {d['vram_gb']:.0f} GB"
        self.lbl_placa = _rot(nome, "valor")
        self.lbl_placa.setStyleSheet("font-size: 15px;")
        cv.addWidget(self.lbl_placa)
        h = QHBoxLayout()
        h.addWidget(_rot(tr("Versão a instalar"), "subtitulo"))
        self.backend = QComboBox()
        self.backend.addItem("NVIDIA (CUDA)", "cuda")
        self.backend.addItem("AMD Radeon (ROCm)", "rocm")
        self.backend.addItem(tr("Sem placa compatível (processador, muito lento)"), "cpu")
        self.backend.setCurrentIndex(["cuda", "rocm", "cpu"].index(d["backend"]))
        self.backend.setMinimumWidth(320)
        self.backend.currentIndexChanged.connect(self._aviso_placa)
        h.addWidget(self.backend)
        h.addStretch()
        cv.addLayout(h)
        self.lbl_aviso = _rot("", "dica")
        cv.addWidget(self.lbl_aviso)
        v.addWidget(c)
        self._aviso_placa()

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(_rot(tr("Pasta de instalação")))
        h = QHBoxLayout()
        self.pasta = QLineEdit(inst.pasta_padrao())
        self.pasta.textChanged.connect(self._atualizar_modo)
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
        self.cb_modelo.setChecked(True)
        cv.addWidget(self.cb_modelo)
        h = QHBoxLayout()
        self.bt_modelo = QPushButton(tr("Já tenho o modelo numa pasta…"))
        self.bt_modelo.clicked.connect(self._modelo_existente)
        h.addWidget(self.bt_modelo)
        self.lbl_modelo = _rot("", "dica")
        h.addWidget(self.lbl_modelo, 1)
        cv.addLayout(h)
        self.cb_atalho = QCheckBox(tr("Criar atalho na área de trabalho"))
        self.cb_atalho.setChecked(True)
        cv.addWidget(self.cb_atalho)
        v.addWidget(c)
        v.addStretch()

        self.lbl_existente = _rot("", "subtitulo")
        v.addWidget(self.lbl_existente)
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
        h.addWidget(self.bt_principal)
        v.addLayout(h)
        return w

    def _aviso_placa(self):
        b = self.backend.currentData()
        d = self.deteccao
        if b == "cpu":
            t = tr("Sem placa NVIDIA ou Radeon compatível: vai funcionar, mas cada imagem pode levar vários minutos.")
        elif b == "rocm":
            t = tr("PyTorch oficial da AMD (ROCm) para {gfx}. Mantenha o driver AMD Adrenalin atualizado.",
                   gfx=d["gfx"] or "gfx1201")
        else:
            t = tr("PyTorch com CUDA 12.8.")
            if d["aviso"] == "driver_antigo":
                t += " " + tr("Seu driver NVIDIA é antigo: atualize para a versão 570 ou mais nova.")
        if d["vram_gb"] and d["vram_gb"] < 7.5 and b != "cpu":
            t += " " + tr("Placa com menos de 8 GB: prefira os modelos secundários de 8 GB.")
        self.lbl_aviso.setText(t)

    def _procurar(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta de instalação"), self.pasta.text())
        if p:
            p = os.path.normpath(p)
            if not os.path.basename(p).lower().startswith("flux") and os.listdir(p):
                p = os.path.join(p, "FluxEstudio")
            self.pasta.setText(p)

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

    def _atualizar_modo(self):
        pasta = self.pasta.text().strip()
        info = inst.ler_info(pasta) if pasta else {}
        try:
            livre = shutil.disk_usage(os.path.splitdrive(pasta)[0] + "\\").free / 2**30
            self.lbl_espaco.setText(tr("Espaço livre no disco: {gb:.0f} GB (precisa de ~25 GB com o modelo)", gb=livre))
        except OSError:
            self.lbl_espaco.setText("")
        self._atualizar_disco(pasta)
        if info:
            self._modo = "atualizar"
            self.bt_principal.setText(tr("Atualizar"))
            self.lbl_existente.setText(tr("Já instalado aqui (versão {v}, {b}). Atualizar baixa a versão nova do programa e mantém modelos, imagens e prompts.",
                                          v=inst.versao_local(pasta), b=info.get("backend", "?").upper()))
        else:
            self._modo = "instalar"
            self.bt_principal.setText(tr("Instalar"))
            self.lbl_existente.setText("")
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
        novo = Instalador()
        novo.idioma.blockSignals(True)
        novo.idioma.setCurrentIndex(i)
        novo.idioma.blockSignals(False)
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
            self.barra.setValue(int(1000 * feito / total))

    def _pedir_cancelar(self):
        if QMessageBox.question(self, tr("Cancelar"), tr("Parar agora? Dá para continuar depois rodando o instalador de novo.")) \
                == QMessageBox.Yes:
            self._cancelar = True
            self.det.setText(tr("Cancelando…"))

    # ------------------------------------------------------------------ execução
    def _comecar(self, modo):
        pasta = os.path.normpath(self.pasta.text().strip())
        if not pasta or len(pasta) < 4:
            return
        self._modo_atual = modo
        self._cancelar = False
        self.pasta_final = pasta
        info = inst.ler_info(pasta)
        if modo == "atualizar" and info.get("backend"):
            self.backend_final, self.gfx_final = info["backend"], info.get("gfx") or "gfx1201"
        else:
            self.backend_final = self.backend.currentData()
            self.gfx_final = self.deteccao["gfx"] or "gfx1201"
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
                with open(arq, encoding="utf-8") as f:
                    dados = json.load(f)
            dados.setdefault("pastas", {})["flux2-klein-4b"] = self.pasta_modelo_existente
            with open(arq, "w", encoding="utf-8") as f:
                json.dump(dados, f, indent=2, ensure_ascii=False)
        # Uma cópia do instalador fica na pasta: serve para atualizar e desinstalar depois.
        copia = None
        if getattr(sys, "frozen", False):
            copia = os.path.join(pasta, "ferramentas", "Instalador.exe")
            if os.path.normcase(sys.executable) != os.path.normcase(copia):
                shutil.copy2(sys.executable, copia)
        inst.criar_atalhos(pasta, self.cb_atalho.isChecked(), copia)
        inst.gravar_info(pasta, backend=self.backend_final, gfx=self.gfx_final if self.backend_final == "rocm" else None,
                         placa=self.deteccao["placa"], versao=inst.versao_local(pasta), idioma=i18n.atual())
        cfg = os.path.join(pasta, "config.json")
        if not os.path.isfile(cfg):
            import json
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"idioma": i18n.atual()}, f)
        inst.limpar_cache(pasta)

    def _testar(self, pasta, cancelado):
        """Roda o autoteste do programa (abre o modelo e gera uma imagem pequena) se o modelo estiver lá."""
        py = inst.python_do_venv(pasta)
        tem_modelo = os.path.isfile(os.path.join(pasta, "modelos", "flux2-klein-4b", "model_index.json")) or \
            self.pasta_modelo_existente
        if not tem_modelo:
            return None
        self.ponte.texto.emit(tr("Abrindo o modelo e gerando uma imagem de teste (1–2 min)…"))
        try:
            inst.rodar([py, "FluxEstudio.py", "--autoteste"], pasta, self.ponte.linha.emit, cancelado)
        except RuntimeError:
            pass
        try:
            with open(os.path.join(pasta, "autoteste.txt"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "FALHOU"

    def _ao_fim(self, ok, msg):
        self.bt_cancelar.setVisible(False)
        self.idioma.setEnabled(True)
        self.barra.setRange(0, 1000)
        self.barra.setValue(1000 if ok else 0)
        if ok:
            self.titulo_prog.setText(tr("Pronto!"))
            if msg.startswith("OK"):
                self.det.setText(tr("Teste da placa: OK — tudo funcionando.") + "\n" + msg.split("\n", 1)[-1])
            elif msg:
                self.det.setText(tr("O programa foi instalado, mas o teste da placa falhou. Detalhes:") + "\n" + msg)
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
            for item in list(inst.DO_PROGRAMA) + [".venv", "instalacao.json", "flux_estudio.log", "autoteste.txt"]:
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


def _apagar(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _apagar_arvore(raiz, exceto=""):
    for atual, pastas, arqs in os.walk(raiz, topdown=False):
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
