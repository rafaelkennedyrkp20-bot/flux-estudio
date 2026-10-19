"""Catálogo de modelos. O FLUX.2 [klein] 4B é o principal (padrão em todas as abas); os outros
são secundários, escolhidos por caber em placas de 8 GB ou 16 GB e por ter suporte no diffusers.

Campos:
  modos      quais abas o modelo atende: gerar / editar / mascara
  tipo       "flux2" usa o caminho otimizado (cache de prompt); "generico" usa o pipeline do diffusers
  classes    pipeline do diffusers por modo (None = o da pasta, via DiffusionPipeline)
  variante   "fp16" baixa só os pesos fp16 (metade do tamanho)
  excluir    componentes que não precisam ser baixados
  passos/guia  padrões bons para o modelo; passos_max limita o controle deslizante
  lado_auto  modo automático: a imagem de trabalho tem no máximo lado_auto² pixels, na proporção da entrada
"""

MODELOS = [
    dict(
        id="flux2-klein-4b", nome="FLUX.2 [klein] 4B", repo="black-forest-labs/FLUX.2-klein-4B",
        principal=True, tipo="flux2", vram=8, lado_auto=1408, gb=15.0, licenca="Apache 2.0",
        modos=("gerar", "editar", "mascara"), passos=4, passos_max=12, guia=1.0, usa_guia=False,
        dtype="bfloat16",
        classes={"gerar": "Flux2KleinPipeline", "editar": "Flux2KleinPipeline", "mascara": "Flux2KleinInpaintPipeline"},
        descricao="O principal. Gera, edita por instrução (com várias referências) e refaz áreas com máscara. "
                  "4 passos, ~13 s por imagem 1024² numa 5060 Ti; pico de ~8 GB de VRAM.",
    ),
    dict(
        id="z-image-turbo", nome="Z-Image Turbo", repo="Tongyi-MAI/Z-Image-Turbo",
        tipo="generico", vram=16, lado_auto=1408, gb=30.6, licenca="Apache 2.0",
        modos=("gerar", "mascara"), passos=8, passos_max=16, guia=0.0, usa_guia=False, dtype="bfloat16",
        classes={"gerar": "ZImagePipeline", "mascara": "ZImageInpaintPipeline"},
        descricao="6B da Alibaba, muito bom em fotorrealismo e texto em inglês/chinês. 8 passos. Pede placa de 16 GB.",
    ),
    dict(
        id="sana-sprint-1.6b", nome="Sana Sprint 1.6B", repo="Efficient-Large-Model/Sana_Sprint_1.6B_1024px_diffusers",
        tipo="generico", vram=8, lado_auto=1024, gb=9.1, licenca="Apache 2.0",
        modos=("gerar",), passos=2, passos_max=4, guia=4.5, usa_guia=False, dtype="bfloat16",
        classes={"gerar": "SanaSprintPipeline"},
        descricao="Gerador ultrarrápido (2 passos) da NVIDIA. Bom para rascunhos e ideias. Roda em 8 GB.",
    ),
    dict(
        id="sdxl", nome="Stable Diffusion XL", repo="stabilityai/stable-diffusion-xl-base-1.0",
        tipo="generico", vram=8, lado_auto=1024, gb=6.4, licenca="OpenRAIL++", variante="fp16",
        modos=("gerar",), passos=30, passos_max=60, guia=6.0, usa_guia=True, dtype="float16",
        classes={"gerar": "StableDiffusionXLPipeline"},
        descricao="O clássico. Enorme variedade de estilos e LoRAs da comunidade. Roda em 8 GB.",
    ),
    dict(
        id="sdxl-inpaint", nome="SDXL Inpainting", repo="diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
        tipo="generico", vram=8, lado_auto=1024, gb=6.4, licenca="OpenRAIL++", variante="fp16",
        modos=("mascara",), passos=30, passos_max=60, guia=7.5, usa_guia=True, dtype="float16",
        classes={"mascara": "StableDiffusionXLInpaintPipeline"},
        descricao="Refaz a área pintada com SDXL. Alternativa leve para máscara em placas de 8 GB.",
    ),
    dict(
        id="instruct-pix2pix", nome="InstructPix2Pix", repo="timbrooks/instruct-pix2pix",
        tipo="generico", vram=8, lado_auto=768, gb=2.0, licenca="MIT", variante="fp16", excluir=("safety_checker",),
        modos=("editar",), passos=20, passos_max=50, guia=7.5, usa_guia=True, dtype="float16",
        classes={"editar": "StableDiffusionInstructPix2PixPipeline"},
        descricao="Edição por instrução bem leve (cabe em 6–8 GB). Qualidade abaixo do FLUX, mas rápida.",
    ),
]

POR_ID = {m["id"]: m for m in MODELOS}
PRINCIPAL = MODELOS[0]


def para_modo(modo):
    return [m for m in MODELOS if modo in m["modos"]]
