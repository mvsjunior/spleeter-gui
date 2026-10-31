# Plano de implementação

Arquitetura, decisões e armadilhas estão em `../CLAUDE.md`. Este arquivo define a ordem do trabalho: marque cada tarefa ao concluí-la.

| Fase | Entrega | Depende de |
|---|---|---|
| 0 | Motores validados e medidos nesta máquina | - |
| 1 | Núcleo sem interface, com testes | 0 |
| 2 | Primeira GUI: separar uma música com Spleeter | 1 |
| 3 | Fila, cancelamento e histórico | 2 |
| 4 | Mixer de stems | 3 |
| 5 | Demucs, como opção avançada com aviso de tempo | 3 |
| 6 | Instalação no menu do XFCE | 3 |

As Fases 4, 5 e 6 são independentes entre si.

## Fase 0: validar os motores

Objetivo: descobrir, antes de escrever a interface, o que realmente funciona nesta CPU sem AVX2 e quanto tempo leva. **Esta fase pode mudar o plano.**

- [x] Criar o repositório: `git init` e `.gitignore` com `.venv/`, `engines/*/.venv/`, `__pycache__/`, `*.db`, `.env`
- [x] Instalar a dependência de sistema: `sudo apt install libxcb-cursor0` (o usuário precisa executar)
- [x] Criar `engines/spleeter/requirements.txt`:
  ```
  tensorflow-cpu==2.15.1
  numpy==1.24.3
  pandas==1.5.3
  norbert==0.2.1
  ffmpeg-python==0.2.0
  httpx[http2]==0.19.0
  typer==0.20.0
  protobuf==4.25.5
  ```
- [x] Instalar o Spleeter e registrar o tamanho do ambiente:
  ```bash
  uv venv --python 3.11 engines/spleeter/.venv
  uv pip install --python engines/spleeter/.venv -r engines/spleeter/requirements.txt
  uv pip install --python engines/spleeter/.venv --no-deps spleeter==2.4.2
  ```
- [x] Criar `engines/demucs/requirements.txt` com `demucs==4.0.1`, `torch==2.8.0`, `torchaudio==2.8.0`, `soundfile==0.14.0`, e instalar em `engines/demucs/.venv` (Python 3.11, índice `https://download.pytorch.org/whl/cpu`, com PyPI como índice extra)
- [x] Rodar `python -c "import PySide6.QtWidgets"` em um ambiente temporário da GUI, para confirmar que o Qt carrega nesta CPU
- [x] Criar `scripts/benchmark.sh`, com `/usr/bin/time -v` para medir tempo e pico de memória
- [x] Separar um trecho de 30 s e uma música inteira (o usuário fornece o áudio) com:
  - Spleeter: 2, 4 e 5 stems; `-16kHz`; com e sem `--mwf`
  - Demucs: `htdemucs` e, se o tempo for aceitável, `htdemucs_ft` e `htdemucs_6s`
  - Comparar 2 e 4 threads (`OMP_NUM_THREADS`), para definir o padrão
- [x] Confirmar que a CLI do Demucs grava WAV, FLAC e MP3 (ver armadilha do `torchaudio` no `CLAUDE.md`)
- [x] Ouvir os resultados e registrar a impressão de qualidade
- [x] Registrar tudo em `docs/BENCHMARKS.md`: tempo, razão tempo/duração, pico de RAM e tamanho de cada ambiente

**Decisões ao final da fase:**

| Resultado | Ação |
|---|---|
| `tensorflow-cpu` falha | Testar `tensorflow==2.15.1` completo |
| Spleeter falha com as duas opções | Parar e rever o plano com o usuário |
| Demucs não grava com torch 2.9.1 | Testar `torchcodec` ou versões anteriores do torch |
| Demucs leva mais de 5x a duração da música | Manter como opção avançada, com aviso de tempo, ou remover a Fase 5 (decisão do usuário) |
| Qt não carrega | Testar uma versão anterior do PySide6 |

**Pronto quando:** `BENCHMARKS.md` estiver preenchido e o `CLAUDE.md` refletir o que foi confirmado, com a seção de itens "a confirmar" revisada.

**Resultado:** `tensorflow-cpu`, Demucs e Qt funcionam nesta CPU. O Demucs não gravou corretamente com torch 2.9.1 e passou a usar torch 2.8.0 com soundfile. Por decisão do usuário, o que passar de 5x fica como opção avançada, com aviso de tempo: `htdemucs` (4,36x) e `htdemucs_6s` (3,10x) ficaram abaixo do limite, e `htdemucs_ft` (20,88x no trecho) ficou acima.

## Fase 1: núcleo sem interface

- [x] Criar `pyproject.toml` (Python 3.12; PySide6-Essentials, numpy, sounddevice; ruff, pytest e pytest-qt como dev)
- [x] Criar `scripts/setup_engines.sh` a partir dos comandos validados na Fase 0, idempotente e com opção de instalar só um motor
- [x] `core/models.py`: `Job`, `JobStatus`, `ModelInfo`, `CommandSpec`
- [x] `core/probe.py`: duração e metadados via `ffprobe`, com erro claro para arquivo inválido
- [x] `engines/base.py` e `engines/spleeter.py`, com `build_command` puro
- [x] Testes de `build_command`:
  - `--duration` sempre presente e igual a `total - offset`, inclusive em música com mais de 600 s
  - Nomes com espaços, acentos e aspas
  - Todas as combinações de modelo, codec, bitrate e `--mwf`
- [x] `core/storage.py` com testes em banco temporário

**Pronto quando:** `pytest` passar sem baixar modelos, e um teste `slow` separar um áudio curto de ponta a ponta usando apenas `core/` e `engines/`.

## Fase 2: primeira GUI

- [x] `runner.py`: executar um job com `QProcess` e `nice`, emitindo signals de log, progresso, conclusão e erro
- [x] Janela principal: escolher arquivo (diálogo e arrastar e soltar) e pasta de destino
- [x] Painel com todas as opções do Spleeter:
  - Modelo: 2, 4 ou 5 stems, com ou sem 16 kHz
  - Trecho: início e duração, em que deixar vazio significa até o fim
  - Saída: formato (WAV, MP3, OGG, M4A, WMA, FLAC) e bitrate, desabilitado para formatos sem perda
  - `--mwf` e modo detalhado
  - Padrão de nome (`-f`), em opções avançadas
- [x] Progresso: tempo decorrido e estado, com indicador de atividade (a CLI não informa percentual)
- [x] Painel de log recolhível, e erro legível com o log disponível
- [x] Ao concluir: botão para abrir a pasta de saída
- [x] Detectar o motor não instalado e o primeiro download do modelo, informando o usuário
- [x] Preferências via `QSettings` (última pasta, último modelo, tamanho da janela)
- [x] Verificar espaço livre no destino antes de começar
- [x] Avisar antes de iniciar quando a memória estimada passar da disponível (5 stems usaram 11,1 GB em 12 minutos, de 15 GB)

**Pronto quando:** uma música com mais de 10 minutos e nome com acentos for separada pela interface, sem cortes, e a interface seguir responsiva durante todo o processo.

## Fase 3: fila, cancelamento e histórico

- [x] Fila: adicionar vários arquivos, reordenar e remover
- [x] Execução sequencial, em que falha de um job não interrompe os próximos
- [x] Cancelamento encerrando toda a árvore de processos, sem `ffmpeg` órfão e sem resultado parcial no destino
- [x] Ao fechar o app com job ativo: confirmar e encerrar os processos filhos
- [x] Na abertura: limpar pastas temporárias de execuções interrompidas
- [x] Histórico em SQLite: arquivo, modelo, opções, duração, resultado; reabrir pasta e repetir job
- [x] Estimativa de tempo com base na velocidade registrada por modelo

**Pronto quando:** uma fila de 3 músicas sobreviver a uma falha forçada e a um cancelamento, sem órfãos (`pgrep ffmpeg`) nem arquivos parciais.

## Fase 4: mixer de stems

- [x] `core/audio.py`: decodificar qualquer formato com `ffmpeg` para float32 em NumPy
- [x] Reprodução com `sounddevice`: um único callback somando os stems, garantindo sincronia
- [x] Controles por stem: volume, solo e mudo; controles gerais: play/pausa e posição
- [x] Forma de onda simples por stem, pré-calculada em thread separada
- [x] Exportar a mistura com `ffmpeg`, evitando clipping (normalizar se o pico passar de 0 dBFS)
- [x] Abrir pelo histórico ou escolhendo uma pasta de stems existente

Referência de memória: cerca de 85 MB por stem a cada 4 minutos de estéreo em float32 (44,1 kHz), ou aproximadamente 425 MB para 5 stems.

**Pronto quando:** a reprodução de 5 stems não falhar enquanto os controles são usados, e a mistura exportada soar como a reprodução.

## Fase 5: Demucs

Aprovada pela Fase 0, como opção avançada; o Spleeter continua sendo o padrão. Tempos em `BENCHMARKS.md`.

- [x] `engines/demucs.py`, com testes de `build_command`
- [x] Modelos: `htdemucs`, `htdemucs_6s`, `htdemucs_ft`; `mdx_extra` só depois de medido
- [x] Opções: `--shifts` (padrão 1), `--overlap`, `--segment`, `--two-stems`, `--clip-mode`, `--int24`/`--float32`, `--flac`/`--mp3`, `--mp3-bitrate`, `-j`
- [x] Sempre passar `-d cpu`, e 4 threads com `OMP_NUM_THREADS=4`, `MKL_NUM_THREADS=4` e `MKL_DYNAMIC=FALSE`
- [x] Coletar a saída em `<destino>/<modelo>/`, onde o Demucs a grava mesmo com `--filename`
- [x] Converter para os demais formatos com `ffmpeg`, para o formulário ficar consistente com o Spleeter
- [x] Progresso real, lendo a barra `tqdm` do stderr, inclusive em modelos com vários sub-modelos
- [x] Exibir o tempo estimado antes de iniciar qualquer modelo do Demucs, com aviso destacado acima de 5x a duração (hoje, `htdemucs_ft`)
- [x] Mixer funcionando com os 6 stems do `htdemucs_6s`

**Pronto quando:** Spleeter e Demucs funcionarem pela mesma interface, e a adição do Demucs não tiver exigido mudanças em `ui/` além do registro de opções.

**Resultado:** Spleeter e Demucs funcionam pela mesma interface, mas o critério de `ui/` foi cumprido em parte. O motor em si não exigiu nada específico na interface, porém ela precisou de mudanças genéricas: seletor de motor, campos numéricos e de escolha no formulário e aviso de tempo antes de iniciar. `mdx_extra` continua de fora, porque não foi medido.

## Fase 6: instalação local

- [x] Script para criar o atalho `.desktop` e o ícone em `~/.local/share/`
- [x] Comando único de instalação e de desinstalação (removendo também os modelos, após confirmação)
- [x] `README.md` com instalação e uso
- [x] Medir o tempo de abertura e registrar em `BENCHMARKS.md`
- [x] Sugerir ao usuário a remoção das imagens Docker do `spleeter-web`

Empacotamento em AppImage, Flatpak ou `.deb` não é necessário para uso em uma só máquina.

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Binário nativo exige AVX2 | Motor não roda | Fase 0, antes de investir na interface |
| Separação lenta demais | Experiência ruim | Medir e expor o custo de cada modelo; padrão no Spleeter |
| Spleeter sem manutenção ativa | Ambiente pode quebrar com o tempo | Versões fixas e ambiente isolado |
| Música longa consome muita RAM | Processo morto pelo sistema | Medir na Fase 0; mostrar erro claro e sugerir processar um trecho |
| Falhas de áudio no mixer em Python | Reprodução com cortes | Buffer maior no `sounddevice`; pré-calcular antes de tocar |
