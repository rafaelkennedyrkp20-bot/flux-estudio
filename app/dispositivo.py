"""Detecção e gerenciamento unificado de placa de vídeo (NVIDIA CUDA, AMD ROCm, Intel Arc XPU) e CPU.

Mantém o topo leve (só biblioteca padrão): o `torch` só é importado quando alguma função de
execução é chamada, para a janela abrir instantaneamente.
"""
import ctypes
import gc
import logging
import os

log = logging.getLogger("dispositivo")

_torch_otimizado = False


def configurar_ambiente(escolher_placa=True):
    """Variáveis de ambiente seguras para AMD (ROCm), NVIDIA (CUDA) e Intel antes de importar o torch.
    escolher_placa: em AMD com vídeo integrado, deixa o PyTorch ver só a placa dedicada (ver _escolher_placa_amd).
    A janela passa False (não importa o torch); o processo do motor e o autoteste escolhem."""
    # AMD ROCm no Windows: evita busca exaustiva de kernels na 1ª convolução (MIOpen),
    # travamentos de hipBLASLt (RX 9070) e resets de driver (TDR) em transferências DMA pesadas.
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    os.environ.setdefault("TORCH_BLAS_PREFER_HIPBLASLT", "0")
    os.environ.setdefault("HSA_ENABLE_SDMA", "0")
    os.environ.setdefault("PYTORCH_HIP_ALLOC_CONF", "garbage_collection_threshold:0.8,max_split_size_mb:512")

    # NVIDIA CUDA no Windows (WDDM): carregamento preguiçoso de módulos economiza ~300-600 MB de VRAM
    # e acelera a inicialização; coletor evita fragmentação antes do fallback para a RAM do sistema.
    os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "garbage_collection_threshold:0.8,max_split_size_mb:512")

    # Evita avisos/deadlocks de threads do tokenizers e telemetria desnecessária.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if escolher_placa:
        _escolher_placa_amd()


# Roda num processo à parte: listar as placas não executa nenhum kernel, então funciona mesmo com o
# vídeo integrado visível (é justamente o kernel que falha).
_SONDA = r"""
import json, torch
arch = sorted({a.split(":")[0] for a in torch.cuda.get_arch_list()})
placas = []
for i in range(torch.cuda.device_count()):
    p = torch.cuda.get_device_properties(i)
    placas.append([i, p.name, getattr(p, "gcnArchName", "").split(":")[0], p.total_memory])
print(json.dumps({"arch": arch, "placas": placas}))
"""


def _escolher_placa_amd():
    """Ryzen com vídeo integrado (ex.: 9950X, gfx1036) + Radeon dedicada (ex.: RX 9070 XT, gfx1201) no Windows:
    com as duas visíveis, o ROCm falha em todo kernel com "invalid kernel file" (o pacote do PyTorch só tem
    kernels da dedicada). Descobre uma vez qual índice é a placa suportada e mais forte e usa
    HIP_VISIBLE_DEVICES=<índice>. Guarda em config.json; refaz se a versão do PyTorch mudar."""
    if os.name != "nt" or os.environ.get("HIP_VISIBLE_DEVICES") is not None:
        return
    try:
        from importlib.metadata import version
        versao_torch = version("torch")
    except Exception:  # noqa: BLE001
        return
    if "rocm" not in versao_torch:
        return
    import json
    import subprocess
    import sys
    from . import config
    salvo = config.carregar().get("placa_amd") or {}
    if salvo.get("torch") != versao_torch:
        py = sys.executable.replace("pythonw.exe", "python.exe")
        try:
            r = subprocess.run([py, "-c", _SONDA], capture_output=True, text=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            d = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            log.warning("não deu para listar as placas AMD", exc_info=True)
            return
        placas, visivel = d["placas"], ""
        if len(placas) > 1:
            suportadas = [p for p in placas if p[2] in d["arch"]] or placas
            visivel = str(max(suportadas, key=lambda p: p[3])[0])
        salvo = {"torch": versao_torch, "visivel": visivel, "placas": [f"{p[1]} ({p[2]})" for p in placas]}
        config.salvar({"placa_amd": salvo})
        log.info("placas AMD: %s · arch do PyTorch: %s · usando: %s", salvo["placas"], d["arch"], visivel or "todas")
    if salvo.get("visivel"):
        os.environ["HIP_VISIBLE_DEVICES"] = salvo["visivel"]


def nucleos_fisicos():
    """Contagem de núcleos físicos (evita saturar E-cores da Intel ou travar a interface)."""
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return max(2, n)
    except Exception:  # noqa: BLE001
        pass
    return max(2, (os.cpu_count() or 4) // 2)


def ram_disponivel_gb():
    """RAM livre real no Windows (em GB)."""
    try:
        import psutil
        return psutil.virtual_memory().available / 2**30
    except Exception:  # noqa: BLE001
        try:
            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPhys / 2**30
        except Exception:  # noqa: BLE001
            pass
    return 8.0


def otimizar_torch():
    """Ajustes globais de velocidade e estabilidade aplicados uma única vez após importar o torch."""
    global _torch_otimizado
    if _torch_otimizado:
        return
    _torch_otimizado = True
    import torch

    # Deixa folga para a interface gráfica e o sistema operacional em CPUs Intel (P+E cores) e AMD.
    threads = min(12, max(2, nucleos_fisicos()))
    try:
        torch.set_num_threads(threads)
    except Exception:  # noqa: BLE001
        pass

    try:
        torch.set_float32_matmul_precision("high")
    except Exception:  # noqa: BLE001
        pass

    if torch.cuda.is_available():
        try:
            if not eh_rocm():
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            # Desliga benchmark dinâmico: tamanhos variados de imagem faziam o cuDNN/MIOpen
            # realocar gigabytes e recompilar planos a cada resolução nova.
            torch.backends.cudnn.benchmark = False
        except Exception:  # noqa: BLE001
            pass


def eh_rocm():
    import torch
    return bool(getattr(torch.version, "hip", None))


def tipo():
    """'cuda' (NVIDIA CUDA ou AMD ROCm), 'xpu' (Intel Arc) ou 'cpu'."""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu"
    return "cpu"


def tem_gpu():
    return tipo() in ("cuda", "xpu")


def dispositivo_torch():
    import torch
    return torch.device(tipo())


def suporta_streams_assincronos():
    """Streams paralelos com memória fixada: estáveis e rápidos em NVIDIA CUDA com RAM folgada."""
    return tipo() == "cuda" and not eh_rocm() and ram_disponivel_gb() >= 10.0


def escolher_dtype(preferido="bfloat16"):
    """Escolhe o tipo numérico seguro para a placa/processador atual."""
    import torch
    t = tipo()
    if t == "cpu":
        # Em CPUs x86 modernas o PyTorch/OneDNN roda bfloat16 gastando metade da RAM do float32;
        # modelos float16 caem para float32 na CPU porque conv2d fp16 em CPU falha em várias versões.
        return torch.bfloat16 if preferido == "bfloat16" else torch.float32
    if preferido == "float16":
        return torch.float16
    if t == "cuda":
        try:
            # Em placas Turing (GTX 16xx / RTX 20xx) sem suporte nenhum a bf16 em kernels antigos,
            # checa inclusive emulação; o PyTorch 2.x suporta bf16 via CUTLASS sem estourar como fp16.
            sig = torch.cuda.is_bf16_supported
            ok = sig()
            if not ok:
                try:
                    ok = sig(including_emulation=True)
                except TypeError:
                    ok = False
            return torch.bfloat16 if ok else torch.float16
        except Exception:  # noqa: BLE001
            return torch.bfloat16
    return getattr(torch, preferido, torch.bfloat16)


def info():
    """Retorna (nome_placa, backend_str, vram_total_gb, vram_livre_gb)."""
    import torch
    otimizar_torch()
    t = tipo()
    if t == "cuda":
        backend = "ROCm" if eh_rocm() else "CUDA"
        nome = torch.cuda.get_device_name(0)
        prop = torch.cuda.get_device_properties(0)
        total = prop.total_memory / 2**30
        try:
            livre = torch.cuda.mem_get_info(0)[0] / 2**30
        except Exception:  # noqa: BLE001
            livre = total
        return nome, backend, total, livre
    if t == "xpu":
        nome = torch.xpu.get_device_name(0)
        prop = torch.xpu.get_device_properties(0)
        total = prop.total_memory / 2**30
        try:
            livre = (torch.xpu.mem_get_info(0)[0] / 2**30) if hasattr(torch.xpu, "mem_get_info") else total
        except Exception:  # noqa: BLE001
            livre = total
        return nome, "Intel XPU", total, livre
    compilacao = "ROCm" if eh_rocm() else ("XPU" if hasattr(torch, "xpu") and getattr(torch.version, "xpu", None) else "CUDA")
    return "", "CPU:" + compilacao, 0.0, 0.0


def memoria_info():
    """Retorna (usado_bytes, total_bytes) da placa, ou None se estiver em CPU.
    IMPORTANTE: chamar apenas fora da thread principal da interface (pode bloquear sob carga intensa)."""
    import torch
    t = tipo()
    if t == "cuda":
        livre, total = torch.cuda.mem_get_info(0)
        return total - livre, total
    if t == "xpu":
        if hasattr(torch.xpu, "mem_get_info"):
            livre, total = torch.xpu.mem_get_info(0)
            return total - livre, total
        total = torch.xpu.get_device_properties(0).total_memory
        usado = torch.xpu.memory_reserved(0) if hasattr(torch.xpu, "memory_reserved") else 0
        return usado, total
    return None


def limitar_vram(reserva_gb=1.8):
    """Define teto de memória no alocador do PyTorch para impedir que o Windows jogue o excesso na RAM
    compartilhada (WDDM sysmem fallback), o que deixaria cada passo 15-20x mais lento."""
    import torch
    if tipo() != "cuda":
        return 1.0
    try:
        livre, total = torch.cuda.mem_get_info(0)
        # Respeita tanto a reserva fixa quanto o que outros programas (Photoshop/navegador) já ocupam,
        # deixando ao menos ~700 MB de margem livre para o desktop não engasgar.
        alvo_teto = min(total - reserva_gb * 2**30, max(total * 0.5, livre - 0.7 * 2**30))
        fracao = max(0.5, min(0.96, alvo_teto / total))
        torch.cuda.set_per_process_memory_fraction(fracao, 0)
        return fracao
    except Exception:  # noqa: BLE001
        return 1.0


def restaurar_vram():
    import torch
    if tipo() == "cuda":
        try:
            torch.cuda.set_per_process_memory_fraction(1.0, 0)
        except Exception:  # noqa: BLE001
            pass


def sincronizar():
    import torch
    t = tipo()
    if t == "cuda":
        torch.cuda.synchronize()
    elif t == "xpu" and hasattr(torch.xpu, "synchronize"):
        torch.xpu.synchronize()


def limpar_memoria(liberar_fixada=False):
    """Recolhe lixo do Python e devolve caches de VRAM e (opcionalmente) RAM fixada (pinned) ao sistema."""
    gc.collect()
    try:
        import torch
        t = tipo()
        if t == "cuda":
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
            if liberar_fixada:
                esvaziar = getattr(torch._C, "_host_emptyCache", None)
                if esvaziar:
                    esvaziar()
        elif t == "xpu" and hasattr(torch.xpu, "empty_cache"):
            torch.xpu.empty_cache()
    except Exception:  # noqa: BLE001
        pass
