"""Borracha LaMa para mangá/anime: apaga texto, marcas d'água e sujeiras numa área marcada e
reconstrói o fundo (retícula, traço, degradê). Rápida (segundos) e sem prompt.

- Pesos: anime-manga-big-lama.pt (TorchScript, ~196 MB, MIT; Big-LaMa ajustado em mangá por
  dreMaz), baixados para modelos/lama-manga/. Retomável (.part).
- Só as regiões marcadas são processadas, cada uma num recorte com margem; o que está fora da
  máscara (dilatada) volta idêntico, pixel a pixel.
- torch só é importado ao carregar. CUDA e ROCm aparecem como torch.cuda; se a placa falhar no
  primeiro uso, cai para a CPU e lembra disso.

Uso sem interface:  python -m app.lama [--baixar] [imagem mascara saida]
"""
import logging
import os
import sys
import time

import numpy as np
from PIL import Image

from . import config, dispositivo

log = logging.getLogger("lama")

ID = "lama-manga"
NOME_ARQ = "anime-manga-big-lama.pt"
URL = "https://github.com/Sanster/models/releases/download/AnimeMangaInpainting/anime-manga-big-lama.pt"
TAMANHO = 205_717_870
PASTA = config.pasta_modelo(ID)
ARQUIVO = os.path.join(PASTA, NOME_ARQ)

LADO_MAX = 1536     # maior lado do recorte que vai para o modelo
MULTIPLO = 8        # o LaMa pede lados múltiplos de 8
LIMIAR = 10         # máscara: acima disso conta como marcado (igual ao motor)

_gpu_falhou = False  # a placa falhou uma vez nesta sessão: daí em diante, só CPU


class Cancelado(Exception):
    pass


# ---------------------------------------------------------------- download

def _arquivo():
    return os.path.join(config.pasta_modelo(ID), NOME_ARQ)


def instalado():
    arq = _arquivo()
    return os.path.isfile(arq) and os.path.getsize(arq) == TAMANHO


def _baixar_arquivo(url, final, tamanho, ao_progresso=None, cancelado=None):
    """Baixa `url` para `final` via `final.part`, continuando de onde parou (Range).
    `ao_progresso(feito, total, velocidade_bps, arquivo)`; `cancelado()` -> True interrompe."""
    import requests
    ao_progresso = ao_progresso or (lambda *a: None)
    cancelado = cancelado or (lambda: False)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    parte = final + ".part"
    nome = os.path.basename(final)
    for tentativa in range(6):
        if cancelado():
            raise Cancelado()
        feito = os.path.getsize(parte) if os.path.isfile(parte) else 0
        if feito > tamanho:  # .part estranho: recomeça
            os.remove(parte)
            feito = 0
        if feito == tamanho:
            break
        try:
            cab = {"User-Agent": "FluxEstudio/1.0"}
            if feito > 0:
                cab["Range"] = f"bytes={feito}-"
            with requests.get(url, headers=cab, stream=True, timeout=(15, 60)) as r:
                if r.status_code == 416:  # intervalo inválido: apaga .part e recomeça
                    if os.path.isfile(parte):
                        os.remove(parte)
                    continue
                if r.status_code == 200:  # servidor ignorou o Range (ou pedido do zero): grava do início
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
            if tentativa == 5 or cancelado():
                raise
            log.warning("falha baixando %s, tentando de novo", nome, exc_info=True)
            time.sleep(2 * (tentativa + 1))
    if os.path.getsize(parte) != tamanho:
        raise IOError(f"tamanho errado em {nome}")
    os.replace(parte, final)
    ao_progresso(tamanho, tamanho, 0.0, "")
    return final


def baixar(ao_progresso=None, cancelado=None):
    """Baixa os pesos (se faltarem). Retorna o caminho do arquivo. Levanta Cancelado."""
    arq = _arquivo()
    if os.path.isfile(arq) and os.path.getsize(arq) == TAMANHO:
        return arq
    return _baixar_arquivo(URL, arq, TAMANHO, ao_progresso, cancelado)


# ---------------------------------------------------------------- máscara

def _dilatar(msk, raio):
    """Dilatação redonda de uma máscara booleana."""
    if raio <= 0 or not msk.any():
        return msk
    from scipy import ndimage
    return ndimage.distance_transform_edt(~msk) <= raio


def _caixas(msk, margem):
    """Caixas (x0, y0, x1, y1) das regiões marcadas, cada uma com margem (`margem` px ou 25%
    do tamanho, o que for maior), juntando as que se encostam."""
    from scipy import ndimage
    h, w = msk.shape
    rotulos, n = ndimage.label(msk)
    caixas = []
    for fatia in ndimage.find_objects(rotulos):
        if fatia is None:
            continue
        y0, y1, x0, x1 = fatia[0].start, fatia[0].stop, fatia[1].start, fatia[1].stop
        mx = max(margem, (x1 - x0) // 4)
        my = max(margem, (y1 - y0) // 4)
        caixas.append([max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)])
    # junta as que se sobrepõem até não sobrar nenhuma sobreposição
    mudou = True
    while mudou:
        mudou = False
        saida = []
        for c in caixas:
            for s in saida:
                if c[0] < s[2] and s[0] < c[2] and c[1] < s[3] and s[1] < c[3]:
                    s[:] = [min(s[0], c[0]), min(s[1], c[1]), max(s[2], c[2]), max(s[3], c[3])]
                    mudou = True
                    break
            else:
                saida.append(c)
        caixas = saida
    return [tuple(c) for c in caixas]


# ---------------------------------------------------------------- modelo

class Lama:
    def __init__(self):
        self.modelo = None
        self.dispositivo = None

    def carregar(self, disp=None):
        """Abre o TorchScript. disp=None: cuda/xpu se houver (e não tiver falhado), senão cpu."""
        import torch
        dispositivo.otimizar_torch()
        arq = _arquivo()
        if not (os.path.isfile(arq) and os.path.getsize(arq) == TAMANHO):
            raise FileNotFoundError(arq)
        if disp is None:
            t = dispositivo.tipo()
            disp = t if t != "cpu" and not _gpu_falhou else "cpu"
        if self.modelo is not None and self.dispositivo == disp:
            return self
        if self.modelo is None:
            modelo = torch.jit.load(arq, map_location="cpu")
            modelo.eval()
            self.modelo = modelo
        self.modelo = self.modelo.to(disp)
        self.dispositivo = disp
        log.info("LaMa aberto em %s", disp)
        return self

    def mover_cpu(self):
        """Recolhe temporariamente os ~200 MB da placa para a CPU sem precisar reler o disco."""
        if self.modelo is not None and self.dispositivo not in (None, "cpu"):
            self.modelo = self.modelo.to("cpu")
            self.dispositivo = "cpu"
            dispositivo.limpar_memoria()

    def descarregar(self):
        """Solta o modelo e devolve a memória da placa."""
        self.modelo = None
        self.dispositivo = None
        dispositivo.limpar_memoria()

    def _rodar(self, img, msk):
        """img uint8 HxWx3, msk bool HxW (lados múltiplos de 8) -> uint8 HxWx3."""
        import torch
        global _gpu_falhou
        t_img = torch.from_numpy(img).permute(2, 0, 1)[None].float().div_(255)
        t_msk = torch.from_numpy(msk)[None, None].float()
        try:
            # sem a otimização do TorchScript: ela recompila a cada tamanho novo (segundos)
            with torch.jit.optimized_execution(False), torch.inference_mode():
                saida = self.modelo(t_img.to(self.dispositivo), t_msk.to(self.dispositivo))
                saida = saida[0].permute(1, 2, 0).float().cpu().numpy()
        except Exception:
            if self.dispositivo == "cpu":
                raise
            # placa falhou (driver, ROCm, falta de memória...): segue na CPU e lembra
            log.warning("LaMa falhou em %s; usando a CPU", self.dispositivo, exc_info=True)
            _gpu_falhou = True
            self.descarregar()
            self.carregar("cpu")
            return self._rodar(img, msk)
        return np.clip(saida * 255 + 0.5, 0, 255).astype(np.uint8)

    def limpar(self, imagem, mascara, dilatar=4, margem=64, pluma=3, lado_max=LADO_MAX,
               ao_progresso=None, cancelado=None):
        """Apaga o que está marcado em `mascara` (L, branco = apagar) e devolve a imagem RGB.
        A máscara é dilatada `dilatar` px (halo do texto); fora dela nada muda. A borda é
        misturada em `pluma` px, por dentro da área dilatada. `ao_progresso(i, n)` por região."""
        if self.modelo is None or (self.dispositivo == "cpu" and dispositivo.tem_gpu() and not _gpu_falhou):
            self.carregar()
        imagem = imagem.convert("RGB")
        if mascara.size != imagem.size:
            mascara = mascara.resize(imagem.size, Image.NEAREST)
        orig = np.asarray(imagem)
        msk = np.asarray(mascara.convert("L")) > LIMIAR
        if not msk.any():
            return imagem.copy()
        from scipy import ndimage
        res = orig.copy()
        # caixas com folga para a dilatação; dilata e calcula a pluma só dentro de cada recorte
        caixas = _caixas(msk, margem + dilatar)
        for i, (x0, y0, x1, y1) in enumerate(caixas):
            if cancelado and cancelado():
                raise Cancelado()
            if ao_progresso:
                ao_progresso(i, len(caixas))
            rec = orig[y0:y1, x0:x1]
            rmsk = _dilatar(msk[y0:y1, x0:x1], dilatar)
            # alfa: 0 fora da máscara, sobe até 1 em `pluma`+1 px para dentro
            alfa = np.clip(ndimage.distance_transform_edt(rmsk) / (pluma + 1), 0, 1).astype(np.float32)
            h, w = rmsk.shape
            f = min(1.0, lado_max / max(w, h))
            if f < 1.0:  # recorte grande demais: reduz, roda e amplia
                tw, th = max(MULTIPLO, round(w * f)), max(MULTIPLO, round(h * f))
                rec_m = np.asarray(Image.fromarray(rec).resize((tw, th), Image.LANCZOS))
                rmsk_m = np.asarray(Image.fromarray(rmsk.astype(np.uint8) * 255).resize((tw, th), Image.BILINEAR)) > 0
            else:
                rec_m, rmsk_m = rec, rmsk
            mh, mw = rmsk_m.shape
            ph, pw = (-mh) % MULTIPLO, (-mw) % MULTIPLO
            modo = "reflect" if ph < mh and pw < mw else "symmetric"
            ent = np.pad(rec_m, ((0, ph), (0, pw), (0, 0)), mode=modo)
            ent_m = np.pad(rmsk_m, ((0, ph), (0, pw)), mode=modo)
            out = self._rodar(ent, ent_m)[:mh, :mw]
            if f < 1.0:
                out = np.asarray(Image.fromarray(out).resize((w, h), Image.BICUBIC))
            a = alfa[..., None]
            mistura = rec.astype(np.float32) * (1 - a) + out.astype(np.float32) * a
            res[y0:y1, x0:x1] = np.where(rmsk[..., None], np.clip(mistura + 0.5, 0, 255).astype(np.uint8), rec)
        if ao_progresso:
            ao_progresso(len(caixas), len(caixas))
        return Image.fromarray(res)


def _main():
    if "--baixar" in sys.argv or not instalado():
        def mostrar(feito, total, vel, arq):
            print(f"\r{100 * feito / total:5.1f}%  {vel / 2**20:6.1f} MB/s  {arq}", end="", flush=True)
        arq = baixar(mostrar)
        print(f"\npronto: {arq}")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) == 3:
        t0 = time.time()
        lama = Lama().carregar()
        Lama.limpar(lama, Image.open(args[0]), Image.open(args[1])).save(args[2])
        print(f"{args[2]} ({lama.dispositivo}, {time.time() - t0:.1f} s)")


if __name__ == "__main__":
    _main()
