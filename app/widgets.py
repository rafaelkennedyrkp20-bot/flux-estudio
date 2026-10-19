"""Componentes da interface: visualizador com zoom/comparação, tela de máscara, área de
transferência (colar do Photoshop/navegador/Explorer, copiar de volta), chips, toasts."""
import os

from PIL import Image
from PySide6.QtCore import (QBuffer, QByteArray, QEasingCurve, QIODevice, QMimeData, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage, QImageReader, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from . import tema
from .i18n import tr

EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")


# ---------------------------------------------------------------- área de transferência
def imagem_do_mime(md):
    """QImage de um QMimeData: PNG bruto (Photoshop, navegadores: mantém transparência),
    bitmap do Windows (DIB), ou arquivo de imagem copiado no Explorer. None se não houver."""
    if md is None:
        return None
    for fmt in md.formats():
        f = fmt.lower()
        if f == "image/png" or f.endswith('value="png"') or f.endswith("value=png"):
            img = QImage.fromData(md.data(fmt), "PNG")
            if not img.isNull():
                return img
    if md.hasImage():
        img = QImage(md.imageData())
        if not img.isNull():
            return img
    if md.hasUrls():
        for u in md.urls():
            p = u.toLocalFile()
            if p and p.lower().endswith(EXT_IMAGEM) and os.path.isfile(p):
                img = carregar_qimage(p)
                if not img.isNull():
                    return img
    return None


def caminhos_do_mime(md):
    if md is None or not md.hasUrls():
        return []
    return [u.toLocalFile() for u in md.urls()
            if u.toLocalFile().lower().endswith(EXT_IMAGEM) and os.path.isfile(u.toLocalFile())]


def imagens_do_mime(md):
    """Várias imagens (arrastar vários arquivos) ou uma (colar)."""
    ps = caminhos_do_mime(md)
    if ps:
        return [i for i in (carregar_qimage(p) for p in ps) if not i.isNull()]
    img = imagem_do_mime(md)
    return [img] if img is not None else []


def area_tem_imagem():
    md = QGuiApplication.clipboard().mimeData()
    return md is not None and (md.hasImage() or bool(caminhos_do_mime(md))
                               or any(f.lower() == "image/png" or 'png' in f.lower() for f in md.formats()))


def copiar_imagem(img):
    """Copia como bitmap + PNG: cola no Photoshop, navegador, WhatsApp, Discord, Word…"""
    md = QMimeData()
    md.setImageData(img)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    dados = QByteArray(buf.data())
    md.setData("PNG", dados)
    md.setData("image/png", dados)
    QGuiApplication.clipboard().setMimeData(md)


def carregar_qimage(caminho):
    r = QImageReader(caminho)
    r.setAutoTransform(True)
    return r.read()


def qimage_para_pil(img):
    """QImage -> PIL RGB. Transparência vira fundo branco; alfa todo zerado (bug comum de DIB) é ignorado."""
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    pil = Image.frombuffer("RGBA", (w, h), bytes(img.constBits()), "raw", "RGBA", img.bytesPerLine(), 1).copy()
    alfa = pil.getchannel("A")
    lo, hi = alfa.getextrema()
    if hi == 0 or lo == 255:
        return pil.convert("RGB")
    fundo = Image.new("RGB", pil.size, (255, 255, 255))
    fundo.paste(pil, mask=alfa)
    return fundo


def miniatura(img, lado=160):
    return QPixmap.fromImage(img.scaled(lado, lado, Qt.KeepAspectRatio, Qt.SmoothTransformation))


# ---------------------------------------------------------------- visualizador base (zoom/pan suaves)
class VisorBase(QWidget):
    vista_mudou = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img = None
        self._pix = None
        self._escala = 1.0
        self._desl = QPointF(0, 0)
        self._auto = True
        self._pan = None
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._passo_zoom)
        self._ancora = QPointF()
        self._de = self._para = 1.0
        self.texto_vazio = ""

    def tem_imagem(self):
        return self._pix is not None

    def imagem(self):
        return self._img

    def definir_imagem(self, img, manter_vista=False):
        mesmo_tamanho = self._img is not None and img is not None and img.size() == self._img.size()
        self._img = img
        self._pix = QPixmap.fromImage(img) if img is not None and not img.isNull() else None
        if not (manter_vista and mesmo_tamanho):
            self.ajustar()
        self.update()

    def ajustar(self):
        self._auto = True
        if self._pix is None:
            return
        pw, ph = self._pix.width(), self._pix.height()
        m = 24
        s = min((self.width() - 2 * m) / pw, (self.height() - 2 * m) / ph)
        self._escala = max(0.01, s)
        self._desl = QPointF((self.width() - pw * self._escala) / 2, (self.height() - ph * self._escala) / 2)
        self.update()
        self.vista_mudou.emit()

    def tamanho_real(self, ponto=None):
        self._zoom_para(1.0, ponto or QPointF(self.width() / 2, self.height() / 2))

    def para_tela(self, p):
        return self._desl + p * self._escala

    def para_imagem(self, p):
        return (QPointF(p) - self._desl) / self._escala

    def _zoom_para(self, alvo, ancora):
        self._auto = False
        self._de, self._para = self._escala, max(0.02, min(40.0, alvo))
        self._ancora = QPointF(ancora)
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _passo_zoom(self, t):
        nova = self._de + (self._para - self._de) * t
        img_pt = self.para_imagem(self._ancora)
        self._escala = nova
        self._desl = self._ancora - img_pt * nova
        self.update()
        self.vista_mudou.emit()

    def wheelEvent(self, e):
        if self._pix is None:
            return
        passos = e.angleDelta().y() / 120
        base = self._para if self._anim.state() == QVariantAnimation.Running else self._escala
        self._zoom_para(base * (1.2 ** passos), e.position())

    def resizeEvent(self, e):
        if self._auto:
            self.ajustar()
        super().resizeEvent(e)

    def _comeca_pan(self, e):
        self._pan = (e.position(), QPointF(self._desl))
        self.setCursor(Qt.ClosedHandCursor)

    def _move_pan(self, e):
        if self._pan is None:
            return False
        ini, desl = self._pan
        self._desl = desl + (e.position() - ini)
        self._auto = False
        self.update()
        self.vista_mudou.emit()
        return True

    def _fim_pan(self):
        self._pan = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        if self._pix is None:
            return
        if self._auto:
            self.tamanho_real(e.position())
        else:
            self.ajustar()

    def _retangulo_imagem(self):
        return QRectF(self._desl.x(), self._desl.y(), self._pix.width() * self._escala, self._pix.height() * self._escala)

    def _pintar_fundo(self, p):
        p.fillRect(self.rect(), QColor(tema.FUNDO))

    def _pintar_vazio(self, p):
        if not self.texto_vazio:
            return
        p.setPen(QColor(tema.APAGADO))
        f = p.font()
        f.setPointSizeF(10.5)
        p.setFont(f)
        r = self.rect().adjusted(40, 0, -40, 0)
        caixa = QRectF(self.width() / 2 - 170, self.height() / 2 - 80, 340, 160)
        pen = QPen(QColor(tema.BORDA2), 1.4, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(caixa, 16, 16)
        p.setPen(QColor(tema.SUAVE))
        p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self.texto_vazio)

    def _desenhar_pix(self, p, pix, alvo, recorte=None):
        p.setRenderHint(QPainter.SmoothPixmapTransform, self._escala < 2.5)
        if recorte is not None:
            p.save()
            p.setClipRect(recorte)
        p.drawPixmap(alvo, pix, QRectF(pix.rect()))
        if recorte is not None:
            p.restore()


# ---------------------------------------------------------------- visualizador de resultados
class Visor(VisorBase):
    """Mostra a imagem com transição suave. Com `comparar` ligado, passa o mouse para ver o antes
    (à esquerda da linha) e o depois (à direita)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._antes = None
        self._antes_pix = None
        self.comparar = False
        self._linha = 0.5
        self._pix_velho = None
        self._fade = 1.0
        self._anim_fade = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.OutCubic,
                                            startValue=0.0, endValue=1.0)
        self._anim_fade.valueChanged.connect(self._ao_fade)
        self.ocupado = False
        self._giro = 0
        self._timer_giro = QTimer(self, interval=16)
        self._timer_giro.timeout.connect(self._girar)

    def _ao_fade(self, v):
        self._fade = v
        self.update()

    def _girar(self):
        self._giro = (self._giro + 6) % 360
        self.update()

    def definir_ocupado(self, sim):
        self.ocupado = sim
        self._timer_giro.start() if sim else self._timer_giro.stop()
        self.update()

    def definir_imagem(self, img, manter_vista=False, animar=True):
        velho = self._pix
        super().definir_imagem(img, manter_vista)
        if animar and velho is not None and self._pix is not None:
            self._pix_velho = velho
            self._anim_fade.stop()
            self._anim_fade.start()
        else:
            self._fade = 1.0

    def definir_antes(self, img):
        self._antes = img
        self._antes_pix = QPixmap.fromImage(img) if img is not None else None
        self.update()

    def pode_comparar(self):
        return self._antes_pix is not None and self._pix is not None and self._antes_pix.cacheKey() != self._pix.cacheKey()

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton) and self._pix is not None:
            if self.comparar and e.button() == Qt.LeftButton:
                return
            self._comeca_pan(e)

    def mouseMoveEvent(self, e):
        if self._move_pan(e):
            return
        if self.comparar and self._pix is not None:
            r = self._retangulo_imagem()
            self._linha = min(1.0, max(0.0, (e.position().x() - r.x()) / max(1.0, r.width())))
            self.update()

    def mouseReleaseEvent(self, e):
        self._fim_pan()

    def leaveEvent(self, e):
        if self.comparar:
            self._linha = 0.5
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._pintar_fundo(p)
        if self._pix is None:
            if not self.ocupado:
                self._pintar_vazio(p)
        else:
            r = self._retangulo_imagem()
            if self._fade < 1.0 and self._pix_velho is not None:
                pv = self._pix_velho
                f = min(r.width() / pv.width(), r.height() / pv.height())
                rv = QRectF(0, 0, pv.width() * f, pv.height() * f)
                rv.moveCenter(r.center())
                p.setOpacity(1.0 - self._fade)
                self._desenhar_pix(p, pv, rv)
                p.setOpacity(self._fade)
            self._desenhar_pix(p, self._pix, r)
            p.setOpacity(1.0)
            if self.comparar and self.pode_comparar():
                x = r.x() + r.width() * self._linha
                corte = QRectF(r.x(), r.y(), x - r.x(), r.height())
                self._desenhar_pix(p, self._antes_pix, r, corte)
                p.setPen(QPen(QColor(255, 255, 255, 220), 2))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                for texto, alinhar, cx in ((tr("ANTES"), Qt.AlignLeft, r.x() + 10), (tr("DEPOIS"), Qt.AlignRight, r.right() - 90)):
                    caixa = QRectF(cx, r.y() + 10, 80, 22)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(0, 0, 0, 170))
                    p.drawRoundedRect(caixa, 6, 6)
                    p.setPen(QColor("white"))
                    p.drawText(caixa, Qt.AlignCenter, texto)
        if self.ocupado:
            c = QPointF(self.width() / 2, self.height() / 2)
            if self._pix is not None:
                p.fillRect(self.rect(), QColor(0, 0, 0, 120))
            p.setPen(QPen(QColor(tema.BORDA2), 4))
            p.drawEllipse(c, 26, 26)
            pen = QPen(QColor(tema.ACENTO2), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(c.x() - 26, c.y() - 26, 52, 52), -self._giro * 16, 100 * 16)
        p.end()


# ---------------------------------------------------------------- tela de máscara
class TelaMascara(VisorBase):
    """Pinte a área a refazer. Botão esquerdo pinta, direito/meio arrasta, roda dá zoom,
    Alt+roda ou [ ] muda o pincel, X troca pincel/borracha, Ctrl+Z desfaz."""
    mudou = Signal()
    pincel_mudou = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mascara = None
        self.borracha = False
        self.raio = 30
        self._ultimo = None
        self._desfazer = []
        self._mouse = None
        self.setCursor(Qt.BlankCursor)

    def definir_imagem(self, img, manter_vista=False):
        super().definir_imagem(img, manter_vista)
        if img is not None:
            self._mascara = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
            self._mascara.fill(Qt.transparent)
            self.raio = max(8, int(max(img.width(), img.height()) / 45))
            self.pincel_mudou.emit(self.raio)
        else:
            self._mascara = None
        self._desfazer.clear()
        self.mudou.emit()

    def tem_mascara(self):
        if self._mascara is None:
            return False
        a = self._mascara.convertToFormat(QImage.Format_Alpha8)
        return any(bytes(a.constBits()))

    def mascara_pil(self):
        a = self._mascara.convertToFormat(QImage.Format_Alpha8)
        return Image.frombuffer("L", (a.width(), a.height()), bytes(a.constBits()), "raw", "L", a.bytesPerLine(), 1).copy()

    def _guardar(self):
        self._desfazer.append(self._mascara.copy())
        del self._desfazer[:-40]

    def desfazer(self):
        if self._desfazer:
            self._mascara = self._desfazer.pop()
            self.update()
            self.mudou.emit()

    def limpar(self):
        if self._mascara is not None:
            self._guardar()
            self._mascara.fill(Qt.transparent)
            self.update()
            self.mudou.emit()

    def inverter(self):
        if self._mascara is None:
            return
        self._guardar()
        nova = QImage(self._mascara.size(), QImage.Format_ARGB32_Premultiplied)
        nova.fill(QColor(tema.ACENTO))
        p = QPainter(nova)
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        p.drawImage(0, 0, self._mascara)
        p.end()
        self._mascara = nova
        self.update()
        self.mudou.emit()

    def definir_raio(self, r):
        self.raio = max(2, min(800, int(r)))
        self.pincel_mudou.emit(self.raio)
        self.update()

    def _pintar(self, a, b):
        p = QPainter(self._mascara)
        p.setRenderHint(QPainter.Antialiasing)
        if self.borracha:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
        pen = QPen(QColor(tema.ACENTO), self.raio * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        if a == b:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tema.ACENTO))
            p.drawEllipse(a, self.raio, self.raio)
        else:
            p.drawLine(a, b)
        p.end()
        self.update()

    def mousePressEvent(self, e):
        if self._pix is None:
            return
        if e.button() == Qt.LeftButton and not (e.modifiers() & Qt.ControlModifier):
            self._guardar()
            pt = self.para_imagem(e.position())
            self._ultimo = pt
            self._pintar(pt, pt)
        else:
            self._comeca_pan(e)

    def mouseMoveEvent(self, e):
        self._mouse = e.position()
        if self._move_pan(e):
            return
        if self._ultimo is not None:
            pt = self.para_imagem(e.position())
            self._pintar(self._ultimo, pt)
            self._ultimo = pt
        self.update()

    def mouseReleaseEvent(self, e):
        if self._ultimo is not None:
            self._ultimo = None
            self.mudou.emit()
        self._fim_pan()
        self.setCursor(Qt.BlankCursor)

    def leaveEvent(self, e):
        self._mouse = None
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.AltModifier:
            d = e.angleDelta().x() or e.angleDelta().y()
            self.definir_raio(self.raio * (1.15 if d > 0 else 1 / 1.15))
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_BracketRight:
            self.definir_raio(self.raio * 1.2)
        elif k == Qt.Key_BracketLeft:
            self.definir_raio(self.raio / 1.2)
        elif k == Qt.Key_X:
            self.borracha = not self.borracha
            self.mudou.emit()
            self.update()
        elif k == Qt.Key_Z and e.modifiers() & Qt.ControlModifier:
            self.desfazer()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._pintar_fundo(p)
        if self._pix is None:
            self._pintar_vazio(p)
            p.end()
            return
        r = self._retangulo_imagem()
        self._desenhar_pix(p, self._pix, r)
        p.setOpacity(0.55)
        p.drawImage(r, self._mascara, QRectF(self._mascara.rect()))
        p.setOpacity(1.0)
        if self._mouse is not None and self._pan is None:
            rr = self.raio * self._escala
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 180), 3))
            p.drawEllipse(self._mouse, rr, rr)
            p.setPen(QPen(QColor(tema.VERMELHO if self.borracha else "white"), 1.4))
            p.drawEllipse(self._mouse, rr, rr)
            p.drawPoint(self._mouse)
        p.end()


# ---------------------------------------------------------------- layout que quebra linha (chips)
class FluxoLayout(QLayout):
    def __init__(self, parent=None, espaco=6):
        super().__init__(parent)
        self._itens = []
        self._esp = espaco
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._itens.append(item)

    def count(self):
        return len(self._itens)

    def itemAt(self, i):
        return self._itens[i] if 0 <= i < len(self._itens) else None

    def takeAt(self, i):
        return self._itens.pop(i) if 0 <= i < len(self._itens) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._arrumar(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._arrumar(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._itens:
            s = s.expandedTo(it.minimumSize())
        return s

    def _arrumar(self, r, so_medir):
        x, y, alt = r.x(), r.y(), 0
        for it in self._itens:
            h = it.sizeHint()
            if x + h.width() > r.right() + 1 and alt > 0:
                x, y, alt = r.x(), y + alt + self._esp, 0
            if not so_medir:
                it.setGeometry(QRect(QPoint(x, y), h))
            x += h.width() + self._esp
            alt = max(alt, h.height())
        return y + alt - r.y()


# ---------------------------------------------------------------- pequenos blocos
def rotulo(texto, nome="rotulo"):
    l = QLabel(texto)
    l.setObjectName(nome)
    return l


def botao_icone(nome_icone, dica, ao_clicar=None, checavel=False, tamanho=18):
    b = QPushButton()
    b.setObjectName("icone")
    b.setIcon(tema.icone(nome_icone, tema.TEXTO, tamanho))
    b.setIconSize(QSize(tamanho, tamanho))
    b.setToolTip(dica)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checavel)
    if ao_clicar:
        b.clicked.connect(ao_clicar)
    return b


class LinhaSlider(QWidget):
    """Rótulo + valor + controle deslizante."""
    mudou = Signal(int)

    def __init__(self, titulo, minimo, maximo, valor, passo=1, formato=str, dica=""):
        super().__init__()
        self._fmt = formato
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        topo = QHBoxLayout()
        topo.addWidget(rotulo(titulo))
        topo.addStretch()
        self._valor = rotulo("", "valor")
        topo.addWidget(self._valor)
        v.addLayout(topo)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimo, maximo)
        self.slider.setSingleStep(passo)
        self.slider.setPageStep(passo)
        self.slider.setValue(valor)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.valueChanged.connect(self._ao_mudar)
        v.addWidget(self.slider)
        if dica:
            self.setToolTip(dica)
        self._ao_mudar(valor)

    def _ao_mudar(self, v):
        self._valor.setText(self._fmt(v))
        self.mudou.emit(v)

    def valor(self):
        return self.slider.value()

    def definir(self, v):
        self.slider.setValue(v)

    def definir_faixa(self, a, b):
        self.slider.setRange(a, b)


class Segmentado(QWidget):
    """Botões exclusivos lado a lado (ex.: 1 2 3 4)."""
    mudou = Signal(object)

    def __init__(self, opcoes, atual=None):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self._botoes = {}
        for valor, texto in opcoes:
            b = QPushButton(texto)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=valor: self.definir(v, True))
            h.addWidget(b)
            self._botoes[valor] = b
        self._valor = None
        self.definir(atual if atual is not None else opcoes[0][0])

    def definir(self, v, avisar=False):
        if v not in self._botoes:
            return
        self._valor = v
        for k, b in self._botoes.items():
            b.setChecked(k == v)
        if avisar:
            self.mudou.emit(v)

    def valor(self):
        return self._valor


class Recolhivel(QWidget):
    """Seção que abre/fecha com animação (ex.: "Ajustes avançados")."""
    alternou = Signal(bool)

    def __init__(self, titulo, aberto=False):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        self._titulo = titulo
        self.bt = QPushButton()
        self.bt.setObjectName("secao")
        self.bt.setCheckable(True)
        self.bt.setCursor(Qt.PointingHandCursor)
        self.bt.clicked.connect(lambda c: self.abrir(c, True))
        v.addWidget(self.bt)
        self.corpo = QWidget()
        self.lay = QVBoxLayout(self.corpo)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(16)
        v.addWidget(self.corpo)
        self._anim = QPropertyAnimation(self.corpo, b"maximumHeight", self, duration=200,
                                        easingCurve=QEasingCurve.OutCubic)
        self._anim.finished.connect(self._fim)
        self.abrir(aberto, False)

    def aberto(self):
        return self.bt.isChecked()

    def abrir(self, sim, animar=True):
        self.bt.setChecked(sim)
        self.bt.setText(("▾  " if sim else "▸  ") + self._titulo)
        self._anim.stop()
        if not animar:
            self.corpo.setVisible(sim)
            self.corpo.setMaximumHeight(16777215)
            return
        alvo = self.corpo.sizeHint().height()
        self.corpo.setVisible(True)
        self._anim.setStartValue(0 if sim else self.corpo.height())
        self._anim.setEndValue(alvo if sim else 0)
        self._anim.start()
        self.alternou.emit(sim)

    def _fim(self):
        if self.bt.isChecked():
            self.corpo.setMaximumHeight(16777215)
        else:
            self.corpo.setVisible(False)


class Toast(QLabel):
    """Aviso que aparece e some sozinho, embaixo no centro da janela."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet(f"background:{tema.CARTAO2}; border:1px solid {tema.BORDA2}; border-radius:12px;"
                           f"padding:10px 18px; font-weight:600;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._ef = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._ef)
        self._anim = QPropertyAnimation(self._ef, b"opacity", self, duration=180)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._sumir)
        self.hide()

    def mostrar(self, texto, tipo="info", ms=2600):
        cor = {"ok": tema.VERDE, "erro": tema.VERMELHO, "aviso": tema.AMARELO}.get(tipo, tema.TEXTO)
        self.setStyleSheet(self.styleSheet().split("color:")[0] + f"color:{cor};")
        self.setText(texto)
        self.setWordWrap(len(texto) > 90)
        self.setMaximumWidth(620)
        self.adjustSize()
        pai = self.parentWidget()
        self.move((pai.width() - self.width()) // 2, pai.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setStartValue(self._ef.opacity() if self.isVisible() else 0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _sumir(self):
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._esconder)
        self._anim.start()

    def _esconder(self):
        try:
            self._anim.finished.disconnect(self._esconder)
        except (RuntimeError, TypeError):
            pass
        if self._ef.opacity() < 0.05:
            self.hide()


class TiraMiniaturas(QListWidget):
    """Fila horizontal de miniaturas. Emite o índice escolhido."""
    escolhido = Signal(int)

    def __init__(self, lado=84):
        super().__init__()
        self.setFlow(QListWidget.LeftToRight)
        self.setViewMode(QListWidget.IconMode)
        self.setWrapping(False)
        self.setMovement(QListWidget.Static)
        self.setIconSize(QSize(lado, lado))
        self.setFixedHeight(lado + 26)
        self.setSpacing(4)
        self.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.currentRowChanged.connect(lambda r: r >= 0 and self.escolhido.emit(r))
        self._lado = lado

    def adicionar(self, img, dica=""):
        it = QListWidgetItem()
        it.setIcon(miniatura(img, self._lado * 2))
        it.setToolTip(dica)
        it.setSizeHint(QSize(self._lado + 8, self._lado + 8))
        self.addItem(it)
        return it
