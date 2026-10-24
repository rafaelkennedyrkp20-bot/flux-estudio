# ✦ Flux Estúdio

Programa de Windows para **gerar e editar imagens com IA localmente**, na sua placa de vídeo, com o
[FLUX.2 \[klein\] 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B). Interface preta e rápida:
copie a imagem no Photoshop (Ctrl+C), cole no programa (Ctrl+V), escreva o que mudar e aperte Ctrl+Enter.

*Español más abajo.*

## Instalar

1. Baixe o **`FluxEstudio-Instalador.exe`** na página de [Releases](../../releases/latest).
2. Abra. Ele detecta a placa de vídeo e instala a versão certa:
   - **NVIDIA** → PyTorch com CUDA 12.8
   - **AMD Radeon RX 9000 / 7000 / 6000 / 5000 e Ryzen AI** → PyTorch oficial da AMD (ROCm)
   - **Intel Arc** → PyTorch com Intel XPU
   - nenhuma compatível → processador Intel/AMD (funciona, mas lento)
3. Ele baixa o programa daqui do GitHub, o Python, as bibliotecas e o modelo (~15 GB; se já tiver o modelo,
   aponte a pasta) e cria os atalhos. No fim testa a placa gerando uma imagem.

Para **atualizar**: no programa, *Configurações → Procurar atualização*. Ou rode o instalador de novo e
escolha *Atualizar*. Modelos, imagens e prompts salvos nunca são apagados.

**Precisa de:** Windows 10/11 64 bits, 16 GB de RAM ou mais (32 GB recomendado), placa de vídeo com 6 GB ou mais de VRAM (modo econômico automático para 6–10 GB; modo residente rápido em 12–16 GB+), ~25 GB livres em SSD.

## Usar

| | |
|---|---|
| **Editar** | Cole ou arraste a imagem, escreva a instrução. O tamanho segue a imagem automaticamente. |
| **Máscara** | Pinte a área. Prompt vazio = apagar/limpar. O resto fica pixel a pixel igual. |
| **Limpar** | Detecta e apaga texto, balões e marcas d'água automaticamente com o LaMa de mangá. |
| **Gerar** | Texto → imagem. |
| **Prompts salvos** | Botão **+** ao lado do prompt. Clique no nome para usar, botão direito para editar. |
| Atalhos | Ctrl+V cola · Ctrl+C copia o resultado · Ctrl+Enter executa · Esc cancela · C compara antes/depois |

Idiomas: português, English, español (segue o Windows; troque em Configurações).

## Desenvolvimento

```
Instalar.bat               cria .venv (Python 3.12/3.11/3.10 + CUDA) para rodar pelo código
Abrir Flux Estudio.bat     abre o programa
instalador\compilar.bat    gera dist\FluxEstudio-Instalador.exe
python FluxEstudio.py --autoteste    verifica a placa e gera uma imagem (resultado em autoteste.txt)
```

---

## Español

Programa de Windows para **generar y editar imágenes con IA en local**, en tu tarjeta de video.

1. Descarga **`FluxEstudio-Instalador.exe`** en [Releases](../../releases/latest) y ábrelo.
2. Detecta tu tarjeta (NVIDIA → CUDA, AMD Radeon RX 9000/7000/6000/5000 → ROCm, Intel Arc → XPU) e instala todo:
   el programa, Python, las bibliotecas y el modelo (~15 GB). Al final prueba la tarjeta generando una imagen.
3. Uso: copia la imagen (Ctrl+C), pégala (Ctrl+V), escribe qué cambiar y pulsa Ctrl+Enter.
   **Máscara**: pinta el área; con el prompt vacío la borra. **Limpiar**: detecta y borra texto y globos al instante.

Para actualizar: *Configuración → Buscar actualización*. Requisitos: Windows 10/11, 16 GB de RAM o más (32 GB recomendado),
tarjeta de 6 GB o más (12–16 GB recomendado), ~25 GB libres en SSD.
