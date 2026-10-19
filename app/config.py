"""Caminhos e configuração. Funciona tanto rodando o .py quanto compilado (PyInstaller):
compilado, tudo (modelos/, saidas/, config.json, prompts.json) fica ao lado do .exe, nunca na
pasta temporária."""
import json
import os
import sys

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASTA_MODELOS = os.path.join(BASE, "modelos")
SAIDAS = os.path.join(BASE, "saidas")
ARQ_CONFIG = os.path.join(BASE, "config.json")
ARQ_PROMPTS = os.path.join(BASE, "prompts.json")
ARQ_PREFS = os.path.join(BASE, "preferencias.ini")
ARQ_LOG = os.path.join(BASE, "flux_estudio.log")



def garantir_pastas():
    os.makedirs(SAIDAS, exist_ok=True)


def carregar():
    try:
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salvar(dados):
    atual = carregar()
    atual.update(dados)
    with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
        json.dump(atual, f, indent=2, ensure_ascii=False)


def pasta_modelo(id_modelo):
    """Pasta do modelo: a escolhida pelo usuário (config.json) ou modelos/<id> ao lado do programa."""
    if id_modelo == "flux2-klein-4b" and os.environ.get("FLUX_MODELO"):
        return os.environ["FLUX_MODELO"]
    return (carregar().get("pastas") or {}).get(id_modelo) or os.path.join(PASTA_MODELOS, id_modelo)


def definir_pasta(id_modelo, pasta):
    pastas = carregar().get("pastas") or {}
    if pasta:
        pastas[id_modelo] = pasta
    else:
        pastas.pop(id_modelo, None)
    salvar({"pastas": pastas})


def modelo_instalado(id_modelo=None, pasta=None, excluir=()):
    """Completo = model_index.json + a pasta de cada componente, e nenhum download pela metade."""
    pasta = pasta or pasta_modelo(id_modelo)
    if id_modelo == "flux2-klein-9b":
        from . import klein9b  # só biblioteca padrão no topo; o torch só entra no abrir()
        return klein9b.instalado(pasta)
    indice = os.path.join(pasta, "model_index.json")
    if not os.path.isfile(indice):
        return False
    try:
        with open(indice, encoding="utf-8") as f:
            comps = [k for k, v in json.load(f).items()
                     if not k.startswith("_") and isinstance(v, list) and v and v[0] is not None]
    except (OSError, ValueError):
        return False
    for c in comps:
        if c in excluir:
            continue
        d = os.path.join(pasta, c)
        if not os.path.isdir(d) or not os.listdir(d):
            return False
    for _, _, arqs in os.walk(pasta):
        if any(a.endswith(".part") for a in arqs):
            return False
    return True


def tamanho_pasta(pasta):
    total = 0
    for raiz, _, arqs in os.walk(pasta):
        for a in arqs:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
    return total
