"""Janela principal: barra lateral, abas, motor em segundo plano, atalhos globais e colar/arrastar."""
import logging
import os
import queue
import sys
import threading

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QProcess, QPropertyAnimation, QRect, QSettings, QSize, Qt,
                            QThread, QTimer, Signal)
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget,
                               QTextEdit, QVBoxLayout, QWidget)

from . import config, tema
from .biblioteca import Biblioteca
from .i18n import tr
from .modelos import POR_ID, PRINCIPAL
from .motor import Motor
from .paginas import (PaginaConfig, PaginaLimpar, PaginaEditar, PaginaGaleria, PaginaGerar, PaginaMascara, PaginaModelos,
                      TrabalhoDownload)
from .widgets import Toast, botao_icone, imagem_do_mime, imagens_do_mime, rotulo

log = logging.getLogger("janela")

ABAS = [("gerar", "Gerar", "gerar"), ("editar", "Editar", "editar"), ("mascara", "Máscara", "mascara"),
        ("limpar", "Limpar", "borracha"), ("galeria", "Galeria", "galeria"), ("modelos", "Modelos", "modelo"), ("config", "Configurações", "chip")]


class BarraLateral(QWidget):
    escolhida = Signal(str)

    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        self.setObjectName("barraLateral")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(224)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 20, 14, 14)
        v.setSpacing(4)
        logo = QHBoxLayout()
        marca = QLabel("✦")
        marca.setStyleSheet(f"font-size: 22px; color: {tema.ACENTO2};")
        logo.addWidget(marca)
        tx = QVBoxLayout()
        tx.setSpacing(0)
        tx.addWidget(rotulo("Flux Estúdio", "logo"))
        tx.addWidget(rotulo(tr("imagens locais na sua placa"), "logoSub"))
        logo.addLayout(tx)
        logo.addStretch()
        v.addLayout(logo)
        v.addSpacing(18)
        self.botoes = {}
        for n, (chave, texto, ic) in enumerate(ABAS):
            if chave == "galeria":
                v.addSpacing(10)
            b = QPushButton("  " + tr(texto))
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(tema.icone(ic, tema.SUAVE, 18))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"Ctrl+{n + 1}")
            b.clicked.connect(lambda _=False, c=chave: self.escolhida.emit(c))
            v.addWidget(b)
            self.botoes[chave] = (b, ic)
        v.addStretch()

        # indicador que desliza até a aba atual
        self.indicador = QWidget(self)
        self.indicador.setStyleSheet(f"background: {tema.ACENTO}; border-radius: 2px;")
        self.indicador.resize(3, 20)
        self._anim = QPropertyAnimation(self.indicador, b"geometry", self, duration=220,
                                        easingCurve=QEasingCurve.OutCubic)

        # rodapé: placa e memória
        rod = QWidget()
        rod.setObjectName("cartao")
        rod.setAttribute(Qt.WA_StyledBackground, True)
        rv = QVBoxLayout(rod)
        rv.setContentsMargins(12, 10, 12, 12)
        rv.setSpacing(6)
        self.placa = rotulo(tr("Verificando a placa…"), "info")
        self.placa.setWordWrap(True)
        rv.addWidget(self.placa)
        self.vram = QProgressBar()
        self.vram.setRange(0, 1000)
        rv.addWidget(self.vram)
        self.vram_txt = rotulo("", "dica")
        rv.addWidget(self.vram_txt)
        h = QHBoxLayout()
        h.setSpacing(4)
        self.ponto = QLabel("●")
        self.ponto.setStyleSheet(f"color: {tema.APAGADO}; font-size: 10px;")
        h.addWidget(self.ponto)
        self.modelo = rotulo(tr("Nenhum modelo aberto"), "dica")
        self.modelo.setWordWrap(True)
        h.addWidget(self.modelo, 1)
        self.bt_liberar = botao_icone("parar", tr("Liberar a placa de vídeo (descarrega o modelo)"),
                                      self.janela.liberar_placa, tamanho=14)
        self.bt_liberar.setVisible(False)
        h.addWidget(self.bt_liberar)
        rv.addLayout(h)
        v.addWidget(rod)

    def marcar(self, chave, animar=True):
        for c, (b, ic) in self.botoes.items():
            b.setChecked(c == chave)
            b.setIcon(tema.icone(ic, tema.TEXTO if c == chave else tema.SUAVE, 18))
        b = self.botoes[chave][0]
        alvo = QRect(2, b.y() + (b.height() - 20) // 2, 3, 20)
        if animar and self.indicador.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self.indicador.geometry())
            self._anim.setEndValue(alvo)
            self._anim.start()
        else:
            self.indicador.setGeometry(alvo)
        self.indicador.show()
        self.indicador.raise_()

    def modelo_aberto(self, id_modelo):
        if id_modelo:
            self.modelo.setText(tr("{nome} na placa", nome=POR_ID[id_modelo]["nome"]))
            self.ponto.setStyleSheet(f"color: {tema.VERDE}; font-size: 10px;")
        else:
            self.modelo.setText(tr("Nenhum modelo aberto"))
            self.ponto.setStyleSheet(f"color: {tema.APAGADO}; font-size: 10px;")
        self.bt_liberar.setVisible(bool(id_modelo))


class JanelaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flux Estúdio")
        self.setMinimumSize(1180, 720)
        self.prefs = QSettings(config.ARQ_PREFS, QSettings.IniFormat)
        self.biblioteca = Biblioteca()
        self.ocupado = False
        self.dono = None
        self.downloads = {}
        self._torch_pronto = False

        raiz = QWidget()
        raiz.setObjectName("raiz")
        h = QHBoxLayout(raiz)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.lateral = BarraLateral(self)
        self.lateral.escolhida.connect(self.ir_para)
        h.addWidget(self.lateral)
        self.pilha = QStackedWidget()
        h.addWidget(self.pilha, 1)
        self.setCentralWidget(raiz)

        self.paginas = {
            "gerar": PaginaGerar(self), "editar": PaginaEditar(self), "mascara": PaginaMascara(self),
            "limpar": PaginaLimpar(self),
            "galeria": PaginaGaleria(self), "modelos": PaginaModelos(self), "config": PaginaConfig(self),
        }
        for p in self.paginas.values():
            self.pilha.addWidget(p)

        self._toast = Toast(self)
        self._cobertura = QLabel(self.pilha)
        self._cobertura.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._cobertura.hide()
        self._ef = QGraphicsOpacityEffect(self._cobertura)
        self._cobertura.setGraphicsEffect(self._ef)
        self._anim_aba = QPropertyAnimation(self._ef, b"opacity", self, duration=170, easingCurve=QEasingCurve.OutCubic)
        self._anim_aba.finished.connect(self._cobertura.hide)

        # Motor numa thread do Python (NÃO QThread): inicializar CUDA numa thread criada pelo Qt
        # corrompe a memória no Windows e o programa fecha sozinho ao abrir o modelo.
        self._fila = queue.Queue()
        self.motor = Motor()
        self.motor.estado.connect(self._estado)
        self.motor.passo.connect(self._passo)
        self.motor.imagem.connect(self._imagem)
        self.motor.concluido.connect(self._concluido)
        self.motor.falhou.connect(self._falhou)
        self.motor.carregado.connect(self.lateral.modelo_aberto)
        self.motor.carregado.connect(self._ao_carregado)
        self.motor.carregando.connect(self._ao_carregando)
        self.modelo_na_placa = ""
        self.modelo_carregando = ""
        self.motor.placa.connect(self._placa)
        self._trabalhador = threading.Thread(target=self._laco_motor, name="motor", daemon=True)
        self._trabalhador.start()
        # Depois que a janela aparece: importar o torch disputa o processador com a montagem da tela.
        QTimer.singleShot(400, lambda: self.pedir({"tipo": "info"}))

        self._monitor = QTimer(self, interval=1500)
        self._monitor.timeout.connect(self._medir_vram)

        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)
        self._restaurar()

    # ------------------------------------------------------------ navegação
    def ir_para(self, chave):
        alvo = self.paginas[chave]
        atual = self.pilha.currentWidget()
        if atual is not alvo and self.isVisible():
            self._cobertura.setPixmap(atual.grab())
            self._cobertura.setGeometry(0, 0, self.pilha.width(), self.pilha.height())
            self._ef.setOpacity(1.0)
            self._cobertura.show()
            self._cobertura.raise_()
            self.pilha.setCurrentWidget(alvo)
            self._anim_aba.stop()
            self._anim_aba.setStartValue(1.0)
            self._anim_aba.setEndValue(0.0)
            self._anim_aba.start()
        else:
            self.pilha.setCurrentWidget(alvo)
        self.lateral.marcar(chave, self.isVisible())
        if chave == "modelos":
            self.paginas["modelos"].atualizar()
        if hasattr(alvo, "prompt") and not self.ocupado:
            QTimer.singleShot(0, alvo.prompt.edit.setFocus)

    def pagina_atual(self):
        return self.pilha.currentWidget()

    def chave_atual(self):
        for k, p in self.paginas.items():
            if p is self.pilha.currentWidget():
                return k
        return "gerar"

    def enviar_para(self, destino, imgs):
        self.paginas[destino].colar(imgs)
        self.ir_para(destino)

    def colar_aqui(self):
        imgs = imagens_do_mime(QGuiApplication.clipboard().mimeData())
        if imgs:
            self.pagina_atual().colar(imgs)
            self.toast(tr("Imagem colada"), "ok", 1400)
        else:
            self.toast(tr("Não há imagem na área de transferência. Copie no Photoshop (Ctrl+C) e cole aqui."), "aviso")

    def toast(self, texto, tipo="info", ms=2600):
        self._toast.mostrar(texto, tipo, ms)

    # ------------------------------------------------------------ motor
    def pedir(self, tarefa):
        self._fila.put(tarefa)

    def _laco_motor(self):
        while True:
            tarefa = self._fila.get()
            if tarefa is None:
                return
            self.motor.executar(tarefa)

    def rodar(self, tarefa, pagina):
        self.ocupado, self.dono = True, pagina
        pagina.ao_comecar()
        self.pedir(tarefa)

    def cancelar(self):
        if self.ocupado:
            self.motor.cancelar = True
            self.dono.ao_estado(tr("Cancelando…"))

    def liberar_placa(self):
        if self.ocupado:
            self.toast(tr("Espere terminar ou cancele (Esc) antes."), "aviso")
            return
        self.pedir({"tipo": "liberar"})

    def _estado(self, texto):
        if self.dono:
            self.dono.ao_estado(texto)
        else:  # carregando em segundo plano (ao abrir o programa)
            self.lateral.modelo.setText(texto)

    def _passo(self, feito, total):
        if self.dono:
            self.dono.ao_passo(feito, total)

    def _imagem(self, caminho):
        if self.dono:
            self.dono.ao_imagem(caminho)

    def _concluido(self, d):
        dono, self.ocupado, self.dono = self.dono, False, None
        if dono:
            dono.ao_fim(d)
        n = len(d.get("caminhos", []))
        if d.get("cancelado"):
            self.toast(tr("Cancelado"), "aviso")
        elif n:
            self.toast(tr("Pronto · salvo em saidas/") if n == 1 else tr("{n} imagens prontas · salvas em saidas/", n=n), "ok")
            if not self.isActiveWindow():
                QApplication.alert(self)

    # ------------------------------------------------------------ modelo na placa
    def estado_modelo(self, id_modelo):
        if getattr(self, "modelo_carregando", "") == id_modelo:
            return "carregando"
        return "pronto" if getattr(self, "modelo_na_placa", "") == id_modelo else "fora"

    def carregar_modelo(self, m, modo="gerar"):
        if self.modelo_carregando:
            return
        if not config.modelo_instalado(m["id"], excluir=m.get("excluir", ())):
            self.toast(tr("Baixe o modelo {nome} primeiro (aba Modelos).", nome=m["nome"]), "aviso", 4000)
            self.ir_para("modelos")
            return
        self.modelo_carregando = m["id"]
        self.lateral.modelo.setText(tr("Carregando {nome}…", nome=m["nome"]))
        self._atualizar_botoes()
        self.pedir({"tipo": "carregar", "modelo": m["id"], "modo": modo})

    def _ao_carregando(self, id_modelo):
        self.modelo_carregando = id_modelo
        self._atualizar_botoes()

    def _ao_carregado(self, id_modelo):
        estava = self.modelo_carregando
        self.modelo_carregando = ""
        self.modelo_na_placa = id_modelo
        self._atualizar_botoes()
        if id_modelo and estava:
            self.toast(tr("{nome} pronto na placa — pode gerar", nome=POR_ID[id_modelo]["nome"]), "ok", 3000)

    def _atualizar_botoes(self):
        for k in ("gerar", "editar", "mascara"):
            self.paginas[k].atualizar_botao()

    def _perguntar_carregar(self):
        from PySide6.QtWidgets import QCheckBox, QDialog
        d = QDialog(self)
        d.setWindowTitle(tr("Carregar o modelo?"))
        d.setStyleSheet(f"QDialog {{ background: {tema.PAINEL}; }}")
        v = QVBoxLayout(d)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(12)
        v.addWidget(rotulo(tr("Carregar o {nome} agora?", nome=PRINCIPAL["nome"]), "titulo"))
        txt = rotulo(tr("O modelo vai para a placa de vídeo uma vez (cerca de 30 s a 1 min). Depois cada imagem sai em poucos segundos. "
                        "Enquanto isso você já pode colar imagens e escrever o prompt."), "subtitulo")
        txt.setWordWrap(True)
        v.addWidget(txt)
        lembrar = QCheckBox(tr("Lembrar minha escolha"))
        v.addWidget(lembrar)
        h = QHBoxLayout()
        h.addStretch()
        nao = QPushButton(tr("Agora não"))
        nao.clicked.connect(d.reject)
        sim = QPushButton(tr("Carregar agora"))
        sim.setObjectName("principal")
        sim.setMinimumWidth(170)
        sim.setDefault(True)
        sim.clicked.connect(d.accept)
        h.addWidget(nao)
        h.addWidget(sim)
        v.addLayout(h)
        d.setMinimumWidth(500)
        tema.barra_titulo_escura(d)
        ok = d.exec() == QDialog.Accepted
        if lembrar.isChecked():
            config.salvar({"ao_abrir": "sempre" if ok else "nunca"})
            cfg = self.paginas["config"]
            cfg.ao_abrir.setCurrentIndex(1 if ok else 2)
        if ok:
            self.carregar_modelo(PRINCIPAL, "editar")

    def _falhou(self, msg):
        if self.modelo_carregando:
            self.modelo_carregando = ""
            self._atualizar_botoes()
        dono, self.ocupado, self.dono = self.dono, False, None
        if dono:
            dono.ao_fim(None, msg)
        self.toast(msg, "erro", 6000)

    def _placa(self, nome, backend, total):
        self._torch_pronto = bool(nome)
        self._vram_total = total
        if nome:
            self.lateral.placa.setText(f"{nome}\n{backend} · {total:.0f} GB")
            self._monitor.start()
            self._medir_vram()
        else:
            self.lateral.placa.setText(tr("Sem placa compatível (processador)"))
            self.lateral.vram.hide()
        self.paginas["config"].definir_placa(nome, backend, total)
        # Carregar o modelo é a parte demorada: na abertura o programa pergunta (ou segue a escolha salva).
        if config.modelo_instalado(PRINCIPAL["id"]):
            escolha = config.carregar().get("ao_abrir", "perguntar")
            if escolha == "sempre":
                self.carregar_modelo(PRINCIPAL, "editar")
            elif escolha == "perguntar":
                QTimer.singleShot(250, self._perguntar_carregar)
        self._atualizar_botoes()

    def _medir_vram(self):
        try:
            import torch
            livre, total = torch.cuda.mem_get_info()
        except Exception:  # noqa: BLE001
            return
        usado = total - livre
        self.lateral.vram.setValue(int(1000 * usado / total))
        self.lateral.vram_txt.setText(tr("VRAM {u:.1f} de {t:.1f} GB", u=usado / 2**30, t=total / 2**30))

    # ------------------------------------------------------------ downloads
    def baixar(self, m):
        if m["id"] in self.downloads:
            return
        th = QThread(self)
        tr_ = TrabalhoDownload(m)
        tr_.moveToThread(th)
        th.started.connect(tr_.rodar)
        cartao = self.paginas["modelos"].cartoes[m["id"]]
        tr_.progresso.connect(cartao.progresso)
        tr_.terminou.connect(lambda ok, erro, mid=m["id"]: self._download_fim(mid, ok, erro))
        self.downloads[m["id"]] = (th, tr_)
        th.start()
        cartao.atualizar()
        self.toast(tr("Baixando {nome}…", nome=m["nome"]))

    def cancelar_download(self, id_modelo):
        if id_modelo in self.downloads:
            self.downloads[id_modelo][1].b.cancelar()

    def _download_fim(self, id_modelo, ok, erro):
        th, _ = self.downloads.pop(id_modelo)
        th.quit()
        th.wait(2000)
        nome = POR_ID[id_modelo]["nome"]
        if ok:
            self.toast(tr("{nome} baixado e pronto para usar", nome=nome), "ok", 4000)
        elif erro:
            log.error("download %s: %s", id_modelo, erro)
            self.toast(tr("Falha ao baixar {nome}: {erro}. Clique em Continuar download.", nome=nome, erro=erro[:160]), "erro", 7000)
        else:
            self.toast(tr("Download pausado — continua de onde parou"), "aviso")
        self.modelos_mudaram()

    def modelos_mudaram(self):
        self.paginas["modelos"].atualizar()
        for k in ("gerar", "editar", "mascara"):
            self.paginas[k].seletor.atualizar()

    # ------------------------------------------------------------ teclado, colar, arrastar
    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and self.isActiveWindow():
            mods, k = e.modifiers(), e.key()
            foco = QApplication.focusWidget()
            em_texto = isinstance(foco, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox))
            ctrl = bool(mods & Qt.ControlModifier)
            if e.matches(QKeySequence.Paste):
                md = QGuiApplication.clipboard().mimeData()
                # Num campo de texto, texto tem preferência (Word/Office também põem um "desenho" do texto).
                if not em_texto or not md.hasText():
                    imgs = imagens_do_mime(md)
                    if imgs:
                        self.pagina_atual().colar(imgs)
                        self.toast(tr("Imagem colada"), "ok", 1200)
                        return True
            elif ctrl and k in (Qt.Key_Return, Qt.Key_Enter):
                p = self.pagina_atual()
                if hasattr(p, "executar"):
                    p.executar()
                return True
            elif k == Qt.Key_Escape and self.ocupado:
                self.cancelar()
                return True
            elif e.matches(QKeySequence.Copy) and not (em_texto and foco.textCursor().hasSelection()
                                                       if hasattr(foco, "textCursor") else em_texto):
                p = self.pagina_atual()
                if hasattr(p, "copiar"):
                    p.copiar()
                    return True
            elif ctrl and Qt.Key_1 <= k <= Qt.Key_1 + len(ABAS) - 1:
                self.ir_para(ABAS[k - Qt.Key_1][0])
                return True
            elif k == Qt.Key_C and not mods and not em_texto:
                p = self.pagina_atual()
                if hasattr(p, "resultado"):
                    p.resultado.alternar_comparar()
                    return True
        return super().eventFilter(obj, e)

    def dragEnterEvent(self, e):
        if imagens_do_mime(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        imgs = imagens_do_mime(e.mimeData())
        if imgs:
            self.pagina_atual().colar(imgs)
            e.acceptProposedAction()

    # ------------------------------------------------------------ janela
    def showEvent(self, e):
        super().showEvent(e)
        tema.barra_titulo_escura(self)
        QTimer.singleShot(0, lambda: self.lateral.marcar(self.chave_atual(), False))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._toast.isVisible():
            self._toast.move((self.width() - self._toast.width()) // 2, self.height() - self._toast.height() - 28)

    def _restaurar(self):
        geo = self.prefs.value("janela/geometria")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1440, 900)
        for k in ("gerar", "editar", "mascara"):
            try:
                self.paginas[k].prefs_carregar(self.prefs)
            except Exception:  # noqa: BLE001 - preferência estragada não impede abrir
                log.exception("prefs %s", k)
        self.ir_para(self.prefs.value("janela/aba", "editar") if self.prefs.value("janela/aba", "editar") in self.paginas else "editar")

    def _salvar_prefs(self):
        self.prefs.setValue("janela/geometria", self.saveGeometry())
        self.prefs.setValue("janela/aba", self.chave_atual())
        for k in ("gerar", "editar", "mascara"):
            self.paginas[k].prefs_salvar(self.prefs)
        self.prefs.sync()

    def reiniciar(self):
        self._salvar_prefs()
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, sys.argv[1:])
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:])
        self.close()

    def closeEvent(self, e):
        self._salvar_prefs()
        for th, trab in list(self.downloads.values()):
            trab.b.cancelar()
            th.quit()
            th.wait(3000)
        self.motor.cancelar = True
        self._fila.put(None)
        self._trabalhador.join(3)
        super().closeEvent(e)
