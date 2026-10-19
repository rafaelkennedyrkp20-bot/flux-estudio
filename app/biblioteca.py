"""Prompts salvos com nome (clicou, usou) e histórico dos últimos prompts de cada aba.
Fica tudo em prompts.json, ao lado do programa: dá para copiar para outro PC."""
import json
import os

from PySide6.QtCore import QObject, Signal

from . import config
from .i18n import tr  # noqa: F401 - os nomes padrão são traduzidos na hora de mostrar
from .motor import PROMPT_LIMPAR

MODOS = ("gerar", "editar", "mascara")


def _padrao():
    return [
        {"nome": "Limpar área", "padrao": True, "modo": "mascara", "texto": PROMPT_LIMPAR},
        {"nome": "Tirar marca d'água", "padrao": True, "modo": "mascara",
         "texto": "Remove the watermark, logo and any overlaid text inside the masked area. Restore exactly what is "
                  "behind it, continuing the surrounding background, colors and texture seamlessly."},
        {"nome": "Tirar marca d'água", "padrao": True, "modo": "editar",
         "texto": "Remove only the semi-transparent watermark and any overlaid text. Keep everything else exactly the "
                  "same: same composition, colors, lighting and details."},
        {"nome": "Fundo branco de estúdio", "padrao": True, "modo": "editar",
         "texto": "Replace the background with a clean seamless white studio background with soft shadows. Keep the "
                  "subject exactly the same."},
        {"nome": "Melhorar qualidade", "padrao": True, "modo": "editar",
         "texto": "Restore and enhance this image: sharper details, cleaner edges, less noise and compression "
                  "artifacts. Keep the content, composition and colors exactly the same."},
        {"nome": "Foto de produto", "padrao": True, "modo": "gerar",
         "texto": "Professional product photo on a minimal background, soft studio lighting, sharp focus, high detail."},
    ]


class Biblioteca(QObject):
    mudou = Signal()

    def __init__(self):
        super().__init__()
        self.salvos, self.historico = [], {m: [] for m in MODOS}
        try:
            with open(config.ARQ_PROMPTS, encoding="utf-8") as f:
                d = json.load(f)
            self.salvos = d.get("salvos", [])
            self.historico.update(d.get("historico", {}))
        except (OSError, ValueError):
            self.salvos = _padrao()
            self._gravar()

    def _gravar(self):
        tmp = config.ARQ_PROMPTS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"salvos": self.salvos, "historico": self.historico}, f, indent=2, ensure_ascii=False)
        os.replace(tmp, config.ARQ_PROMPTS)
        self.mudou.emit()

    def para_modo(self, modo):
        """(índice, item) dos prompts que aparecem nesta aba ("todos" aparece em todas)."""
        return [(i, p) for i, p in enumerate(self.salvos) if p.get("modo") in (modo, "todos")]

    def adicionar(self, nome, texto, modo):
        self.salvos.append({"nome": nome.strip(), "texto": texto.strip(), "modo": modo})
        self._gravar()

    def atualizar(self, i, **campos):
        if "nome" in campos:
            self.salvos[i].pop("padrao", None)  # renomeado: o nome passa a ser do usuário
        self.salvos[i].update(campos)
        self._gravar()

    def remover(self, i):
        del self.salvos[i]
        self._gravar()

    def mover(self, i, delta):
        j = i + delta
        if 0 <= j < len(self.salvos):
            self.salvos[i], self.salvos[j] = self.salvos[j], self.salvos[i]
            self._gravar()

    def lembrar(self, modo, texto):
        texto = texto.strip()
        if not texto:
            return
        h = [t for t in self.historico.get(modo, []) if t != texto]
        self.historico[modo] = [texto] + h[:29]
        self._gravar()
