"""O motor: abre o modelo escolhido e roda gerar / editar / máscara numa thread própria, para a
interface nunca travar. torch e diffusers só são importados aqui, na primeira tarefa: a janela
abre na hora. Compatível com NVIDIA (CUDA), AMD (ROCm), Intel Arc (XPU) e processadores Intel/AMD."""
import inspect
import json
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

from . import config, dispositivo
from .i18n import tr
from .modelos import POR_ID, PRINCIPAL

log = logging.getLogger("motor")

PROMPT_LIMPAR = (
    "Remove everything inside the masked area and restore exactly what is behind it, continuing "
    "the surrounding background, line art, colors, shading and texture seamlessly. Do not add new "
    "objects, text or symbols."
)


class _CanceladoPeloUsuario(Exception):
    pass


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
    (o modelo rende mal muito pequeno) e tiras gigantes (webtoon) têm teto de 2048 px no maior
    lado para não estourar a atenção quadrática na placa. Manual: `maximo` px no lado maior."""
    w, h = img.size
    maior = max(w, h)
    if maximo:
        f = min(1.0, maximo / maior)
    else:
        f = min(1.0, (m.get("lado_auto", 1024) ** 2 / max(1, w * h)) ** 0.5)
        if maior * f < 768:
            f = 768 / maior
        f = min(f, 2048 / maior)
    return multiplo(w * f, mult), multiplo(h * f, mult)


def caixa_trabalho(mascara, margem_rel, minimo):
    """Caixa em volta da área pintada, com margem de contexto (fração do tamanho da área, ao menos `minimo` px)."""
    x0, y0, x1, y1 = mascara.getbbox()
    mg = max(minimo, int(max(x1 - x0, y1 - y0) * margem_rel))
    return max(0, x0 - mg), max(0, y0 - mg), min(mascara.width, x1 + mg), min(mascara.height, y1 + mg)


def corrigir_cores(gerada, original, raio=25):
    """O klein, editando sem máscara, costuma puxar as cores. Devolve a gerada com as cores de
    baixa frequência do original: detalhes da gerada, tom de cada região do original."""
    import numpy as np
    from scipy import ndimage
    gerada = gerada.convert("RGB").resize(original.size, Image.LANCZOS)
    # A correção é de baixa frequência: calcula em 1/4 da resolução (~15x mais rápido) e amplia canal a canal.
    f = 4
    peq = (max(1, original.width // f), max(1, original.height // f))
    borrar = lambda im: np.stack([ndimage.gaussian_filter(c, raio / f) for c in
                                  np.asarray(im.resize(peq, Image.BILINEAR), dtype=np.float32).transpose(2, 0, 1)], -1)
    dif = borrar(original.convert("RGB")) - borrar(gerada)
    buf = np.asarray(gerada, dtype=np.float32).copy()
    for c in range(3):
        buf[..., c] += np.asarray(Image.fromarray(dif[..., c]).resize(original.size, Image.BILINEAR))
    np.clip(buf, 0, 255, out=buf)
    return Image.fromarray(buf.astype(np.uint8))


def info_placa():
    """(nome, backend) da placa, sem abrir o modelo."""
    nome, backend, _total, _livre = dispositivo.info()
    return (nome or None), backend


def _ativar_economia_vae(vae):
    if vae is None:
        return
    if hasattr(vae, "enable_tiling"):
        try:
            vae.enable_tiling()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(vae, "enable_slicing"):
        try:
            vae.enable_slicing()
        except Exception:  # noqa: BLE001
            pass


class Motor(QObject):
    """Roda numa thread do Python (ver janela.py). Os sinais chegam na janela pela fila do Qt."""
    estado = Signal(str)           # texto curto do que está acontecendo
    passo = Signal(int, int)       # feito, total
    imagem = Signal(str)           # caminho de cada imagem assim que fica pronta
    concluido = Signal(dict)       # {"modo", "caminhos", "info", "cancelado"}
    falhou = Signal(str)
    carregado = Signal(str)        # id do modelo na placa ("" = nenhum)
    carregando = Signal(str)       # id do modelo que começou a carregar (leitura do disco)
    liberado = Signal()            # emitido quando a tarefa "liberar" termina de soltar a VRAM/arquivos
    placa = Signal(str, str, float)
    detectado = Signal(int, object, object, str)  # id, detecções, máscara, aviso/erro (aba Limpar)  # nome, backend (CUDA/ROCm/Intel XPU/CPU), VRAM total em GB

    def __init__(self):
        super().__init__()
        self._id = None                   # modelo aberto
        self._pipes = {}                  # modo -> pipeline
        self._cache_prompt = {}           # prompt -> embeddings em CPU (economiza VRAM)
        self._rapido = False              # transformer residente na placa
        self._usa_embeds = False          # permite guardar embeddings do prompt em cache
        self._compartilha_direto = False  # pode criar outro modo com classe(**base.components) sem re-hookar
        self._lama = None                 # apagador LaMa (carregado no primeiro uso)
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
        if not self._pipes:
            self.carregando.emit(m["id"])
        import diffusers

        dispositivo.otimizar_torch()
        t0 = time.time()
        dtype = dispositivo.escolher_dtype(m.get("dtype", "bfloat16"))
        classe = getattr(diffusers, m["classes"][modo])

        if self._pipes:
            # Mesmo modelo, outro modo: reaproveita os componentes já carregados na memória (sem reler o disco).
            base_existente = next(iter(self._pipes.values()))
            comps = dict(base_existente.components)
            if self._compartilha_direto:
                pipe = classe(**comps)
                pipe.set_progress_bar_config(disable=True)
                self._pipes[modo] = pipe
                return m, pipe
            # Modelos secundários com enable_model_cpu_offload: solta os ganchos antigos antes de reencaixar
            # para nunca acumular dois CpuOffload no mesmo módulo.
            for ant in list(self._pipes.values()):
                if getattr(ant, "_all_hooks", None):
                    ant.remove_all_hooks()
            self._pipes.clear()
            pipe = classe(**comps)
            if dispositivo.tem_gpu():
                pipe.enable_model_cpu_offload(device=dispositivo.dispositivo_torch())
            pipe.set_progress_bar_config(disable=True)
            self._pipes[modo] = pipe
            return m, pipe

        if m["tipo"] == "flux2_9b":
            from . import klein9b
            base = klein9b.abrir(pasta, dtype)
            base.set_progress_bar_config(disable=True)
            self._rapido = True
            self._usa_embeds = True
            self._compartilha_direto = True
            self._pipes["gerar"] = self._pipes["editar"] = base
            self._id = m["id"]
            log.info("%s aberto em %.1f s", m["id"], time.time() - t0)
            self._aquecer(base)
            self.carregado.emit(m["id"])
            return self._abrir(id_modelo, modo)

        if m["tipo"] == "flux2":
            _nome, _bk, total_gb, livre_gb = dispositivo.info()
            # Modo residente rápido só quando a placa tem >= 11,5 GB E >= 9,8 GB realmente livres agora;
            # caso contrário (placas de 6/8/10 GB ou placa de 12/16 GB ocupada pelo Photoshop/navegador),
            # transmite o transformer em blocos pela placa sem cair na RAM compartilhada lenta do Windows.
            residente = dispositivo.tem_gpu() and total_gb >= 11.5 and livre_gb >= 9.8
            base = self._abrir_flux2(diffusers.Flux2KleinPipeline, pasta, dtype, residente=residente, livre_gb=livre_gb)
            self._pipes["gerar"] = self._pipes["editar"] = base
            self._id = m["id"]
            log.info("%s aberto (%s, %.1f/%.1f GB livres) em %.1f s",
                     m["id"], "residente" if residente else "streaming", livre_gb, total_gb, time.time() - t0)
            if residente:
                self._aquecer(base)
            self.carregado.emit(m["id"])
            return self._abrir(id_modelo, modo)

        kw = dict(torch_dtype=dtype, local_files_only=True)
        if m.get("variante"):
            kw["variant"] = m["variante"]
        for c in m.get("excluir", ()):
            kw[c] = None
        if "safety_checker" in m.get("excluir", ()):
            kw["requires_safety_checker"] = False
        pipe = classe.from_pretrained(pasta, **kw)
        _ativar_economia_vae(getattr(pipe, "vae", None))
        if dispositivo.tem_gpu():
            pipe.enable_model_cpu_offload(device=dispositivo.dispositivo_torch())
        pipe.set_progress_bar_config(disable=True)
        self._usa_embeds = False
        self._compartilha_direto = not dispositivo.tem_gpu()
        self._pipes[modo] = pipe
        self._id = m["id"]
        log.info("%s/%s aberto em %.1f s", m["id"], modo, time.time() - t0)
        self.carregado.emit(m["id"])
        return m, pipe

    @staticmethod
    def _cabe_na_placa(gb=12):
        _nome, _bk, total_gb, livre_gb = dispositivo.info()
        return dispositivo.tem_gpu() and total_gb >= (gb - 0.5) and livre_gb >= max(4.0, gb - 2.2)

    def _abrir_flux2(self, classe, pasta, dtype, residente=True, livre_gb=0.0):
        """Abre o FLUX.2 klein 4B adaptando-se à placa e à RAM disponíveis:
        - `te` (Qwen3-4B): lê do disco APENAS as camadas 0-27 (sem 28-35 nem lm_head: economiza 2,6 GB de
          disco e RAM, idêntico bit a bit) direto em blocos `_Arena`.
        - `residente=True` (12 GB+ com VRAM livre): transformer + VAE moram na placa com teto `limitar_vram`
          (impede fallback lento para a RAM do Windows) e o `te` faz streaming por blocos.
        - `residente=False` (placas de 6-10 GB ou placas de 12-16 GB ocupadas por outros apps): VAE mora na
          placa (~170 MB, com tiling/slicing) e tanto `te` quanto `transformer` fazem streaming por blocos
          (pico de ~3-4 GB de VRAM, mantendo cache de prompt e troca instantânea entre Gerar/Editar/Máscara).
        - Sem GPU (CPU Intel/AMD): roda direto na CPU em bfloat16."""
        import diffusers
        from diffusers.hooks import apply_group_offloading
        from . import klein9b, leitura

        t0 = time.time()
        tem_gpu = dispositivo.tem_gpu()
        dev = dispositivo.dispositivo_torch()
        usar_stream = tem_gpu and dispositivo.suporta_streams_assincronos() and dispositivo.ram_disponivel_gb() >= 11.0
        te = transformer = vae = None
        try:
            if tem_gpu:
                if residente:
                    dispositivo.limitar_vram(1.6)
                elif livre_gb >= 4.0:
                    dispositivo.limitar_vram(0.9)

            # Primeiro o transformer, direto do disco para a placa (não passa pela RAM); só depois o
            # codificador de texto, que mora na RAM fixada. Na ordem inversa, os 6 GB fixados do codificador
            # deixavam o Windows sem RAM para ler o transformer (85 s em vez de ~5 s).
            pasta_tf = os.path.join(pasta, "transformer")
            self.estado.emit(tr("Lendo o gerador de imagens (1/3)…"))
            transformer = None
            if tem_gpu and residente:
                try:
                    transformer = leitura.montar_na_placa(diffusers.Flux2Transformer2DModel, pasta_tf, dev, dtype)
                except Exception:  # noqa: BLE001 - arquivo fora do padrão: cai no caminho do diffusers
                    log.warning("leitura direta do transformer falhou; usando from_pretrained", exc_info=True)
                    dispositivo.limpar_memoria()
                    transformer = diffusers.Flux2Transformer2DModel.from_pretrained(
                        pasta_tf, torch_dtype=dtype, device_map=dev.type, local_files_only=True)
            else:
                transformer = diffusers.Flux2Transformer2DModel.from_pretrained(
                    pasta_tf, torch_dtype=dtype, local_files_only=True)
            log.info("transformer em %.1f s", time.time() - t0)

            self.estado.emit(tr("Lendo o codificador de texto (2/3)…"))
            te = klein9b.carregar_qwen3_aparado(
                os.path.join(pasta, "text_encoder"), dtype, device=dev, int8=False, fixar=usar_stream)
            log.info("codificador de texto em %.1f s", time.time() - t0)

            self.estado.emit(tr("Montando na placa de vídeo (3/3)…"))
            vae = diffusers.AutoencoderKLFlux2.from_pretrained(
                os.path.join(pasta, "vae"), torch_dtype=dtype, local_files_only=True)
            _ativar_economia_vae(vae)
            if tem_gpu:
                vae = vae.to(dev)
            from transformers import AutoTokenizer
            with open(os.path.join(pasta, "model_index.json"), encoding="utf-8") as f:
                destilado = json.load(f).get("is_distilled", False)
            pipe = classe(
                scheduler=diffusers.FlowMatchEulerDiscreteScheduler.from_pretrained(pasta, subfolder="scheduler"),
                vae=vae, text_encoder=te, transformer=transformer, is_distilled=destilado,
                tokenizer=AutoTokenizer.from_pretrained(os.path.join(pasta, "tokenizer")))

            if tem_gpu:
                klein9b.descarregar_te(te, dev, usar_stream)
                if not residente:
                    stream_tf = usar_stream and dispositivo.ram_disponivel_gb() >= 10.0
                    apply_group_offloading(
                        transformer, onload_device=dev, offload_type="block_level",
                        num_blocks_per_group=1 if stream_tf else 2,
                        use_stream=stream_tf, low_cpu_mem_usage=stream_tf)

            pipe.set_progress_bar_config(disable=True)
            self._rapido = bool(tem_gpu and residente)
            self._usa_embeds = True
            self._compartilha_direto = True
            dispositivo.limpar_memoria()
            return pipe
        except BaseException:
            del te, transformer, vae
            dispositivo.restaurar_vram()
            dispositivo.limpar_memoria(liberar_fixada=True)
            raise

    def _aquecer(self, pipe):
        """Uma geração mínima (256²) logo após abrir: a 1ª imagem de verdade já sai na velocidade normal."""
        import torch
        self.estado.emit(tr("Preparando a placa…"))
        try:
            with torch.inference_mode():
                pipe(prompt_embeds=self._embeds(pipe, "warmup"), width=256, height=256, num_inference_steps=1,
                     guidance_scale=1.0, generator=torch.Generator("cpu").manual_seed(0), output_type="latent")
            dispositivo.sincronizar()
            dispositivo.limpar_memoria()
        except Exception:  # noqa: BLE001 - aquecer é opcional
            log.warning("aquecimento falhou", exc_info=True)

    def _embeds(self, pipe, prompt):
        """Prompt -> embeddings, uma vez por prompt; guarda em CPU (~7-15 MB) para não ocupar VRAM."""
        if prompt not in self._cache_prompt:
            import torch
            if prompt != "warmup":
                self.estado.emit(tr("Lendo o prompt…"))
            with torch.inference_mode():
                emb = pipe.encode_prompt(prompt, device=pipe._execution_device)[0]
            if len(self._cache_prompt) >= 24:
                self._cache_prompt.pop(next(iter(self._cache_prompt)))
            self._cache_prompt[prompt] = emb.detach().to("cpu")
            del emb
        return self._cache_prompt[prompt].to(device=pipe._execution_device)

    def _liberar(self):
        had = bool(self._pipes) or (self._lama is not None)
        for ant in list(self._pipes.values()):
            if getattr(ant, "_all_hooks", None):
                try:
                    ant.remove_all_hooks()
                except Exception:  # noqa: BLE001
                    pass
        self._pipes.clear()
        self._cache_prompt.clear()
        self._rapido = False
        self._usa_embeds = False
        self._compartilha_direto = False
        if self._lama is not None:
            self._lama.descarregar()
            self._lama = None
        self._id = None
        dispositivo.restaurar_vram()
        dispositivo.limpar_memoria(liberar_fixada=True)
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
                import diffusers  # noqa: F401
                from scipy import ndimage  # noqa: F401 - usado no "acertar as cores"
                nome, backend, total, _livre = dispositivo.info()
                self.placa.emit(nome, backend, total)
            elif tipo == "carregar":
                self._abrir(t.get("modelo", PRINCIPAL["id"]), t.get("modo", "gerar"))
                self.carregado.emit(self._id or "")  # mesmo se já estava aberto: a tela sai de "carregando"
                self.estado.emit(tr("Modelo pronto na placa de vídeo"))
            elif tipo == "liberar":
                try:
                    self._liberar()
                    self.estado.emit(tr("Placa de vídeo liberada"))
                finally:
                    self.liberado.emit()  # apagar modelo / testar placa esperam por isto
            else:
                getattr(self, "_" + tipo)(t)
        except Exception as e:  # noqa: BLE001 - qualquer erro vira aviso na interface
            if self.cancelar and e.__class__.__name__ in ("Cancelado", "_CanceladoPeloUsuario"):
                self._terminar(t.get("tipo", "gerar"), [], "", 0)
                return
            log.error("falha em %s\n%s", t.get("tipo"), traceback.format_exc())
            dispositivo.limpar_memoria()
            msg = str(e)
            baixo = msg.lower()
            if "out of memory" in baixo or "not enough memory" in baixo or "bad alloc" in baixo:
                msg = tr("Faltou memória na placa de vídeo ou na RAM. Feche outros programas pesados ou diminua o tamanho.")
            self.falhou.emit(msg)

    def _rodar(self, m, pipe, t, **kw):
        """Roda n imagens, uma por vez (cabe na placa), avisando cada passo e cada imagem."""
        import torch
        n, passos, prompt = int(t["quantas"]), int(t["passos"]), t["prompt"]
        semente = t.get("semente", -1)
        s = int(semente) if semente is not None and int(semente) >= 0 else random.randint(0, 2**31 - 1)
        if self._usa_embeds and m["tipo"].startswith("flux2"):
            kw["prompt_embeds"] = self._embeds(pipe, prompt)
        else:
            kw["prompt"] = prompt
        kw["guidance_scale"] = 1.0 if m["tipo"].startswith("flux2") else float(t.get("guia", m["guia"]))
        params = inspect.signature(pipe.__call__).parameters
        total = n * passos
        feitos = [0]

        def ao_passo(p, i, ts, cb):
            feitos[0] += 1
            self.passo.emit(min(feitos[0], total), total)
            if self.cancelar:
                p._interrupt = True
                raise _CanceladoPeloUsuario()
            return cb

        if "callback_on_step_end" in params:
            kw["callback_on_step_end"] = ao_passo
        imgs = []
        for k in range(n):
            if self.cancelar:
                break
            self.estado.emit(tr("Imagem {a} de {b}", a=k + 1, b=n) if n > 1 else tr("Gerando…"))
            g = torch.Generator("cpu").manual_seed(s + k)
            try:
                out = pipe(generator=g, num_images_per_prompt=1, num_inference_steps=passos, **kw).images
            except _CanceladoPeloUsuario:
                if hasattr(pipe, "maybe_free_model_hooks"):
                    pipe.maybe_free_model_hooks()
                break
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
        if m["tipo"].startswith("flux2"):
            w, h = tamanho_trabalho(refs[0], m, t.get("maximo"))
            imgs, s = self._rodar(m, pipe, t, image=refs, width=w, height=h)
        else:  # InstructPix2Pix: uma referência, tamanho em múltiplos de 8
            w, h = tamanho_trabalho(refs[0], m, t.get("maximo"), 8)
            imgs, s = self._rodar(m, pipe, t, image=refs[0].resize((w, h), Image.LANCZOS), image_guidance_scale=1.5)
        caminhos = []
        auto = not t.get("maximo")
        for im, sk in imgs:
            if t.get("manter_cores"):
                self.estado.emit(tr("Acertando as cores…"))
                im = corrigir_cores(im, refs[0])
            if auto and im.size != refs[0].size:
                # Automático: devolve exatamente no tamanho da imagem colada (para sobrepor no Photoshop).
                im = im.resize(refs[0].size, Image.LANCZOS)
            caminhos.append(self._salvar(im, "editar", t["prompt"], sk, self._meta(m, t)))
        tam = f"{refs[0].width}×{refs[0].height}" if auto else f"{w}×{h}"
        self._terminar("editar", caminhos, f"{m['nome']} · {tr('Semente')} {s} · {tam} · {time.time() - t0:.1f} s", s)

    def _mascara(self, t):
        """Refaz só a área pintada. Trabalha num recorte em volta da área (com margem de contexto), em
        resolução boa mesmo em imagens grandes, e cola de volta só os pixels pintados.
        O Flux2KleinInpaintPipeline usa a própria imagem de entrada como referência: se ela ainda tem a
        pessoa/objeto, o modelo desenha de volta (ou duplica). Por isso o LaMa limpa a área ANTES e essa
        imagem já limpa vira a entrada e a referência; com força 100% o FLUX redesenha a área do zero."""
        fundo = t["imagem"]           # PIL RGB
        mascara = t["mascara"]        # PIL L, mesmo tamanho
        if mascara.getbbox() is None:
            raise RuntimeError(tr("Pinte a área que quer refazer."))
        mascara = mascara.point(lambda v: 255 if v > 10 else 0).filter(ImageFilter.MaxFilter(7))
        m = POR_ID.get(t["modelo"], PRINCIPAL)
        base = fundo
        if t.get("preencher_lama"):
            base = self._lama_limpar(fundo, mascara)
            if self._lama is not None and (not self._rapido or m["tipo"] == "flux2_9b"):
                self._lama.mover_cpu()
        if self.cancelar:
            self._terminar("mascara", [], "", 0)
            return
        # O FLUX refaz a área pintada + 6 px; a colagem é 100% gerada em toda a área pintada e só esmaece
        # nessa faixa de fora. Antes a pluma ficava centrada na borda: em letras finas de marca d'água,
        # 70% dos pixels pintados traziam de volta parte do original (até 68%) — o "fantasma" da marca.
        area_flux = mascara.filter(ImageFilter.MaxFilter(13))
        caixa = caixa_trabalho(area_flux, 0.5, 128)
        rec, rec_m = base.crop(caixa), area_flux.crop(caixa)
        w, h = tamanho_trabalho(rec, m, t.get("maximo"))
        img, msk = rec.resize((w, h), Image.LANCZOS), rec_m.resize((w, h), Image.NEAREST)
        t = dict(t, prompt=t["prompt"].strip() or PROMPT_LIMPAR)
        m, pipe = self._abrir(t["modelo"], "mascara")
        t0 = time.time()
        geradas, s = self._rodar(m, pipe, t, image=img, mask_image=msk, width=w, height=h, strength=float(t["forca"]))
        pluma = mascara.crop(caixa).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(2))
        caminhos = []
        nome = "limpar" if t["prompt"] == PROMPT_LIMPAR else t["prompt"]
        for g, sk in geradas:
            final = fundo.copy()
            final.paste(Image.composite(g.resize(rec.size, Image.LANCZOS), fundo.crop(caixa), pluma), caixa[:2])
            caminhos.append(self._salvar(final, "mascara", nome, sk,
                                         self._meta(m, t, forca=t["forca"], lama=bool(t.get("preencher_lama")))))
        self._terminar("mascara", caminhos, f"{m['nome']} · {tr('Semente')} {s} · {time.time() - t0:.1f} s", s)

    # ---------- LaMa (baixado na primeira vez que for usado) ----------
    def _lama_limpar(self, imagem, mascara):
        from . import lama
        if not lama.instalado():
            self.estado.emit(tr("Baixando o LaMa de mangá (~200 MB, só na primeira vez)…"))
            lama.baixar(
                ao_progresso=lambda f, tot, _v, _a: self.passo.emit(int(f // 2**20), max(1, int(tot // 2**20))),
                cancelado=lambda: self.cancelar,
            )
        if self._lama is None:
            self._lama = lama.Lama()
        self._lama.carregar()
        self.estado.emit(tr("Limpando com o LaMa…"))
        return self._lama.limpar(
            imagem, mascara,
            ao_progresso=lambda i, n: self.passo.emit(i, max(1, n)),
            cancelado=lambda: self.cancelar,
        )

    def _detectar(self, t):
        """Aba Limpar: acha texto e balões (ONNX na CPU). Roda aqui, no processo do motor, para a janela
        não engasgar enquanto o onnxruntime abre e calcula."""
        ident = t["id"]
        try:
            from . import detector
            if not detector.instalado():
                self.detectado.emit(ident, None, None, tr("Baixando o detector de texto (só na primeira vez)…"))
                detector.baixar()
            if getattr(self, "_detector", None) is None:
                self._detector = detector.Detector()
            dets = self._detector.detectar(t["imagem"])
            msk = self._detector.mascara_texto(t["imagem"], incluir_baloes=t.get("baloes", False), deteccoes=dets)
            self.detectado.emit(ident, dets, msk, "")
        except Exception as e:  # noqa: BLE001
            log.warning("detecção falhou", exc_info=True)
            self.detectado.emit(ident, None, None, "erro:" + str(e)[:200])

    def _limpar(self, t):
        """Aba Limpar: só o LaMa (rápido, sem o FLUX)."""
        t0 = time.time()
        final = self._lama_limpar(t["imagem"], t["mascara"])
        caminho = self._salvar(final, "limpar", "lama", 0, {"modelo": "LaMa mangá"})
        self._terminar("limpar", [caminho], f"LaMa · {time.time() - t0:.1f} s", 0)
