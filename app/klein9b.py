"""FLUX.2 [klein] 9B numa placa de 16 GB, sem precisar do repositório fechado (gated) da BFL.

O que é baixado (tudo público, sem token):
  transformer/  unsloth/FLUX.2-klein-9B-GGUF, Q8_0 (~9,3 GB). O bf16 tem 17,3 GB e não cabe numa placa
                de 16 GB junto com o resto; o Q8_0 (int8 por blocos de 32 com escala) fica praticamente
                igual ao bf16. O config.json do transformer é escrito aqui (CONFIG_TRANSFORMER), com os
                tamanhos tirados do próprio GGUF: 8 blocos duplos, 24 simples, 32 cabeças x 128, texto 12288.
  text_encoder/ Qwen/Qwen3-8B (Apache 2.0, ~15,3 GB). O klein 9B usa o Qwen3-8B sem mudanças: mesma
                arquitetura e exatamente os mesmos bytes de tensores (16.381.516.776 B) que a pasta
                text_encoder do repositório oficial; o 4B, conferido tensor a tensor, é o Qwen3-4B original.
  vae/, tokenizer/, scheduler/, model_index.json
                do FLUX.2-klein-4B (público, Apache 2.0). São idênticos aos do 9B oficial (mesmo id git de
                cada arquivo no Hugging Face). Se o 4B já estiver instalado, são copiados de lá.
  LICENSE.md    a licença do 9B (FLUX Non-Commercial), igual à do repositório oficial.

A LICENÇA do 9B é a FLUX Non-Commercial License, mesmo baixando de um espelho: o programa deve mostrar
LICENCA_URL e só baixar/abrir depois que o usuário aceitar.

Modo rápido (como o _abrir_flux2_rapido do 4B, em app/motor.py):
  - transformer Q8_0 morando na placa (~9,3 GB), lido do GGUF por mmap direto para a placa e
    desquantizado camada a camada durante a conta;
  - VAE na placa em bf16;
  - Qwen3-8B só com as 28 primeiras camadas (o klein lê as saídas 9, 18 e 27; idêntico bit a bit), sem o
    lm_head, em int8 por grupos (te_int8=True: ~5,7 GB; bf16: ~11,2 GB) em memória fixada (pinned),
    indo para a placa camada por camada com streams (group offload). Os embeddings do prompt podem ser
    guardados como no motor (pipe.encode_prompt(...)[0] -> prompt_embeds=...);
  - teto de VRAM do PyTorch (limitar_vram): sem ele, a edição 1024² passava do limite da placa e o
    Windows jogava o excesso na RAM (47 s por passo em vez de 5,5 s).

Medido numa RTX 5060 Ti 16 GB (PCIe 3.0 x8), 1024², 4 passos: gerar ~10,7 s; editar com 1 referência
~23 s; máscara ~23 s; prompt novo ~1,05 s. Abrir: ~35-50 s com o disco em cache. RAM: pico ~10,5 GB no
processo ao abrir, ~7-8 GB depois. VRAM: ~12 GB alocados no pico + contexto CUDA.

Uso:
    pipe = klein9b.abrir(pasta)                     # Flux2KleinPipeline (gerar / editar com referências)
    inp = diffusers.Flux2KleinInpaintPipeline(**pipe.components)   # máscara, mesmos pesos

Sem interface:  python -m app.klein9b <pasta> [--listar | --baixar | --verificar]
"""
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

ID = "flux2-klein-9b"
NOME = "FLUX.2 [klein] 9B"
REPO_OFICIAL = "black-forest-labs/FLUX.2-klein-9B"   # gated: não é usado para baixar nada
LICENCA = "FLUX Non-Commercial License"
LICENCA_URL = "https://huggingface.co/black-forest-labs/FLUX.2-klein-9B/blob/main/LICENSE.md"

REPO_GGUF, REV_GGUF = "unsloth/FLUX.2-klein-9B-GGUF", "fde8634245fe6b749a221c25b34672b5b8fbd079"
REPO_TE, REV_TE = "Qwen/Qwen3-8B", "b968826d9c46dd6066d109eabc6255188de91218"
REPO_4B, REV_4B = "black-forest-labs/FLUX.2-klein-4B", "e7b7dc27f91deacad38e78976d1f2b499d76a294"

ARQ_GGUF = "flux-2-klein-9b-Q8_0.gguf"
CAMADAS_TE = 28          # o klein lê hidden_states 9, 18 e 27; a 28ª camada só precisa existir até a 27

# (destino na pasta, repo, revisão, caminho no repo, tamanho em bytes, sha256 ou None)
ARQUIVOS = [
    (f"transformer/{ARQ_GGUF}", REPO_GGUF, REV_GGUF, ARQ_GGUF, 9978304800,
     "dfa8908dd58c5af6479b944f80558e226ee40a8fe0903729309e8d1b98fe5297"),
    ("LICENSE.md", REPO_GGUF, REV_GGUF, "LICENSE.md", 18158, None),
    ("text_encoder/config.json", REPO_TE, REV_TE, "config.json", 728, None),
    ("text_encoder/generation_config.json", REPO_TE, REV_TE, "generation_config.json", 239, None),
    ("text_encoder/model.safetensors.index.json", REPO_TE, REV_TE, "model.safetensors.index.json", 32878, None),
    ("text_encoder/model-00001-of-00005.safetensors", REPO_TE, REV_TE, "model-00001-of-00005.safetensors",
     3996250744, "31d6a825ae35f11fb85b195b4c42c146c051e446433125a215336abdf95cbf5f"),
    ("text_encoder/model-00002-of-00005.safetensors", REPO_TE, REV_TE, "model-00002-of-00005.safetensors",
     3993160032, "5991236cea6fe21f3d43cab0f0e84448734fbbe0789816202989f2ddc9d18282"),
    ("text_encoder/model-00003-of-00005.safetensors", REPO_TE, REV_TE, "model-00003-of-00005.safetensors",
     3959604768, "c5185c4794be2d8a9784d5753c9922db38df478ce11f9ed0b415b7304d896836"),
    ("text_encoder/model-00004-of-00005.safetensors", REPO_TE, REV_TE, "model-00004-of-00005.safetensors",
     3187841392, "b5ee7de71fbf17db3d5704e0c8f2bc7d005ca9e1d7ca2aeb19827b0cfcaa917a"),
    ("text_encoder/model-00005-of-00005.safetensors", REPO_TE, REV_TE, "model-00005-of-00005.safetensors",
     1244659840, "20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff"),
    ("model_index.json", REPO_4B, REV_4B, "model_index.json", 446, None),
    ("scheduler/scheduler_config.json", REPO_4B, REV_4B, "scheduler/scheduler_config.json", 486, None),
    ("vae/config.json", REPO_4B, REV_4B, "vae/config.json", 821, None),
    ("vae/diffusion_pytorch_model.safetensors", REPO_4B, REV_4B, "vae/diffusion_pytorch_model.safetensors",
     168120878, "ca70d2202afe6415bdbcb8793ba8cd99fd159cfe6192381504d6c4d3036e0f04"),
    ("tokenizer/added_tokens.json", REPO_4B, REV_4B, "tokenizer/added_tokens.json", 707, None),
    ("tokenizer/chat_template.jinja", REPO_4B, REV_4B, "tokenizer/chat_template.jinja", 4168, None),
    ("tokenizer/merges.txt", REPO_4B, REV_4B, "tokenizer/merges.txt", 1671853, None),
    ("tokenizer/special_tokens_map.json", REPO_4B, REV_4B, "tokenizer/special_tokens_map.json", 613, None),
    ("tokenizer/tokenizer.json", REPO_4B, REV_4B, "tokenizer/tokenizer.json", 11422654,
     "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    ("tokenizer/tokenizer_config.json", REPO_4B, REV_4B, "tokenizer/tokenizer_config.json", 5404, None),
    ("tokenizer/vocab.json", REPO_4B, REV_4B, "tokenizer/vocab.json", 2776833, None),
]

# Mesmo formato do transformer/config.json do 4B; tamanhos lidos do GGUF:
#   double_blocks.N (8) / single_blocks.N (24); img_attn.qkv 4096->12288 (32 cabeças x 128);
#   txt_in 12288->4096 (3 x 4096 do Qwen3-8B, camadas 9/18/27); img_mlp.0 4096->24576 (SwiGLU, 3 x 4096);
#   sem guidance_in (destilado); img_in 128 canais; time_in 256.
CONFIG_TRANSFORMER = {
    "_class_name": "Flux2Transformer2DModel",
    "_diffusers_version": "0.37.0.dev0",
    "attention_head_dim": 128,
    "axes_dims_rope": [32, 32, 32, 32],
    "eps": 1e-06,
    "guidance_embeds": False,
    "in_channels": 128,
    "joint_attention_dim": 12288,
    "mlp_ratio": 3.0,
    "num_attention_heads": 32,
    "num_layers": 8,
    "num_single_layers": 24,
    "out_channels": None,
    "patch_size": 1,
    "rope_theta": 2000,
    "timestep_guidance_channels": 256,
}

PEDACO = 64 * 1024 * 1024
TRABALHADORES = 8


class Cancelado(Exception):
    pass


def _url(repo, rev, caminho):
    return f"https://huggingface.co/{repo}/resolve/{rev}/{caminho}"


def arquivos_necessarios():
    """[(destino relativo, tamanho em bytes, url)] de tudo que vai para a pasta do 9B."""
    return [(dest, tam, _url(repo, rev, cam)) for dest, repo, rev, cam, tam, _ in ARQUIVOS]


def tamanho_total():
    return sum(a[4] for a in ARQUIVOS)


def _escrever_config(pasta):
    """transformer/config.json não existe no espelho GGUF: é gerado aqui (pequeno, sem download)."""
    arq = os.path.join(pasta, "transformer", "config.json")
    if os.path.isfile(arq):
        try:
            with open(arq, encoding="utf-8") as f:
                if json.load(f) == CONFIG_TRANSFORMER:
                    return arq
        except (OSError, ValueError):
            pass
    os.makedirs(os.path.dirname(arq), exist_ok=True)
    with open(arq, "w", encoding="utf-8") as f:
        json.dump(CONFIG_TRANSFORMER, f, indent=2)
    return arq


def faltando(pasta):
    """Arquivos que ainda faltam (ou estão com tamanho errado)."""
    out = []
    for dest, _repo, _rev, _cam, tam, _sha in ARQUIVOS:
        final = os.path.join(pasta, *dest.split("/"))
        if not (os.path.isfile(final) and os.path.getsize(final) == tam):
            out.append(dest)
    return out


def instalado(pasta):
    return bool(pasta) and os.path.isdir(pasta) and not faltando(pasta)


def verificar(pasta, ao_progresso=None):
    """Confere o sha256 dos arquivos grandes (uns 25 GB de leitura: ~1-2 min num NVMe).
    Retorna a lista de arquivos com conteúdo errado."""
    ruins = []
    for dest, _repo, _rev, _cam, tam, sha in ARQUIVOS:
        if not sha:
            continue
        final = os.path.join(pasta, *dest.split("/"))
        h = hashlib.sha256()
        try:
            with open(final, "rb") as f:
                while True:
                    b = f.read(16 * 2**20)
                    if not b:
                        break
                    h.update(b)
        except OSError:
            ruins.append(dest)
            continue
        if h.hexdigest() != sha:
            ruins.append(dest)
        if ao_progresso:
            ao_progresso(dest)
    return ruins


def _parado(cancelado):
    if cancelado is None:
        return False
    if isinstance(cancelado, threading.Event):
        return cancelado.is_set()
    return bool(cancelado())


def baixar(pasta, ao_progresso=None, cancelado=None, pasta_4b=None):
    """Baixa (ou copia do 4B instalado) tudo o que falta para `pasta`. Retomável: pedaços de 64 MB em
    paralelo, gravados em <arquivo>.part com o estado em <arquivo>.part.json; se cair a internet ou
    fechar o programa, continua de onde parou.

    ao_progresso(feito, total, velocidade_bps, arquivo) ~4x por segundo.
    cancelado: threading.Event ou função sem argumentos que retorna True para parar (levanta Cancelado).
    pasta_4b: pasta do FLUX.2 klein 4B; o vae/tokenizer/scheduler idênticos são copiados de lá.
    Nenhum token é enviado: todos os repositórios usados são públicos."""
    import requests

    ao_progresso = ao_progresso or (lambda *a: None)
    if pasta_4b is None:
        try:
            from . import config
            pasta_4b = config.pasta_modelo("flux2-klein-4b")
        except Exception:  # noqa: BLE001 - sem config: baixa tudo
            pasta_4b = None
    os.makedirs(pasta, exist_ok=True)
    trava = threading.Lock()
    parar = threading.Event()
    progresso = {"feito": 0, "atual": ""}
    total = tamanho_total()
    tarefas, finais = [], []

    for dest, repo, rev, cam, tam, _sha in ARQUIVOS:
        final = os.path.join(pasta, *dest.split("/"))
        os.makedirs(os.path.dirname(final), exist_ok=True)
        if os.path.isfile(final) and os.path.getsize(final) == tam:
            progresso["feito"] += tam
            continue
        if repo == REPO_4B and pasta_4b:
            origem = os.path.join(pasta_4b, *cam.split("/"))
            if os.path.isfile(origem) and os.path.getsize(origem) == tam:
                shutil.copyfile(origem, final + ".part")
                os.replace(final + ".part", final)
                progresso["feito"] += tam
                continue
        parte, arq_estado = final + ".part", final + ".part.json"
        estado = set()
        if os.path.isfile(parte) and os.path.getsize(parte) == tam and os.path.isfile(arq_estado):
            try:
                with open(arq_estado) as f:
                    estado = set(json.load(f))
            except (OSError, ValueError):
                estado = set()
        else:
            with open(parte, "wb") as f:
                # seek + 1 byte: no Windows, truncate() estende escrevendo zeros em laço (~60 MB/s)
                f.seek(tam - 1)
                f.write(b"\0")
        url = _url(repo, rev, cam)
        for ini in range(0, tam, PEDACO):
            fim = min(ini + PEDACO, tam) - 1
            if ini in estado:
                progresso["feito"] += fim - ini + 1
            else:
                tarefas.append((url, dest, parte, ini, fim, estado, arq_estado))
        finais.append((final, parte, arq_estado))

    sessao = requests.Session()
    sessao.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=TRABALHADORES * 2))
    cab_base = {"User-Agent": "FluxEstudio/1.0"}

    def somar(n):
        with trava:
            progresso["feito"] += n

    def pedaco(url, dest, parte, ini, fim, estado, arq_estado):
        for tentativa in range(6):
            recebidos = 0
            try:
                with sessao.get(url, headers=dict(cab_base, Range=f"bytes={ini}-{fim}"),
                                stream=True, timeout=(15, 60)) as r:
                    if r.status_code not in (200, 206):
                        raise IOError(f"HTTP {r.status_code} em {dest}")
                    if r.status_code == 200 and (ini > 0 or fim - ini + 1 != int(r.headers.get("Content-Length", -1))):
                        raise IOError(f"servidor ignorou o Range em {dest}")
                    with open(parte, "r+b") as f:
                        f.seek(ini)
                        for bloco in r.iter_content(1024 * 1024):
                            if parar.is_set():
                                raise Cancelado()
                            f.write(bloco)
                            recebidos += len(bloco)
                            somar(len(bloco))
                            progresso["atual"] = dest
                if recebidos != fim - ini + 1:
                    raise IOError(f"pedaço incompleto em {dest}")
                with trava:
                    estado.add(ini)
                    with open(arq_estado, "w") as f:
                        json.dump(sorted(estado), f)
                return
            except Cancelado:
                somar(-recebidos)
                raise
            except Exception:  # noqa: BLE001 - tenta de novo
                somar(-recebidos)
                if tentativa == 5 or parar.is_set():
                    raise
                time.sleep(2 * (tentativa + 1))

    parar_relogio = threading.Event()

    def relogio():
        janela = deque(maxlen=12)
        while not parar_relogio.wait(0.25):
            if _parado(cancelado):
                parar.set()
            janela.append((time.time(), progresso["feito"]))
            vel = 0.0
            if len(janela) > 1 and janela[-1][0] > janela[0][0]:
                vel = (janela[-1][1] - janela[0][1]) / (janela[-1][0] - janela[0][0])
            ao_progresso(progresso["feito"], total, max(vel, 0.0), progresso["atual"])

    t_rel = threading.Thread(target=relogio, daemon=True)
    t_rel.start()
    try:
        with ThreadPoolExecutor(TRABALHADORES) as ex:
            futuros = [ex.submit(pedaco, *t) for t in tarefas]
            for fu in futuros:
                try:
                    fu.result()
                except BaseException:
                    parar.set()
                    for outro in futuros:
                        outro.cancel()
                    raise
    finally:
        parar_relogio.set()
        t_rel.join()

    for final, parte, arq_estado in finais:
        os.replace(parte, final)
        try:
            os.remove(arq_estado)
        except OSError:
            pass
    _escrever_config(pasta)
    ao_progresso(total, total, 0.0, "")
    return pasta


class _Arena:
    """Memória em blocos grandes (fixada/pinned quando `fixar=True`), repartida entre muitos tensores.
    O alocador de memória fixada do PyTorch arredonda cada pedido para a potência de 2 seguinte
    (96 MB viram 128 MB, a tabela de embeddings de 1,19 GB vira 2 GB): fixando tensor a tensor, os
    11,2 GB do Qwen3 aparado ocupavam ~18 GB. Em blocos de potência de 2, a perda é só o fim de cada bloco.
    Em PCs com pouca RAM livre ou no ROCm (sem streams assíncronos), `fixar=False` usa RAM paginável normal."""

    def __init__(self, tam_bloco, fixar=True):
        self.tam = tam_bloco
        self.fixar = bool(fixar)
        self.blocos = []
        self.pos = 0

    def _alocar(self, forma, dtype):
        import torch
        if self.fixar:
            try:
                return torch.empty(forma, dtype=dtype, device="cpu", pin_memory=True)
            except RuntimeError:
                # Se o Windows recusar fixar mais páginas físicas, cai para RAM comum sem falhar.
                self.fixar = False
        return torch.empty(forma, dtype=dtype, device="cpu")

    def reservar(self, n):
        """Um pedaço uint8 de `n` bytes (alinhado a 512) para a leitura direta do disco escrever nele."""
        import torch
        if n > self.tam:
            return self._alocar((n,), torch.uint8)
        if not self.blocos or self.pos + n > self.tam:
            self.blocos.append(self._alocar((self.tam,), torch.uint8))
            self.pos = 0
        out = self.blocos[-1][self.pos:self.pos + n]
        self.pos += (n + 511) // 512 * 512
        return out

    def copiar(self, t):
        import torch
        n = t.numel() * t.element_size()
        if n > self.tam:
            out = self._alocar(t.shape, t.dtype)
            out.copy_(t)
            return out
        if not self.blocos or self.pos + n > self.tam:
            self.blocos.append(self._alocar((self.tam,), torch.uint8))
            self.pos = 0
        out = self.blocos[-1][self.pos:self.pos + n].view(t.dtype).view(t.shape)
        self.pos += (n + 511) // 512 * 512
        out.copy_(t)
        return out


def _quantizar(peso, grupo=128, linhas_por_vez=8192):
    """int8 simétrico em grupos de `grupo` colunas por linha. Devolve (q int8 [L, C], escala [L, C/grupo]).
    Faz a conta em fatias de linhas para não precisar de uma cópia float32 inteira (a tabela de
    embeddings em float32 teria 2,3 GB) ao lado do transformer na placa."""
    import torch
    linhas, colunas = peso.shape
    g = grupo if colunas % grupo == 0 else colunas
    q = torch.empty((linhas, colunas), dtype=torch.int8, device=peso.device)
    esc = torch.empty((linhas, colunas // g), dtype=peso.dtype, device=peso.device)
    for i in range(0, linhas, linhas_por_vez):
        w = peso[i:i + linhas_por_vez].float().view(-1, colunas // g, g)
        s = w.abs().amax(-1, keepdim=True).clamp_(min=1e-12) / 127
        q[i:i + linhas_por_vez] = w.div_(s).round_().clamp_(-127, 127).to(torch.int8).view(-1, colunas)
        esc[i:i + linhas_por_vez] = s.squeeze(-1).to(peso.dtype)
    return q, esc


def _classes_int8():
    """(LinearInt8, EmbeddingInt8), criadas sob demanda (torch só é importado ao abrir). Peso int8 em
    grupos de 128 por linha + escala bf16 por grupo; fica fixado na RAM e vai para a placa assim (metade
    dos bytes do bf16); lá é desquantizado na hora do uso. Subclasses de Linear/Embedding para o group
    offload tratar cada uma como folha. Erro relativo nos embeddings do klein ~1,4% (Qwen3-8B)."""
    global _CLASSES_INT8
    if _CLASSES_INT8 is not None:
        return _CLASSES_INT8
    import torch
    import torch.nn.functional as F

    class LinearInt8(torch.nn.Linear):
        def __init__(self, peso, arena):
            torch.nn.Module.__init__(self)
            self.out_features, self.in_features = peso.shape
            q, s = _quantizar(peso)
            self.grupo = self.in_features // s.shape[1]
            self.weight = torch.nn.Parameter(arena.copiar(q), requires_grad=False)
            self.escala = torch.nn.Parameter(arena.copiar(s), requires_grad=False)
            self.bias = None   # o Qwen3 não usa bias nas camadas lineares

        def forward(self, x):
            w = self.weight.view(self.out_features, -1, self.grupo).to(x.dtype) * self.escala.unsqueeze(-1)
            return F.linear(x, w.view(self.out_features, self.in_features))

    class EmbeddingInt8(torch.nn.Embedding):
        def __init__(self, peso, arena):
            torch.nn.Module.__init__(self)
            self.num_embeddings, self.embedding_dim = peso.shape
            self.padding_idx, self.max_norm, self.norm_type = None, None, 2.0
            self.scale_grad_by_freq, self.sparse = False, False
            q, s = _quantizar(peso)
            self.grupo = self.embedding_dim // s.shape[1]
            self.weight = torch.nn.Parameter(arena.copiar(q), requires_grad=False)
            self.escala = torch.nn.Parameter(arena.copiar(s), requires_grad=False)

        def forward(self, ids):
            q = F.embedding(ids, self.weight)
            s = F.embedding(ids, self.escala)
            forma = q.shape
            return (q.view(*forma[:-1], -1, self.grupo).to(s.dtype) * s.unsqueeze(-1)).view(forma)

    _CLASSES_INT8 = (LinearInt8, EmbeddingInt8)
    return _CLASSES_INT8


_CLASSES_INT8 = None


def descarregar_te(te, dev, usar_stream):
    """Codificador de texto mora na RAM e passa pela placa camada a camada na hora de ler o prompt.
    Aplicado em `te.model`, não em `te`: com `block_modules=["model"]` e streams, o diffusers põe um gancho
    "preguiçoso" em `te` (por causa do lm_head=Identity) e outro em `te.model`, e o segundo estoura
    "layer_execution_tracker already exists" — o modelo nunca abria com RAM folgada.
    `low_cpu_mem_usage=True`: os pesos já estão em RAM fixada (_Arena); sem isso o diffusers chamava
    pin_memory() em cada um de novo, o que às vezes falhava com "CUDA error: resource already mapped".
    Se ainda assim falhar, tira os ganchos pela metade e usa o modo sem streams (um pouco mais lento)."""
    import logging
    from diffusers.hooks import apply_group_offloading

    def aplicar(stream):
        apply_group_offloading(te.model, onload_device=dev, offload_type="block_level",
                               num_blocks_per_group=1 if stream else 2, use_stream=stream, low_cpu_mem_usage=stream)

    if not usar_stream:
        return aplicar(False)
    try:
        aplicar(True)
    except RuntimeError:
        logging.getLogger("klein9b").warning("offload com streams falhou; sem streams", exc_info=True)
        for sub in te.model.modules():
            reg = getattr(sub, "_diffusers_hook", None)
            for nome in list(getattr(reg, "hooks", {})):
                reg.remove_hook(nome, recurse=False)
        aplicar(False)


def carregar_qwen3_aparado(pasta_te, dtype, device=None, int8=False, fixar=None, cancelado=None):
    """Monta o Qwen3 (4B ou 8B) aparado tensor a tensor: disco (safetensors) -> [int8] -> RAM em blocos.
    Nunca existe uma cópia inteira extra na RAM comum, e só lê as camadas 0-27 (o klein lê as saídas
    9, 18 e 27; o lm_head e as camadas 28-35 nem saem do disco — resultado idêntico bit a bit)."""
    import torch
    from accelerate import init_empty_weights
    from safetensors import safe_open
    from transformers import AutoConfig, Qwen3ForCausalLM
    from . import dispositivo

    if fixar is None:
        fixar = dispositivo.suporta_streams_assincronos() and dispositivo.ram_disponivel_gb() >= (9.5 if int8 else 11.0)

    cfg = AutoConfig.from_pretrained(pasta_te)
    cfg.num_hidden_layers = CAMADAS_TE
    if getattr(cfg, "layer_types", None):
        cfg.layer_types = cfg.layer_types[:CAMADAS_TE]
    with init_empty_weights():
        te = Qwen3ForCausalLM._from_config(cfg, dtype=dtype)
    te.lm_head = torch.nn.Identity()   # o klein só lê hidden_states; logits nunca são usados
    te.eval()
    L8, E8 = _classes_int8() if int8 else (None, None)
    maior = cfg.vocab_size * cfg.hidden_size * (1 if int8 else torch.tensor([], dtype=dtype).element_size())
    bloco = 256 * 2**20
    while bloco < maior:
        bloco *= 2
    arena = _Arena(bloco, fixar=fixar)

    # Se int8=True e houver >= 2,8 GB livres na placa, quantiza na placa (muito rápido);
    # se a placa estiver apertada (ou int8=False), lê direto na CPU sem tocar na VRAM.
    dev_quant = torch.device("cpu")
    if int8 and device is not None and torch.device(device).type != "cpu":
        mem = dispositivo.memoria_info()
        if mem is None or (mem[1] - mem[0]) >= 2.8 * 2**30:
            dev_quant = torch.device(device)

    arq_indice = os.path.join(pasta_te, "model.safetensors.index.json")
    precisa = dict(te.named_parameters())
    por_arquivo = {}
    if os.path.isfile(arq_indice):
        with open(arq_indice, encoding="utf-8") as f:
            mapa = json.load(f)["weight_map"]
        for nome in precisa:
            por_arquivo.setdefault(mapa[nome], []).append(nome)
    else:
        por_arquivo["model.safetensors"] = list(precisa)

    feitos = set()
    if not int8:
        # bf16 sem quantizar: cada tensor sai do disco direto para o seu lugar no bloco (sem cópia no meio).
        from . import leitura
        for arq, nomes in por_arquivo.items():
            lidos = leitura.para_ram(os.path.join(pasta_te, arq), nomes, arena.reservar, dtype, cancelado)
            for nome, t in lidos.items():
                dono_nome, _, folha = nome.rpartition(".")
                te.get_submodule(dono_nome)._parameters[folha] = torch.nn.Parameter(t, requires_grad=False)
                feitos.add(nome)
    with torch.no_grad():
        for arq, nomes in ({} if not int8 else por_arquivo).items():
            with safe_open(os.path.join(pasta_te, arq), framework="pt", device="cpu") as f:
                for nome in nomes:
                    bruto = f.get_tensor(nome)
                    t = bruto.to(device=dev_quant, dtype=dtype) if (int8 or bruto.dtype != dtype) else bruto
                    dono_nome, _, folha = nome.rpartition(".")
                    dono = te.get_submodule(dono_nome)
                    pai, _, filho = dono_nome.rpartition(".")
                    if int8 and folha == "weight" and type(dono) is torch.nn.Linear:
                        setattr(te.get_submodule(pai), filho, L8(t, arena))
                    elif int8 and folha == "weight" and type(dono) is torch.nn.Embedding:
                        setattr(te.get_submodule(pai), filho, E8(t, arena))
                    else:
                        dono._parameters[folha] = torch.nn.Parameter(arena.copiar(t), requires_grad=False)
                    feitos.add(nome)
                    del t, bruto
    falta = [n for n in precisa if n not in feitos]
    if falta:
        raise RuntimeError(f"pesos do codificador de texto não encontrados: {falta[:3]}")
    if any(p.device.type == "meta" for p in te.parameters()):
        raise RuntimeError("codificador de texto montado pela metade (parâmetros em meta)")
    return te


def _carregar_te(pasta_te, dtype, device, int8):
    return carregar_qwen3_aparado(pasta_te, dtype, device=device, int8=int8)


def liberar_ram_fixada():
    """Depois de soltar o pipeline: devolve ao Windows a memória fixada que o PyTorch guarda em cache
    (sem isso, os ~6-12 GB do codificador de texto continuam presos ao processo)."""
    from . import dispositivo
    dispositivo.limpar_memoria(liberar_fixada=True)


def _ler_gguf(caminho):
    """Estado do transformer lido do GGUF sem copiar para a RAM: cada tensor é uma janela do arquivo
    mapeado (mmap) e vai direto para a placa. (O load_gguf_checkpoint do diffusers faz .copy() de tudo:
    9,3 GB de RAM comum + as páginas do arquivo, ~19 GB de pico no processo.)"""
    import warnings

    import gguf
    import torch
    from diffusers.quantizers.gguf.utils import SUPPORTED_GGUF_QUANT_TYPES, GGUFParameter

    leitor = gguf.GGUFReader(caminho)
    normais = (gguf.GGMLQuantizationType.F32, gguf.GGMLQuantizationType.F16)
    estado = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # "array não gravável": só é lido
        for t in leitor.tensors:
            if t.tensor_type not in normais and t.tensor_type not in SUPPORTED_GGUF_QUANT_TYPES:
                raise ValueError(f"{t.name}: tipo GGUF {t.tensor_type!r} não suportado pelo diffusers")
            dados = torch.from_numpy(t.data)
            estado[t.name] = dados if t.tensor_type in normais else GGUFParameter(dados, quant_type=t.tensor_type)
    return estado


def limitar_vram(reserva_gb=3.0):
    """Faz o PyTorch nunca passar de (VRAM total - reserva_gb), respeitando também o que outros programas ocupam."""
    from . import dispositivo
    return dispositivo.limitar_vram(reserva_gb)


def abrir(pasta, dtype=None, te_int8=True, reserva_vram_gb=3.0):
    """Abre o 9B em modo rápido e devolve um Flux2KleinPipeline pronto (sem barra de progresso).
    A máscara usa os mesmos pesos: diffusers.Flux2KleinInpaintPipeline(**pipe.components).

    Na placa: transformer Q8_0 (~9,3 GB) + VAE (~0,16 GB) + ativações (pico ~12 GB alocados na edição
    1024² com 1 referência). Na RAM: Qwen3-8B aparado (28 camadas, sem lm_head), ~5,7 GB em int8
    (te_int8=True; embeddings com erro relativo ~1,4%, imagens com a mesma composição) ou ~11,2 GB em bf16.
    reserva_vram_gb: deixada livre para o Windows/desktop/Photoshop (ver limitar_vram); None = sem teto.
    Ao fechar: soltar o pipeline e chamar liberar_ram_fixada()."""
    import diffusers
    import torch
    from diffusers import GGUFQuantizationConfig
    from . import dispositivo
    from .i18n import tr

    dispositivo.otimizar_torch()
    if not dispositivo.tem_gpu():
        raise RuntimeError(tr("O FLUX.2 klein 9B precisa de uma placa de vídeo (NVIDIA, AMD ou Intel Arc)."))
    _nome, _bk, total_gb, livre_gb = dispositivo.info()
    if total_gb < 14.5:
        raise RuntimeError(tr("O {nome} precisa de uma placa de 16 GB.", nome=NOME))
    if livre_gb < 12.3:  # pico medido ~12 GB numa edição 1024²
        raise RuntimeError(tr(
            "A placa tem {livre} GB livres agora (o 9B precisa de pelo menos ~12,5 GB livres). "
            "Feche outros programas que estejam usando a placa de vídeo ou use o FLUX.2 4B.",
            livre=f"{livre_gb:.1f}",
        ))
    dtype = dtype or dispositivo.escolher_dtype("bfloat16")
    falta = faltando(pasta)
    if falta:
        raise RuntimeError(f"O {NOME} não está completo em {pasta}: faltam {', '.join(falta[:3])}"
                           + ("…" if len(falta) > 3 else ""))
    _escrever_config(pasta)
    dev = dispositivo.dispositivo_torch()
    usar_stream = dispositivo.suporta_streams_assincronos() and dispositivo.ram_disponivel_gb() >= 9.5
    transformer = vae = te = None
    try:
        if reserva_vram_gb is not None:
            limitar_vram(reserva_vram_gb)

        # 1) Transformer GGUF: do arquivo mapeado (mmap) direto para a placa, tensor a tensor.
        estado = _ler_gguf(os.path.join(pasta, "transformer", ARQ_GGUF))
        transformer = diffusers.Flux2Transformer2DModel.from_single_file(
            estado, config=os.path.join(pasta, "transformer"),
            quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
            torch_dtype=dtype, device=dev, local_files_only=True)
        del estado
        transformer.to(dev)
        vae = diffusers.AutoencoderKLFlux2.from_pretrained(
            os.path.join(pasta, "vae"), torch_dtype=dtype, local_files_only=True).to(dev)
        if hasattr(vae, "enable_tiling"):
            vae.enable_tiling()
        if hasattr(vae, "enable_slicing"):
            vae.enable_slicing()

        # 2) Qwen3-8B: só as 28 primeiras camadas, tensor a tensor, direto para blocos na RAM.
        te = carregar_qwen3_aparado(os.path.join(pasta, "text_encoder"), dtype, device=dev,
                                    int8=te_int8, fixar=usar_stream)
        dispositivo.limpar_memoria()
        pipe = diffusers.Flux2KleinPipeline.from_pretrained(
            pasta, text_encoder=te, transformer=transformer, vae=vae, torch_dtype=dtype, local_files_only=True)
        descarregar_te(te, dev, usar_stream)
        pipe.set_progress_bar_config(disable=True)
        return pipe
    except BaseException:
        del transformer, vae, te
        dispositivo.restaurar_vram()
        liberar_ram_fixada()
        raise


def _main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modelos", ID)
    if "--listar" in sys.argv:
        for dest, tam, url in arquivos_necessarios():
            print(f"{tam / 2**20:10.1f} MB  {dest:50s} {url}")
        print(f"total {tamanho_total() / 2**30:.2f} GB  ->  {pasta}")
        return
    if "--verificar" in sys.argv:
        print("faltando:", faltando(pasta))
        print("sha256 errado:", verificar(pasta, lambda d: print("ok?", d, flush=True)))
        return
    if "--baixar" in sys.argv:
        def mostrar(feito, total, vel, arq):
            pct = 100 * feito / total if total else 0
            print(f"\r{pct:5.1f}%  {feito / 2**30:6.2f}/{total / 2**30:.2f} GB  {vel / 2**20:6.1f} MB/s  {arq[-48:]:<48}",
                  end="", flush=True)
        parar = threading.Event()
        try:
            baixar(pasta, mostrar, parar)
        except KeyboardInterrupt:
            parar.set()
            print("\ncancelado (dá para continuar depois)")
            return
        print(f"\npronto: {pasta}  completo={instalado(pasta)}")
        return
    print(f"{NOME}: instalado={instalado(pasta)} em {pasta}")


if __name__ == "__main__":
    _main()
