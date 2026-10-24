"""Leitura rápida de .safetensors, no estilo do ComfyUI: o arquivo é lido em sequência, em pedaços grandes,
direto para onde o peso vai morar (a placa de vídeo ou um bloco de RAM fixada), sem cópia no meio.

Por que não o `from_pretrained`: ele mapeia o arquivo inteiro na memória (mmap) e copia tensor a tensor.
Com a RAM apertada (Photoshop aberto + codificador de texto já na RAM), o Windows fica trocando páginas e
o transformer do klein 4B (7,4 GB) levava 85 s; lido assim leva ~3-7 s (o limite é o próprio disco)."""
import json
import os
import struct

_TIPOS = {"BF16": "bfloat16", "F16": "float16", "F32": "float32", "F64": "float64", "I64": "int64",
          "I32": "int32", "I16": "int16", "I8": "int8", "U8": "uint8", "BOOL": "bool",
          "F8_E4M3": "float8_e4m3fn", "F8_E5M2": "float8_e5m2"}
PEDACO = 64 * 2**20


def cabecalho(arq):
    """(início da área de dados, {nome: (dtype_torch, forma, ini, fim)}) com posições relativas aos dados."""
    import torch
    with open(arq, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        cab = json.loads(f.read(n))
    cab.pop("__metadata__", None)
    return 8 + n, {k: (getattr(torch, _TIPOS[v["dtype"]]), v["shape"], *v["data_offsets"]) for k, v in cab.items()}


def _ler_em(f, destino_u8, cancelado=None):
    """Lê len(destino_u8) bytes de `f` para um tensor uint8 da CPU, em pedaços (memoryview: sem cópia)."""
    mv = memoryview(destino_u8.numpy())
    pos, total = 0, len(mv)
    while pos < total:
        if cancelado and cancelado():
            raise InterruptedError
        k = f.readinto(mv[pos:pos + min(PEDACO, total - pos)])
        if not k:
            raise EOFError("arquivo do modelo terminou antes do esperado (download incompleto?)")
        pos += k


def montar_na_placa(classe, pasta, device, dtype, arquivo="diffusion_pytorch_model.safetensors", cancelado=None):
    """Cria o modelo diffusers vazio (sem alocar nada) e pendura os pesos lidos direto na placa."""
    from accelerate import init_empty_weights
    with init_empty_weights():
        m = classe.from_config(classe.load_config(pasta, local_files_only=True))
    # safetensors direto para a placa, ANTES do codificador de texto ocupar a RAM: ~7-10 s.
    # (Uma leitura própria em pedaços era ~3 s mais rápida, mas em 1 de cada 3-5 cargas o 1º kernel
    # falhava com "CUDA error: shared object initialization failed" na RTX 5060 Ti; esta passou 9 de 9.)
    from safetensors.torch import load_file
    sd = load_file(os.path.join(pasta, arquivo), device=str(device))
    if dtype is not None:
        sd = {k: (v.to(dtype) if v.is_floating_point() and v.dtype != dtype else v) for k, v in sd.items()}
    m.load_state_dict(sd, assign=True, strict=True)
    return m.to(device).eval()   # .to só move os buffers que nasceram na CPU (os pesos já estão lá)


def para_ram(arq, nomes, reservar, dtype=None, cancelado=None):
    """Lê só os tensores `nomes` de `arq`, na ordem em que estão no disco, direto para os blocos devolvidos
    por `reservar(nbytes)` (uint8 da CPU, p.ex. RAM fixada). Devolve {nome: tensor}."""
    import torch
    base, cab = cabecalho(arq)
    ordem = sorted(nomes, key=lambda n: cab[n][2])
    saida = {}
    with open(arq, "rb", buffering=0) as f:
        for nome in ordem:
            dt, forma, a, b = cab[nome]
            f.seek(base + a)
            if dtype is not None and dt.is_floating_point and dt != dtype:
                tmp = torch.empty(b - a, dtype=torch.uint8)
                _ler_em(f, tmp, cancelado)
                t = tmp.view(dt).view(forma).to(dtype)
                slot = reservar(t.numel() * t.element_size())
                slot.view(dtype).view(forma).copy_(t)
                saida[nome] = slot.view(dtype).view(forma)
            else:
                slot = reservar(b - a)
                _ler_em(f, slot, cancelado)
                saida[nome] = slot.view(dt).view(forma)
    return saida
