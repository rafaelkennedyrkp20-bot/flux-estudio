"""O motor: abre o modelo escolhido e roda gerar / editar / máscara numa thread própria, para a
interface nunca travar. torch e diffusers só são importados aqui, na primeira tarefa: a janela
abre na hora. Funciona igual em NVIDIA (CUDA) e AMD (ROCm): os dois aparecem como torch.cuda."""
import gc
import inspect
import logging
import os
import random
import re
import time
import traceback
from datetime import datetime

from PIL import Image, ImageFilter
from PIL.PngImagePlugin import PngInfo
from PySide6.QtCore import QObject, Signal, Slot

from . import config
from .i18n import tr
from .modelos import POR_ID, PRINCIPAL

log = logging.getLogger("motor")

PROMPT_LIMPAR = (
    "Remove everything inside the masked area and restore exactly what is behind it, continuing "
    "the surrounding background, line art, colors, shading and texture seamlessly. Do not add new "
    "objects, text or symbols."
)


def multiplo(v, m=16):
    return max(256, int(round(v / m)) * m)


def tamanho_cabendo(img, maximo, m=16):
    """Cabe a imagem em `maximo` px no maior lado, em múltiplos de m."""
    w, h = img.size
    f = min(1.0, maximo / max(w, h))
    return multiplo(w * f, m), multiplo(h * f, m)


def tamanho_trabalho(img, m, maximo=None, mult=16):
    """Tamanho em que o modelo trabalha, sempre na proporção da imagem de entrada.
    Automático (maximo=None): até lado_auto² pixels; imagens pequenas sobem para ~768 px
    (o modelo rende mal muito pequeno). Manual: `maximo` px no lado maior."""
    w, h = img.size
    if maximo:
        f = min(1.0, maximo / max(w, h))
    else:
        f = min(1.0, (m.get("lado_auto", 1024) ** 2 / (w * h)) ** 0.5)
        if max(w, h) * f < 768:
            f = 768 / max(w, h)
    return multiplo(w * f, mult), multiplo(h * f, mult)


def corrigir_cores(gerada, original, raio=25):
    """O klein, editando sem máscara, costuma puxar as cores. Devolve a gerada com as cores de
    baixa frequência do original: detalhes da gerada, tom de cada região do original."""
    import numpy as np
    from scipy import ndimage
    o = np.asarray(original.convert("RGB")).astype(np.float32)
    r = np.asarray(gerada.convert("RGB").resize(original.size, Image.LANCZOS)).astype(np.float32)
    borrar = lambda x: np.stack([ndimage.gaussian_filter(x[..., c], raio) for c in range(3)], -1)
    return Image.fromarray(np.clip(r + borrar(o) - borrar(r), 0, 255).astype(np.uint8))


def info_placa():
    """(nome, backend) da placa, sem abrir o modelo."""
    import torch
    compilacao = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
    if torch.cuda.is_available():
        return torch.cuda.get_device_name(0), compilacao
    return None, "CPU:" + compilacao


class Motor(QObject):
    """Roda numa thread do Python (ver janela.py). Os sinais chegam na janela pela fila do Qt."""
    estado = Signal(str)           # texto curto do que está acontecendo
    passo = Signal(int, int)       # feito, total
    imagem = Signal(str)           # caminho de cada imagem assim que fica pronta
    concluido = Signal(dict)       # {"modo", "caminhos", "info", "cancelado"}
    falhou = Signal(str)
    carregado = Signal(str)        # id do modelo na placa ("" = nenhum)
    placa = Signal(str, str, float)  # nome, backend (CUDA/ROCm/CPU), VRAM total em GB

    def __init__(self):
        super().__init__()
        self._id = None            # modelo aberto
        self._pipes = {}           # modo -> pipeline
        self.cancelar = False

    # ---------- modelo ----------
    def _abrir(self, id_modelo, modo):
        m = POR_ID.get(id_modelo, PRINCIPAL)
        if self._id != m["id"]:
            self._liberar()
        if modo in self._pipes:
            return m, self._pipes[modo]
        pasta = config.pasta_modelo(m["id"])
        if not config.modelo_instalado(m["id"], excluir=m.get("excluir", ())):
            raise RuntimeError(tr("O modelo {nome} ainda não foi baixado. Baixe na aba Modelos.", nome=m["nome"]))
        self.estado.emit(tr("Abrindo {nome}… (só na primeira vez)", nome=m["nome"]))
        import diffusers
        import torch

        t0 = time.time()
        dtype = getattr(torch, m.get("dtype", "bfloat16"))
        classe = getattr(diffusers, m["classes"][modo])
        if self._pipes:
            # Mesmo modelo, outro modo: reaproveita os componentes já carregados (sem ler o disco de novo).
            pipe = classe(**next(iter(self._pipes.values())).components)
        else:
            kw = dict(torch_dtype=dtype)
            if m.get("variante"):
                kw["variant"] = m["variante"]
            for c in m.get("excluir", ()):
                kw[c] = None
            if "safety_checker" in m.get("excluir", ()):
                kw["requires_safety_checker"] = False
            pipe = classe.from_pretrained(pasta, **kw)
        if torch.cuda.is_available():
            pipe.enable_model_cpu_offload()   # 16 GB não comporta tudo junto: um componente por vez na placa
        pipe.set_progress_bar_config(disable=True)
        self._pipes[modo] = pipe
        self._id = m["id"]
        log.info("%s/%s aberto em %.0f s", m["id"], modo, time.time() - t0)
        self.carregado.emit(m["id"])
        return m, pipe

    def _liberar(self):
        had = bool(self._pipes)
        self._pipes.clear()
        self._id = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        if had:
            self.carregado.emit("")

    # ---------- execução ----------
    @Slot(dict)
    def executar(self, t):
        self.cancelar = False
        try:
            tipo = t["tipo"]
            if tipo == "info":
                # Importa torch/diffusers já na abertura (em segundo plano): a 1ª geração fica mais rápida.
                import torch
                import diffusers  # noqa: F401
                nome, backend = info_placa()
                total = torch.cuda.get_device_properties(0).total_memory / 2**30 if nome else 0.0
                self.placa.emit(nome or "", backend, total)
            elif tipo == "carregar":
                self._abrir(t.get("modelo", PRINCIPAL["id"]), t.get("modo", "gerar"))
                self.estado.emit(tr("Modelo pronto na placa de vídeo"))
            elif tipo == "liberar":
                self._liberar()
                self.estado.emit(tr("Placa de vídeo liberada"))
            else:
                getattr(self, "_" + tipo)(t)
        except Exception as e:  # noqa: BLE001 - qualquer erro vira aviso na interface
            log.error("falha em %s\n%s", t.get("tipo"), traceback.format_exc())
            msg = str(e)
            if "out of memory" in msg.lower():
                msg = tr("Faltou memória na placa de vídeo. Feche outros programas pesados ou diminua o tamanho.")
            self.falhou.emit(msg)

    def _rodar(self, m, pipe, t, **kw):
        """Roda n imagens, uma por vez (cabe na placa), avisando cada passo e cada imagem."""
        import torch
        n, passos, prompt = int(t["quantas"]), int(t["passos"]), t["prompt"]
        semente = t.get("semente", -1)
        s = int(semente) if semente is not None and int(semente) >= 0 else random.randint(0, 2**31 - 1)
        # Sempre pelo prompt: o pipeline cuida de levar cada parte do modelo para a placa e de volta.
        # (Chamar encode_prompt por fora quebra esse descarregamento: fica lento e vaza memória.)
        kw["prompt"] = prompt
        kw["guidance_scale"] = 1.0 if m["tipo"] == "flux2" else float(t.get("guia", m["guia"]))
        params = inspect.signature(pipe.__call__).parameters
        total = n * passos
        feitos = [0]

        def ao_passo(p, i, ts, cb):
            feitos[0] += 1
            self.passo.emit(min(feitos[0], total), total)
            if self.cancelar:
                p._interrupt = True
            return cb

        if "callback_on_step_end" in params:
            kw["callback_on_step_end"] = ao_passo
        imgs = []
        for k in range(n):
            if self.cancelar:
                break
            self.estado.emit(tr("Imagem {a} de {b}", a=k + 1, b=n) if n > 1 else tr("Gerando…"))
            g = torch.Generator("cpu").manual_seed(s + k)
            out = pipe(generator=g, num_images_per_prompt=1, num_inference_steps=passos, **kw).images
            if self.cancelar:
                break
            imgs.append((out[0], s + k))
        return imgs, s

    def _salvar(self, img, modo, prompt, semente, extra):
        config.garantir_pastas()
        nome = re.sub(r"[^\w\-]+", "_", (prompt or "limpar").strip())[:50].strip("_") or "imagem"
        hora = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(config.SAIDAS, f"{hora}_{modo}_{nome}_s{semente}")
        caminho, n = base + ".png", 1
        while os.path.exists(caminho):
            caminho, n = f"{base}_{n}.png", n + 1
        meta = PngInfo()
        meta.add_text("prompt", prompt or "")
        meta.add_text("modo", modo)
        meta.add_text("semente", str(semente))
        for k, v in extra.items():
            meta.add_text(k, str(v))
        img.save(caminho, pnginfo=meta)
        self.imagem.emit(caminho)
        return caminho

    def _terminar(self, modo, caminhos, info, semente):
        self.concluido.emit({"modo": modo, "caminhos": caminhos, "info": info, "semente": semente,
                             "cancelado": self.cancelar})

    def _meta(self, m, t, **extra):
        return dict(modelo=m["nome"], passos=t["passos"], **extra)

    def _gerar(self, t):
        m, pipe = self._abrir(t["modelo"], "gerar")
        t0 = time.time()
        w, h = multiplo(t["largura"]), multiplo(t["altura"])
        imgs, s = self._rodar(m, pipe, t, width=w, height=h)
        caminhos = [self._salvar(im, "gerar", t["prompt"], sk, self._meta(m, t)) for im, sk in imgs]
        self._terminar("gerar", caminhos, f"{m['nome']} · {tr('Semente')} {s} · {w}×{h} · {time.time() - t0:.1f} s", s)

    def _editar(self, t):
        refs = t["referencias"]  # lista de PIL RGB
        m, pipe = self._abrir(t["modelo"], "editar")
        t0 = time.time()
        if m["tipo"] == "flux2":
            w, h = tamanho_trabalho(refs[0], m, t.get("maximo"))
            imgs, s = self._rodar(m, pipe, t, image=refs, width=w, height=h)
        else:  # InstructPix2Pix: uma referência, tamanho em múltiplos de 8
            w, h = tamanho_trabalho(refs[0], m, t.get("maximo"), 8)
            imgs, s = self._rodar(m, pipe, t, image=refs[0].resize((w, h), Image.LANCZOS), image_guidance_scale=1.5)
        caminhos = []
        for im, sk in imgs:
            if t.get("manter_cores"):
                self.estado.emit(tr("Acertando as cores…"))
                im = corrigir_cores(im, refs[0])
            caminhos.append(self._salvar(im, "editar", t["prompt"], sk, self._meta(m, t)))
        self._terminar("editar", caminhos, f"{m['nome']} · {tr('Semente')} {s} · {w}×{h} · {time.time() - t0:.1f} s", s)

    def _mascara(self, t):
        fundo = t["imagem"]           # PIL RGB
        mascara = t["mascara"]        # PIL L, mesmo tamanho
        if mascara.getbbox() is None:
            raise RuntimeError(tr("Pinte a área que quer refazer."))
        mascara = mascara.point(lambda v: 255 if v > 10 else 0).filter(ImageFilter.MaxFilter(7))
        w0, h0 = fundo.size
        w, h = tamanho_trabalho(fundo, POR_ID.get(t["modelo"], PRINCIPAL), t.get("maximo"))
        img, msk = fundo.resize((w, h), Image.LANCZOS), mascara.resize((w, h), Image.NEAREST)
        t = dict(t, prompt=t["prompt"].strip() or PROMPT_LIMPAR)
        m, pipe = self._abrir(t["modelo"], "mascara")
        t0 = time.time()
        geradas, s = self._rodar(m, pipe, t, image=img, mask_image=msk, width=w, height=h, strength=float(t["forca"]))
        # Volta ao tamanho original e cola só a área pintada: o resto fica pixel a pixel igual.
        pluma = mascara.filter(ImageFilter.GaussianBlur(3))
        caminhos = []
        nome = "limpar" if t["prompt"] == PROMPT_LIMPAR else t["prompt"]
        for g, sk in geradas:
            final = Image.composite(g.resize((w0, h0), Image.LANCZOS), fundo, pluma)
            caminhos.append(self._salvar(final, "mascara", nome, sk, self._meta(m, t, forca=t["forca"])))
        self._terminar("mascara", caminhos, f"{m['nome']} · {tr('Semente')} {s} · {time.time() - t0:.1f} s", s)
