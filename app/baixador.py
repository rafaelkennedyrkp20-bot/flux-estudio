"""Baixa o FLUX.2 [klein] 4B do Hugging Face direto para a pasta do programa.

- Só o formato diffusers (transformer/, text_encoder/, vae/, tokenizer/, scheduler/): ~15 GB.
- Arquivos grandes são baixados em pedaços em paralelo (bem mais rápido que um fluxo só).
- Retomável: se cair a internet ou fechar o programa, continua de onde parou (.part + .part.json).

Uso sem interface:  python -m app.baixador [id_do_modelo] [--listar]
"""
import json
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config
from .modelos import POR_ID

PEDACO = 64 * 1024 * 1024
TRABALHADORES = 8
EXT_PESO = (".safetensors", ".bin", ".pth", ".pt", ".ckpt", ".msgpack", ".onnx", ".onnx_data", ".pb", ".h5")
RAIZ_OK = (".json", ".md", ".txt")


class Cancelado(Exception):
    pass


def _cabecalhos():
    h = {"User-Agent": "FluxEstudio/1.0"}
    token = os.environ.get("HF_TOKEN") or config.carregar().get("hf_token")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _eh_peso(nome):
    return nome.endswith(EXT_PESO) or ".safetensors.index" in nome or ".bin.index" in nome


def listar_arquivos(repo, revisao="main", variante=None, excluir=()):
    """Só o formato diffusers: model_index.json + as pastas dos componentes que ele lista.
    Nada de checkpoint único na raiz, imagens de exemplo, onnx/flax, ou pesos duplicados."""
    url = f"https://huggingface.co/api/models/{repo}/tree/{revisao}?recursive=true"
    r = requests.get(url, headers=_cabecalhos(), timeout=30)
    r.raise_for_status()
    itens = [(i["path"], int((i.get("lfs") or {}).get("size", i.get("size", 0))))
             for i in r.json() if i.get("type") == "file"]
    r = requests.get(f"https://huggingface.co/{repo}/resolve/{revisao}/model_index.json", headers=_cabecalhos(), timeout=30)
    r.raise_for_status()
    comps = {k for k, v in r.json().items() if not k.startswith("_") and isinstance(v, list) and v and v[0]}
    comps -= set(excluir)

    por_pasta = {}
    for caminho, tam in itens:
        partes = caminho.split("/")
        if len(partes) == 1:
            if caminho.endswith(RAIZ_OK) and not caminho.startswith("."):
                por_pasta.setdefault("", []).append((caminho, tam))
        elif len(partes) == 2 and partes[0] in comps:
            por_pasta.setdefault(partes[0], []).append((caminho, tam))

    arquivos = []
    for pasta, lista in por_pasta.items():
        nomes = [c.split("/")[-1] for c, _ in lista]
        tem_st = any(n.endswith(".safetensors") for n in nomes)
        tem_var = bool(variante) and any(f".{variante}" in n for n in nomes if _eh_peso(n))
        for caminho, tam in lista:
            n = caminho.split("/")[-1]
            if "openvino" in n:
                continue
            if _eh_peso(n):
                if tem_st and not (n.endswith(".safetensors") or ".safetensors.index" in n):
                    continue
                if tem_var and f".{variante}" not in n:
                    continue
                if not tem_var and any(f".{v}" in n for v in ("fp16", "bf16", "fp32")):
                    continue
            arquivos.append((caminho, tam))
    return arquivos


class Baixador:
    """`ao_progresso(feito, total, velocidade_bps, arquivo)` é chamado ~4x por segundo."""

    def __init__(self, modelo, destino=None, ao_progresso=None):
        self.modelo = modelo
        self.destino = destino or config.pasta_modelo(modelo["id"])
        self.repo, self.revisao = modelo["repo"], modelo.get("revisao", "main")
        self.ao_progresso = ao_progresso or (lambda *a: None)
        self._cancelar = threading.Event()
        self._trava = threading.Lock()
        self._feito = 0
        self._total = 0
        self._atual = ""

    def cancelar(self):
        self._cancelar.set()

    def _url(self, caminho):
        return f"https://huggingface.co/{self.repo}/resolve/{self.revisao}/{caminho}"

    def _somar(self, n):
        with self._trava:
            self._feito += n

    def _baixar_pedaco(self, caminho, parte, ini, fim, estado, arq_estado, sessao):
        """Baixa bytes [ini, fim] para dentro do .part, na posição certa. Tenta de novo se cair."""
        for tentativa in range(6):
            recebidos = 0
            try:
                cab = dict(_cabecalhos(), Range=f"bytes={ini}-{fim}")
                with sessao.get(self._url(caminho), headers=cab, stream=True, timeout=(15, 60)) as r:
                    if r.status_code not in (200, 206):
                        raise IOError(f"HTTP {r.status_code} em {caminho}")
                    if r.status_code == 200 and (ini > 0 or fim - ini + 1 != int(r.headers.get("Content-Length", -1))):
                        raise IOError(f"servidor ignorou o Range em {caminho}")
                    with open(parte, "r+b") as f:
                        f.seek(ini)
                        for bloco in r.iter_content(1024 * 1024):
                            if self._cancelar.is_set():
                                raise Cancelado()
                            f.write(bloco)
                            recebidos += len(bloco)
                            self._somar(len(bloco))
                            self._atual = caminho
                if recebidos != fim - ini + 1:
                    raise IOError(f"pedaço incompleto em {caminho}")
                with self._trava:
                    estado.add(ini)
                    with open(arq_estado, "w") as f:
                        json.dump(sorted(estado), f)
                return
            except Cancelado:
                self._somar(-recebidos)
                raise
            except Exception:
                self._somar(-recebidos)
                if tentativa == 5 or self._cancelar.is_set():
                    raise
                time.sleep(2 * (tentativa + 1))

    def baixar(self):
        """Baixa tudo. Retorna a pasta. Levanta Cancelado se cancelar()."""
        arquivos = listar_arquivos(self.repo, self.revisao, self.modelo.get("variante"), self.modelo.get("excluir", ()))
        self._total = sum(t for _, t in arquivos)
        self._feito = 0
        tarefas, finais = [], []
        sessao = requests.Session()
        sessao.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=TRABALHADORES * 2))

        for caminho, tamanho in arquivos:
            final = os.path.join(self.destino, *caminho.split("/"))
            os.makedirs(os.path.dirname(final), exist_ok=True)
            if os.path.isfile(final) and os.path.getsize(final) == tamanho:
                self._feito += tamanho
                continue
            if tamanho == 0:
                open(final, "wb").close()
                continue
            parte, arq_estado = final + ".part", final + ".part.json"
            estado = set()
            if os.path.isfile(parte) and os.path.getsize(parte) == tamanho and os.path.isfile(arq_estado):
                try:
                    with open(arq_estado) as f:
                        estado = set(json.load(f))
                except (OSError, ValueError):
                    estado = set()
            else:
                with open(parte, "wb") as f:
                    f.seek(tamanho - 1)  # truncate() no Windows grava zeros devagar
                    f.write(b"\0")
            for ini in range(0, tamanho, PEDACO):
                fim = min(ini + PEDACO, tamanho) - 1
                if ini in estado:
                    self._feito += fim - ini + 1
                else:
                    tarefas.append((caminho, parte, ini, fim, estado, arq_estado))
            finais.append((final, parte, arq_estado))

        parar_relogio = threading.Event()

        def relogio():
            janela = deque(maxlen=12)
            while not parar_relogio.wait(0.25):
                janela.append((time.time(), self._feito))
                vel = 0.0
                if len(janela) > 1 and janela[-1][0] > janela[0][0]:
                    vel = (janela[-1][1] - janela[0][1]) / (janela[-1][0] - janela[0][0])
                self.ao_progresso(self._feito, self._total, max(vel, 0.0), self._atual)

        t_rel = threading.Thread(target=relogio, daemon=True)
        t_rel.start()
        try:
            with ThreadPoolExecutor(TRABALHADORES) as ex:
                futuros = [ex.submit(self._baixar_pedaco, *t, sessao) for t in tarefas]
                for fu in futuros:
                    try:
                        fu.result()
                    except BaseException:
                        self._cancelar.set()  # um falhou de vez: para os outros
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
        self.ao_progresso(self._total, self._total, 0.0, "")
        return self.destino


def _main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    modelo = POR_ID[args[0] if args else "flux2-klein-4b"]
    if "--listar" in sys.argv:
        arqs = listar_arquivos(modelo["repo"], "main", modelo.get("variante"), modelo.get("excluir", ()))
        for c, t in arqs:
            print(f"{t / 2**20:10.1f} MB  {c}")
        print(f"total {sum(t for _, t in arqs) / 2**30:.2f} GB")
        return

    def mostrar(feito, total, vel, arq):
        pct = 100 * feito / total if total else 0
        print(f"\r{pct:5.1f}%  {feito / 2**30:6.2f}/{total / 2**30:.2f} GB  {vel / 2**20:6.1f} MB/s  {arq[-48:]:<48}", end="", flush=True)

    b = Baixador(modelo, ao_progresso=mostrar)
    try:
        b.baixar()
    except KeyboardInterrupt:
        b.cancelar()
        print("\ncancelado (dá para continuar depois)")
        return
    print(f"\npronto: {b.destino}  completo={config.modelo_instalado(modelo['id'], excluir=modelo.get('excluir', ()))}")


if __name__ == "__main__":
    _main()
