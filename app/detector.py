"""Detector de texto e balões de quadrinhos: acha sozinho o que a borracha deve apagar.

- Modelo: ogkalu/comic-text-and-bubble-detector (RT-DETR-v2, Apache-2.0), versão ONNX int8
  (~42 MB), baixada para modelos/detector-texto/. Roda na CPU com onnxruntime.
- Classes: bubble (balão inteiro), text_bubble (texto dentro de balão), text_free (texto solto).
- Entrada do modelo: 640x640 RGB em 0..1 (só redimensiona, sem normalizar), mais o tamanho
  original; ele já devolve as caixas em pixels da imagem original.
- Tiras de webtoon (muito altas ou muito largas) são cortadas em pedaços quase quadrados com
  sobreposição, como no treino; as caixas repetidas nas emendas são juntadas.

Uso sem interface:  python -m app.detector [--baixar] [imagem [saida.png]]
"""
import logging
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

from . import config

log = logging.getLogger("detector")

ID = "detector-texto"
REPO = "ogkalu/comic-text-and-bubble-detector"
NOME_ARQ = "detector_int8.onnx"
URL = f"https://huggingface.co/{REPO}/resolve/main/{NOME_ARQ}"
TAMANHO = 43_838_857
PASTA = config.pasta_modelo(ID)
ARQUIVO = os.path.join(PASTA, NOME_ARQ)

CLASSES = ("bubble", "text_bubble", "text_free")
TEXTO = ("text_bubble", "text_free")
LADO = 640          # tamanho de treino
LIMIAR = 0.35       # score mínimo
PROPORCAO_MAX = 2.0  # acima disso (lado maior / menor) corta em pedaços
PEDACO = 1.5        # cada pedaço: lado curto x 1.5 no sentido do lado maior
SOBREPOR = 0.25     # sobreposição entre pedaços


class Cancelado(Exception):
    pass


# ---------------------------------------------------------------- download

def instalado():
    return os.path.isfile(ARQUIVO) and os.path.getsize(ARQUIVO) == TAMANHO


def _baixar_arquivo(url, final, tamanho, ao_progresso=None, cancelado=None):
    """Baixa `url` para `final` via `final.part`, continuando de onde parou (Range).
    `ao_progresso(feito, total, velocidade_bps, arquivo)`; `cancelado()` -> True interrompe."""
    import requests
    ao_progresso = ao_progresso or (lambda *a: None)
    cancelado = cancelado or (lambda: False)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    parte = final + ".part"
    nome = os.path.basename(final)
    cab = {"User-Agent": "FluxEstudio/1.0"}
    token = os.environ.get("HF_TOKEN") or config.carregar().get("hf_token")
    if token:
        cab["Authorization"] = f"Bearer {token}"
    for tentativa in range(6):
        feito = os.path.getsize(parte) if os.path.isfile(parte) else 0
        if feito > tamanho:  # .part estranho: recomeça
            os.remove(parte)
            feito = 0
        if feito == tamanho:
            break
        try:
            with requests.get(url, headers=dict(cab, Range=f"bytes={feito}-"), stream=True, timeout=(15, 60)) as r:
                if r.status_code == 200:  # servidor ignorou o Range: recomeça do zero
                    feito = 0
                elif r.status_code != 206:
                    raise IOError(f"HTTP {r.status_code} em {nome}")
                t0, f0, ultimo = time.time(), feito, 0.0
                with open(parte, "r+b" if feito else "wb") as f:
                    f.seek(feito)
                    for bloco in r.iter_content(1024 * 1024):
                        if cancelado():
                            raise Cancelado()
                        f.write(bloco)
                        feito += len(bloco)
                        agora = time.time()
                        if agora - ultimo > 0.25:
                            ultimo = agora
                            ao_progresso(feito, tamanho, (feito - f0) / max(agora - t0, 1e-3), nome)
            if feito == tamanho:
                break
            raise IOError(f"download incompleto em {nome}")
        except Cancelado:
            raise
        except Exception:
            if tentativa == 5:
                raise
            log.warning("falha baixando %s, tentando de novo", nome, exc_info=True)
            time.sleep(2 * (tentativa + 1))
    if os.path.getsize(parte) != tamanho:
        raise IOError(f"tamanho errado em {nome}")
    os.replace(parte, final)
    ao_progresso(tamanho, tamanho, 0.0, "")
    return final


def baixar(ao_progresso=None, cancelado=None):
    """Baixa o modelo (se faltar). Retorna o caminho do arquivo. Levanta Cancelado."""
    if instalado():
        return ARQUIVO
    return _baixar_arquivo(URL, ARQUIVO, TAMANHO, ao_progresso, cancelado)


# ---------------------------------------------------------------- caixas

def _pedacos(w, h):
    """Janelas (x0, y0, x1, y1) para cobrir a imagem. Normal: uma só. Tira longa: pedaços de
    curto x (curto * PEDACO) no sentido do lado maior, com SOBREPOR de sobreposição."""
    if max(w, h) <= PROPORCAO_MAX * min(w, h):
        return [(0, 0, w, h)]
    curto, longo = min(w, h), max(w, h)
    tam = int(curto * PEDACO)
    passo = max(1, int(tam * (1 - SOBREPOR)))
    inicios = list(range(0, longo - tam, passo)) + [longo - tam]
    if h >= w:
        return [(0, y, w, y + tam) for y in inicios]
    return [(x, 0, x + tam, h) for x in inicios]


def _juntar(dets, vertical=True, cobre=0.5):
    """Junta caixas da mesma classe repetidas nas emendas dos pedaços: as que se cobrem
    (interseção > `cobre` da menor) e as metades cortadas na emenda ("corte") que se tocam e
    se alinham no outro eixo. Fica a união, com o maior score."""
    a0, a1 = (0, 2) if vertical else (1, 3)  # eixo perpendicular às emendas
    dets = sorted((dict(d) for d in dets), key=lambda d: -d["score"])
    mudou = True
    while mudou:
        mudou = False
        saida = []
        for d in dets:
            a = d["caixa"]
            for s in saida:
                b = s["caixa"]
                if s["classe"] != d["classe"]:
                    continue
                iw = min(a[2], b[2]) - max(a[0], b[0])
                ih = min(a[3], b[3]) - max(a[1], b[1])
                if iw <= 0 or ih <= 0:
                    continue
                menor = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                cobre_ok = iw * ih > cobre * max(menor, 1e-6)
                p_int = min(a[a1], b[a1]) - max(a[a0], b[a0])
                p_uni = max(a[a1], b[a1]) - min(a[a0], b[a0])
                emenda = (d.get("corte") or s.get("corte")) and p_int > 0.5 * p_uni
                if cobre_ok or emenda:
                    s["caixa"] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    s["corte"] = s.get("corte") or d.get("corte")
                    mudou = True
                    break
            else:
                saida.append(d)
        dets = saida
    for d in dets:
        d.pop("corte", None)
    return dets


class Detector:
    def __init__(self):
        self.sessao = None

    def carregar(self):
        if self.sessao is None:
            import onnxruntime as ort
            if not instalado():
                raise FileNotFoundError(ARQUIVO)
            op = ort.SessionOptions()
            op.log_severity_level = 3
            self.sessao = ort.InferenceSession(ARQUIVO, op, providers=["CPUExecutionProvider"])
        return self

    def descarregar(self):
        self.sessao = None

    def _rodar(self, img, limiar):
        """img RGB PIL -> lista de detecções em pixels de `img`."""
        w, h = img.size
        x = np.asarray(img.resize((LADO, LADO), Image.BILINEAR), dtype=np.float32) / 255.0
        x = x.transpose(2, 0, 1)[None]
        tam = np.array([[w, h]], dtype=np.int64)
        rotulos, caixas, scores = self.sessao.run(None, {"images": x, "orig_target_sizes": tam})
        saida = []
        for r, c, s in zip(rotulos[0], caixas[0], scores[0]):
            if s < limiar or not 0 <= r < len(CLASSES):
                continue
            x0, y0, x1, y1 = (float(v) for v in c)
            x0, x1 = max(0.0, min(x0, x1)), min(float(w), max(x0, x1))
            y0, y1 = max(0.0, min(y0, y1)), min(float(h), max(y0, y1))
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            saida.append({"classe": CLASSES[int(r)], "caixa": (x0, y0, x1, y1), "score": float(s)})
        return saida

    def detectar(self, imagem, limiar=LIMIAR):
        """-> [{"classe": "bubble"|"text_bubble"|"text_free", "caixa": (x0, y0, x1, y1), "score"}]
        com caixas em pixels da imagem original."""
        self.carregar()
        imagem = imagem.convert("RGB")
        janelas = _pedacos(*imagem.size)
        if len(janelas) == 1:
            return self._rodar(imagem, limiar)
        w, h = imagem.size
        vertical = h >= w
        dets = []
        for x0, y0, x1, y1 in janelas:
            # emendas internas deste pedaço (a borda da imagem não conta)
            ini, fim, tam = (y0, y1, h) if vertical else (x0, x1, w)
            tol = max(4, 0.02 * (fim - ini))
            for d in self._rodar(imagem.crop((x0, y0, x1, y1)), limiar):
                a = d["caixa"]
                d["caixa"] = (a[0] + x0, a[1] + y0, a[2] + x0, a[3] + y0)
                c0, c1 = (d["caixa"][1], d["caixa"][3]) if vertical else (d["caixa"][0], d["caixa"][2])
                d["corte"] = (ini > 0 and c0 - ini < tol) or (fim < tam and fim - c1 < tol)
                dets.append(d)
        return _juntar(dets, vertical)

    def mascara_texto(self, imagem, incluir_baloes=False, margem=6, deteccoes=None, limiar=LIMIAR):
        """Máscara L (255 = apagar) com as caixas de texto (text_bubble + text_free), alargadas
        `margem` px. incluir_baloes=True junta os balões inteiros. `deteccoes` reaproveita uma
        chamada anterior a detectar()."""
        if deteccoes is None:
            deteccoes = self.detectar(imagem, limiar)
        classes = TEXTO + (("bubble",) if incluir_baloes else ())
        w, h = imagem.size
        msk = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(msk)
        for det in deteccoes:
            if det["classe"] in classes:
                x0, y0, x1, y1 = det["caixa"]
                d.rectangle((max(0, int(x0) - margem), max(0, int(y0) - margem),
                             min(w - 1, int(np.ceil(x1)) + margem), min(h - 1, int(np.ceil(y1)) + margem)), fill=255)
        return msk


CORES = {"bubble": (0, 160, 255), "text_bubble": (255, 40, 40), "text_free": (40, 200, 40)}


def desenhar(imagem, deteccoes):
    """Cópia da imagem com as caixas desenhadas (para conferir)."""
    img = imagem.convert("RGB").copy()
    d = ImageDraw.Draw(img)
    for det in deteccoes:
        cor = CORES.get(det["classe"], (255, 255, 0))
        d.rectangle(det["caixa"], outline=cor, width=3)
        d.text((det["caixa"][0] + 3, det["caixa"][1] + 2), f"{det['classe']} {det['score']:.2f}", fill=cor)
    return img


def _main():
    if "--baixar" in sys.argv or not instalado():
        def mostrar(feito, total, vel, arq):
            print(f"\r{100 * feito / total:5.1f}%  {vel / 2**20:6.1f} MB/s  {arq}", end="", flush=True)
        baixar(mostrar)
        print(f"\npronto: {ARQUIVO}")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        img = Image.open(args[0])
        t0 = time.time()
        dets = Detector().detectar(img)
        print(f"{len(dets)} caixas em {time.time() - t0:.2f} s")
        for det in dets:
            print(f"  {det['classe']:<12} {det['score']:.2f}  {tuple(round(v) for v in det['caixa'])}")
        if len(args) > 1:
            desenhar(img, dets).save(args[1])


if __name__ == "__main__":
    _main()
