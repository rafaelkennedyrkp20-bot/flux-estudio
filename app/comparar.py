"""Janela de comparação antes/depois: lado a lado (zoom e posição juntos), deslizar (linha arrastável)
e alternar (Espaço troca). Mostra os dois tamanhos e avisa se não forem iguais."""
import html

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from . import tema
from .i18n import tr
from .widgets import Segmentado, Toast, VisorBase, copiar_imagem, rotulo


def _etiqueta(p, texto, x, y, direita=False, cor="white"):
    """Plaquinha escura com texto (ANTES / DEPOIS) no canto do visor."""
    if not texto:
        return
    f = p.font()
    f.setPointSizeF(8.5)
    f.setBold(True)
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance(texto) + 22
    caixa = QRectF(x - w if direita else x, y, w, 24)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 175))
    p.drawRoundedRect(caixa, 7, 7)
    p.setPen(QColor(cor))
    p.drawText(caixa, Qt.AlignCenter, texto)


def _copiar_vista(de, para):
    """Leva zoom e posição de um visor para outro em coordenadas normalizadas da imagem
    (funciona mesmo se as duas imagens tiverem tamanhos diferentes)."""
    if de._pix is None or para._pix is None:
        return
    para._anim.stop()
    if de._auto:
        para.ajustar()
        return
    centro = de.para_imagem(QPointF(de.width() / 2, de.height() / 2))
    nx, ny = centro.x() / de._pix.width(), centro.y() / de._pix.height()
    para._escala = de._escala * de._pix.width() / para._pix.width()
    alvo = QPointF(nx * para._pix.width(), ny * para._pix.height())
    para._desl = QPointF(para.width() / 2, para.height() / 2) - alvo * para._escala
    para._auto = False
    para.update()


# ---------------------------------------------------------------- visor
class VisorComparar(VisorBase):
    """modo "so": uma imagem (cada lado do lado a lado); "deslizar": o depois com o antes à esquerda
    da linha; "alternar": antes ou depois inteiro. O antes ocupa o mesmo retângulo do depois."""
    ponteiro = Signal(object)  # posição do mouse normalizada (0..1) ou None

    def __init__(self, img, antes=None, etiqueta=""):
        super().__init__()
        self.modo = "so"
        self.etiqueta = etiqueta
        self.mostrar_antes = False
        self.marca = None  # ponto normalizado vindo do outro lado (lado a lado)
        self._antes_pix = QPixmap.fromImage(antes) if antes is not None else None
        self._linha = 0.5  # fração da largura do visor (fica no lugar com zoom)
        self._arrastando = False
        self.definir_imagem(img)

    def escala(self):
        return self._escala

    def atualizar_cursor(self):
        if self.modo == "deslizar":
            self.setCursor(Qt.SplitHCursor)
        else:
            self.unsetCursor()

    def _normalizado(self, pos):
        pt = self.para_imagem(pos)
        nx, ny = pt.x() / self._pix.width(), pt.y() / self._pix.height()
        return QPointF(nx, ny) if 0 <= nx <= 1 and 0 <= ny <= 1 else None

    def _mover_linha(self, x):
        self._linha = min(1.0, max(0.0, x / max(1, self.width())))
        self.update()

    # -- mouse
    def mousePressEvent(self, e):
        if self._pix is None:
            return
        if self.modo == "deslizar" and e.button() == Qt.LeftButton:
            self._arrastando = True
            self._mover_linha(e.position().x())
        else:
            self._comeca_pan(e)

    def mouseMoveEvent(self, e):
        if self._arrastando:
            self._mover_linha(e.position().x())
            return
        if self._move_pan(e):
            return
        if self.modo == "so" and self._pix is not None:
            self.ponteiro.emit(self._normalizado(e.position()))

    def mouseReleaseEvent(self, e):
        self._arrastando = False
        self._fim_pan()
        self.atualizar_cursor()

    def leaveEvent(self, e):
        self.ponteiro.emit(None)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._auto:
            self.vista_mudou.emit()  # o outro lado acompanha

    # -- pintura
    def _pintar_linha(self, p, x, r):
        topo, base = max(r.top(), 0.0), min(r.bottom(), float(self.height()))
        if base <= topo:
            return
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawLine(QPointF(x, topo), QPointF(x, base))
        c = QPointF(x, (topo + base) / 2)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QColor("white"))
        p.drawEllipse(c, 15, 15)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tema.CARTAO2))
        for s in (-1, 1):
            p.drawPolygon(QPolygonF([QPointF(c.x() + s * 3, c.y() - 5), QPointF(c.x() + s * 9, c.y()),
                                     QPointF(c.x() + s * 3, c.y() + 5)]))

    def _pintar_marca(self, p, r):
        pt = QPointF(r.x() + self.marca.x() * r.width(), r.y() + self.marca.y() * r.height())
        p.setBrush(Qt.NoBrush)
        for cor, larg in ((QColor(0, 0, 0, 200), 3.5), (QColor(tema.ACENTO2), 1.5)):
            p.setPen(QPen(cor, larg))
            p.drawEllipse(pt, 8, 8)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p.drawLine(pt + QPointF(dx * 4, dy * 4), pt + QPointF(dx * 14, dy * 14))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._pintar_fundo(p)
        if self._pix is None:
            p.end()
            return
        r = self._retangulo_imagem()
        # plaquinhas dentro da imagem quando ela cabe; senão, no canto do visor
        esq, dir_, topo = max(12.0, r.left() + 12), min(self.width() - 12.0, r.right() - 12), max(12.0, r.top() + 12)
        ver_antes = self.modo == "alternar" and self.mostrar_antes and self._antes_pix is not None
        self._desenhar_pix(p, self._antes_pix if ver_antes else self._pix, r)
        if self.modo == "deslizar" and self._antes_pix is not None:
            x = self.width() * self._linha
            self._desenhar_pix(p, self._antes_pix, r, QRectF(r.x(), r.y(), max(0.0, x - r.x()), r.height()))
            self._pintar_linha(p, x, r)
            _etiqueta(p, tr("ANTES"), esq, topo)
            _etiqueta(p, tr("DEPOIS"), dir_, topo, direita=True)
        elif self.modo == "alternar":
            _etiqueta(p, tr("ANTES") if ver_antes else tr("DEPOIS"), esq, topo, cor=tema.AMARELO if ver_antes else "white")
        else:
            _etiqueta(p, self.etiqueta, esq, topo)
            if self.marca is not None:
                self._pintar_marca(p, r)
        p.end()


# ---------------------------------------------------------------- janela
class DialogoComparar(QDialog):
    """Confere o resultado contra o original. 1/2/3 trocam o modo, F ajusta, Espaço troca
    antes/depois (alternar), Ctrl+C copia o depois, Esc fecha."""
    MODOS = ("lado", "deslizar", "alternar")

    def __init__(self, antes, depois, nome_antes="", nome_depois="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Comparar antes/depois"))
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(f"QDialog {{ background: {tema.FUNDO}; }}")
        self._antes, self._depois = antes, depois
        self._modo = "lado"
        self._sincronizando = False
        self._espaco_antes = False
        self._dicas = {
            "lado": tr("Roda: zoom nas duas · arraste para mover · duplo clique: 100%"),
            "deslizar": tr("Arraste para mover a linha · roda: zoom · botão direito arrasta a imagem"),
            "alternar": tr("Segure Espaço para trocar · roda: zoom · arraste para mover"),
        }
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(10)

        # cabeçalho: tamanhos + selo
        topo = QHBoxLayout()
        topo.setSpacing(12)
        self.tamanhos = QLabel()
        self.tamanhos.setStyleSheet("font-size: 13px;")
        self.tamanhos.setTextInteractionFlags(Qt.TextSelectableByMouse)
        topo.addWidget(self.tamanhos)
        self.selo = QLabel()
        topo.addWidget(self.selo)
        topo.addStretch()
        self.zoom = rotulo("", "info")
        self.zoom.setMinimumWidth(56)
        self.zoom.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        topo.addWidget(self.zoom)
        v.addLayout(topo)

        # visores
        self.v_antes = VisorComparar(antes, etiqueta=tr("ANTES"))
        self.v_depois = VisorComparar(depois, etiqueta=tr("DEPOIS"))
        self.unico = VisorComparar(depois, antes)
        lado = QWidget()
        hl = QHBoxLayout(lado)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        sep = QFrame()
        sep.setFixedWidth(2)
        sep.setStyleSheet(f"background: {tema.BORDA2};")
        hl.addWidget(self.v_antes, 1)
        hl.addWidget(sep)
        hl.addWidget(self.v_depois, 1)
        self.pilha = QStackedWidget()
        self.pilha.addWidget(lado)
        self.pilha.addWidget(self.unico)
        moldura = QFrame()
        moldura.setObjectName("moldura")
        moldura.setStyleSheet(f"QFrame#moldura {{ border: 1px solid {tema.BORDA}; }}")
        ml = QVBoxLayout(moldura)
        ml.setContentsMargins(1, 1, 1, 1)
        ml.addWidget(self.pilha)
        v.addWidget(moldura, 1)

        # rodapé: modos, dica, ações
        baixo = QHBoxLayout()
        baixo.setSpacing(8)
        self.modos = Segmentado([("lado", tr("Lado a lado")), ("deslizar", tr("Deslizar")),
                                 ("alternar", tr("Alternar"))], "lado")
        self.modos.setFixedWidth(330)
        self.modos.mudou.connect(self.definir_modo)
        baixo.addWidget(self.modos)
        baixo.addSpacing(6)
        self.dica = rotulo("", "dica")
        baixo.addWidget(self.dica, 1)
        self.bt_antes = QPushButton(tr("Ver antes"))
        self.bt_antes.setCheckable(True)
        self.bt_antes.toggled.connect(self._ver_antes)
        bt_ajustar = QPushButton(tr("Ajustar"))
        bt_ajustar.setIcon(tema.icone("ajustar", tema.TEXTO, 16))
        bt_ajustar.setToolTip(tr("Ajustar à tela") + " · F")
        bt_ajustar.clicked.connect(self.ajustar)
        bt_real = QPushButton("100%")
        bt_real.clicked.connect(lambda: self._visor_principal().tamanho_real())
        bt_copiar = QPushButton(tr("Copiar depois"))
        bt_copiar.setIcon(tema.icone("copiar", tema.TEXTO, 16))
        bt_copiar.setToolTip(tr("Copiar imagem (Ctrl+C)"))
        bt_copiar.clicked.connect(self.copiar)
        bt_fechar = QPushButton(tr("Fechar"))
        bt_fechar.clicked.connect(self.close)
        for b in (self.bt_antes, bt_ajustar, bt_real, bt_copiar, bt_fechar):
            baixo.addWidget(b)
        for b in self.findChildren(QPushButton):
            b.setFocusPolicy(Qt.NoFocus)  # Espaço e Enter ficam com a janela, não com os botões
            b.setAutoDefault(False)
            b.setCursor(Qt.PointingHandCursor)
        v.addLayout(baixo)
        self._toast = Toast(self)

        for a, b in ((self.v_antes, self.v_depois), (self.v_depois, self.v_antes)):
            a.vista_mudou.connect(lambda a=a, b=b: self._sincronizar(a, b))
            a.ponteiro.connect(lambda n, b=b: self._marcar(b, n))
        self.v_depois.vista_mudou.connect(self._mostrar_zoom)
        self.unico.vista_mudou.connect(self._mostrar_zoom)

        self._preencher_cabecalho(nome_antes, nome_depois)
        self.definir_modo("lado")
        tela = (parent.screen() if parent is not None else QGuiApplication.primaryScreen()).availableGeometry()
        self.resize(int(tela.width() * 0.85), int(tela.height() * 0.85))
        self.move(tela.center() - self.rect().center())

    # -- cabeçalho
    def _preencher_cabecalho(self, nome_antes, nome_depois):
        a, d = self._antes.size(), self._depois.size()

        def parte(titulo, nome, s):
            extra = f' <span style="color:{tema.APAGADO}">{html.escape(nome)}</span>' if nome else ""
            return f'<span style="color:{tema.SUAVE}">{titulo}</span> <b>{s.width()} × {s.height()}</b>{extra}'

        self.tamanhos.setText(parte(tr("Antes"), nome_antes, a) + f'<span style="color:{tema.APAGADO}">'
                              f'&nbsp;&nbsp;·&nbsp;&nbsp;</span>' + parte(tr("Depois"), nome_depois, d))
        if a == d:
            texto, cor, fundo = tr("Mesmo tamanho"), tema.VERDE, "#0c2a1f"
        elif abs((a.width() / a.height()) / (d.width() / d.height()) - 1) < 0.002:
            texto, cor, fundo = tr("Tamanho diferente · mesma proporção"), tema.AMARELO, "#2d2408"
        else:
            texto, cor, fundo = tr("Tamanho diferente · proporção diferente"), tema.VERMELHO, "#2a1215"
        self.selo.setText(texto)
        self.selo.setStyleSheet(f"background: {fundo}; color: {cor}; border: 1px solid {cor}; border-radius: 7px;"
                                f"padding: 2px 9px; font-size: 11.5px; font-weight: 700;")
        if a != d:
            self.selo.setToolTip(tr("Depois − antes: {dx:+d} × {dy:+d} px",
                                    dx=d.width() - a.width(), dy=d.height() - a.height()))

    # -- modos
    def _visor_principal(self):
        return self.v_depois if self._modo == "lado" else self.unico

    def definir_modo(self, m):
        if m not in self.MODOS:
            return
        anterior, self._modo = self._modo, m
        self.modos.definir(m)
        if m == "lado" and anterior != "lado":
            _copiar_vista(self.unico, self.v_depois)
        elif m != "lado" and anterior == "lado":
            _copiar_vista(self.v_depois, self.unico)
        self.pilha.setCurrentIndex(0 if m == "lado" else 1)
        if m == "lado":
            self._sincronizar(self.v_depois, self.v_antes)
        else:
            self.unico.modo = m
        self.bt_antes.blockSignals(True)
        self.bt_antes.setChecked(False)
        self.bt_antes.blockSignals(False)
        self.unico.mostrar_antes = False
        self.bt_antes.setVisible(m == "alternar")
        self.dica.setText(self._dicas[m])
        self.unico.atualizar_cursor()
        self.unico.update()
        self._visor_principal().setFocus()
        self._mostrar_zoom()

    def _sincronizar(self, de, para):
        if self._sincronizando or self._modo != "lado":
            return
        self._sincronizando = True
        try:
            _copiar_vista(de, para)
        finally:
            self._sincronizando = False

    def _marcar(self, visor, ponto):
        visor.marca = ponto
        visor.update()

    def _mostrar_zoom(self):
        self.zoom.setText(f"{round(self._visor_principal().escala() * 100)}%")

    def _ver_antes(self, sim):
        self.unico.mostrar_antes = sim
        self.unico.update()

    # -- ações
    def ajustar(self):
        self._visor_principal().ajustar()

    def copiar(self):
        copiar_imagem(self._depois)
        self._toast.mostrar(tr("Imagem copiada — é só colar (Ctrl+V)"), "ok")

    # -- teclado / janela
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_1, Qt.Key_2, Qt.Key_3) and not e.modifiers() & Qt.ControlModifier:
            self.definir_modo(self.MODOS[k - Qt.Key_1])
        elif k == Qt.Key_F:
            self.ajustar()
        elif k == Qt.Key_Space:
            if not e.isAutoRepeat() and self._modo == "alternar":
                self._espaco_antes = self.bt_antes.isChecked()
                self.bt_antes.setChecked(not self._espaco_antes)
        elif e.matches(QKeySequence.Copy):
            self.copiar()
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat() and self._modo == "alternar":
            self.bt_antes.setChecked(self._espaco_antes)
        else:
            super().keyReleaseEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        tema.barra_titulo_escura(self)
        self._visor_principal().setFocus()
