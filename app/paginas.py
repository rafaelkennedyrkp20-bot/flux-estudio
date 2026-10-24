"""As abas do programa."""
import html
import os
import shutil
import subprocess
import sys

from PySide6.QtCore import QFileSystemWatcher, QObject, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import config, i18n, instalacao, tema
from .baixador import Baixador, Cancelado
from .comparar import DialogoComparar
from .i18n import tr
from .modelos import MODELOS, POR_ID
from .paineis import CampoPrompt, LinhaSemente, PainelResultado, SeletorModelo
from .widgets import (EXT_IMAGEM, LinhaSlider, Recolhivel, Segmentado, TelaMascara, Visor, botao_icone, carregar_qimage,
                      copiar_imagem, imagens_do_mime, mime_tem_imagem, miniatura, qimage_para_pil, rotulo)

FILTRO_IMAGENS = "Imagens (*.png *.jpg *.jpeg *.jfif *.webp *.bmp *.tif *.tiff *.gif *.avif)"


class ErroUsuario(Exception):
    pass


def _cartao():
    f = QFrame()
    f.setObjectName("cartao")
    return f


def _abrir_pasta(p):
    os.makedirs(p, exist_ok=True)
    os.startfile(p)


def _gb(n):
    return f"{n / 2**30:.1f} GB"


# ================================================================ base das abas de trabalho
class PaginaTrabalho(QWidget):
    modo = "gerar"
    titulo = ""
    texto_botao = ""
    texto_vazio = ""

    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 18, 18, 18)
        h.setSpacing(16)

        cartao = _cartao()
        cartao.setFixedWidth(372)
        cv = QVBoxLayout(cartao)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        interno = QWidget()
        self.col = QVBoxLayout(interno)
        self.col.setContentsMargins(18, 18, 18, 12)
        self.col.setSpacing(16)
        rolagem.setWidget(interno)
        cv.addWidget(rolagem, 1)

        cab = QVBoxLayout()
        cab.setSpacing(2)
        cab.addWidget(rotulo(self.titulo, "titulo"))
        self.sub_modelo = rotulo("", "dica")
        cab.addWidget(self.sub_modelo)
        self.col.addLayout(cab)

        # O essencial fica à vista (imagem + prompt + botão). O resto é automático e fica recolhido.
        self._montar_controles()
        self.avancado = Recolhivel(tr("Ajustes avançados"))
        a = self.avancado.lay
        self.seletor = SeletorModelo(self.modo)
        self.seletor.mudou.connect(self._ao_trocar_modelo)
        a.addWidget(self.seletor)
        self._montar_avancados(a)
        self.passos = LinhaSlider(tr("Passos"), 1, 12, 4, dica=tr("O FLUX.2 klein foi feito para 4 passos."))
        self.guia = LinhaSlider(tr("Guia (CFG)"), 0, 150, 60, formato=lambda v: f"{v / 10:.1f}",
                                dica=tr("Quanto o modelo segue o prompt. Só para alguns modelos."))
        self.quantas = Segmentado([(i, str(i)) for i in range(1, 5)], 1)
        q = QVBoxLayout()
        q.setSpacing(6)
        q.addWidget(rotulo(tr("Quantas imagens")))
        q.addWidget(self.quantas)
        self.semente = LinhaSemente()
        a.addLayout(q)
        a.addWidget(self.passos)
        a.addWidget(self.guia)
        a.addWidget(self.semente)
        self._controles_finais(a)
        self.col.addWidget(self.avancado)
        self.col.addStretch()

        rodape = QVBoxLayout()
        rodape.setContentsMargins(18, 10, 18, 18)
        rodape.setSpacing(6)
        self.botao = QPushButton(self.texto_botao)
        self.botao.setObjectName("principal")
        self.botao.setCursor(Qt.PointingHandCursor)
        self.botao.setMinimumHeight(46)
        self.botao.clicked.connect(self._clique_botao)
        rodape.addWidget(self.botao)
        dica = rotulo(tr("Ctrl+Enter executa · Esc cancela · Ctrl+V cola"), "dica")
        dica.setAlignment(Qt.AlignCenter)
        rodape.addWidget(dica)
        cv.addLayout(rodape)
        h.addWidget(cartao)

        self.resultado = PainelResultado(self.texto_vazio, self.modo != "gerar", modo_atual=self.modo)
        self.resultado.enviar.connect(lambda destino, img: self.janela.enviar_para(destino, [img]))
        self.resultado.continuar.connect(self.continuar)
        h.addWidget(self._area_direita(), 1)
        if hasattr(self, "prompt"):
            self.prompt.edit.textChanged.connect(self.atualizar_botao)
        self._ao_trocar_modelo(self.seletor.modelo())

    # ---- ganchos das subclasses
    def _montar_controles(self):
        pass

    def _montar_avancados(self, lay):
        pass

    def _controles_finais(self, lay):
        pass

    @staticmethod
    def _slider_tamanho(titulo, dica):
        """7 = Automático (segue a imagem de entrada); 8..32 = lado maior fixo em múltiplos de 64 px."""
        return LinhaSlider(titulo, 7, 32, 7, formato=lambda v: tr("Automático") if v == 7 else f"{v * 64}px", dica=dica)

    @staticmethod
    def _maximo(slider):
        return None if slider.valor() == 7 else slider.valor() * 64

    def _area_direita(self):
        return self.resultado

    def montar_tarefa(self):
        raise NotImplementedError

    def pode_rodar(self):
        """Checagem barata (roda a cada tecla): só olha se tem o mínimo, sem converter imagens."""
        return bool(self.prompt.edit.toPlainText().strip())

    def colar(self, imgs):
        pass

    def continuar(self, img):
        self.colar([img])

    # ---- comum
    def _ao_trocar_modelo(self, m):
        self.sub_modelo.setText(m["nome"])
        if hasattr(self, "botao"):
            self.atualizar_botao()
        self.passos.definir_faixa(1, m["passos_max"])
        self.passos.definir(m["passos"])
        self.guia.setVisible(m.get("usa_guia", False))
        self.guia.definir(int(m["guia"] * 10))

    def executar(self):
        if self.janela.ocupado:
            return
        m = self.seletor.modelo()
        if not config.modelo_instalado(m["id"], excluir=m.get("excluir", ())):
            self.janela.toast(tr("Baixe o modelo {nome} primeiro (aba Modelos).", nome=m["nome"]), "aviso", 4000)
            self.janela.ir_para("modelos")
            return
        if not self.janela.aceitar_licenca(m):
            return
        try:
            t = self.montar_tarefa()
        except ErroUsuario as e:
            # Se o modelo ainda está fora da placa e faltam dados da tarefa, o clique pré-carrega o modelo;
            # se o modelo já está pronto (ou o usuário apertou Ctrl+Enter), avisa o que falta preencher.
            if self.janela.estado_modelo(m["id"]) == "fora":
                self.janela.carregar_modelo(m, self.modo)
            else:
                self.janela.toast(str(e), "aviso")
            return
        t.update(modelo=m["id"], passos=self.passos.valor(), quantas=self.quantas.valor(),
                 semente=self.semente.valor(), guia=self.guia.valor() / 10)
        if t.get("prompt"):
            self.janela.biblioteca.lembrar(self.modo, t["prompt"])
        # Mesmo se o modelo estiver fora ou terminando de carregar em segundo plano, a fila do motor
        # abre/conclui o carregamento e já executa a geração na sequência sem exigir um 2º clique.
        self.janela.rodar(t, self)

    def _clique_botao(self):
        if self.janela.ocupado and self.janela.dono is self:
            self.janela.cancelar()
            return
        self.executar()

    def atualizar_botao(self):
        """Mostra o estado do modelo sem bloquear o clique quando o usuário já quer enfileirar a tarefa."""
        if self.janela.ocupado and self.janela.dono is self:
            return
        m = self.seletor.modelo()
        estado = self.janela.estado_modelo(m["id"])
        if estado == "pronto":
            texto, nome, ativo = self.texto_botao, "principal", True
        elif estado == "carregando":
            texto, nome, ativo = self.texto_botao, "principal", True
        else:
            # Se já tem prompt/imagem pronta, mostra direto a ação (vai carregar e rodar num clique só).
            texto = self.texto_botao if self.pode_rodar() else tr("Carregar modelo")
            nome, ativo = "principal", True
        self.botao.setText(texto)
        self.botao.setEnabled(ativo)
        self.botao.setToolTip("" if estado == "pronto" else
                              tr("Carrega {nome} na placa de vídeo (uma vez por sessão) e executa.",
                                 nome=m["nome"]))
        if self.botao.objectName() != nome:
            self.botao.setObjectName(nome)
            self.botao.style().unpolish(self.botao)
            self.botao.style().polish(self.botao)
        if not self.janela.ocupado:
            carregando = estado == "carregando"
            self.resultado.visor.definir_ocupado(carregando)
            if carregando:
                self.resultado.status(tr("Carregando {nome} na placa… (só na abertura)", nome=m["nome"]))

    def ao_comecar(self):
        self.botao.setText(tr("Cancelar"))
        self.botao.setObjectName("perigo")
        self.botao.style().unpolish(self.botao)
        self.botao.style().polish(self.botao)
        self.resultado.limpar()
        self.resultado.ocupado(True, tr("Preparando…"))

    def ao_estado(self, texto):
        self.resultado.status(texto)

    def ao_passo(self, feito, total):
        self.resultado.passo(feito, total)

    def ao_imagem(self, caminho):
        self.resultado.adicionar(caminho)

    def ao_fim(self, d=None, erro=None):
        self.botao.setObjectName("")
        self.resultado.ocupado(False)
        self.atualizar_botao()
        if d:
            if d.get("semente") is not None:
                self.semente.ultima = d.get("semente")
            self.resultado.status(tr("Cancelado") if d.get("cancelado") else d.get("info", ""))
        elif erro:
            self.resultado.status("")

    def copiar(self):
        self.resultado.copiar()

    def prefs_salvar(self, s):
        s.setValue(f"{self.modo}/modelo", self.seletor.modelo()["id"])
        s.setValue(f"{self.modo}/prompt", self.prompt.texto())
        s.setValue(f"{self.modo}/quantas", self.quantas.valor())
        s.setValue(f"{self.modo}/avancado", self.avancado.aberto())

    def prefs_carregar(self, s):
        self.seletor.definir(s.value(f"{self.modo}/modelo", "flux2-klein-4b"))
        self.prompt.definir(s.value(f"{self.modo}/prompt", "") or "")
        self.quantas.definir(int(s.value(f"{self.modo}/quantas", 1)))
        self.avancado.abrir(str(s.value(f"{self.modo}/avancado", "false")).lower() == "true", False)


# ================================================================ Gerar
PROPORCOES = [("1:1", 1, 1), ("4:5", 4, 5), ("3:4", 3, 4), ("2:3", 2, 3), ("9:16", 9, 16), ("3:2", 3, 2), ("16:9", 16, 9)]


class PaginaGerar(PaginaTrabalho):
    modo = "gerar"

    def __init__(self, janela):
        self.titulo = tr("Gerar")
        self.texto_botao = tr("Gerar")
        self.texto_vazio = tr("Descreva a imagem e clique em Gerar.\n\nCole uma imagem (Ctrl+V) para editá-la.")
        super().__init__(janela)

    def _montar_controles(self):
        self.prompt = CampoPrompt(self.janela.biblioteca, "gerar", tr("O que gerar"),
                                  tr("Ex.: menina de cabelo rosa num café à noite, estilo webtoon, luz de neon"))
        self.col.addWidget(self.prompt)
        v = QVBoxLayout()
        v.setSpacing(6)
        v.addWidget(rotulo(tr("Proporção")))
        self.proporcao = Segmentado([(p[0], p[0]) for p in PROPORCOES], "1:1")
        self.proporcao.mudou.connect(lambda _: self._atualizar_tamanho())
        v.addWidget(self.proporcao)
        self.tamanho_txt = rotulo("", "dica")
        v.addWidget(self.tamanho_txt)
        self.col.addLayout(v)

    def _montar_avancados(self, lay):
        self.lado = LinhaSlider(tr("Tamanho (lado maior)"), 8, 24, 16, formato=lambda v: f"{v * 64}px")
        self.lado.mudou.connect(lambda _: self._atualizar_tamanho())
        lay.addWidget(self.lado)
        self._atualizar_tamanho()

    def _dimensoes(self):
        _, a, b = next(p for p in PROPORCOES if p[0] == self.proporcao.valor())
        lado = self.lado.valor() * 64
        if a >= b:
            w, h = lado, lado * b / a
        else:
            w, h = lado * a / b, lado
        return int(round(w / 16)) * 16, int(round(h / 16)) * 16

    def _atualizar_tamanho(self):
        if hasattr(self, "lado"):
            w, h = self._dimensoes()
            self.tamanho_txt.setText(f"{w} × {h} px")

    def montar_tarefa(self):
        if not self.prompt.texto():
            raise ErroUsuario(tr("Escreva o que quer gerar."))
        w, h = self._dimensoes()
        return {"tipo": "gerar", "prompt": self.prompt.texto(), "largura": w, "altura": h}

    def colar(self, imgs, **_kw):
        self.janela.enviar_para("editar", imgs, aviso_redirecionamento=True)

    def prefs_salvar(self, s):
        super().prefs_salvar(s)
        s.setValue("gerar/proporcao", self.proporcao.valor())
        s.setValue("gerar/lado", self.lado.valor())

    def prefs_carregar(self, s):
        super().prefs_carregar(s)
        self.proporcao.definir(s.value("gerar/proporcao", "1:1"))
        self.lado.definir(int(s.value("gerar/lado", 16)))
        self._atualizar_tamanho()


# ================================================================ Editar
class ListaReferencias(QListWidget):
    """Imagens de referência: a 1ª é a imagem editada; as outras dão estilo/objetos/rostos.
    Arraste para reordenar ou solte imagens externas do Explorer/Photoshop/navegador; Delete remove."""
    mudou = Signal()
    externas_soltas = Signal(list)
    enviar_item = Signal(str, QImage)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setIconSize(QSize(92, 92))
        self.setGridSize(QSize(102, 102))
        self.setMovement(QListWidget.Snap)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setFixedHeight(112)
        self.setResizeMode(QListWidget.Adjust)
        self.setStyleSheet(f"QListWidget {{ background: {tema.CARTAO}; border: 1px dashed {tema.BORDA2}; border-radius: 12px; }}")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.model().rowsMoved.connect(lambda *a: self.mudou.emit())
        self._imgs = {}

    def dragEnterEvent(self, e):
        if e.source() is self:
            super().dragEnterEvent(e)
        elif mime_tem_imagem(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self:
            super().dragMoveEvent(e)
        elif mime_tem_imagem(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.source() is self:
            super().dropEvent(e)
            self.mudou.emit()
            return
        imgs = imagens_do_mime(e.mimeData())
        if imgs:
            self.externas_soltas.emit(imgs)
            e.acceptProposedAction()
            return
        e.ignore()

    def adicionar(self, img):
        it = QListWidgetItem()
        it.setIcon(QIcon(miniatura(img, 184)))
        it.setToolTip(f"{img.width()} × {img.height()}")
        chave = id(it)
        it.setData(Qt.UserRole, chave)
        self._imgs[chave] = img
        self.addItem(it)
        self.setFixedHeight(112 if self.count() <= 3 else 214)
        self.mudou.emit()

    def substituir_primeira(self, img):
        if self.count() == 0:
            self.adicionar(img)
            return
        it = self.item(0)
        self._imgs[it.data(Qt.UserRole)] = img
        it.setIcon(QIcon(miniatura(img, 184)))
        it.setToolTip(f"{img.width()} × {img.height()}")
        self.mudou.emit()

    def imagens(self):
        return [self._imgs[self.item(i).data(Qt.UserRole)] for i in range(self.count())
                if self.item(i).data(Qt.UserRole) in self._imgs]

    def remover_selecionadas(self):
        for it in self.selectedItems():
            self._imgs.pop(it.data(Qt.UserRole), None)
            self.takeItem(self.row(it))
        self.setFixedHeight(112 if self.count() <= 3 else 214)
        self.mudou.emit()

    def limpar(self):
        self.clear()
        self._imgs.clear()
        self.setFixedHeight(112)
        self.mudou.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remover_selecionadas()
        else:
            super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it:
            return
        img = self._imgs.get(it.data(Qt.UserRole))
        if img is None:
            return
        m = QMenu(self)
        m.addAction(tr("Copiar"), lambda: copiar_imagem(img))
        m.addAction(tr("Tornar a principal"), lambda: (self.insertItem(0, self.takeItem(self.row(it))), self.mudou.emit()))
        m.addSeparator()
        m.addAction(tr("Mandar para Máscara"), lambda: self.enviar_item.emit("mascara", img))
        m.addAction(tr("Mandar para Limpar"), lambda: self.enviar_item.emit("limpar", img))
        m.addSeparator()
        m.addAction(tr("Remover"), lambda: (self.setCurrentItem(it), self.remover_selecionadas()))
        m.exec(self.mapToGlobal(pos))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            from PySide6.QtGui import QColor, QPainter
            p = QPainter(self.viewport())
            p.setPen(QColor(tema.APAGADO))
            p.drawText(self.viewport().rect(), Qt.AlignCenter, tr("Ctrl+V para colar · ou arraste imagens aqui"))
            p.end()


class PaginaEditar(PaginaTrabalho):
    modo = "editar"

    def __init__(self, janela):
        self.titulo = tr("Editar")
        self.texto_botao = tr("Editar")
        self.texto_vazio = tr("Cole a imagem (Ctrl+V) — direto do Photoshop, navegador ou pasta — ou arraste aqui.")
        self._chave_base = None
        super().__init__(janela)

    def _montar_controles(self):
        v = QVBoxLayout()
        v.setSpacing(6)
        topo = QHBoxLayout()
        topo.setSpacing(2)
        topo.addWidget(rotulo(tr("Imagens de referência")))
        topo.addStretch()
        topo.addWidget(botao_icone("colar", tr("Colar (Ctrl+V)"), self.janela.colar_aqui, tamanho=16))
        topo.addWidget(botao_icone("abrir", tr("Abrir arquivos…"), self._abrir, tamanho=16))
        self.bt_ir_mascara = botao_icone("mascara", tr("Mandar 1ª imagem para Máscara"),
                                         lambda: self._enviar_primeira("mascara"), tamanho=16)
        self.bt_ir_limpar = botao_icone("borracha", tr("Mandar 1ª imagem para Limpar"),
                                        lambda: self._enviar_primeira("limpar"), tamanho=16)
        self.bt_ir_mascara.setVisible(False)
        self.bt_ir_limpar.setVisible(False)
        topo.addWidget(self.bt_ir_mascara)
        topo.addWidget(self.bt_ir_limpar)
        topo.addWidget(botao_icone("limpar", tr("Remover todas"), self._limpar_tudo, tamanho=16))
        v.addLayout(topo)
        self.refs = ListaReferencias()
        self.refs.mudou.connect(self._refs_mudaram)
        self.refs.externas_soltas.connect(self.colar)
        self.refs.enviar_item.connect(lambda dest, im: self.janela.enviar_para(dest, [im]))
        v.addWidget(self.refs)
        v.addWidget(rotulo(tr("A 1ª é a imagem editada; as outras servem de referência."), "dica"))
        self.col.addLayout(v)
        self.prompt = CampoPrompt(self.janela.biblioteca, "editar", tr("O que mudar"),
                                  tr("Ex.: troque o fundo por uma praia ao pôr do sol, mantenha a personagem igual"))
        self.col.addWidget(self.prompt)
        dica = rotulo(tr("Dica: para tirar marca d'água ou texto sem mexer no resto, use Máscara ou Limpar."), "dica")
        dica.setWordWrap(True)
        self.col.addWidget(dica)

    def _montar_avancados(self, lay):
        self.maximo = self._slider_tamanho(tr("Tamanho de trabalho"),
                                           tr("Automático: segue a proporção e o tamanho da imagem colada."))
        lay.addWidget(self.maximo)

    def _controles_finais(self, lay):
        self.manter_cores = QCheckBox(tr("Manter as cores do original"))
        self.manter_cores.setChecked(True)
        self.manter_cores.setToolTip(tr("Editando sem máscara o modelo puxa o tom. Isto devolve as cores do original."))
        lay.addWidget(self.manter_cores)

    def _enviar_primeira(self, destino):
        imgs = self.refs.imagens()
        if imgs:
            self.janela.enviar_para(destino, [imgs[0]])

    def _limpar_tudo(self):
        self._chave_base = None
        self.refs.limpar()
        self.resultado.definir_entrada_nova(None)

    def _refs_mudaram(self):
        imgs = self.refs.imagens()
        tem = bool(imgs)
        self.bt_ir_mascara.setVisible(tem)
        self.bt_ir_limpar.setVisible(tem)
        if not imgs:
            self._chave_base = None
            self.resultado.definir_entrada_nova(None)
        else:
            nova_chave = imgs[0].cacheKey()
            if nova_chave != self._chave_base:
                self._chave_base = nova_chave
                self.resultado.definir_entrada_nova(imgs[0])
            else:
                self.resultado.mostrar_entrada(imgs[0])
        if hasattr(self, "botao"):
            self.atualizar_botao()

    def _abrir(self):
        ps, _ = QFileDialog.getOpenFileNames(self, tr("Abrir imagens"), "", FILTRO_IMAGENS)
        self.colar([carregar_qimage(p) for p in ps])

    def colar(self, imgs, mascara=None, substituir_base=False):
        validas = [i for i in imgs if i is not None and not i.isNull()]
        if not validas:
            return
        if substituir_base and self.refs.count() > 0:
            self.refs.substituir_primeira(validas[0])
            for extra in validas[1:]:
                self.refs.adicionar(extra)
            self.resultado.definir_entrada_nova(validas[0])
            return
        estava_vazio = self.refs.count() == 0
        for img in validas:
            self.refs.adicionar(img)
        if estava_vazio:
            self.resultado.definir_entrada_nova(validas[0])

    def continuar(self, img):
        self.refs.substituir_primeira(img)
        self.resultado.definir_entrada_nova(img)
        self.janela.toast(tr("Resultado virou a nova imagem base"), "ok")

    def pode_rodar(self):
        return self.refs.count() > 0 and bool(self.prompt.edit.toPlainText().strip())

    def montar_tarefa(self):
        imgs = self.refs.imagens()
        if not imgs:
            raise ErroUsuario(tr("Cole ou abra pelo menos uma imagem."))
        if not self.prompt.texto():
            raise ErroUsuario(tr("Diga o que mudar na imagem."))
        return {"tipo": "editar", "prompt": self.prompt.texto(), "referencias": [qimage_para_pil(i) for i in imgs],
                "maximo": self._maximo(self.maximo), "manter_cores": self.manter_cores.isChecked()}

    def prefs_salvar(self, s):
        super().prefs_salvar(s)
        s.setValue("editar/maximo", self.maximo.valor())
        s.setValue("editar/cores", self.manter_cores.isChecked())

    def prefs_carregar(self, s):
        super().prefs_carregar(s)
        self.maximo.definir(int(s.value("editar/maximo", 7)))
        self.manter_cores.setChecked(str(s.value("editar/cores", "true")).lower() == "true")


# ================================================================ Máscara
class PaginaMascara(PaginaTrabalho):
    modo = "mascara"

    def __init__(self, janela):
        self.titulo = tr("Máscara")
        self.texto_botao = tr("Refazer área pintada")
        self.texto_vazio = tr("Veja aqui o resultado.")
        super().__init__(janela)

    def _montar_controles(self):
        self.prompt = CampoPrompt(self.janela.biblioteca, "mascara", tr("O que vai na área pintada"),
                                  tr("Vazio = limpar/remover o que foi pintado"))
        self.col.addWidget(self.prompt)
        self.lama = QCheckBox(tr("Limpar antes com o LaMa (recomendado)"))
        self.lama.setChecked(True)
        self.lama.setToolTip(tr("Antes do FLUX, o LaMa apaga o que foi pintado e continua o fundo. Assim o FLUX não desenha de volta nem inventa pessoas ou membros."))
        self.col.addWidget(self.lama)
        self.forca = LinhaSlider(tr("Força"), 2, 20, 20, formato=lambda v: f"{v * 5}%",
                                 dica=tr("Quanto o FLUX redesenha a área. 100% (padrão): redesenha a partir do fundo já limpo. "
                                         "Menos: mantém mais do que estava."))
        self.col.addWidget(self.forca)
        dica = rotulo(tr("Pinte por cima do que quer trocar ou apagar (pessoa, objeto, texto, marca d'água). "
                         "Prompt vazio = apagar e completar o fundo. O resto fica pixel a pixel igual."), "dica")
        dica.setWordWrap(True)
        self.col.addWidget(dica)

    def _montar_avancados(self, lay):
        self.maximo = self._slider_tamanho(tr("Tamanho de trabalho"),
                                           tr("Automático: segue a imagem. O resultado volta ao tamanho original; só a área pintada muda."))
        lay.addWidget(self.maximo)

    def _area_direita(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        barra = QHBoxLayout()
        barra.setSpacing(4)
        self.vista = Segmentado([("pintar", tr("Pintar")), ("resultado", tr("Resultado"))], "pintar")
        self.vista.setFixedWidth(220)
        self.vista.mudou.connect(self._trocar_vista)
        barra.addWidget(self.vista)
        barra.addSpacing(10)
        self.ferramentas = QWidget()
        fh = QHBoxLayout(self.ferramentas)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(4)
        self.tela = TelaMascara()
        self.tela.texto_vazio = tr("Cole a imagem (Ctrl+V) ou arraste aqui.\nDepois pinte o que quer refazer.")
        fh.addWidget(botao_icone("colar", tr("Colar imagem (Ctrl+V)"), self.janela.colar_aqui))
        fh.addWidget(botao_icone("abrir", tr("Abrir imagem…"), self._abrir))
        fh.addSpacing(8)
        self.bt_pincel = botao_icone("pincel", tr("Pincel (X alterna)"), lambda: self._ferramenta(False), True)
        self.bt_borracha = botao_icone("borracha", tr("Borracha (X alterna)"), lambda: self._ferramenta(True), True)
        self.bt_pincel.setChecked(True)
        fh.addWidget(self.bt_pincel)
        fh.addWidget(self.bt_borracha)
        self.tam_pincel = LinhaSlider("", 2, 400, 30)
        self.tam_pincel.setFixedWidth(150)
        self.tam_pincel.setToolTip(tr("Tamanho do pincel ( [ e ] ou Alt+roda )"))
        self.tam_pincel.mudou.connect(lambda v: self.tela.raio != v and self.tela.definir_raio(v))
        self.tela.pincel_mudou.connect(lambda r: self.tam_pincel.definir(min(400, r)))
        fh.addWidget(self.tam_pincel)
        fh.addSpacing(8)
        fh.addWidget(botao_icone("desfazer", tr("Desfazer (Ctrl+Z)"), self.tela.desfazer))
        fh.addWidget(botao_icone("inverter", tr("Inverter máscara"), self.tela.inverter))
        fh.addWidget(botao_icone("limpar", tr("Limpar máscara"), self.tela.limpar))
        fh.addWidget(botao_icone("ajustar", tr("Ajustar à tela"), self.tela.ajustar))
        fh.addSpacing(8)
        self.bt_ir_editar = botao_icone("editar", tr("Mandar para Editar"),
                                        lambda: self._enviar_tela("editar"))
        self.bt_ir_limpar = botao_icone("borracha", tr("Mandar para Limpar (mantém a máscara)"),
                                        lambda: self._enviar_tela("limpar"))
        self.bt_ir_editar.setVisible(False)
        self.bt_ir_limpar.setVisible(False)
        fh.addWidget(self.bt_ir_editar)
        fh.addWidget(self.bt_ir_limpar)
        barra.addWidget(self.ferramentas)
        barra.addStretch()
        v.addLayout(barra)
        self.pilha = QStackedWidget()
        self.pilha.addWidget(self.tela)
        self.pilha.addWidget(self.resultado)
        v.addWidget(self.pilha, 1)
        self.tela.mudou.connect(self._sync_ferramenta)
        return w

    def _enviar_tela(self, destino):
        if self.tela.tem_imagem():
            msk = self.tela.mascara_pil() if destino == "limpar" and self.tela.tem_mascara() else None
            self.janela.enviar_para(destino, [self.tela.imagem()], mascara=msk)

    def _sync_ferramenta(self):
        self.bt_pincel.setChecked(not self.tela.borracha)
        self.bt_borracha.setChecked(self.tela.borracha)
        tem = self.tela.tem_imagem()
        self.bt_ir_editar.setVisible(tem)
        self.bt_ir_limpar.setVisible(tem)
        if hasattr(self, "botao"):
            self.atualizar_botao()

    def _ferramenta(self, borracha):
        self.tela.borracha = borracha
        self._sync_ferramenta()
        self.tela.setFocus()

    def _trocar_vista(self, v):
        self.pilha.setCurrentIndex(0 if v == "pintar" else 1)
        self.ferramentas.setVisible(v == "pintar")
        (self.tela if v == "pintar" else self.resultado.visor).setFocus()

    def _abrir(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("Abrir imagem"), "", FILTRO_IMAGENS)
        if p:
            self.colar([carregar_qimage(p)])

    def colar(self, imgs, mascara=None, substituir_base=False):
        if not imgs or imgs[0] is None or imgs[0].isNull():
            return
        img = imgs[0].convertToFormat(QImage.Format_RGB32)
        self.tela.definir_imagem(img)
        if mascara is not None:
            self.tela.definir_mascara(mascara)
        self.resultado.definir_entrada_nova(img)
        self.vista.definir("pintar", True)
        self._sync_ferramenta()

    def continuar(self, img):
        self.colar([img])
        self.janela.toast(tr("Resultado virou a nova imagem base — pinte a próxima área"), "ok")

    def pode_rodar(self):
        return self.tela.tem_imagem()

    def montar_tarefa(self):
        if not self.tela.tem_imagem():
            raise ErroUsuario(tr("Cole ou abra uma imagem primeiro."))
        if not self.tela.tem_mascara():
            raise ErroUsuario(tr("Pinte a área que quer refazer."))
        return {"tipo": "mascara", "prompt": self.prompt.texto(), "imagem": qimage_para_pil(self.tela.imagem()),
                "mascara": self.tela.mascara_pil(), "maximo": self._maximo(self.maximo), "forca": self.forca.valor() / 20,
                "preencher_lama": self.lama.isChecked()}

    def ao_comecar(self):
        super().ao_comecar()
        self.vista.definir("resultado", True)

    def copiar(self):
        if self.pilha.currentIndex() == 1:
            self.resultado.copiar()
        elif self.tela.tem_imagem():
            copiar_imagem(self.tela.imagem())
            self.janela.toast(tr("Imagem copiada — é só colar (Ctrl+V)"), "ok")

    def prefs_salvar(self, s):
        super().prefs_salvar(s)
        s.setValue("mascara/maximo", self.maximo.valor())
        s.setValue("mascara/forca3", self.forca.valor())
        s.setValue("mascara/lama", self.lama.isChecked())

    def prefs_carregar(self, s):
        super().prefs_carregar(s)
        self.maximo.definir(int(s.value("mascara/maximo", 7)))
        self.forca.definir(int(s.value("mascara/forca3", 20)))
        self.lama.setChecked(str(s.value("mascara/lama", "true")).lower() == "true")


# ================================================================ Limpar (LaMa de mangá)
class PaginaLimpar(QWidget):
    """Apagador rápido para texto, balões e marca d'água: só o LaMa de mangá (não precisa do FLUX).
    Detecta texto/balões sozinho (ou pinte à mão) e limpa em fração de segundo."""
    modo = "limpar"
    _detectado = Signal(int, object, object, str)

    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        self.texto_botao = tr("Limpar")
        self._detector = None
        self._cache_det = (None, None)   # (cacheKey da QImage, lista de detecções)
        self._id_det = 0
        self._detectado.connect(self._ao_detectar)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 18, 18, 18)
        h.setSpacing(16)
        cartao = _cartao()
        cartao.setFixedWidth(372)
        v = QVBoxLayout(cartao)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(14)
        v.addWidget(rotulo(tr("Limpar"), "titulo"))
        sub = rotulo(tr("Apaga texto, balões e marca d'água e completa o fundo. Rápido e não inventa nada."), "subtitulo")
        sub.setWordWrap(True)
        v.addWidget(sub)
        self.bt_detectar = QPushButton(tr("Detectar texto automaticamente"))
        self.bt_detectar.setIcon(tema.icone("check", tema.TEXTO, 16))
        self.bt_detectar.setCursor(Qt.PointingHandCursor)
        self.bt_detectar.clicked.connect(self._detectar)
        v.addWidget(self.bt_detectar)
        self.baloes = QCheckBox(tr("Apagar o balão inteiro, não só o texto"))
        v.addWidget(self.baloes)
        dica = rotulo(tr("Confira a marcação: dá para completar com o pincel ou tirar com a borracha (X). "
                         "Para marca d'água, pinte por cima dela."), "dica")
        dica.setWordWrap(True)
        v.addWidget(dica)
        v.addStretch()
        self.botao = QPushButton(self.texto_botao)
        self.botao.setObjectName("principal")
        self.botao.setCursor(Qt.PointingHandCursor)
        self.botao.setMinimumHeight(46)
        self.botao.clicked.connect(self._clique_botao)
        v.addWidget(self.botao)
        d = rotulo(tr("Ctrl+Enter executa · Esc cancela · Ctrl+V cola"), "dica")
        d.setAlignment(Qt.AlignCenter)
        v.addWidget(d)
        h.addWidget(cartao)

        self.resultado = PainelResultado(tr("Veja aqui o resultado."), True, modo_atual="limpar")
        self.resultado.enviar.connect(lambda destino, img: self.janela.enviar_para(destino, [img]))
        self.resultado.continuar.connect(self.continuar)
        direita = QWidget()
        dv = QVBoxLayout(direita)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(8)
        barra = QHBoxLayout()
        barra.setSpacing(4)
        self.vista = Segmentado([("pintar", tr("Pintar")), ("resultado", tr("Resultado"))], "pintar")
        self.vista.setFixedWidth(220)
        self.vista.mudou.connect(self._trocar_vista)
        barra.addWidget(self.vista)
        barra.addSpacing(10)
        self.tela = TelaMascara()
        self.tela.texto_vazio = tr("Cole a página (Ctrl+V) ou arraste aqui.\nDepois detecte o texto ou pinte o que quer apagar.")
        self.ferramentas = QWidget()
        fh = QHBoxLayout(self.ferramentas)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(4)
        fh.addWidget(botao_icone("colar", tr("Colar imagem (Ctrl+V)"), self.janela.colar_aqui))
        fh.addWidget(botao_icone("abrir", tr("Abrir imagem…"), self._abrir))
        fh.addSpacing(8)
        self.bt_pincel = botao_icone("pincel", tr("Pincel (X alterna)"), lambda: self._ferramenta(False), True)
        self.bt_borracha = botao_icone("borracha", tr("Borracha (X alterna)"), lambda: self._ferramenta(True), True)
        self.bt_pincel.setChecked(True)
        fh.addWidget(self.bt_pincel)
        fh.addWidget(self.bt_borracha)
        self.tam_pincel = LinhaSlider("", 2, 400, 30)
        self.tam_pincel.setFixedWidth(150)
        self.tam_pincel.setToolTip(tr("Tamanho do pincel ( [ e ] ou Alt+roda )"))
        self.tam_pincel.mudou.connect(lambda v: self.tela.raio != v and self.tela.definir_raio(v))
        self.tela.pincel_mudou.connect(lambda r: self.tam_pincel.definir(min(400, r)))
        fh.addWidget(self.tam_pincel)
        fh.addSpacing(8)
        fh.addWidget(botao_icone("desfazer", tr("Desfazer (Ctrl+Z)"), self.tela.desfazer))
        fh.addWidget(botao_icone("limpar", tr("Limpar máscara"), self.tela.limpar))
        fh.addWidget(botao_icone("ajustar", tr("Ajustar à tela"), self.tela.ajustar))
        fh.addSpacing(8)
        self.bt_ir_editar = botao_icone("editar", tr("Mandar para Editar"),
                                        lambda: self._enviar_tela("editar"))
        self.bt_ir_mascara = botao_icone("mascara", tr("Mandar para Máscara (mantém a máscara)"),
                                         lambda: self._enviar_tela("mascara"))
        self.bt_ir_editar.setVisible(False)
        self.bt_ir_mascara.setVisible(False)
        fh.addWidget(self.bt_ir_editar)
        fh.addWidget(self.bt_ir_mascara)
        barra.addWidget(self.ferramentas)
        barra.addStretch()
        dv.addLayout(barra)
        self.pilha = QStackedWidget()
        self.pilha.addWidget(self.tela)
        self.pilha.addWidget(self.resultado)
        dv.addWidget(self.pilha, 1)
        self.tela.mudou.connect(self._sync_tela)
        h.addWidget(direita, 1)

    def _sync_tela(self):
        self.bt_pincel.setChecked(not self.tela.borracha)
        self.bt_borracha.setChecked(self.tela.borracha)
        tem = self.tela.tem_imagem()
        self.bt_ir_editar.setVisible(tem)
        self.bt_ir_mascara.setVisible(tem)

    def _enviar_tela(self, destino):
        if self.tela.tem_imagem():
            msk = self.tela.mascara_pil() if destino == "mascara" and self.tela.tem_mascara() else None
            self.janela.enviar_para(destino, [self.tela.imagem()], mascara=msk)

    def _ferramenta(self, borracha):
        self.tela.borracha = borracha
        self._sync_tela()
        self.tela.setFocus()

    def _trocar_vista(self, v):
        self.pilha.setCurrentIndex(0 if v == "pintar" else 1)
        self.ferramentas.setVisible(v == "pintar")
        (self.tela if v == "pintar" else self.resultado.visor).setFocus()

    def _abrir(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("Abrir imagem"), "", FILTRO_IMAGENS)
        if p:
            self.colar([carregar_qimage(p)])

    def colar(self, imgs, mascara=None, substituir_base=False):
        if not imgs or imgs[0] is None or imgs[0].isNull():
            return
        self._id_det += 1
        self.bt_detectar.setEnabled(True)
        self.bt_detectar.setText(tr("Detectar texto automaticamente"))
        img = imgs[0].convertToFormat(QImage.Format_RGB32)
        self.tela.definir_imagem(img)
        if mascara is not None:
            self.tela.definir_mascara(mascara)
        self.resultado.definir_entrada_nova(img)
        self.vista.definir("pintar", True)
        self._sync_tela()

    def continuar(self, img):
        self.colar([img])
        self.janela.toast(tr("Resultado virou a nova imagem base"), "ok")

    # ---- detecção automática (thread do Python: o ONNX roda no processador)
    def _detectar(self):
        if not self.tela.tem_imagem():
            self.janela.toast(tr("Cole ou abra uma imagem primeiro."), "aviso")
            return
        qimg = self.tela.imagem()
        chave = qimg.cacheKey()
        baloes = self.baloes.isChecked()
        img = qimage_para_pil(qimg)
        # Reaproveita as caixas já detectadas se a imagem continuar a mesma (ex.: trocou só a opção "balão inteiro").
        if self._cache_det[0] == chave and self._cache_det[1] is not None and self._detector is not None:
            mascara = self._detector.mascara_texto(img, incluir_baloes=baloes, deteccoes=self._cache_det[1])
            self._aplicar_mascara_detectada(mascara)
            return

        self._id_det += 1
        meu_id = self._id_det
        self.bt_detectar.setEnabled(False)
        self.bt_detectar.setText(tr("Detectando…"))
        # A detecção roda no processo do motor (resposta chega em _ao_detectar): a janela não engasga.
        self.janela.pedir({"tipo": "detectar", "id": meu_id, "imagem": img, "baloes": baloes})

    def _aplicar_mascara_detectada(self, mascara):
        if mascara is None or mascara.getbbox() is None:
            self.janela.toast(tr("Nenhum texto encontrado. Pinte à mão o que quer apagar."), "aviso")
            return
        self.tela.somar_mascara(mascara)
        self.janela.toast(tr("Texto marcado. Confira e clique em Limpar."), "ok")

    def _ao_detectar(self, id_det, dets, mascara, texto):
        if id_det != self._id_det:
            return
        if mascara is None and texto and not texto.startswith("erro:"):
            self.janela.toast(texto, "info", 4000)
            return
        self.bt_detectar.setEnabled(True)
        self.bt_detectar.setText(tr("Detectar texto automaticamente"))
        if texto.startswith("erro:"):
            self.janela.toast(tr("A detecção falhou: {e}", e=texto[5:]), "erro", 6000)
            return
        if self.tela.tem_imagem() and dets is not None:
            self._cache_det = (self.tela.imagem().cacheKey(), dets)
            if self._detector is None:
                from . import detector
                self._detector = detector.Detector()  # só desenha máscaras de detecções prontas (sem ONNX)
        self._aplicar_mascara_detectada(mascara)

    # ---- execução
    def _clique_botao(self):
        if self.janela.ocupado and self.janela.dono is self:
            self.janela.cancelar()
            return
        self.executar()

    def executar(self):
        if self.janela.ocupado:
            return
        if not self.tela.tem_imagem():
            self.janela.toast(tr("Cole ou abra uma imagem primeiro."), "aviso")
            return
        if not self.tela.tem_mascara():
            self.janela.toast(tr("Marque o que apagar: Detectar texto ou pinte por cima."), "aviso")
            return
        self.janela.rodar({"tipo": "limpar", "imagem": qimage_para_pil(self.tela.imagem()),
                           "mascara": self.tela.mascara_pil()}, self)

    def ao_comecar(self):
        self.botao.setText(tr("Cancelar"))
        self.botao.setObjectName("perigo")
        self.botao.style().unpolish(self.botao)
        self.botao.style().polish(self.botao)
        self.resultado.limpar()
        self.resultado.ocupado(True, tr("Limpando…"))
        self.vista.definir("resultado", True)

    def ao_estado(self, texto):
        self.resultado.status(texto)

    def ao_passo(self, feito, total):
        self.resultado.passo(feito, total)

    def ao_imagem(self, caminho):
        self.resultado.adicionar(caminho)

    def ao_fim(self, d=None, erro=None):
        self.botao.setText(self.texto_botao)
        self.botao.setObjectName("principal")
        self.botao.style().unpolish(self.botao)
        self.botao.style().polish(self.botao)
        self.botao.setEnabled(True)
        self.resultado.ocupado(False)
        if d:
            self.resultado.status(tr("Cancelado") if d.get("cancelado") else d.get("info", ""))
        elif erro:
            self.resultado.status("")

    def atualizar_botao(self):
        pass

    def copiar(self):
        if self.pilha.currentIndex() == 1:
            self.resultado.copiar()
        elif self.tela.tem_imagem():
            copiar_imagem(self.tela.imagem())
            self.janela.toast(tr("Imagem copiada — é só colar (Ctrl+V)"), "ok")

    def prefs_salvar(self, s):
        s.setValue("limpar/baloes", self.baloes.isChecked())

    def prefs_carregar(self, s):
        self.baloes.setChecked(str(s.value("limpar/baloes", "false")).lower() == "true")


# ================================================================ Galeria
class _SinalMini(QObject):
    pronto = Signal(str, QImage)


class _CarregaMini(QRunnable):
    def __init__(self, caminho, sinal):
        super().__init__()
        self.caminho, self.sinal = caminho, sinal

    def run(self):
        r = QImageReader(self.caminho)
        r.setAutoTransform(True)
        s = r.size()
        if s.isValid():
            s.scale(QSize(360, 360), Qt.KeepAspectRatio)
            r.setScaledSize(s)
        img = r.read()
        if not img.isNull():
            self.sinal.pronto.emit(self.caminho, img)


class PaginaGaleria(QWidget):
    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        self._cache = {}
        self._itens_por_caminho = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._sinal = _SinalMini()
        self._sinal.pronto.connect(self._mini_pronta)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 18, 18, 18)
        h.setSpacing(16)

        esq = QVBoxLayout()
        esq.setSpacing(12)
        topo = QHBoxLayout()
        topo.addWidget(rotulo(tr("Galeria"), "titulo"))
        topo.addSpacing(16)
        self.filtro = Segmentado([("", tr("Todas")), ("gerar", tr("Gerar")), ("editar", tr("Editar")),
                                  ("mascara", tr("Máscara")), ("limpar", tr("Limpar"))], "")
        self.filtro.setFixedWidth(410)
        self.filtro.mudou.connect(lambda _: self._filtrar())
        topo.addWidget(self.filtro)
        topo.addStretch()
        self.busca = QLineEdit()
        self.busca.setPlaceholderText(tr("Buscar no nome…"))
        self.busca.setFixedWidth(220)
        self.busca.textChanged.connect(lambda _: self._filtrar())
        topo.addWidget(self.busca)
        topo.addWidget(botao_icone("abrir", tr("Abrir a pasta das imagens"), lambda: _abrir_pasta(config.SAIDAS)))
        esq.addLayout(topo)
        # Seleção: com "Selecionar" ligado, cada clique marca/desmarca; desligado vale Ctrl/Shift+clique e Ctrl+A.
        marcar = QHBoxLayout()
        marcar.setSpacing(8)
        self.bt_selecionar = QPushButton(tr("Selecionar"))
        self.bt_selecionar.setCheckable(True)
        self.bt_selecionar.setCursor(Qt.PointingHandCursor)
        self.bt_selecionar.toggled.connect(self._modo_selecionar)
        marcar.addWidget(self.bt_selecionar)
        self.bt_todas = QPushButton(tr("Marcar todas"))
        self.bt_todas.clicked.connect(self._marcar_todas)
        marcar.addWidget(self.bt_todas)
        self.bt_nenhuma = QPushButton(tr("Desmarcar"))
        self.bt_nenhuma.clicked.connect(lambda: self.lista.clearSelection())
        marcar.addWidget(self.bt_nenhuma)
        marcar.addStretch()
        self.bt_apagar_marcadas = QPushButton()
        self.bt_apagar_marcadas.setObjectName("perigo")
        self.bt_apagar_marcadas.setStyleSheet("padding: 6px 14px;")
        self.bt_apagar_marcadas.clicked.connect(self._apagar)
        marcar.addWidget(self.bt_apagar_marcadas)
        esq.addLayout(marcar)
        self.lista = QListWidget()
        self.lista.setSelectionMode(QListWidget.ExtendedSelection)
        self.lista.setViewMode(QListWidget.IconMode)
        self.lista.setIconSize(QSize(170, 170))
        self.lista.setGridSize(QSize(184, 184))
        self.lista.setResizeMode(QListWidget.Adjust)
        self.lista.setMovement(QListWidget.Static)
        self.lista.setUniformItemSizes(True)
        self.lista.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.lista.verticalScrollBar().setSingleStep(24)
        self.lista.currentItemChanged.connect(self._selecionar)
        self.lista.itemSelectionChanged.connect(self._atualizar_marcadas)
        self.lista.itemDoubleClicked.connect(lambda it: self._enviar("editar"))
        self.lista.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lista.customContextMenuRequested.connect(self._menu)
        esq.addWidget(self.lista, 1)
        h.addLayout(esq, 1)

        cartao = _cartao()
        cartao.setFixedWidth(440)
        dv = QVBoxLayout(cartao)
        dv.setContentsMargins(14, 14, 14, 14)
        dv.setSpacing(10)
        self.visor = Visor()
        self.visor.texto_vazio = tr("Escolha uma imagem")
        dv.addWidget(self.visor, 1)
        self.meta = QLabel()
        self.meta.setObjectName("info")
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dv.addWidget(self.meta)
        acoes = QHBoxLayout()
        acoes.setSpacing(2)
        acoes.addWidget(botao_icone("copiar", tr("Copiar imagem (Ctrl+C)"), self.copiar))
        acoes.addWidget(botao_icone("historico", tr("Reusar o prompt"), self._reusar_prompt))
        acoes.addWidget(botao_icone("editar", tr("Mandar para Editar"), lambda: self._enviar("editar")))
        acoes.addWidget(botao_icone("mascara", tr("Mandar para Máscara"), lambda: self._enviar("mascara")))
        acoes.addWidget(botao_icone("borracha", tr("Mandar para Limpar"), lambda: self._enviar("limpar")))
        acoes.addWidget(botao_icone("abrir", tr("Mostrar na pasta"), self._mostrar_na_pasta))
        acoes.addWidget(botao_icone("lado_a_lado", tr("Comparar com…"), self._comparar_com))
        acoes.addStretch()
        acoes.addWidget(botao_icone("lixo", tr("Mandar para a Lixeira"), self._apagar))
        dv.addLayout(acoes)
        h.addWidget(cartao)

        os.makedirs(config.SAIDAS, exist_ok=True)
        self._vigia = QFileSystemWatcher([config.SAIDAS], self)
        self._adiar = QTimer(self, singleShot=True, interval=500)
        self._adiar.timeout.connect(self.recarregar)
        self._vigia.directoryChanged.connect(lambda _: self._adiar.start())
        self._carregado = False
        self._modo_selecionar(False)

    def showEvent(self, e):
        if not self._carregado:
            self.recarregar()
        super().showEvent(e)

    def recarregar(self):
        self._carregado = True
        atual = self.lista.currentItem().data(Qt.UserRole) if self.lista.currentItem() else None
        entradas = []
        try:
            with os.scandir(config.SAIDAS) as it_dir:
                for e in it_dir:
                    if e.is_file() and e.name.lower().endswith(EXT_IMAGEM):
                        try:
                            entradas.append((e.stat().st_mtime, e.path))
                        except OSError:
                            pass
        except OSError:
            pass
        entradas.sort(key=lambda x: x[0], reverse=True)
        arqs = [p for _, p in entradas]
        self.lista.blockSignals(True)
        self.lista.clear()
        self._itens_por_caminho.clear()
        vazio = QPixmap(170, 170)
        vazio.fill(Qt.transparent)
        for p in arqs:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, p)
            it.setToolTip(os.path.basename(p))
            it.setSizeHint(QSize(180, 180))
            if p in self._cache:
                it.setIcon(QIcon(self._cache[p]))
            else:
                it.setIcon(QIcon(vazio))
                self._pool.start(_CarregaMini(p, self._sinal))
            self.lista.addItem(it)
            self._itens_por_caminho[p] = it
            if p == atual:
                self.lista.setCurrentItem(it)
        self.lista.blockSignals(False)
        if self.lista.currentItem() is None:
            self.visor.definir_imagem(None)
            self.meta.setText("")
        self._filtrar()

    def _mini_pronta(self, caminho, img):
        pm = QPixmap.fromImage(img)
        if len(self._cache) >= 500 and caminho not in self._cache:
            self._cache.pop(next(iter(self._cache)))
        self._cache[caminho] = pm
        it = self._itens_por_caminho.get(caminho)
        if it is not None:
            it.setIcon(QIcon(pm))

    def _filtrar(self):
        modo, busca = self.filtro.valor(), self.busca.text().lower().strip()
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            nome = os.path.basename(it.data(Qt.UserRole)).lower()
            ok = (not modo or f"_{modo}_" in nome) and (not busca or busca in nome)
            it.setHidden(not ok)

    def _caminho(self):
        it = self.lista.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _marcadas(self):
        return [it.data(Qt.UserRole) for it in self.lista.selectedItems() if not it.isHidden()]

    def _modo_selecionar(self, ligado):
        self.lista.setSelectionMode(QListWidget.MultiSelection if ligado else QListWidget.ExtendedSelection)
        # marcadas bem visíveis no modo seleção (fundo roxo + borda grossa)
        self.lista.setStyleSheet(
            f"QListWidget::item:selected {{ background: rgba(139, 92, 246, 0.33); border: 3px solid {tema.ACENTO}; }}"
            if ligado else "")
        for b in (self.bt_todas, self.bt_nenhuma):
            b.setVisible(ligado)
        if not ligado:
            atual = self.lista.currentItem()
            self.lista.clearSelection()
            if atual is not None:
                atual.setSelected(True)
        self._atualizar_marcadas()

    def _marcar_todas(self):
        self.lista.blockSignals(True)
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            if not it.isHidden():
                it.setSelected(True)
        self.lista.blockSignals(False)
        self._atualizar_marcadas()

    def _atualizar_marcadas(self):
        n = len(self._marcadas())
        self.bt_apagar_marcadas.setText(tr("Apagar {n} marcadas", n=n))
        self.bt_apagar_marcadas.setVisible(n > 1 or self.bt_selecionar.isChecked())
        self.bt_apagar_marcadas.setEnabled(n > 0)

    def _selecionar(self, it, _ant=None):
        if not it:
            self.visor.definir_imagem(None)
            self.meta.setText("")
            return
        p = it.data(Qt.UserRole)
        r = QImageReader(p)
        r.setAutoTransform(True)
        img = r.read()
        if img.isNull():
            img = carregar_qimage(p)
        self.visor.definir_imagem(img if not img.isNull() else None)
        prompt = r.text("prompt")
        partes = [f"<b>{html.escape(os.path.basename(p))}</b>"]
        if not img.isNull():
            partes.append(f"{img.width()} × {img.height()}")
        for chave, rot in (("modelo", tr("Modelo")), ("semente", tr("Semente")), ("passos", tr("Passos"))):
            val = r.text(chave)
            if val:
                partes.append(f"{html.escape(rot)}: {html.escape(val)}")
        texto = " · ".join(partes)
        if prompt and prompt != "limpar":
            texto += "<br><br>" + html.escape(prompt).replace("\n", "<br>")
        self.meta.setText(texto)

    def copiar(self):
        img = self.visor.imagem()
        if img is not None:
            copiar_imagem(img)
            self.janela.toast(tr("Imagem copiada — é só colar (Ctrl+V)"), "ok")

    def _enviar(self, destino):
        img = self.visor.imagem()
        if img is not None:
            self.janela.enviar_para(destino, [img])

    def _reusar_prompt(self):
        p = self._caminho()
        if not p:
            return
        r = QImageReader(p)
        prompt, modo = r.text("prompt"), r.text("modo") or "gerar"
        if modo == "limpar" or prompt == "limpar":
            self._enviar("limpar")
            return
        if not prompt:
            self.janela.toast(tr("Esta imagem não tem prompt salvo."), "aviso")
            return
        modo = modo if modo in ("gerar", "editar", "mascara") else "gerar"
        self.janela.paginas[modo].prompt.definir(prompt)
        self.janela.ir_para(modo)

    def _mostrar_na_pasta(self):
        p = self._caminho()
        if p:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])

    def _comparar_com(self):
        """Escolhe outra imagem (normalmente o original) e abre a comparação: ela é o antes, esta o depois."""
        p = self._caminho()
        if not p:
            return
        pasta = self.janela.prefs.value("galeria/pasta_comparar", "") or os.path.expanduser("~")
        outro, _ = QFileDialog.getOpenFileName(self, tr("Comparar com…"), pasta, FILTRO_IMAGENS)
        if not outro:
            return
        self.janela.prefs.setValue("galeria/pasta_comparar", os.path.dirname(outro))
        antes, depois = carregar_qimage(outro), carregar_qimage(p)
        if antes.isNull() or depois.isNull():
            self.janela.toast(tr("Não foi possível abrir a imagem."), "erro")
            return
        DialogoComparar(antes, depois, os.path.basename(outro), os.path.basename(p), self.janela).show()

    def _apagar(self):
        """Manda para a Lixeira as marcadas (ou a aberta). Várias de uma vez pedem confirmação."""
        from PySide6.QtCore import QFile
        caminhos = self._marcadas() or ([self._caminho()] if self._caminho() else [])
        if not caminhos:
            return
        if len(caminhos) > 1:
            r = QMessageBox.question(self, tr("Mandar para a Lixeira"),
                                     tr("Mandar {n} imagens para a Lixeira? Dá para recuperar pela Lixeira do Windows.",
                                        n=len(caminhos)))
            if r != QMessageBox.Yes:
                return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            feitas = 0
            for p in caminhos:
                if QFile.moveToTrash(p):
                    self._cache.pop(p, None)
                    feitas += 1
        finally:
            QApplication.restoreOverrideCursor()
        if feitas:
            self.janela.toast(tr("Enviada para a Lixeira") if feitas == 1
                              else tr("{n} imagens enviadas para a Lixeira", n=feitas), "ok")
        if feitas < len(caminhos):
            self.janela.toast(tr("{n} imagens não puderam ser apagadas (abertas em outro programa?)",
                                 n=len(caminhos) - feitas), "aviso", 4000)
        self.recarregar()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self._apagar()
            return
        super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.lista.itemAt(pos)
        if not it:
            return
        if not it.isSelected():  # clique direito fora das marcadas: vale só esta
            self.lista.setCurrentItem(it)
        m = QMenu(self)
        m.addAction(tr("Copiar imagem"), self.copiar)
        m.addAction(tr("Reusar o prompt"), self._reusar_prompt)
        m.addAction(tr("Mandar para Editar"), lambda: self._enviar("editar"))
        m.addAction(tr("Mandar para Máscara"), lambda: self._enviar("mascara"))
        m.addAction(tr("Mandar para Limpar"), lambda: self._enviar("limpar"))
        m.addAction(tr("Mostrar na pasta"), self._mostrar_na_pasta)
        m.addAction(tr("Comparar com…"), self._comparar_com)
        m.addSeparator()
        n = len(self._marcadas())
        m.addAction(tr("Mandar {n} marcadas para a Lixeira", n=n) if n > 1 else tr("Mandar para a Lixeira"), self._apagar)
        m.exec(self.lista.mapToGlobal(pos))

    def colar(self, imgs, **_kw):
        self.janela.enviar_para("editar", imgs)


# ================================================================ Modelos
class TrabalhoDownload(QObject):
    progresso = Signal(float, float, float, str)
    terminou = Signal(bool, str)

    def __init__(self, modelo):
        super().__init__()
        self.m = modelo
        self.b = Baixador(modelo, ao_progresso=lambda *a: self.progresso.emit(*a))
        self._parar = False

    def rodar(self):
        try:
            if self.m["id"] == "flux2-klein-9b":
                from . import klein9b
                self.b.cancelar = lambda: setattr(self, "_parar", True)
                klein9b.baixar(config.pasta_modelo(self.m["id"]), lambda *a: self.progresso.emit(*a),
                               lambda: self._parar, pasta_4b=config.pasta_modelo("flux2-klein-4b"))
            else:
                self.b.baixar()
            self.terminou.emit(True, "")
        except Cancelado:
            self.terminou.emit(False, "")
        except Exception as e:  # noqa: BLE001
            # o klein9b tem a sua própria exceção Cancelado
            self.terminou.emit(False, "" if type(e).__name__ == "Cancelado" else str(e))


class CartaoModelo(QFrame):
    def __init__(self, pagina, m):
        super().__init__()
        self.pagina, self.m = pagina, m
        self.setObjectName("cartao")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        topo = QHBoxLayout()
        topo.setSpacing(8)
        nome = QLabel(m["nome"])
        nome.setStyleSheet("font-size: 15.5px; font-weight: 700;")
        topo.addWidget(nome)
        if m.get("principal"):
            topo.addWidget(rotulo(tr("PRINCIPAL"), "etiquetaAcento"))
        nomes = {"gerar": "Gerar", "editar": "Editar", "mascara": "Máscara"}
        for t in [f"{m['vram']} GB VRAM", f"~{m['gb']:.0f} GB"] + [tr(nomes[x]) for x in m["modos"]] + [m["licenca"]]:
            topo.addWidget(rotulo(t, "etiqueta"))
        topo.addStretch()
        self.estado = rotulo("", "valor")
        topo.addWidget(self.estado)
        v.addLayout(topo)
        desc = rotulo(tr(m["descricao"]), "subtitulo")
        desc.setWordWrap(True)
        v.addWidget(desc)
        self.caminho = rotulo("", "dica")
        v.addWidget(self.caminho)
        self.barra = QProgressBar()
        self.barra.setObjectName("grossa")
        self.barra.setRange(0, 1000)
        self.barra.setVisible(False)
        v.addWidget(self.barra)
        self.detalhe = rotulo("", "info")
        self.detalhe.setVisible(False)
        v.addWidget(self.detalhe)
        acoes = QHBoxLayout()
        acoes.setSpacing(6)
        self.bt_baixar = QPushButton(tr("Baixar"))
        self.bt_baixar.setIcon(tema.icone("salvar", "white", 16))
        self.bt_baixar.setCursor(Qt.PointingHandCursor)
        self.bt_baixar.clicked.connect(lambda: self.pagina.janela.baixar(self.m))
        self.bt_cancelar = QPushButton(tr("Pausar"))
        self.bt_cancelar.clicked.connect(lambda: self.pagina.janela.cancelar_download(self.m["id"]))
        self.bt_pasta = QPushButton(tr("Abrir pasta"))
        self.bt_pasta.clicked.connect(lambda: _abrir_pasta(config.pasta_modelo(self.m["id"])))
        self.bt_escolher = QPushButton(tr("Usar pasta existente…"))
        self.bt_escolher.setToolTip(tr("Se você já tem este modelo baixado em outro lugar."))
        self.bt_escolher.clicked.connect(self._escolher)
        self.bt_apagar = QPushButton(tr("Apagar"))
        self.bt_apagar.clicked.connect(self._apagar)
        for b in (self.bt_baixar, self.bt_cancelar, self.bt_pasta, self.bt_escolher):
            acoes.addWidget(b)
        acoes.addStretch()
        acoes.addWidget(self.bt_apagar)
        v.addLayout(acoes)
        self.atualizar()

    def atualizar(self):
        m = self.m
        pasta = config.pasta_modelo(m["id"])
        ok = config.modelo_instalado(m["id"], excluir=m.get("excluir", ()))
        baixando = m["id"] in self.pagina.janela.downloads
        parcial = os.path.isdir(pasta) and not ok
        self.estado.setText(tr("Baixando…") if baixando else (tr("✓ Instalado") if ok else (tr("Incompleto") if parcial else tr("Não baixado"))))
        self.estado.setStyleSheet(f"color: {tema.VERDE if ok else (tema.ACENTO2 if baixando else tema.SUAVE)};")
        self.caminho.setText(pasta)
        self.bt_baixar.setVisible(not ok and not baixando)
        self.bt_baixar.setText(tr("Continuar download") if parcial else tr("Baixar ({gb} GB)", gb=f"{m['gb']:.0f}"))
        self.bt_baixar.setObjectName("principal" if m.get("principal") else "")
        self.bt_baixar.setStyleSheet("padding: 8px 16px; font-size: 13px;" if m.get("principal") else "")
        self.bt_baixar.style().unpolish(self.bt_baixar)
        self.bt_baixar.style().polish(self.bt_baixar)
        self.bt_cancelar.setVisible(baixando)
        self.bt_pasta.setVisible(os.path.isdir(pasta))
        self.bt_escolher.setVisible(not baixando)
        externo = os.path.normcase(os.path.abspath(pasta)) != os.path.normcase(os.path.join(config.PASTA_MODELOS, m["id"]))
        self.bt_apagar.setText(tr("Esquecer pasta") if externo else tr("Apagar"))
        self.bt_apagar.setVisible((ok or parcial or externo) and not baixando)
        self.barra.setVisible(baixando)
        self.detalhe.setVisible(baixando)

    def progresso(self, feito, total, vel, arq):
        self.barra.setValue(int(1000 * feito / total) if total else 0)
        resta = (total - feito) / vel if vel > 1 else 0
        eta = f" · {tr('faltam')} {int(resta // 60)} min {int(resta % 60)} s" if resta else ""
        self.detalhe.setText(f"{_gb(feito)} / {_gb(total)} · {vel / 2**20:.1f} MB/s{eta}")

    def _escolher(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta do modelo {nome}", nome=self.m["nome"]))
        if not p:
            return
        if not config.modelo_instalado(self.m["id"], pasta=p, excluir=self.m.get("excluir", ())):
            QMessageBox.warning(self, tr("Pasta inválida"),
                                tr("Essa pasta não tem o modelo completo no formato diffusers (model_index.json + subpastas)."))
            return
        config.definir_pasta(self.m["id"], p)
        self.pagina.janela.modelos_mudaram()

    def _apagar(self):
        if self.pagina.janela.ocupado:
            self.pagina.janela.toast(tr("Espere terminar ou cancele (Esc) antes."), "aviso")
            return
        pasta = config.pasta_modelo(self.m["id"])
        externo = os.path.normcase(os.path.abspath(pasta)) != os.path.normcase(os.path.join(config.PASTA_MODELOS, self.m["id"]))
        if externo:
            config.definir_pasta(self.m["id"], None)
            self.pagina.janela.modelos_mudaram()
            return
        r = QMessageBox.question(self, tr("Apagar modelo"),
                                 tr("Apagar {nome} do disco ({gb})? Dá para baixar de novo depois.",
                                    nome=self.m["nome"], gb=_gb(config.tamanho_pasta(pasta))))
        if r == QMessageBox.Yes:
            self.bt_apagar.setEnabled(False)

            def _remover():
                shutil.rmtree(pasta, ignore_errors=True)
                self.bt_apagar.setEnabled(True)
                self.pagina.janela.modelos_mudaram()
                if os.path.isdir(pasta):
                    self.pagina.janela.toast(tr("Alguns arquivos ainda estavam em uso e não puderam ser apagados."), "aviso", 5000)
                else:
                    self.pagina.janela.toast(tr("Modelo apagado do disco."), "ok")

            self.pagina.janela.liberar_placa(ao_terminar=_remover)


class PaginaModelos(QWidget):
    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        fora = QVBoxLayout(self)
        fora.setContentsMargins(0, 0, 0, 0)
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        interno = QWidget()
        v = QVBoxLayout(interno)
        v.setContentsMargins(28, 22, 28, 28)
        v.setSpacing(12)
        v.addWidget(rotulo(tr("Modelos"), "titulo"))
        sub = rotulo(tr("Ficam na pasta “modelos” ao lado do programa. O download continua de onde parou se cair."), "subtitulo")
        sub.setWordWrap(True)
        v.addWidget(sub)
        self.cartoes = {}
        grupos = [(tr("Principal"), [m for m in MODELOS if m.get("principal")]),
                  (tr("Secundários · placas de 16 GB"), [m for m in MODELOS if not m.get("principal") and m["vram"] == 16]),
                  (tr("Secundários · placas de 8 GB"), [m for m in MODELOS if not m.get("principal") and m["vram"] == 8])]
        for titulo, lista in grupos:
            if not lista:
                continue
            l = rotulo(titulo.upper(), "rotulo")
            l.setContentsMargins(2, 12, 0, 0)
            v.addWidget(l)
            for m in lista:
                c = CartaoModelo(self, m)
                self.cartoes[m["id"]] = c
                v.addWidget(c)
        v.addStretch()
        rolagem.setWidget(interno)
        fora.addWidget(rolagem)

    def atualizar(self):
        for c in self.cartoes.values():
            c.atualizar()

    def colar(self, imgs, **_kw):
        self.janela.enviar_para("editar", imgs)


# ================================================================ Configurações
class PaginaConfig(QWidget):
    _sinal_atualizacao = Signal(str, str)

    def __init__(self, janela):
        super().__init__()
        self.janela = janela
        self._sinal_atualizacao.connect(self._resposta_atualizacao)
        fora = QVBoxLayout(self)
        fora.setContentsMargins(0, 0, 0, 0)
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        interno = QWidget()
        v = QVBoxLayout(interno)
        v.setContentsMargins(28, 22, 28, 28)
        v.setSpacing(14)
        v.addWidget(rotulo(tr("Configurações"), "titulo"))

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(10)
        cv.addWidget(rotulo(tr("Idioma"), "rotulo"))
        h = QHBoxLayout()
        self.idioma = QComboBox()
        for cod, nome in i18n.IDIOMAS.items():
            self.idioma.addItem(nome, cod)
        self.idioma.setCurrentIndex(list(i18n.IDIOMAS).index(i18n.atual()))
        self.idioma.setFixedWidth(260)
        self.idioma.currentIndexChanged.connect(self._trocar_idioma)
        h.addWidget(self.idioma)
        self.bt_reiniciar = QPushButton(tr("Reiniciar para aplicar"))
        self.bt_reiniciar.setObjectName("principal")
        self.bt_reiniciar.setStyleSheet("padding: 8px 16px; font-size: 13px;")
        self.bt_reiniciar.setVisible(False)
        self.bt_reiniciar.clicked.connect(self.janela.reiniciar)
        h.addWidget(self.bt_reiniciar)
        h.addStretch()
        cv.addLayout(h)
        cv.addWidget(rotulo(tr("Na primeira vez o programa usa o idioma do Windows."), "dica"))
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(rotulo(tr("Placa de vídeo"), "rotulo"))
        self.pre_carregar = QWidget()
        hp = QHBoxLayout(self.pre_carregar)
        hp.setContentsMargins(0, 6, 0, 0)
        hp.addWidget(rotulo(tr("Ao abrir o programa"), "subtitulo"))
        self.ao_abrir = QComboBox()
        for cod, texto in (("perguntar", tr("Perguntar se quer carregar o modelo")),
                           ("sempre", tr("Carregar o modelo sempre")), ("nunca", tr("Não carregar (carrego quando for usar)"))):
            self.ao_abrir.addItem(texto, cod)
        atual = config.carregar().get("ao_abrir", "perguntar")
        self.ao_abrir.setCurrentIndex(max(0, ["perguntar", "sempre", "nunca"].index(atual) if atual in ("perguntar", "sempre", "nunca") else 0))
        self.ao_abrir.currentIndexChanged.connect(lambda i: config.salvar({"ao_abrir": self.ao_abrir.itemData(i)}))
        hp.addWidget(self.ao_abrir)
        hp.addStretch()
        self.placa = rotulo(tr("Verificando…"), "valor")
        self.placa.setStyleSheet("font-size: 14px;")
        cv.addWidget(self.placa)
        self.placa_dica = rotulo("", "dica")
        self.placa_dica.setWordWrap(True)
        cv.addWidget(self.placa_dica)
        cv.addWidget(self.pre_carregar)
        self.bandeja = QCheckBox(tr("Ao fechar a janela, continuar na bandeja com o modelo carregado (reabre na hora)"))
        self.bandeja.setChecked(config.carregar().get("fechar_para_bandeja", True))
        self.bandeja.toggled.connect(lambda v: config.salvar({"fechar_para_bandeja": v}))
        cv.addWidget(self.bandeja)
        self.com_windows = QCheckBox(tr("Abrir junto com o Windows (escondido na bandeja, já carregando o modelo)"))
        self.com_windows.setChecked(config.carregar().get("iniciar_com_windows", False))
        self.com_windows.toggled.connect(self._iniciar_com_windows)
        cv.addWidget(self.com_windows)
        bt_teste = QPushButton(tr("Testar a placa"))
        bt_teste.setToolTip(tr("Abre o modelo e gera uma imagem pequena, mostrando cada etapa. Salva autoteste.txt na pasta do programa."))
        bt_teste.clicked.connect(self._testar_placa)
        cv.addWidget(bt_teste, 0, Qt.AlignLeft)
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(rotulo(tr("Hugging Face (modelos com termos de uso)"), "rotulo"))
        exp = rotulo(tr("Alguns modelos (ex.: FLUX.2 klein 9B) só baixam depois que VOCÊ aceita os termos na página do modelo "
                        "no huggingface.co e cola aqui o seu token de leitura (huggingface.co/settings/tokens). "
                        "Fica salvo só neste computador."), "dica")
        exp.setWordWrap(True)
        cv.addWidget(exp)
        h = QHBoxLayout()
        self.token = QLineEdit(config.carregar().get("hf_token", ""))
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("hf_…")
        self.token.editingFinished.connect(lambda: config.salvar({"hf_token": self.token.text().strip()}))
        h.addWidget(self.token, 1)
        cv.addLayout(h)
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(rotulo(tr("Atualizações"), "rotulo"))
        h = QHBoxLayout()
        h.addWidget(rotulo(tr("Versão {v}", v=instalacao.versao_local(config.BASE)), "valor"))
        self.atualiza_txt = rotulo("", "dica")
        h.addWidget(self.atualiza_txt, 1)
        self.bt_atualizar = QPushButton(tr("Procurar atualização"))
        self.bt_atualizar.clicked.connect(self._procurar_atualizacao)
        h.addWidget(self.bt_atualizar)
        cv.addLayout(h)
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(8)
        cv.addWidget(rotulo(tr("Pastas"), "rotulo"))
        for texto, pasta in ((tr("Imagens geradas"), config.SAIDAS), (tr("Modelos"), config.PASTA_MODELOS),
                             (tr("Programa"), config.BASE)):
            h = QHBoxLayout()
            h.addWidget(rotulo(texto, "valor"))
            h.addWidget(rotulo(pasta, "dica"), 1)
            b = QPushButton(tr("Abrir"))
            b.clicked.connect(lambda _=False, p=pasta: _abrir_pasta(p))
            h.addWidget(b)
            cv.addLayout(h)
        v.addWidget(c)

        c = _cartao()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(6)
        cv.addWidget(rotulo(tr("Atalhos"), "rotulo"))
        for tecla, o_que in (("Ctrl+V", tr("Colar imagem (Photoshop, navegador, Explorer…) na aba atual")),
                             ("Ctrl+C", tr("Copiar o resultado para colar em outro programa")),
                             ("Ctrl+Enter", tr("Gerar / Editar / Refazer")), ("Esc", tr("Cancelar")),
                             ("C", tr("Comparar antes/depois")), ("Ctrl+1…7", tr("Trocar de aba")),
                             ("[  ]  ·  Alt+roda", tr("Tamanho do pincel (Máscara)")), ("X", tr("Pincel / borracha")),
                             ("Ctrl+Z", tr("Desfazer pincelada")), (tr("Roda / duplo clique"), tr("Zoom / ajustar ↔ 100%"))):
            h = QHBoxLayout()
            k = rotulo(tecla, "etiqueta")
            k.setFixedWidth(150)
            h.addWidget(k)
            h.addWidget(rotulo(o_que, "subtitulo"), 1)
            cv.addLayout(h)
        v.addWidget(c)
        v.addStretch()
        rolagem.setWidget(interno)
        fora.addWidget(rolagem)

    def _iniciar_com_windows(self, ligar):
        from . import instancia
        try:
            instancia.iniciar_com_windows(ligar)
            config.salvar({"iniciar_com_windows": ligar})
        except OSError as e:
            self.janela.toast(str(e), "erro", 5000)

    def _testar_placa(self):
        """Roda `FluxEstudio.py --autoteste` num processo à parte e mostra cada etapa ao vivo."""
        if self.janela.ocupado:
            self.janela.toast(tr("Espere terminar ou cancele (Esc) antes."), "aviso")
            return
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QDialog, QPlainTextEdit
        d = QDialog(self)
        d.setWindowTitle(tr("Testar a placa"))
        d.resize(760, 460)
        d.setStyleSheet(f"QDialog {{ background: {tema.PAINEL}; }}")
        v = QVBoxLayout(d)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setStyleSheet("font-family: Consolas; font-size: 12px;")
        v.addWidget(txt)
        fechar = QPushButton(tr("Fechar"))
        v.addWidget(fechar, 0, Qt.AlignRight)
        py = sys.executable.replace("pythonw.exe", "python.exe")
        proc = QProcess(d)
        proc.setWorkingDirectory(config.BASE)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: txt.appendPlainText(bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))
        proc.finished.connect(lambda code, _s: txt.appendPlainText(
            "\n" + (tr("Tudo certo: o modelo roda na placa de vídeo.") if code == 0 else
                    tr("O teste falhou. Mande o arquivo {arq}", arq=os.path.join(config.BASE, "autoteste.txt")))))
        estado = {"fechado": False}
        fechar.clicked.connect(lambda: (estado.__setitem__("fechado", True), proc.kill(), d.accept()))
        txt.appendPlainText(tr("Liberando a placa de vídeo e iniciando o teste (a primeira vez pode levar alguns minutos)…"))

        def iniciar_proc():
            if not estado["fechado"]:
                proc.start(py, [os.path.join(config.BASE, "FluxEstudio.py"), "--autoteste"])

        self.janela.liberar_placa(ao_terminar=iniciar_proc)
        tema.barra_titulo_escura(d)
        d.exec()
        estado["fechado"] = True
        proc.kill()
        proc.waitForFinished(1000)

    def definir_placa(self, nome, backend, total):
        if nome:
            suf = f" · {total:.0f} GB" if total else ""
            self.placa.setText(f"{nome} · {backend}{suf}")
            if total >= 11.5:
                dica = tr("Tudo certo: o modelo roda na placa de vídeo.")
            elif total >= 5.5:
                dica = tr("Modo econômico ativo: o FLUX.2 4B transmite blocos pela placa sem estourar a memória.")
            else:
                dica = tr("Pouca VRAM detectada: use tamanhos menores (768–1024 px) e feche outros programas pesados.")
        else:
            self.placa.setText(tr("Nenhuma placa compatível encontrada · rodando no processador"))
            dica = tr("Vai funcionar no processador, mas bem mais devagar. Placas compatíveis: NVIDIA (CUDA), AMD Radeon (ROCm) e Intel Arc (XPU).")
        self.placa_dica.setText(dica)

    # ---- atualização pelo GitHub (roda numa thread do Python; avisa a tela por sinais)
    def _procurar_atualizacao(self):
        if os.path.isdir(os.path.join(config.BASE, ".git")):
            self.atualiza_txt.setText(tr("Pasta de desenvolvimento (git): atualize com git pull."))
            return
        self.bt_atualizar.setEnabled(False)
        self.atualiza_txt.setText(tr("Procurando…"))
        import threading
        threading.Thread(target=self._thread_atualizacao, daemon=True).start()

    def _thread_atualizacao(self):
        s = self._sinal_atualizacao
        try:
            remota, local = instalacao.versao_remota(), instalacao.versao_local(config.BASE)
            if not instalacao.mais_nova(remota, local):
                s.emit("ok", tr("Você já tem a versão mais nova."))
                return
            s.emit("pergunta", remota)
        except Exception as e:  # noqa: BLE001
            s.emit("erro", tr("Sem conexão com o GitHub: {e}", e=str(e)[:120]))

    def _aplicar_atualizacao(self, remota):
        import threading
        self.atualiza_txt.setText(tr("Baixando a versão {v}…", v=remota))

        def trabalho():
            s = self._sinal_atualizacao
            try:
                mudou = instalacao.instalar_programa(config.BASE)
                if mudou and os.path.isfile(instalacao.caminho_uv(config.BASE)):
                    s.emit("info", tr("Atualizando as bibliotecas…"))
                    info = instalacao.ler_info(config.BASE)
                    bk = info.get("backend") or "cuda"
                    gfx = info.get("gfx") or "gfx1201"
                    if bk not in instalacao.TORCH:
                        bk = "cuda"
                    cmd = instalacao.comandos_instalacao(config.BASE, bk, gfx)[2][1]  # só o requisitos.txt, mantendo o backend
                    instalacao.rodar(cmd, config.BASE)
                instalacao.gravar_info(config.BASE, versao=instalacao.versao_local(config.BASE))
                s.emit("feito", remota)
            except Exception as e:  # noqa: BLE001
                s.emit("erro", tr("A atualização falhou: {e}", e=str(e)[:200]))
        threading.Thread(target=trabalho, daemon=True).start()

    def _resposta_atualizacao(self, tipo, texto):
        if tipo == "pergunta":
            r = QMessageBox.question(self, tr("Atualização"),
                                     tr("Tem uma versão nova ({v}). Atualizar agora? Modelos, imagens e prompts ficam como estão.", v=texto))
            if r == QMessageBox.Yes:
                self._aplicar_atualizacao(texto)
                return
            texto = ""
        elif tipo == "info":
            self.atualiza_txt.setText(texto)
            return
        elif tipo == "feito":
            self.atualiza_txt.setText(tr("Atualizado para a versão {v}.", v=texto))
            self.bt_reiniciar.setVisible(True)
            self.janela.toast(tr("Atualizado. Reinicie para usar a versão nova."), "ok", 5000)
            return
        self.atualiza_txt.setText(texto)
        self.bt_atualizar.setEnabled(True)

    def _trocar_idioma(self, i):
        cod = self.idioma.itemData(i)
        i18n.definir(cod)
        self.bt_reiniciar.setVisible(cod != i18n.atual())

    def colar(self, imgs, **_kw):
        self.janela.enviar_para("editar", imgs)
