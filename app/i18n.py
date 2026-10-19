"""Idiomas: português do Brasil (padrão), inglês e espanhol.
Na primeira vez o programa segue o idioma do Windows; depois vale o que for escolhido em
Configurações. O texto em português é a chave: tr("Gerar") -> "Generate" / "Generar"."""
from PySide6.QtCore import QLocale

from . import config

IDIOMAS = {"pt": "Português (Brasil)", "en": "English", "es": "Español"}
_atual = "pt"


def detectar():
    nome = QLocale.system().name().lower()  # ex.: es_mx, en_us, pt_br
    for cod in ("es", "en", "pt"):
        if nome.startswith(cod):
            return cod
    return "pt"


def iniciar(so_detectar=False):
    global _atual
    escolhido = None if so_detectar else config.carregar().get("idioma")
    _atual = escolhido if escolhido in IDIOMAS else detectar()
    return _atual


def atual():
    return _atual


def definir(cod):
    config.salvar({"idioma": cod})


def tr(texto, **kw):
    if _atual != "pt":
        from .traducoes import TRADUCOES
        texto = TRADUCOES.get(_atual, {}).get(texto, texto)
    return texto.format(**kw) if kw else texto
