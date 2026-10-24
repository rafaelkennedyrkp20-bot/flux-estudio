"""Instalação e atualização do Flux Estúdio (usado pelo instalador e pelo botão "Atualizar").

Só biblioteca padrão do Python: roda dentro do instalador compilado, sem PyTorch nem nada.

Como fica a pasta instalada:
    FluxEstudio.py, app/, icone.ico, requisitos.txt, VERSAO   <- vêm do GitHub (trocados ao atualizar)
    .venv/            Python 3.12 + PyTorch (CUDA, ROCm, XPU ou CPU) + bibliotecas, criado pelo uv
    ferramentas/      uv.exe, Instalador.exe e o Python que o uv baixa
    modelos/ saidas/ config.json prompts.json preferencias.ini   <- do usuário, nunca apagados
    instalacao.json   o que foi instalado (placa, backend, gfx, versão)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

REPO = "rafaelkennedyrkp20-bot/flux-estudio"
RAMO = "main"
URL_ZIP = f"https://codeload.github.com/{REPO}/zip/refs/heads/{RAMO}"
URL_VERSAO = f"https://raw.githubusercontent.com/{REPO}/{RAMO}/VERSAO"
URL_UV = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
PYTHON = "3.12"
PYPI_URL = "https://pypi.org/simple"

# Arquivos e pastas do programa (vêm do repositório). O resto da pasta é do usuário.
DO_PROGRAMA = ("FluxEstudio.py", "app", "icone.ico", "requisitos.txt", "VERSAO", "README.md", "LICENSE",
               "Abrir Flux Estudio.bat", "Instalar.bat")

TORCH = {
    "cuda": dict(pacotes=["torch==2.11.0+cu128"], indice="https://download.pytorch.org/whl/cu128"),
    "rocm": dict(pacotes=["torch[device-{gfx}]==2.14.0+rocm10.1.0"], indice="https://stable.repo.amd.com/rocm/whl-next/"),
    "xpu": dict(pacotes=["torch==2.11.0+xpu"], indice="https://download.pytorch.org/whl/xpu"),
    "cpu": dict(pacotes=["torch==2.11.0"], indice="https://download.pytorch.org/whl/cpu"),
}

# Famílias AMD Radeon disponíveis para escolha automática ou manual no instalador
FAMILIAS_AMD = [
    ("gfx1201", "Radeon RX 9070 / 9070 XT (gfx1201)"),
    ("gfx1200", "Radeon RX 9060 / 9060 XT (gfx1200)"),
    ("gfx1100", "Radeon RX 7900 XT / XTX / GRE · PRO W7900/W7800 (gfx1100)"),
    ("gfx1101", "Radeon RX 7700 XT / 7800 XT · PRO W7700 (gfx1101)"),
    ("gfx1102", "Radeon RX 7600 / 7600 XT / 7650 GRE / 7600S (gfx1102)"),
    ("gfx1151", "Ryzen AI Max · Radeon 8060S / 8050S (gfx1151)"),
    ("gfx1150", "Ryzen AI 300 · Radeon 890M / 880M (gfx1150)"),
    ("gfx1103", "Ryzen 7040/8040 · Radeon 780M / 760M (gfx1103)"),
    ("gfx1030", "Radeon RX 6800 / 6900 / 6950 XT · PRO W6800 (gfx1030)"),
    ("gfx1031", "Radeon RX 6700 / 6700 XT / 6750 XT (gfx1031)"),
    ("gfx1032", "Radeon RX 6600 / 6600 XT / 6650 XT (gfx1032)"),
    ("gfx1034", "Radeon RX 6400 / 6500 XT (gfx1034)"),
    ("gfx1010", "Radeon RX 5500 / 5600 / 5700 XT (gfx1010)"),
]

# Radeon -> alvo do ROCm (pacote torch[device-gfxNNNN] da AMD)
_AMD_GFX = [
    (r"RX\s*90[789]0", "gfx1201"),
    (r"RX\s*90[56]0", "gfx1200"),
    (r"(RX\s*79\d0|W7[89]00)", "gfx1100"),
    (r"(RX\s*7[78]\d0\s*XT|RX\s*7800|W7700)", "gfx1101"),
    (r"(RX\s*76\d0|RX\s*7700S|W7[56]00)", "gfx1102"),
    (r"80[56]0S", "gfx1151"),
    (r"8[89]0M", "gfx1150"),
    (r"7[468]0M", "gfx1103"),
    (r"(RX\s*6[89]\d0|W6800)", "gfx1030"),
    (r"RX\s*67\d0", "gfx1031"),
    (r"(RX\s*66\d0|W6600)", "gfx1032"),
    (r"RX\s*6[45]\d0", "gfx1034"),
    (r"RX\s*5[567]\d0", "gfx1010"),
]

# Placas Intel Arc compatíveis com XPU
_INTEL_XPU = re.compile(r"(Arc\s*\(TM\)|Intel.*Arc\s+[AB]\d{3}|Arc\s+[AB]\d{3}|Arc\s+1[34]0[VT])", re.I)

_ADAPTADORES_VIRTUAIS = re.compile(
    r"(Microsoft Basic Display|Parsec|Virtual Desktop|Citrix|VMware|Hyper-V|RDPUDD|Indirect Display|LuminonCore)",
    re.I,
)

_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _int_memoria(mem):
    """Converte valor de VRAM do registro do Windows (int, REG_BINARY bytes, str) em inteiro de bytes."""
    if mem is None:
        return 0
    if isinstance(mem, (bytes, bytearray)):
        return int.from_bytes(mem[:8], "little", signed=False)
    if isinstance(mem, int):
        return max(0, mem)
    try:
        return max(0, int(mem))
    except (TypeError, ValueError):
        return 0


def _pnp_ativos():
    """Retorna conjunto de prefixos PNPDeviceID (ex.: 'pci\\ven_10de&dev_2d04') das placas fisicamente conectadas hoje.
    Se o PowerShell falhar ou demorar, retorna None (aceita tudo do registro)."""
    cmd = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_VideoController | "
        "? { $_.ConfigManagerErrorCode -eq 0 -or $_.Status -eq 'OK' } | "
        "Select-Object -ExpandProperty PNPDeviceID) | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                           capture_output=True, text=True, timeout=4,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        bruto = json.loads(r.stdout)
        itens = [bruto] if isinstance(bruto, str) else (bruto if isinstance(bruto, list) else [])
        ativos = set()
        for pnp in itens:
            m = re.search(r"ven_[0-9a-f]{4}&dev_[0-9a-f]{4}", str(pnp).lower())
            if m:
                ativos.add(m.group(0))
        return ativos or None
    except Exception:
        return None


# ------------------------------------------------------------------ placa de vídeo
def placas():
    """Placas de vídeo ativas no Windows: [{nome, fabricante, vram_gb, driver}]."""
    import winreg
    raiz = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    achadas = []
    ativos = _pnp_ativos()
    try:
        chave = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, raiz)
    except OSError:
        return achadas
    vistos = set()
    for i in range(64):
        try:
            sub = winreg.EnumKey(chave, i)
        except OSError:
            break
        if not sub.isdigit():
            continue
        try:
            k = winreg.OpenKey(chave, sub)
        except OSError:
            continue

        def ler(nome, padrao=None):
            try:
                return winreg.QueryValueEx(k, nome)[0]
            except OSError:
                return padrao

        nome = str(ler("DriverDesc") or "").strip()
        if not nome or _ADAPTADORES_VIRTUAIS.search(nome):
            continue
        dispositivo_id = str(ler("MatchingDeviceId") or "").lower()
        if ativos is not None:
            m = re.search(r"ven_[0-9a-f]{4}&dev_[0-9a-f]{4}", dispositivo_id)
            if m and m.group(0) not in ativos:
                continue
        mem = _int_memoria(ler("HardwareInformation.qwMemorySize"))
        if not mem:
            mem = _int_memoria(ler("HardwareInformation.MemorySize", 0))
        fab = ("nvidia" if "ven_10de" in dispositivo_id or "nvidia" in nome.lower() else
               "amd" if "ven_1002" in dispositivo_id or "radeon" in nome.lower() else
               "intel" if "ven_8086" in dispositivo_id or "intel" in nome.lower() else "outra")
        vram_gb = round(mem / 2**30, 1)
        chave_unica = (nome.lower(), fab, vram_gb)
        if chave_unica in vistos:
            continue
        vistos.add(chave_unica)
        achadas.append(dict(nome=nome, fabricante=fab, vram_gb=vram_gb, driver=str(ler("DriverVersion") or "")))
    return achadas


def _driver_nvidia(versao_windows):
    """'32.0.16.1742' -> 617.42 (versão do driver NVIDIA)."""
    num = re.sub(r"\D", "", str(versao_windows or ""))[-5:]
    return float(f"{num[:3]}.{num[3:]}") if len(num) == 5 else 0.0


def detectar_gfx_amd(nome_placa):
    """Retorna o código gfxNNNN para uma placa Radeon conhecida, ou None se não reconhecida."""
    for padrao, gfx in _AMD_GFX:
        if re.search(padrao, nome_placa or "", re.I):
            return gfx
    return None


def escolher_backend(lista=None):
    """Decide o que instalar. Retorna {backend: cuda/rocm/xpu/cpu, gfx, placa, vram_gb, aviso}."""
    lista = placas() if lista is None else lista
    nvidia = [p for p in lista if p["fabricante"] == "nvidia"]
    if nvidia:
        p = max(nvidia, key=lambda p: p["vram_gb"])
        aviso = ""
        drv = _driver_nvidia(p["driver"])
        if drv and drv < 570:
            aviso = "driver_antigo"
        return dict(backend="cuda", gfx=None, placa=p["nome"], vram_gb=p["vram_gb"], aviso=aviso)

    amd = sorted([p for p in lista if p["fabricante"] == "amd"], key=lambda p: -p["vram_gb"])
    for p in amd:
        gfx = detectar_gfx_amd(p["nome"])
        if gfx:
            return dict(backend="rocm", gfx=gfx, placa=p["nome"], vram_gb=p["vram_gb"], aviso="")

    intel = sorted([p for p in lista if p["fabricante"] == "intel"], key=lambda p: -p["vram_gb"])
    for p in intel:
        if _INTEL_XPU.search(p["nome"]) and (p["vram_gb"] >= 5.5 or "arc" in p["nome"].lower()):
            return dict(backend="xpu", gfx=None, placa=p["nome"], vram_gb=p["vram_gb"], aviso="")

    if amd:
        p = amd[0]
        return dict(backend="cpu", gfx="gfx1100", placa=p["nome"], vram_gb=p["vram_gb"], aviso="amd_desconhecida")

    outra = lista[0]["nome"] if lista else ""
    return dict(backend="cpu", gfx=None, placa=outra, vram_gb=0, aviso="sem_placa")


# ------------------------------------------------------------------ download
def baixar(url, destino, progresso=None, cancelado=None):
    """Baixa url -> destino. progresso(feito, total)."""
    req = urllib.request.Request(url, headers={"User-Agent": "FluxEstudio-Instalador"})
    part = destino + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            feito = 0
            while True:
                if cancelado and cancelado():
                    raise InterruptedError
                bloco = r.read(1024 * 256)
                if not bloco:
                    break
                f.write(bloco)
                feito += len(bloco)
                if progresso:
                    progresso(feito, total)
        os.replace(part, destino)
        return destino
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def versao_remota():
    req = urllib.request.Request(URL_VERSAO, headers={"User-Agent": "FluxEstudio", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode().strip()


def versao_local(pasta):
    try:
        with open(os.path.join(pasta, "VERSAO"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0"


def mais_nova(a, b):
    """a é mais nova que b? ('1.2.0' > '1.10.0' não: compara número a número)."""
    num = lambda v: [int(x) for x in re.findall(r"\d+", v)] or [0]
    return num(a) > num(b)


def instalar_programa(pasta, progresso=None, cancelado=None):
    """Baixa o programa do GitHub e põe na pasta, trocando só os arquivos do programa com troca segura.
    Retorna True se o requisitos.txt mudou (precisa reinstalar bibliotecas)."""
    os.makedirs(pasta, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fluxestudio_")
    try:
        zipado = baixar(URL_ZIP, os.path.join(tmp, "programa.zip"), progresso, cancelado)
        with zipfile.ZipFile(zipado) as z:
            z.extractall(tmp)
        raiz = next(os.path.join(tmp, d) for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d)))
        antes = _ler(os.path.join(pasta, "requisitos.txt"))
        for item in DO_PROGRAMA:
            origem, alvo = os.path.join(raiz, item), os.path.join(pasta, item)
            if not os.path.exists(origem):
                continue
            if os.path.isdir(origem):
                novo = alvo + ".novo"
                velho = alvo + ".velho"
                shutil.rmtree(novo, ignore_errors=True)
                shutil.rmtree(velho, ignore_errors=True)
                shutil.copytree(origem, novo)
                if os.path.isdir(alvo):
                    os.replace(alvo, velho)
                try:
                    os.replace(novo, alvo)
                except OSError:
                    if os.path.isdir(velho) and not os.path.exists(alvo):
                        os.replace(velho, alvo)  # devolve a versão anterior: nunca fica sem app/
                    raise
                shutil.rmtree(velho, ignore_errors=True)
            else:
                novo = alvo + ".novo"
                shutil.copy2(origem, novo)
                os.replace(novo, alvo)
        return antes != _ler(os.path.join(pasta, "requisitos.txt"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ler(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ------------------------------------------------------------------ Python e bibliotecas (uv)
def caminho_uv(pasta):
    return os.path.join(pasta, "ferramentas", "uv.exe")


def ambiente_uv(pasta):
    """Tudo do uv dentro da pasta do programa: desinstalar = apagar a pasta."""
    env = dict(os.environ)
    env.pop("UV_NO_PROGRESS", None)
    env.update(UV_PYTHON_INSTALL_DIR=os.path.join(pasta, "ferramentas", "python"),
               UV_CACHE_DIR=os.path.join(pasta, "ferramentas", "cache"),
               UV_LINK_MODE="copy", UV_HTTP_TIMEOUT="120", PYTHONUTF8="1")
    return env


def instalar_uv(pasta, progresso=None, cancelado=None):
    destino = caminho_uv(pasta)
    if os.path.isfile(destino):
        return destino
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fluxestudio_uv_")
    try:
        zipado = baixar(URL_UV, os.path.join(tmp, "uv.zip"), progresso, cancelado)
        with zipfile.ZipFile(zipado) as z:
            nome = next(n for n in z.namelist() if n.endswith("uv.exe"))
            with z.open(nome) as o, open(destino + ".part", "wb") as d:
                shutil.copyfileobj(o, d)
            os.replace(destino + ".part", destino)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return destino


def python_do_venv(pasta, janela=False):
    return os.path.join(pasta, ".venv", "Scripts", "pythonw.exe" if janela else "python.exe")


def programa_aberto(pasta):
    """Retorna True se o Python do `.venv` desta pasta estiver em execução agora."""
    if not pasta or not os.path.isdir(os.path.join(pasta, ".venv")):
        return False
    alvo = os.path.normcase(os.path.abspath(os.path.join(pasta, ".venv"))) + os.sep
    cmd = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Select-Object -ExpandProperty ExecutablePath) | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                           capture_output=True, text=True, timeout=4,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not (r.stdout or "").strip():
            return False
        bruto = json.loads(r.stdout)
        caminhos = [bruto] if isinstance(bruto, str) else (bruto if isinstance(bruto, list) else [])
        return any(c and os.path.normcase(os.path.abspath(str(c))).startswith(alvo) for c in caminhos)
    except Exception:
        return False


def comandos_instalacao(pasta, backend, gfx=None):
    """Lista de (descrição, comando) para criar o ambiente e instalar as bibliotecas sem conflito de índices."""
    uv = caminho_uv(pasta)
    venv = os.path.join(pasta, ".venv")
    py = python_do_venv(pasta)
    bk = backend if backend in TORCH else "cpu"
    t = TORCH[bk]
    gfx_ok = gfx or "gfx1201"
    pacotes = [p.format(gfx=gfx_ok) for p in t["pacotes"]]
    flags_indice = ["--index-url", t["indice"], "--extra-index-url", PYPI_URL, "--index-strategy", "unsafe-best-match"]
    return [
        ("python", [uv, "venv", venv, "--python", PYTHON, "--allow-existing"]),
        ("torch", [uv, "pip", "install", "--python", py, *flags_indice, *pacotes]),
        ("bibliotecas", [uv, "pip", "install", "--python", py, *flags_indice, "-r", os.path.join(pasta, "requisitos.txt")]),
    ]


class TempoEsgotado(RuntimeError):
    pass


def rodar(cmd, pasta, ao_linha=None, cancelado=None, limite_s=None):
    """Roda um comando sem abrir janela, repassando cada linha (incluindo progresso com \\r do uv).
    A saída é lida em blocos numa thread: cancelar e limite de tempo funcionam mesmo se travar."""
    import queue
    import threading
    import time
    proc = subprocess.Popen(cmd, cwd=pasta, env=ambiente_uv(pasta), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    fila = queue.Queue()

    def ler():
        fd = proc.stdout.fileno()
        buf = b""
        ultimo_cr = 0.0
        while True:
            try:
                bloco = os.read(fd, 4096)
            except OSError:
                bloco = b""
            if not bloco:
                break
            buf += bloco
            while True:
                idx_n = buf.find(b"\n")
                idx_r = buf.find(b"\r")
                if idx_n == -1 and idx_r == -1:
                    break
                if idx_n != -1 and (idx_r == -1 or idx_n < idx_r):
                    parte, buf, eh_cr = buf[:idx_n], buf[idx_n + 1:], False
                else:
                    if idx_r + 1 < len(buf) and buf[idx_r + 1:idx_r + 2] == b"\n":
                        parte, buf, eh_cr = buf[:idx_r], buf[idx_r + 2:], False
                    else:
                        parte, buf, eh_cr = buf[:idx_r], buf[idx_r + 1:], True
                texto = _RE_ANSI.sub("", parte.decode("utf-8", "replace")).strip()
                if not texto:
                    continue
                agora = time.monotonic()
                if eh_cr:
                    if agora - ultimo_cr < 0.18:
                        continue
                    ultimo_cr = agora
                fila.put((texto, eh_cr))
        resto = _RE_ANSI.sub("", buf.decode("utf-8", "replace")).strip()
        if resto:
            fila.put((resto, False))
        fila.put(None)

    threading.Thread(target=ler, daemon=True).start()
    ultimas, t0 = [], time.time()
    while True:
        try:
            item = fila.get(timeout=0.35)
        except queue.Empty:
            item = ("", False)
        if item is None:
            break
        linha, eh_cr = item
        if linha:
            if not eh_cr:
                ultimas = (ultimas + [linha])[-20:]
            elif not ultimas or ultimas[-1] != linha:
                ultimas = (ultimas[:-1] + [linha])[-20:] if ultimas else [linha]
            if ao_linha:
                ao_linha(linha)
        if cancelado and cancelado():
            proc.kill()
            raise InterruptedError
        if limite_s and time.time() - t0 > limite_s:
            proc.kill()
            raise TempoEsgotado("\n".join(ultimas[-8:]))
    if proc.wait() != 0:
        raise RuntimeError("\n".join(ultimas[-8:]) or f"falhou: {cmd[0]}")


def limpar_cache(pasta):
    shutil.rmtree(os.path.join(pasta, "ferramentas", "cache"), ignore_errors=True)


# ------------------------------------------------------------------ atalhos e registro
def criar_atalhos(pasta, area_de_trabalho=True, instalador=None):
    """Menu Iniciar (+ área de trabalho). Usa o PowerShell, que todo Windows tem."""
    alvo = python_do_venv(pasta, janela=True)
    script = os.path.join(pasta, "FluxEstudio.py")
    icone = os.path.join(pasta, "icone.ico")
    menu = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Flux Estúdio")
    os.makedirs(menu, exist_ok=True)
    atalhos = [(os.path.join(menu, "Flux Estúdio.lnk"), alvo, f'"{script}"')]
    if instalador:
        atalhos.append((os.path.join(menu, "Atualizar ou desinstalar.lnk"), instalador, ""))
    if area_de_trabalho:
        desk = _pasta_area_de_trabalho()
        atalhos.append((os.path.join(desk, "Flux Estúdio.lnk"), alvo, f'"{script}"'))
    linhas = ["$w = New-Object -ComObject WScript.Shell"]
    for lnk, tgt, args in atalhos:
        linhas += [f"$s = $w.CreateShortcut('{_ps(lnk)}')", f"$s.TargetPath = '{_ps(tgt)}'",
                   f"$s.Arguments = '{_ps(args)}'", f"$s.WorkingDirectory = '{_ps(pasta)}'",
                   f"$s.IconLocation = '{_ps(icone)}'", "$s.Save()"]
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(linhas)],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def remover_atalhos():
    menu = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Flux Estúdio")
    shutil.rmtree(menu, ignore_errors=True)
    try:
        os.remove(os.path.join(_pasta_area_de_trabalho(), "Flux Estúdio.lnk"))
    except OSError:
        pass


def _pasta_area_de_trabalho():
    import ctypes
    from ctypes import wintypes
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return buf.value or os.path.join(os.environ["USERPROFILE"], "Desktop")


def _ps(texto):
    return texto.replace("'", "''")


def gravar_info(pasta, **dados):
    caminho = os.path.join(pasta, "instalacao.json")
    atual = ler_info(pasta)
    atual.update(dados)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(atual, f, indent=2, ensure_ascii=False)


def ler_info(pasta):
    try:
        with open(os.path.join(pasta, "instalacao.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_DISCOS = None
_BUS = {1: "SCSI", 3: "ATA", 7: "USB", 8: "RAID", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC",
        15: "Virtual", 16: "Storage Spaces", 17: "NVMe"}


def tipos_de_disco():
    """{"C": {"tipo": "nvme"|"ssd"|"hdd"|"desconhecido", "bus": "NVMe", "livre": bytes}, ...} dos discos fixos.

    Consulta o WMI de armazenamento (MSFT_Partition -> MSFT_PhysicalDisk) por PowerShell uma vez só
    e guarda o resultado. Nunca levanta exceção: em qualquer falha o tipo fica "desconhecido".
    """
    global _DISCOS
    if _DISCOS is not None:
        return _DISCOS
    import string
    discos = {}
    try:
        import ctypes
        tipo_unidade = ctypes.windll.kernel32.GetDriveTypeW
    except Exception:
        tipo_unidade = None
    for letra in string.ascii_uppercase:
        raiz = f"{letra}:\\"
        try:
            if tipo_unidade is not None and tipo_unidade(raiz) != 3:  # DRIVE_FIXED (ignora rede/nuvem)
                continue
            livre = shutil.disk_usage(raiz).free
        except Exception:
            continue
        discos[letra] = {"tipo": "desconhecido", "bus": "", "livre": livre}
    if discos:
        ns = r"root\Microsoft\Windows\Storage"
        cmd = ("$ErrorActionPreference='Stop'; "
               f"$f=@(Get-CimInstance -Namespace {ns} -ClassName MSFT_PhysicalDisk | "
               "select @{n='Id';e={[string]$_.DeviceId}},@{n='Bus';e={[int]$_.BusType}},@{n='Media';e={[int]$_.MediaType}}); "
               f"$p=@(Get-CimInstance -Namespace {ns} -ClassName MSFT_Partition | ? {{ [int]$_.DriveLetter -ne 0 }} | "
               "select @{n='L';e={[string]$_.DriveLetter}},@{n='Disco';e={[string]$_.DiskNumber}}); "
               "ConvertTo-Json -Compress -InputObject @{f=$f; p=$p}")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                               capture_output=True, text=True, timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dados = json.loads(r.stdout or "{}")
            fisicos = {str(f.get("Id")): f for f in (dados.get("f") or []) if isinstance(f, dict)}
            for part in dados.get("p") or []:
                letra = str(part.get("L") or "").upper()[:1]
                f = fisicos.get(str(part.get("Disco")))
                if letra not in discos or not f:
                    continue
                bus, media = f.get("Bus"), f.get("Media")
                if bus == 17:
                    tipo = "nvme"
                elif media == 4:     # SSD
                    tipo = "ssd"
                elif media == 3:     # HDD
                    tipo = "hdd"
                else:
                    tipo = "desconhecido"
                discos[letra].update(tipo=tipo, bus=_BUS.get(bus, str(bus or "")))
        except Exception:
            pass
    _DISCOS = discos
    return discos


def pasta_padrao_rapida():
    """Escolhe uma pasta inicial instantânea (< 1 ms, sem PowerShell) para a janela abrir na hora."""
    import string
    minimo = 40 * 2**30
    melhor = None
    try:
        import ctypes
        tipo_unidade = ctypes.windll.kernel32.GetDriveTypeW
    except Exception:
        tipo_unidade = None
    sistema = (os.environ.get("SystemDrive") or "C:")[:1].upper()
    for letra in string.ascii_uppercase:
        raiz = f"{letra}:\\"
        try:
            if tipo_unidade is not None and tipo_unidade(raiz) != 3:
                continue
            livre = shutil.disk_usage(raiz).free
        except OSError:
            continue
        if livre >= minimo:
            peso = (1 if letra != sistema else 0, livre)
            if melhor is None or peso > melhor[0]:
                melhor = (peso, raiz)
    if melhor:
        return os.path.join(melhor[1], "FluxEstudio")
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "FluxEstudio")


def tipo_do_disco(pasta):
    """Tipo ("nvme"/"ssd"/"hdd"/"desconhecido") do disco onde está `pasta`."""
    try:
        if _DISCOS is None:
            return "desconhecido"
        letra = os.path.splitdrive(pasta.strip())[0][:1].upper() if pasta and pasta.strip() else ""
        return _DISCOS.get(letra, {}).get("tipo", "desconhecido")
    except Exception:
        return "desconhecido"


def pasta_padrao():
    """O disco mais rápido com 40 GB livres (o modelo de ~15 GB é lido do disco a cada abertura):
    NVMe > SSD SATA > HDD, preferindo um disco que não seja o do sistema. Senão, pasta_padrao_rapida."""
    minimo = 40 * 2**30
    try:
        sistema = (os.environ.get("SystemDrive") or "C:")[:1].upper()
        discos = tipos_de_disco()
        for tipo in ("nvme", "ssd", "hdd"):
            ok = [(l, d["livre"]) for l, d in discos.items() if d["tipo"] == tipo and d["livre"] >= minimo]
            if ok:
                fora = [x for x in ok if x[0] != sistema]
                letra = max(fora or ok, key=lambda x: x[1])[0]
                return os.path.join(f"{letra}:\\", "FluxEstudio")
    except Exception:
        pass
    return pasta_padrao_rapida()
