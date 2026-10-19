"""Tema preto + ícones (SVG desenhados aqui, coloridos na hora)."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

FUNDO = "#000000"
PAINEL = "#0a0a0b"
CARTAO = "#111113"
CARTAO2 = "#18181b"
BORDA = "#1f1f23"
BORDA2 = "#2a2a30"
TEXTO = "#f4f4f5"
SUAVE = "#8b8b94"
APAGADO = "#55555c"
ACENTO = "#8b5cf6"
ACENTO2 = "#a78bfa"
ACENTO_ESCURO = "#6d28d9"
VERDE = "#34d399"
VERMELHO = "#f87171"
AMARELO = "#fbbf24"

def _seta():
    """Seta do QComboBox: o QSS só aceita arquivo, então grava um SVG pequeno uma vez."""
    import os
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "fluxestudio_seta.svg")
    if not os.path.isfile(p):
        with open(p, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#8b8b94" '
                    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    return p.replace("\\", "/")


SETA = _seta()

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; color: {TEXTO}; outline: 0; }}
QMainWindow, QWidget#raiz {{ background: {FUNDO}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {CARTAO2}; color: {TEXTO}; border: 1px solid {BORDA2}; border-radius: 6px; padding: 6px 8px; }}

#barraLateral {{ background: {PAINEL}; border-right: 1px solid {BORDA}; }}
#logo {{ font-size: 17px; font-weight: 700; letter-spacing: 0.3px; }}
#logoSub {{ color: {APAGADO}; font-size: 11px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; background: transparent; border-radius: 10px; color: {SUAVE};
    font-size: 13.5px; font-weight: 500; }}
QPushButton#nav:hover {{ background: {CARTAO}; color: {TEXTO}; }}
QPushButton#nav:checked {{ background: {CARTAO2}; color: {TEXTO}; }}

#cartao {{ background: {PAINEL}; border: 1px solid {BORDA}; border-radius: 14px; }}
#titulo {{ font-size: 20px; font-weight: 700; }}
#subtitulo {{ color: {SUAVE}; }}
#rotulo {{ color: {SUAVE}; font-size: 12px; font-weight: 600; }}
#valor {{ color: {TEXTO}; font-size: 12px; font-weight: 600; }}
#dica {{ color: {APAGADO}; font-size: 12px; }}
#info {{ color: {SUAVE}; font-size: 12px; }}

QPlainTextEdit, QLineEdit, QSpinBox {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: 10px;
    padding: 8px 10px; selection-background-color: {ACENTO_ESCURO}; }}
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {ACENTO}; }}
QPlainTextEdit {{ font-size: 13.5px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: {CARTAO2}; border: 1px solid {BORDA2}; border-radius: 9px; padding: 7px 12px; font-weight: 500; }}
QPushButton:hover {{ background: #222227; border-color: #3a3a42; }}
QPushButton:pressed {{ background: #2a2a30; }}
QPushButton:disabled {{ color: {APAGADO}; background: {CARTAO}; border-color: {BORDA}; }}
QPushButton:checked {{ background: {ACENTO_ESCURO}; border-color: {ACENTO}; color: white; }}
QPushButton#principal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {ACENTO}, stop:1 {ACENTO_ESCURO});
    border: none; border-radius: 12px; padding: 12px; font-size: 14px; font-weight: 700; color: white; }}
QPushButton#principal:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {ACENTO2}, stop:1 {ACENTO}); }}
QPushButton#principal:disabled {{ background: {CARTAO2}; color: {APAGADO}; }}
QPushButton#perigo {{ background: #2a1215; border: 1px solid #5b1d24; color: {VERMELHO}; border-radius: 12px; padding: 12px;
    font-size: 14px; font-weight: 700; }}
QPushButton#perigo:hover {{ background: #3a161b; }}
QPushButton#chip {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: 8px; padding: 5px 0; font-size: 12px; color: {SUAVE}; }}
QPushButton#chip:hover {{ color: {TEXTO}; border-color: {BORDA2}; }}
QPushButton#chip:checked {{ background: {CARTAO2}; border-color: {ACENTO}; color: {TEXTO}; }}
QPushButton#icone {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px; }}
QPushButton#icone:hover {{ background: {CARTAO2}; border-color: {BORDA2}; }}
QPushButton#icone:checked {{ background: {CARTAO2}; border-color: {ACENTO}; }}
QPushButton#ferramenta {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: 9px; padding: 6px 10px; }}
QPushButton#ferramenta:hover {{ border-color: {BORDA2}; background: {CARTAO2}; }}
QPushButton#ferramenta:checked {{ background: {CARTAO2}; border-color: {ACENTO}; }}

QPushButton#chipPrompt {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: 13px; padding: 4px 11px;
    font-size: 12px; color: {SUAVE}; }}
QPushButton#chipPrompt:hover {{ color: {TEXTO}; border-color: {ACENTO}; background: #15121f; }}
QPushButton#chipPrompt:pressed {{ background: {ACENTO_ESCURO}; color: white; }}

QComboBox {{ background: {CARTAO}; border: 1px solid {BORDA}; border-radius: 10px; padding: 8px 10px; }}
QComboBox:hover {{ border-color: {BORDA2}; }}
QComboBox:focus {{ border-color: {ACENTO}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({SETA}); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {CARTAO}; border: 1px solid {BORDA2}; border-radius: 8px; padding: 4px;
    selection-background-color: {CARTAO2}; outline: 0; }}

#etiqueta {{ background: {CARTAO2}; border: 1px solid {BORDA2}; border-radius: 7px; padding: 2px 8px; font-size: 11px;
    color: {SUAVE}; font-weight: 600; }}
#etiquetaAcento {{ background: #1d1533; border: 1px solid {ACENTO_ESCURO}; border-radius: 7px; padding: 2px 8px;
    font-size: 11px; color: {ACENTO2}; font-weight: 700; }}

QPushButton#secao {{ background: transparent; border: none; border-top: 1px solid {BORDA}; border-radius: 0;
    text-align: left; padding: 12px 0 2px 0; color: {SUAVE}; font-weight: 600; font-size: 12.5px; }}
QPushButton#secao:hover {{ color: {TEXTO}; }}
QPushButton#secao:checked {{ background: transparent; color: {TEXTO}; }}

QCheckBox {{ spacing: 8px; color: {SUAVE}; }}
QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px; background: {CARTAO2}; border: 1px solid {BORDA2}; }}
QCheckBox::indicator:checked {{ background: {ACENTO}; border-color: {ACENTO}; }}

QSlider::groove:horizontal {{ height: 4px; background: {CARTAO2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACENTO}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACENTO2}; }}

QProgressBar {{ background: {CARTAO2}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACENTO_ESCURO}, stop:1 {ACENTO2}); border-radius: 2px; }}
QProgressBar#grossa {{ max-height: 8px; min-height: 8px; border-radius: 4px; }}
QProgressBar#grossa::chunk {{ border-radius: 4px; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDA2}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a3a42; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDA2}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ border-radius: 10px; padding: 3px; }}
QListWidget::item:hover {{ background: {CARTAO2}; }}
QListWidget::item:selected {{ background: {CARTAO2}; border: 1px solid {ACENTO}; }}

QMenu {{ background: {CARTAO}; border: 1px solid {BORDA2}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 18px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARTAO2}; }}
QMenu::separator {{ height: 1px; background: {BORDA}; margin: 4px 6px; }}
QMessageBox {{ background: {PAINEL}; }}
QFileDialog {{ background: {PAINEL}; }}
"""

# Ícones 24x24, traço. "{c}" vira a cor.
_SVG = {
    "gerar": '<path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "editar": '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "mascara": '<path d="M9.5 14.5c-2 0-3.5 1.5-3.5 3.5 0 1-1 2-2 2 1 1 2.5 1.5 4 1.5 2.5 0 4.5-2 4.5-4.5 0-1.4-1.2-2.5-3-2.5z"/><path d="M20.5 3.5L11 13l-1.5-1.5L19 2z"/>',
    "galeria": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
    "modelo": '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 22V12M21 7l-9 5-9-5"/>',
    "colar": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    "copiar": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "abrir": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "salvar": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
    "dado": '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.2"/><circle cx="16" cy="16" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="16" cy="8" r="1.2"/><circle cx="8" cy="16" r="1.2"/>',
    "historico": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    "pincel": '<path d="M18.4 2.6a2 2 0 0 1 2.9 2.9L11 15.8 8.2 13z"/><path d="M7 14c-1.7 0-3 1.3-3 3 0 1.2-.5 2.3-2 3 1 .7 2.5 1 4 1 2.2 0 4-1.8 4-4 0-1.7-1.3-3-3-3z"/>',
    "borracha": '<path d="M20 20H9L4 15a2 2 0 0 1 0-2.8L13.2 3a2 2 0 0 1 2.8 0L21 8a2 2 0 0 1 0 2.8L12 20"/><path d="M6.5 10.5l7 7"/>',
    "limpar": '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M6 6l1 14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-14"/>',
    "desfazer": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "inverter": '<circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="{c}"/>',
    "comparar": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 2v20"/>',
    "continuar": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "ajustar": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "mais": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "chip": '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "lixo": '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    "parar": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
}


def icone(nome, cor=TEXTO, tamanho=20):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{cor}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_SVG[nome].replace("{c}", cor)}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for escala in (1, 2):
        pm = QPixmap(tamanho * escala, tamanho * escala)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, tamanho * escala, tamanho * escala))
        p.end()
        pm.setDevicePixelRatio(escala)
        ic.addPixmap(pm)
    return ic


def aplicar(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for papel, cor in [(QPalette.Window, FUNDO), (QPalette.Base, CARTAO), (QPalette.AlternateBase, CARTAO2),
                       (QPalette.Text, TEXTO), (QPalette.WindowText, TEXTO), (QPalette.Button, CARTAO2),
                       (QPalette.ButtonText, TEXTO), (QPalette.Highlight, ACENTO_ESCURO),
                       (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, CARTAO2),
                       (QPalette.ToolTipText, TEXTO), (QPalette.PlaceholderText, APAGADO)]:
        pal.setColor(papel, QColor(cor))
    app.setPalette(pal)
    f = QFont("Segoe UI Variable Text")
    f.setPointSizeF(9.75)
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    app.setStyleSheet(QSS)


def barra_titulo_escura(janela):
    """Barra de título preta no Windows 10/11."""
    try:
        import ctypes
        hwnd = int(janela.winId())
        dwm = ctypes.windll.dwmapi
        um = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(um), 4)          # modo escuro
        preto = ctypes.c_int(0x00000000)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(preto), 4)       # cor da barra (Win 11)
        borda = ctypes.c_int(0x00231F1F)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(borda), 4)       # cor da borda (Win 11)
    except Exception:
        pass
