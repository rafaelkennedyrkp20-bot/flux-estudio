"""Instalação e atualização do Flux Estúdio (usado pelo instalador e pelo botão "Atualizar").

Só biblioteca padrão do Python: roda dentro do instalador compilado, sem PyTorch nem nada.

Como fica a pasta instalada:
    FluxEstudio.py, app/, icone.ico, requisitos.txt, VERSAO   <- vêm do GitHub (trocados ao atualizar)
    .venv/            Python 3.12 + PyTorch (CUDA ou ROCm) + bibliotecas, criado pelo uv
    ferramentas/      uv.exe e o Python que ele baixa
    modelos/ saidas/ config.json prompts.json preferencias.ini   <- do usuário, nunca apagados
    instalacao.json   o que foi instalado (placa, versão)
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

# Arquivos e pastas do programa (vêm do repositório). O resto da pasta é do usuário.
DO_PROGRAMA = ("FluxEstudio.py", "app", "icone.ico", "requisitos.txt", "VERSAO", "README.md", "LICENSE",
               "Abrir Flux Estudio.bat")

TORCH = {
    "cuda": dict(pacotes=["torch==2.11.0+cu128"], indice="https://download.pytorch.org/whl/cu128"),
    "rocm": dict(pacotes=["torch[device-{gfx}]==2.14.0+rocm10.1.0"], indice="https://stable.repo.amd.com/rocm/whl-next/"),
    "cpu": dict(pacotes=["torch==2.11.0"], indice="https://download.pytorch.org/whl/cpu"),
}

# Radeon de mesa -> alvo do ROCm (pacote torch[device-gfxNNNN] da AMD)
_AMD_GFX = [
    (r"RX\s*90[78]0", "gfx1201"), (r"RX\s*9060", "gfx1200"),
    (r"RX\s*7900", "gfx1100"), (r"RX\s*7[78]00", "gfx1101"), (r"RX\s*76\d0", "gfx1102"),
    (r"RX\s*6[89]\d0", "gfx1030"), (r"RX\s*67\d0", "gfx1031"), (r"RX\s*66\d0", "gfx1032"), (r"RX\s*6[45]\d0", "gfx1034"),
]


# ------------------------------------------------------------------ placa de vídeo
def placas():
    """Placas de vídeo pelo registro do Windows: [{nome, fabricante, vram_gb, driver}]."""
    import winreg
    raiz = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    achadas = []
    try:
        chave = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, raiz)
    except OSError:
        return achadas
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

        nome = ler("DriverDesc") or ""
        dispositivo = (ler("MatchingDeviceId") or "").lower()
        mem = ler("HardwareInformation.qwMemorySize")
        if mem is None:
            mem = ler("HardwareInformation.MemorySize", 0)
            if isinstance(mem, bytes):
                mem = int.from_bytes(mem[:8], "little")
        fab = "nvidia" if "ven_10de" in dispositivo else "amd" if "ven_1002" in dispositivo else \
              "intel" if "ven_8086" in dispositivo else "outra"
        if nome:
            achadas.append(dict(nome=nome, fabricante=fab, vram_gb=round((mem or 0) / 2**30, 1),
                                driver=ler("DriverVersion") or ""))
    return achadas


def _driver_nvidia(versao_windows):
    """'32.0.16.1742' -> 617.42 (versão do driver NVIDIA)."""
    num = re.sub(r"\D", "", versao_windows)[-5:]
    return float(f"{num[:3]}.{num[3:]}") if len(num) == 5 else 0.0


def escolher_backend(lista=None):
    """Decide o que instalar. Retorna {backend: cuda/rocm/cpu, gfx, placa, vram_gb, aviso}."""
    lista = placas() if lista is None else lista
    nvidia = [p for p in lista if p["fabricante"] == "nvidia"]
    if nvidia:
        p = max(nvidia, key=lambda p: p["vram_gb"])
        aviso = ""
        drv = _driver_nvidia(p["driver"])
        if drv and drv < 570:
            aviso = "driver_antigo"
        return dict(backend="cuda", gfx=None, placa=p["nome"], vram_gb=p["vram_gb"], aviso=aviso)
    for p in sorted([p for p in lista if p["fabricante"] == "amd"], key=lambda p: -p["vram_gb"]):
        for padrao, gfx in _AMD_GFX:
            if re.search(padrao, p["nome"], re.I):
                return dict(backend="rocm", gfx=gfx, placa=p["nome"], vram_gb=p["vram_gb"], aviso="")
    outra = lista[0]["nome"] if lista else ""
    return dict(backend="cpu", gfx=None, placa=outra, vram_gb=0, aviso="sem_placa")


# ------------------------------------------------------------------ download
def baixar(url, destino, progresso=None, cancelado=None):
    """Baixa url -> destino. progresso(feito, total)."""
    req = urllib.request.Request(url, headers={"User-Agent": "FluxEstudio-Instalador"})
    with urllib.request.urlopen(req, timeout=60) as r, open(destino + ".part", "wb") as f:
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
    os.replace(destino + ".part", destino)
    return destino


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
    """Baixa o programa do GitHub e põe na pasta, trocando só os arquivos do programa.
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
                if os.path.isdir(alvo):
                    shutil.rmtree(alvo)
                shutil.copytree(origem, alvo)
            else:
                shutil.copy2(origem, alvo)
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
    env.update(UV_PYTHON_INSTALL_DIR=os.path.join(pasta, "ferramentas", "python"),
               UV_CACHE_DIR=os.path.join(pasta, "ferramentas", "cache"),
               UV_LINK_MODE="copy", UV_NO_PROGRESS="1", PYTHONUTF8="1")
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
            with z.open(nome) as o, open(destino, "wb") as d:
                shutil.copyfileobj(o, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return destino


def python_do_venv(pasta, janela=False):
    return os.path.join(pasta, ".venv", "Scripts", "pythonw.exe" if janela else "python.exe")


def comandos_instalacao(pasta, backend, gfx=None):
    """Lista de (descrição, comando) para criar o ambiente e instalar as bibliotecas."""
    uv = caminho_uv(pasta)
    venv = os.path.join(pasta, ".venv")
    py = python_do_venv(pasta)
    t = TORCH[backend]
    pacotes = [p.format(gfx=gfx) for p in t["pacotes"]]
    return [
        ("python", [uv, "venv", venv, "--python", PYTHON, "--allow-existing"]),
        ("torch", [uv, "pip", "install", "--python", py, "--index-url", t["indice"], *pacotes]),
        ("bibliotecas", [uv, "pip", "install", "--python", py, "-r", os.path.join(pasta, "requisitos.txt")]),
    ]


def rodar(cmd, pasta, ao_linha=None, cancelado=None):
    """Roda um comando sem abrir janela, repassando cada linha da saída. Levanta erro se falhar."""
    proc = subprocess.Popen(cmd, cwd=pasta, env=ambiente_uv(pasta), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ultimas = []
    buf = b""
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch in (b"\n", b"\r"):
            linha = buf.decode("utf-8", "replace").strip()
            buf = b""
            if linha:
                ultimas = (ultimas + [linha])[-15:]
                if ao_linha:
                    ao_linha(linha)
            if cancelado and cancelado():
                proc.kill()
                raise InterruptedError
        else:
            buf += ch
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
    (~1 s) e guarda o resultado. Nunca levanta exceção: em qualquer falha o tipo fica "desconhecido".
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
            if tipo_unidade is not None and tipo_unidade(raiz) != 3:  # DRIVE_FIXED
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
                               capture_output=True, text=True, timeout=3,
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


def tipo_do_disco(pasta):
    """Tipo ("nvme"/"ssd"/"hdd"/"desconhecido") do disco onde está `pasta`."""
    try:
        letra = os.path.splitdrive(pasta.strip())[0][:1].upper() if pasta and pasta.strip() else ""
        return tipos_de_disco().get(letra, {}).get("tipo", "desconhecido")
    except Exception:
        return "desconhecido"


def pasta_padrao():
    """O disco mais rápido com 40 GB livres (o modelo de ~15 GB é lido do disco a cada abertura):
    NVMe > SSD SATA > HDD, preferindo um disco que não seja o do sistema. Senão, o disco não-sistema
    com mais espaço; senão AppData."""
    import string
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
    melhor = None
    for letra in string.ascii_uppercase[3:]:
        raiz = f"{letra}:\\"
        try:
            livre = shutil.disk_usage(raiz).free
        except OSError:
            continue
        if livre > 40 * 2**30 and (melhor is None or livre > melhor[1]):
            melhor = (raiz, livre)
    if melhor:
        return os.path.join(melhor[0], "FluxEstudio")
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "FluxEstudio")
