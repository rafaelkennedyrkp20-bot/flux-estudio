"""Blocos usados pelas abas: campo de prompt com prompts salvos, painel de resultado,
seletor de modelo e semente."""
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from . import config, tema
from .comparar import DialogoComparar
from .i18n import tr
from .modelos import para_modo
from .widgets import FluxoLayout, TiraMiniaturas, Visor, botao_icone, carregar_qimage, copiar_imagem, rotulo

def nome_prompt(p):
    """Prompts que vêm com o programa aparecem no idioma escolhido; os do usuário, como ele salvou."""
    return tr(p["nome"]) if p.get("padrao") else p["nome"]


NOME_MODO = {"gerar": "Gerar", "editar": "Editar", "mascara": "Máscara"}


# ---------------------------------------------------------------- salvar prompt
class DialogoPrompt(QDialog):
    def __init__(self, parent, texto, modo, nome="", todos=False, titulo=None):
        super().__init__(parent)
        self.setWindowTitle(titulo or tr("Salvar prompt"))
        self.setMinimumWidth(520)
        self.setStyleSheet(f"QDialog {{ background: {tema.PAINEL}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        v.addWidget(rotulo(tr("Nome")))
        self.nome = QLineEdit(nome)
        self.nome.setPlaceholderText(tr("Ex.: Tirar marca d'água"))
        v.addWidget(self.nome)
        v.addWidget(rotulo(tr("Prompt")))
        self.texto = QPlainTextEdit(texto)
        self.texto.setMinimumHeight(130)
        v.addWidget(self.texto)
        v.addWidget(rotulo(tr("Aparece em")))
        self.onde = QComboBox()
        self.onde.addItem(tr("Só na aba {aba}", aba=tr(NOME_MODO[modo])), modo)
        self.onde.addItem(tr("Todas as abas"), "todos")
        self.onde.setCurrentIndex(1 if todos else 0)
        v.addWidget(self.onde)
        h = QHBoxLayout()
        h.addStretch()
        cancelar = QPushButton(tr("Cancelar"))
        cancelar.clicked.connect(self.reject)
        ok = QPushButton(tr("Salvar"))
        ok.setObjectName("principal")
        ok.setMinimumWidth(120)
        ok.clicked.connect(self._ok)
        h.addWidget(cancelar)
        h.addWidget(ok)
        v.addLayout(h)
        self.nome.setFocus()

    def _ok(self):
        if not self.nome.text().strip():
            self.nome.setFocus()
            self.nome.setPlaceholderText(tr("Dê um nome para o prompt"))
            return
        if not self.texto.toPlainText().strip():
            return
        self.accept()

    def valores(self):
        return self.nome.text().strip(), self.texto.toPlainText().strip(), self.onde.currentData()


# ---------------------------------------------------------------- campo de prompt
class CampoPrompt(QWidget):
    """Texto do prompt + histórico + prompts salvos como chips (clique usa, Shift+clique acrescenta,
    botão direito: atualizar, renomear, apagar…)."""

    def __init__(self, biblioteca, modo, titulo, dica_vazio):
        super().__init__()
        self.bib, self.modo = biblioteca, modo
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        topo = QHBoxLayout()
        topo.setSpacing(2)
        topo.addWidget(rotulo(titulo))
        topo.addStretch()
        self.bt_hist = botao_icone("historico", tr("Prompts recentes"), self._menu_historico, tamanho=16)
        self.bt_salvar = botao_icone("salvar", tr("Salvar este prompt com um nome"), self._salvar, tamanho=16)
        self.bt_salvar.setIcon(tema.icone("mais", tema.TEXTO, 16))
        topo.addWidget(self.bt_hist)
        topo.addWidget(self.bt_salvar)
        v.addLayout(topo)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(dica_vazio)
        self.edit.setMinimumHeight(118)
        self.edit.setTabChangesFocus(True)
        v.addWidget(self.edit)
        self._caixa_chips = QWidget()
        self._chips = FluxoLayout(self._caixa_chips, 6)
        v.addWidget(self._caixa_chips)
        self.bib.mudou.connect(self._montar_chips)
        self._montar_chips()

    def texto(self):
        return self.edit.toPlainText().strip()

    def definir(self, t):
        self.edit.setPlainText(t)
        self.edit.moveCursor(self.edit.textCursor().MoveOperation.End)

    def _montar_chips(self):
        while self._chips.count():
            w = self._chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        itens = self.bib.para_modo(self.modo)
        for i, p in itens:
            b = QPushButton(nome_prompt(p))
            b.setObjectName("chipPrompt")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(p["texto"][:400] + "\n\n" + tr("Clique: usar · Shift+clique: acrescentar · Botão direito: opções"))
            b.clicked.connect(lambda _=False, i=i: self._usar(i, QApplication.keyboardModifiers() & Qt.ShiftModifier))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, i=i, b=b: self._menu_chip(i, b))
            self._chips.addWidget(b)
        self._caixa_chips.setVisible(bool(itens))
        self._caixa_chips.updateGeometry()

    def _usar(self, i, acrescentar=False):
        t = self.bib.salvos[i]["texto"]
        if acrescentar and self.texto():
            t = self.texto() + (" " if self.texto().endswith(".") else ". ") + t
        self.definir(t)
        self.edit.setFocus()

    def _menu_chip(self, i, botao):
        p = self.bib.salvos[i]
        m = QMenu(self)
        m.addAction(tr("Usar"), lambda: self._usar(i))
        m.addAction(tr("Acrescentar ao prompt atual"), lambda: self._usar(i, True))
        m.addSeparator()
        m.addAction(tr("Editar…"), lambda: self._editar(i))
        a = m.addAction(tr("Substituir pelo texto atual"), lambda: self.bib.atualizar(i, texto=self.texto()))
        a.setEnabled(bool(self.texto()))
        m.addAction(tr("Renomear…"), lambda: self._renomear(i))
        if p.get("modo") == "todos":
            m.addAction(tr("Mostrar só nesta aba"), lambda: self.bib.atualizar(i, modo=self.modo))
        else:
            m.addAction(tr("Mostrar em todas as abas"), lambda: self.bib.atualizar(i, modo="todos"))
        m.addAction(tr("Mover para a esquerda"), lambda: self.bib.mover(i, -1))
        m.addAction(tr("Mover para a direita"), lambda: self.bib.mover(i, 1))
        m.addSeparator()
        m.addAction(tr("Apagar"), lambda: self.bib.remover(i))
        m.exec(botao.mapToGlobal(botao.rect().bottomLeft()))

    def _editar(self, i):
        p = self.bib.salvos[i]
        d = DialogoPrompt(self, p["texto"], self.modo, nome_prompt(p), p.get("modo") == "todos", tr("Editar prompt"))
        if d.exec():
            nome, texto, modo = d.valores()
            self.bib.atualizar(i, nome=nome, texto=texto, modo=modo)

    def _renomear(self, i):
        nome, ok = QInputDialog.getText(self, tr("Renomear"), tr("Nome"), text=nome_prompt(self.bib.salvos[i]))
        if ok and nome.strip():
            self.bib.atualizar(i, nome=nome.strip())

    def _salvar(self):
        d = DialogoPrompt(self, self.texto(), self.modo)
        if d.exec():
            nome, texto, modo = d.valores()
            self.bib.adicionar(nome, texto, modo)
            self.window().toast(tr("Prompt “{nome}” salvo", nome=nome), "ok")

    def _menu_historico(self):
        m = QMenu(self)
        hist = self.bib.historico.get(self.modo, [])
        if not hist:
            a = m.addAction(tr("Nada ainda: os prompts usados aparecem aqui"))
            a.setEnabled(False)
        for t in hist[:20]:
            curto = t if len(t) < 70 else t[:67] + "…"
            a = m.addAction(curto, lambda t=t: self.definir(t))
            a.setToolTip(t)
        m.setToolTipsVisible(True)
        m.exec(self.bt_hist.mapToGlobal(self.bt_hist.rect().bottomLeft()))


# ---------------------------------------------------------------- seletor de modelo
class SeletorModelo(QWidget):
    mudou = Signal(dict)

    def __init__(self, modo):
        super().__init__()
        self.modo = modo
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(rotulo(tr("Modelo")))
        self.combo = QComboBox()
        self.combo.setCursor(Qt.PointingHandCursor)
        v.addWidget(self.combo)
        self._modelos = para_modo(modo)
        self.combo.currentIndexChanged.connect(lambda i: i >= 0 and self.mudou.emit(self._modelos[i]))
        self.atualizar()

    def atualizar(self):
        atual = self.combo.currentIndex()
        self.combo.blockSignals(True)
        self.combo.clear()
        for m in self._modelos:
            ok = config.modelo_instalado(m["id"], excluir=m.get("excluir", ()))
            extra = tr(" · principal") if m.get("principal") else f" · {m['vram']} GB"
            self.combo.addItem(m["nome"] + extra + ("" if ok else tr("  (não baixado)")), m["id"])
        self.combo.setCurrentIndex(max(0, atual))
        self.combo.blockSignals(False)

    def modelo(self):
        return self._modelos[max(0, self.combo.currentIndex())]

    def definir(self, id_modelo):
        for i, m in enumerate(self._modelos):
            if m["id"] == id_modelo:
                self.combo.setCurrentIndex(i)


# ---------------------------------------------------------------- semente
class LinhaSemente(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(rotulo(tr("Semente")))
        h = QHBoxLayout()
        h.setSpacing(6)
        self.spin = QSpinBox()
        self.spin.setRange(-1, 2**31 - 1)
        self.spin.setValue(-1)
        self.spin.setSpecialValueText(tr("Aleatória"))
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        h.addWidget(self.spin, 1)
        h.addWidget(botao_icone("dado", tr("Aleatória a cada vez"), lambda: self.spin.setValue(-1)))
        self.bt_ultima = botao_icone("historico", tr("Repetir a última semente"), self._ultima)
        h.addWidget(self.bt_ultima)
        v.addLayout(h)
        self.ultima = None

    def _ultima(self):
        if self.ultima is not None:
            self.spin.setValue(self.ultima)

    def valor(self):
        return self.spin.value()


# ---------------------------------------------------------------- painel de resultado
class PainelResultado(QWidget):
    """Visualizador grande + miniaturas + ações (copiar, salvar, comparar, mandar para outra aba)."""
    enviar = Signal(str, QImage)       # destino ("editar"/"mascara"), imagem
    continuar = Signal(QImage)         # usar o resultado como nova entrada nesta aba

    def __init__(self, texto_vazio, com_continuar=True):
        super().__init__()
        self._itens = []  # [(QImage, caminho)]
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        barra = QHBoxLayout()
        barra.setSpacing(2)
        self.info = rotulo("", "info")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        barra.addWidget(self.info, 1)
        self.bt_comparar = botao_icone("comparar", tr("Comparar antes/depois (passe o mouse) · C"), self._comparar, True)
        self.bt_janela = botao_icone("lado_a_lado", tr("Abrir comparação antes/depois em janela"), self._abrir_comparacao)
        self.bt_ajustar = botao_icone("ajustar", tr("Ajustar à tela (duplo clique alterna 100%)"), lambda: self.visor.ajustar())
        self.bt_copiar = botao_icone("copiar", tr("Copiar imagem (Ctrl+C) — cola no Photoshop"), self.copiar)
        self.bt_salvar = botao_icone("salvar", tr("Salvar como…"), self._salvar_como)
        self.bt_pasta = botao_icone("abrir", tr("Mostrar na pasta"), self._mostrar_na_pasta)
        self.bt_editar = botao_icone("editar", tr("Mandar para Editar"), lambda: self._enviar("editar"))
        self.bt_mascara = botao_icone("mascara", tr("Mandar para Máscara"), lambda: self._enviar("mascara"))
        self.bt_continuar = botao_icone("continuar", tr("Continuar editando este resultado"), self._continuar)
        self._com_continuar = com_continuar
        for b in (self.bt_comparar, self.bt_janela, self.bt_ajustar, self.bt_copiar, self.bt_salvar, self.bt_pasta,
                  self.bt_editar, self.bt_mascara, self.bt_continuar):
            barra.addWidget(b)
        v.addLayout(barra)
        self.progresso = QProgressBar()
        self.progresso.setRange(0, 100)
        self.progresso.setValue(0)
        self.progresso.setVisible(False)
        v.addWidget(self.progresso)
        self.visor = Visor()
        self.visor.texto_vazio = texto_vazio
        v.addWidget(self.visor, 1)
        self.tira = TiraMiniaturas(72)
        self.tira.escolhido.connect(self._mostrar)
        self.tira.setVisible(False)
        v.addWidget(self.tira)
        self._entrada = None
        self._atualizar_botoes()

    # -- estado
    def ocupado(self, sim, texto=""):
        self.visor.definir_ocupado(sim)
        self.progresso.setVisible(sim)
        if sim:
            self.progresso.setRange(0, 0)  # animado até chegar o 1º passo
            self.info.setText(texto)

    def status(self, texto):
        self.info.setText(texto)

    def passo(self, feito, total):
        self.progresso.setRange(0, total)
        self.progresso.setValue(feito)

    # -- imagens
    def mostrar_entrada(self, img):
        """Antes de ter resultado, mostra a imagem de entrada (e ela vira o "antes" da comparação)."""
        self._entrada = img
        self.visor.definir_antes(img)
        if not self._itens and img is not None:
            self.visor.definir_imagem(img)
        elif img is None and not self._itens:
            self.visor.definir_imagem(None)
        self._atualizar_botoes()

    def limpar(self):
        self._itens.clear()
        self.tira.clear()
        self.tira.setVisible(False)
        if self._entrada is not None:
            self.visor.definir_imagem(self._entrada)
        self._atualizar_botoes()

    def adicionar(self, caminho):
        img = carregar_qimage(caminho)
        if img.isNull():
            return
        self._itens.append((img, caminho))
        self.tira.adicionar(img, os.path.basename(caminho))
        self.tira.setVisible(len(self._itens) > 1)
        self.tira.blockSignals(True)
        self.tira.setCurrentRow(len(self._itens) - 1)
        self.tira.blockSignals(False)
        self.visor.definir_imagem(img, manter_vista=True)
        self._atualizar_botoes()

    def _mostrar(self, i):
        if 0 <= i < len(self._itens):
            self.visor.definir_imagem(self._itens[i][0], manter_vista=True)

    def atual(self):
        """(QImage, caminho) do resultado mostrado, ou (entrada, None)."""
        i = self.tira.currentRow() if self._itens else -1
        if 0 <= i < len(self._itens):
            return self._itens[i]
        if self._itens:
            return self._itens[-1]
        return (self.visor.imagem(), None)

    def _atualizar_botoes(self):
        tem = self.visor.tem_imagem()
        tem_res = bool(self._itens)
        for b in (self.bt_ajustar, self.bt_copiar, self.bt_salvar, self.bt_editar, self.bt_mascara):
            b.setVisible(tem)
        self.bt_pasta.setVisible(tem_res)
        self.bt_continuar.setVisible(tem_res and self._com_continuar)
        self.bt_comparar.setVisible(tem_res and self._entrada is not None)
        self.bt_janela.setVisible(tem_res and self._entrada is not None)
        self.bt_comparar.setEnabled(tem_res and self._entrada is not None)
        if not self.bt_comparar.isEnabled():
            self.bt_comparar.setChecked(False)
            self.visor.comparar = False

    # -- ações
    def _comparar(self):
        self.visor.comparar = self.bt_comparar.isChecked()
        self.visor.update()

    def _abrir_comparacao(self):
        img, caminho = self.atual()
        if self._entrada is None or img is None or not self._itens:
            return
        DialogoComparar(self._entrada, img, "", os.path.basename(caminho or ""), self.window()).show()

    def alternar_comparar(self):
        if self.bt_comparar.isEnabled():
            self.bt_comparar.toggle()
            self._comparar()

    def copiar(self):
        img, _ = self.atual()
        if img is not None and not img.isNull():
            copiar_imagem(img)
            self.window().toast(tr("Imagem copiada — é só colar (Ctrl+V)"), "ok")

    def _salvar_como(self):
        img, caminho = self.atual()
        if img is None:
            return
        sugestao = caminho or os.path.join(config.SAIDAS, "imagem.png")
        destino, _ = QFileDialog.getSaveFileName(self, tr("Salvar como"), sugestao, "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp)")
        if destino:
            img.save(destino, quality=95)
            self.window().toast(tr("Salvo"), "ok")

    def _mostrar_na_pasta(self):
        _, caminho = self.atual()
        if caminho:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])

    def _enviar(self, destino):
        img, _ = self.atual()
        if img is not None:
            self.enviar.emit(destino, img)

    def _continuar(self):
        img, _ = self.atual()
        if img is not None and self._itens:
            self.continuar.emit(img)
