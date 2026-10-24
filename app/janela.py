"""Janela principal: barra lateral, abas, motor em segundo plano, atalhos globais e colar/arrastar."""
import logging
import os
import sys
import threading

from PySide6.QtCore import QEasingCurve, QEvent, QProcess, QPropertyAnimation, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from . import config, tema
from .biblioteca import Biblioteca
from .i18n import tr
from .modelos import POR_ID, PRINCIPAL
from .processo_motor import MotorRemoto
from .paginas import (PaginaConfig, PaginaEditar, PaginaGaleria, PaginaGerar, PaginaLimpar, PaginaMascara, PaginaModelos,
                      TrabalhoDownload)
from .widgets import Toast, botao_icone, imagens_do_mime, mime_tem_imagem, rotulo

log = logging.getLogger("janela")

ABAS = [("gerar", "Gerar", "gerar"), ("editar", "Editar", "editar"), ("mascara", "Máscara", "mascara"),
        ("limpar", "Limpar", "borracha"), ("galeria", "Galeria", "galeria"), ("modelos", "Modelos", "modelo"),
        ("config", "Configurações", "chip")]

NOMES_ABAS = {chave: texto for chave, texto, _ in ABAS}


class BarraLateral(QWidget):
    escolhida = Signal(str)

    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        self.setObjectName("barraLateral")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(224)
        self.setAcceptDrops(True)
        self._aba_arraste = None
        self._timer_arraste = QTimer(self, singleShot=True, interval=300)
        self._timer_arraste.timeout.connect(self._ativar_aba_arraste)

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
        self._anim = QPropertyAnimation(self.indicador, b"geometry", self, duration=180,
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

    def _aba_em(self, pos):
        for chave, (b, _) in self.botoes.items():
            if b.geometry().contains(pos):
                return chave
        return None

    def _destacar_arraste(self, chave):
        if chave == self._aba_arraste:
            return
        self._aba_arraste = chave
        self._timer_arraste.stop()
        for c, (b, _) in self.botoes.items():
            ativo = c == chave
            if bool(b.property("arrastando")) != ativo:
                b.setProperty("arrastando", ativo)
                b.style().unpolish(b)
                b.style().polish(b)
        if chave in ("editar", "mascara", "limpar"):
            self._timer_arraste.start()

    def _ativar_aba_arraste(self):
        if self._aba_arraste in ("editar", "mascara", "limpar"):
            self.escolhida.emit(self._aba_arraste)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if mime_tem_imagem(md) or md.hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        md = e.mimeData()
        if mime_tem_imagem(md) or md.hasUrls():
            self._destacar_arraste(self._aba_em(e.position().toPoint()))
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._destacar_arraste(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        alvo = self._aba_em(e.position().toPoint())
        self._destacar_arraste(None)
        destino = alvo if alvo in ("editar", "mascara", "limpar") else "editar"
        if self.janela.receber_mime(e.mimeData(), destino_forcado=destino):
            e.acceptProposedAction()

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
    _sinal_vram = Signal(float, float)

    def __init__(self, na_bandeja=False):
        super().__init__()
        self._na_bandeja = na_bandeja
        self.setWindowTitle("Flux Estúdio")
        self.setMinimumSize(1024, 640)
        self.prefs = QSettings(config.ARQ_PREFS, QSettings.IniFormat)
        self.biblioteca = Biblioteca()
        self.ocupado = False
        self.dono = None
        self.downloads = {}
        self._torch_pronto = False
        self._pos_liberar = []
        self._parar_vram = threading.Event()
        self._thread_vram = None
        self._sinal_vram.connect(self._atualizar_barra_vram)

        raiz = QWidget()
        raiz.setObjectName("raiz")
        h = QHBoxLayout(raiz)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.lateral = BarraLateral(self)
        self.lateral.escolhida.connect(self.ir_para)
        h.addWidget(self.lateral)

        painel_dir = QWidget()
        vd = QVBoxLayout(painel_dir)
        vd.setContentsMargins(0, 0, 0, 0)
        vd.setSpacing(0)
        self.pilha = QStackedWidget()
        vd.addWidget(self.pilha, 1)
        h.addWidget(painel_dir, 1)
        self.setCentralWidget(raiz)

        self.paginas = {
            "gerar": PaginaGerar(self), "editar": PaginaEditar(self), "mascara": PaginaMascara(self),
            "limpar": PaginaLimpar(self),
            "galeria": PaginaGaleria(self), "modelos": PaginaModelos(self), "config": PaginaConfig(self),
        }
        for p in self.paginas.values():
            self.pilha.addWidget(p)

        self._toast = Toast(self)

        # Motor num processo separado (ver processo_motor.py): a janela nunca congela por causa do PyTorch
        # e nem importa o torch. (Dentro deste processo, CUDA numa QThread corrompia a memória.)
        self.motor = MotorRemoto()
        self.motor.vram.connect(self._atualizar_barra_vram)
        self.motor.detectado.connect(self.paginas["limpar"]._ao_detectar)
        self.motor.estado.connect(self._estado)
        self.motor.passo.connect(self._passo)
        self.motor.imagem.connect(self._imagem)
        self.motor.concluido.connect(self._concluido)
        self.motor.falhou.connect(self._falhou)
        self.motor.carregado.connect(self.lateral.modelo_aberto)
        self.motor.carregado.connect(self._ao_carregado)
        self.motor.carregando.connect(self._ao_carregando)
        self.motor.liberado.connect(self._ao_liberado)
        self.modelo_na_placa = ""
        self.modelo_carregando = ""
        self.motor.placa.connect(self._placa)
        # Depois que a janela aparece: importar o torch disputa o processador com a montagem da tela.
        QTimer.singleShot(350, lambda: self.pedir({"tipo": "info"}))

        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)
        self._restaurar()
        self._criar_bandeja()
        QTimer.singleShot(400, self._decidir_abertura)  # logo depois do pedido "info" (350 ms), que vai antes na fila

    def _perguntar_ao_abrir(self):
        """Janela no meio da tela perguntando se liga o modelo agora (escolha pode ser lembrada)."""
        if self.ocupado or self.modelo_carregando or self.modelo_na_placa or not self.isVisible():
            return
        from .carregando import perguntar_ao_abrir
        carregar, lembrar = perguntar_ao_abrir(self, PRINCIPAL)
        if lembrar:
            config.salvar({"ao_abrir": "sempre" if carregar else "nunca"})
            cfg = self.paginas["config"]
            cfg.ao_abrir.blockSignals(True)
            cfg.ao_abrir.setCurrentIndex(1 if carregar else 2)
            cfg.ao_abrir.blockSignals(False)
        if carregar:
            self.carregar_modelo(PRINCIPAL, "editar")

    # ------------------------------------------------------------ navegação
    def ir_para(self, chave):
        if chave not in self.paginas:
            return
        alvo = self.paginas[chave]
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

    def enviar_para(self, destino, imgs, mascara=None, aviso_redirecionamento=False):
        if destino not in self.paginas:
            return
        validas = [i for i in imgs if i is not None and not i.isNull()]
        if not validas:
            return
        estava_em = self.chave_atual()
        self.paginas[destino].colar(validas, mascara=mascara, substituir_base=True)
        self.ir_para(destino)
        if aviso_redirecionamento:
            self.toast(tr("Imagem aberta na aba Editar — use os ícones da barra se quiser Máscara ou Limpar."), "ok", 3400)
        elif estava_em != destino:
            nome_aba = tr(NOMES_ABAS.get(destino, destino))
            self.toast(tr("Imagem enviada para {aba}", aba=nome_aba), "ok", 1800)

    def receber_mime(self, md, destino_forcado=None):
        """Recebe imagens arrastadas de qualquer lugar da janela (ou sobre o campo de prompt / barra lateral)."""
        imgs = imagens_do_mime(md)
        if not imgs:
            if md is not None and md.hasUrls():
                self.toast(tr("Formato não suportado ao arrastar. Use PNG/JPG/WebP ou copie (Ctrl+C) no Photoshop e cole (Ctrl+V)."),
                           "aviso", 4500)
            return False
        if destino_forcado in ("editar", "mascara", "limpar"):
            redirecionou = destino_forcado == "editar" and self.chave_atual() not in ("editar", "mascara", "limpar")
            self.enviar_para(destino_forcado, imgs, aviso_redirecionamento=redirecionou)
            return True
        atual = self.chave_atual()
        if atual in ("editar", "mascara", "limpar"):
            self.paginas[atual].colar(imgs)
            self.toast(tr("Imagem carregada"), "ok", 1500)
        else:
            self.enviar_para("editar", imgs, aviso_redirecionamento=True)
        return True

    def colar_aqui(self):
        imgs = imagens_do_mime(QGuiApplication.clipboard().mimeData())
        if imgs:
            atual = self.chave_atual()
            if atual in ("editar", "mascara", "limpar"):
                self.paginas[atual].colar(imgs)
                self.toast(tr("Imagem colada"), "ok", 1400)
            else:
                self.enviar_para("editar", imgs, aviso_redirecionamento=True)
        else:
            self.toast(tr("Não há imagem na área de transferência. Copie no Photoshop (Ctrl+C) e cole aqui."), "aviso")

    def toast(self, texto, tipo="info", ms=2600):
        self._toast.mostrar(texto, tipo, ms)

    # ------------------------------------------------------------ motor
    def pedir(self, tarefa):
        self.motor.enviar(tarefa)

    def rodar(self, tarefa, pagina):
        self.ocupado, self.dono = True, pagina
        pagina.ao_comecar()
        self.pedir(tarefa)
        # Clicou em Gerar/Editar com o modelo fora da placa: a fila carrega e já executa; mostra a espera.
        if tarefa.get("modelo") and self.estado_modelo(tarefa["modelo"]) != "pronto":
            self._mostrar_carregando(POR_ID.get(tarefa["modelo"]))

    def cancelar(self):
        if self.ocupado:
            self.motor.cancelar = True
            if self.dono:
                self.dono.ao_estado(tr("Cancelando…"))

    def liberar_placa(self, ao_terminar=None):
        if self.ocupado:
            self.toast(tr("Espere terminar ou cancele (Esc) antes."), "aviso")
            return
        if callable(ao_terminar):
            self._pos_liberar.append(ao_terminar)
        self.pedir({"tipo": "liberar"})

    def _ao_liberado(self):
        pendentes, self._pos_liberar = self._pos_liberar[:], []
        for fn in pendentes:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("erro em callback pós-liberar")

    def _estado(self, texto):
        if getattr(self, "_dlg_carregando", None) is not None:
            self._dlg_carregando.definir_fase(texto)
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
        if self.modelo_carregando or not self.aceitar_licenca(m):
            return
        if not config.modelo_instalado(m["id"], excluir=m.get("excluir", ())):
            self.toast(tr("Baixe o modelo {nome} primeiro (aba Modelos).", nome=m["nome"]), "aviso", 4000)
            self.ir_para("modelos")
            return
        self.modelo_carregando = m["id"]
        self.lateral.modelo.setText(tr("Carregando {nome}…", nome=m["nome"]))
        self._atualizar_botoes()
        self.pedir({"tipo": "carregar", "modelo": m["id"], "modo": modo})
        self._mostrar_carregando(m)

    def _mostrar_carregando(self, m):
        """Janela de espera com etapa, tempo passado e tempo estimado; trava o programa até o modelo ficar pronto."""
        if m is None or getattr(self, "_dlg_carregando", None) is not None:
            return
        from .carregando import DialogoCarregando
        self._dlg_carregando = DialogoCarregando(self, m, ao_cancelar=self.cancelar_carga)
        self._dlg_carregando.open()

    def cancelar_carga(self):
        """Cancelar/X na janela de carga: reinicia o motor (a carga não tem como parar no meio de outro jeito)."""
        self._dlg_carregando = None
        self.motor.reiniciar()
        self.modelo_carregando = ""
        self.modelo_na_placa = ""
        dono, self.ocupado, self.dono = self.dono, False, None
        if dono:
            dono.ao_fim({"cancelado": True})
        self._atualizar_botoes()
        self.lateral.modelo.setText(tr("Nenhum modelo aberto"))
        self.toast(tr("Carregamento cancelado"), "aviso")

    def _fechar_carregando(self, ok):
        dlg, self._dlg_carregando = getattr(self, "_dlg_carregando", None), None
        if dlg is not None:
            dlg.concluir(ok)

    def _ao_carregando(self, id_modelo):
        self.modelo_carregando = id_modelo
        self._atualizar_botoes()
        self._mostrar_carregando(POR_ID.get(id_modelo))

    def _ao_carregado(self, id_modelo):
        if id_modelo:
            self._fechar_carregando(True)
        estava = self.modelo_carregando
        self.modelo_carregando = ""
        self.modelo_na_placa = id_modelo
        self._atualizar_botoes()
        if id_modelo and estava:
            self.toast(tr("{nome} pronto na placa — pode gerar", nome=POR_ID[id_modelo]["nome"]), "ok", 3000)

    def _atualizar_botoes(self):
        for k in ("gerar", "editar", "mascara"):
            self.paginas[k].atualizar_botao()

    def _falhou(self, msg):
        self._fechar_carregando(False)
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
            suf = f" · {total:.0f} GB" if total else ""
            self.lateral.placa.setText(f"{nome}\n{backend}{suf}")
            if total > 0:
                self._iniciar_monitor_vram()
            else:
                self.lateral.vram.hide()
        else:
            self.lateral.placa.setText(tr("Sem placa compatível (processador)"))
            self.lateral.vram.hide()
        self.paginas["config"].definir_placa(nome, backend, total)
        self._atualizar_botoes()

    def _decidir_abertura(self):
        """Logo que a janela aparece (sem esperar o motor ver a placa): segue a escolha salva ou pergunta.
        Se carregar, o pedido entra na fila do motor logo atrás da checagem da placa."""
        if not config.modelo_instalado(PRINCIPAL["id"]) or self.ocupado or self.modelo_carregando or self.modelo_na_placa:
            return
        escolha = config.carregar().get("ao_abrir", "perguntar")
        if getattr(self, "_na_bandeja", False):
            escolha = "sempre"  # aberto junto com o Windows: já deixa o modelo pronto
        if escolha == "sempre":
            self.carregar_modelo(PRINCIPAL, "editar")
        elif escolha == "perguntar":
            self._perguntar_ao_abrir()

    def _iniciar_monitor_vram(self):
        """A VRAM é medida no processo do motor e chega pelo sinal motor.vram."""

    def _atualizar_barra_vram(self, usado_gb, total_gb):
        if total_gb <= 0:
            return
        self.lateral.vram.setValue(max(0, min(1000, int(1000 * usado_gb / total_gb))))
        self.lateral.vram_txt.setText(tr("VRAM {u:.1f} de {t:.1f} GB", u=usado_gb, t=total_gb))

    # ------------------------------------------------------------ downloads
    def aceitar_licenca(self, m):
        """Modelos com licença restrita (ex.: FLUX Non-Commercial do 9B): o usuário lê e aceita uma vez."""
        if not m.get("exige_aceite"):
            return True
        aceites = config.carregar().get("licencas_aceitas", [])
        if m["id"] in aceites:
            return True
        from PySide6.QtWidgets import QMessageBox
        caixa = QMessageBox(self)
        caixa.setWindowTitle(tr("Licença do modelo"))
        caixa.setTextFormat(Qt.RichText)
        caixa.setText(tr("O {nome} usa a licença <b>{lic}</b>: pode usar para fins pessoais e não comerciais.<br><br>"
                         "<a href=\"{url}\">Ler a licença completa</a>", nome=m["nome"], lic=m["licenca"],
                         url=m.get("licenca_url", "")))
        caixa.setTextInteractionFlags(Qt.TextBrowserInteraction)
        aceito = caixa.addButton(tr("Li e aceito"), QMessageBox.AcceptRole)
        caixa.addButton(tr("Cancelar"), QMessageBox.RejectRole)
        caixa.exec()
        if caixa.clickedButton() is not aceito:
            return False
        config.salvar({"licencas_aceitas": aceites + [m["id"]]})
        return True

    def baixar(self, m):
        if m["id"] in self.downloads or not self.aceitar_licenca(m):
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
        if id_modelo not in self.downloads:
            return
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
                        atual = self.chave_atual()
                        if atual in ("editar", "mascara", "limpar"):
                            self.paginas[atual].colar(imgs)
                            self.toast(tr("Imagem colada"), "ok", 1200)
                        else:
                            self.enviar_para("editar", imgs, aviso_redirecionamento=True)
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
        md = e.mimeData()
        if mime_tem_imagem(md) or md.hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        md = e.mimeData()
        if mime_tem_imagem(md) or md.hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if self.receber_mime(e.mimeData()):
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
        tela = QGuiApplication.primaryScreen()
        disp = tela.availableGeometry() if tela else QRect(0, 0, 1440, 900)
        if geo is not None:
            self.restoreGeometry(geo)
            tela_atual = QGuiApplication.screenAt(self.frameGeometry().center()) or tela
            if tela_atual:
                disp = tela_atual.availableGeometry()
                if not disp.intersects(self.frameGeometry().adjusted(40, 40, -40, -40)):
                    self.resize(min(1440, disp.width() - 40), min(900, disp.height() - 40))
                    self.move(disp.x() + max(0, (disp.width() - self.width()) // 2),
                              disp.y() + max(0, (disp.height() - self.height()) // 2))
        else:
            self.resize(min(1440, max(1024, disp.width() - 60)), min(900, max(640, disp.height() - 60)))
        for k in ("gerar", "editar", "mascara", "limpar"):
            try:
                self.paginas[k].prefs_carregar(self.prefs)
            except Exception:  # noqa: BLE001 - preferência estragada não impede abrir
                log.exception("prefs %s", k)
        aba_salva = self.prefs.value("janela/aba", "editar")
        self.ir_para(aba_salva if aba_salva in self.paginas else "editar")

    def _salvar_prefs(self):
        self.prefs.setValue("janela/geometria", self.saveGeometry())
        self.prefs.setValue("janela/aba", self.chave_atual())
        for k in ("gerar", "editar", "mascara", "limpar"):
            try:
                self.paginas[k].prefs_salvar(self.prefs)
            except Exception:  # noqa: BLE001
                log.exception("salvar prefs %s", k)
        self.prefs.sync()

    def reiniciar(self):
        from . import instancia
        self._salvar_prefs()
        self._sair_de_verdade = True
        instancia.parar()
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, sys.argv[1:])
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:])
        self.close()

    # ------------------------------------------------------------ bandeja (fechar = continuar aberto)
    def _criar_bandeja(self):
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon
        self._sair_de_verdade = False
        self.bandeja = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.bandeja = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self.bandeja.setToolTip("Flux Estúdio")
        menu = QMenu()
        menu.addAction(tr("Abrir o Flux Estúdio"), self.trazer_para_frente)
        menu.addSeparator()
        menu.addAction(tr("Sair (libera a placa de vídeo)"), self.sair)
        self.bandeja.setContextMenu(menu)
        self._menu_bandeja = menu
        self.bandeja.activated.connect(
            lambda motivo: self.trazer_para_frente() if motivo in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.bandeja.show()

    def trazer_para_frente(self):
        self.showNormal() if self.isMinimized() or not self.isVisible() else self.show()
        self.raise_()
        self.activateWindow()

    def sair(self):
        self._sair_de_verdade = True
        self.close()

    def closeEvent(self, e):
        if (not getattr(self, "_sair_de_verdade", True) and getattr(self, "bandeja", None) is not None
                and config.carregar().get("fechar_para_bandeja", True)):
            # Continua rodando escondido: o modelo segue na placa e reabrir é instantâneo.
            self._salvar_prefs()
            e.ignore()
            self.hide()
            if not config.carregar().get("aviso_bandeja_visto"):
                config.salvar({"aviso_bandeja_visto": True})
                self.bandeja.showMessage(
                    "Flux Estúdio", tr("Continua aberto aqui na bandeja, com o modelo carregado: reabrir é instantâneo. "
                                       "Para fechar de vez, clique com o botão direito no ícone e escolha Sair."))
            return
        self._salvar_prefs()
        self._parar_vram.set()
        if getattr(self, "bandeja", None) is not None:
            self.bandeja.hide()
        self.hide()
        for th, trab in list(self.downloads.values()):
            trab.b.cancelar()
            th.quit()
            th.wait(800)
        # Carregando um modelo o motor não responde ao pedido de parar: fecha na hora em vez de esperar.
        self.motor.encerrar(0.3 if self.modelo_carregando else 4.0)
        super().closeEvent(e)
