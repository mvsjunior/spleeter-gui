# CLAUDE.md

Spleeter GUI: aplicativo desktop para Linux, de uso **somente local**, que separa músicas em stems (voz, bateria, baixo etc.) e permite mixá-los. Substitui o uso do `spleeter-web` em Docker nesta máquina.

## Estado atual

**Fases 0 a 7 concluídas; o plano terminou.** O app tem fila, cancelamento, histórico, mixer com mudança de tom e acordes, Spleeter, Demucs e instalação no menu. O código está em `github.com/mvsjunior/spleeter-gui`. Pendências de uso, como avaliar de ouvido o mixer, a mudança de tom e os acordes, dependem do usuário.

Ao iniciar uma sessão:
1. Leia `docs/PLANO_IMPLEMENTACAO.md` e continue pela primeira tarefa desmarcada.
2. Ao concluir tarefas, marque-as no plano e atualize esta seção.
3. Se uma decisão abaixo mudar, atualize este arquivo no mesmo trabalho e registre o motivo.

## Objetivo e prioridades

Em ordem: (1) baixo consumo de disco e memória e abertura rápida; (2) acesso a todas as opções úteis do Spleeter; (3) código simples, mantido por uma pessoa.

A GUI **não acelera a separação**: o tempo depende do modelo e do processador. O ganho em relação ao Docker está em eliminar a imagem de 25,2 GB e os serviços sempre ativos (nginx, Django, Redis, 2 workers Celery).

## Máquina-alvo

| Item | Valor | Consequência |
|---|---|---|
| CPU | i5-3230M, 2 núcleos / 4 threads | Uma separação por vez; modelos pesados são lentos |
| Instruções | Tem AVX e SSE4.2; **não tem AVX2 nem FMA** | Validar cada binário nativo antes de depender dele |
| GPU | Só Intel integrada; sem CUDA | Sempre CPU; instalar builds sem CUDA |
| RAM | 15 GB | Suficiente para o mixer, mas arquivos longos no Spleeter chegam perto do limite |
| Disco | 25 GB livres em 117 GB | Evitar dependências grandes |
| Sistema | Ubuntu-like, XFCE em X11, Python 3.12.3 | Instalar ferramentas com `uv` 0.13.6 |

`ffmpeg`, `ffprobe`, `libportaudio2` e `libxcb-cursor0` já estão instalados.

Sob carga, a CPU chega a 99 °C e sofre limitação térmica, com o clock caindo até 1,2 GHz. Os tempos medidos variam por isso.

## Decisões de arquitetura

| Decisão | Motivo |
|---|---|
| Interface em PySide6 (Qt 6), com classes e signals/slots | Nativa e leve, sem navegador, Node ou Rust |
| Cada motor roda como **processo filho** (`QProcess`) | Interface não trava, dá para cancelar e a memória volta ao sistema ao terminar |
| GUI e motores em **ambientes Python separados** | Spleeter exige Python < 3.12 e NumPy 1.x; a GUI usa 3.12 e NumPy 2.x |
| A GUI **nunca importa** TensorFlow, PyTorch, Spleeter ou Demucs | Abertura rápida e isolamento de dependências |
| Fila sequencial, um processo por vez | Paralelizar em 2 núcleos não compensa |
| Uma instância do app por vez (`QLockFile`) | Duas instâncias disputariam a CPU, e a limpeza ao abrir poderia apagar o trabalho da outra |
| Spleeter com 2 threads; Demucs com 4 | Medido: 4 threads não trazem ganho consistente no Spleeter e são cerca de 6% mais rápidas no Demucs |
| Demucs como opção avançada, com aviso de tempo | Decisão do usuário na Fase 0: `htdemucs` levou 4,36x a duração e `htdemucs_ft`, 20,88x no trecho |
| SQLite (`sqlite3`) para histórico e `QSettings` para preferências | Sem Redis, Celery ou servidor |
| Mixer, NumPy e `sounddevice` carregados só no primeiro uso | O import do NumPy levou até 530 ms, e abrir rápido é a prioridade 1 |
| Tom em tempo real com `pylibrb` (Rubber Band), motor `faster`, aplicado à mistura | Custo não cresce com os stems. O motor `finer` teve falhas de áudio até com a CPU livre |
| Buffer de 0,5 s só com o tom alterado | Reduz falhas de áudio sob carga, sem atrasar o mudo e a pausa no tom original |
| Acordes analisados com NumPy ao carregar a música, sem salvar | Leva de 1 a 3 s e não exige ambiente novo; o Chordino ocupou 423 MB e exigiu compilar |
| Saída escrita em pasta temporária e movida ao concluir | Falha ou cancelamento não deixa resultado parcial no destino |
| Motores chamados **pela CLI** deles | Evita acoplamento às APIs internas e a wrappers próprios |

Fora do escopo, salvo pedido explícito: treinamento e avaliação de modelos, importação do YouTube, multiusuário, servidor web, Docker e GPU. BS-RoFormer fica de fora porque tende a ser lento demais nesta CPU.

## Estrutura

```
spleeter-gui/
├── CLAUDE.md
├── README.md
├── LICENSE
├── assets/                    # ícone do app
├── pyproject.toml             # ambiente da GUI (Python 3.12)
├── docs/
│   ├── PLANO_IMPLEMENTACAO.md
│   └── BENCHMARKS.md          # medições da Fase 0
├── engines/                   # um ambiente isolado por motor (.venv no .gitignore)
│   ├── spleeter/requirements.txt
│   └── demucs/requirements.txt
├── scripts/
│   ├── install.sh             # ambientes, motores e atalho no menu
│   ├── uninstall.sh
│   ├── setup_engines.sh       # cria e instala os ambientes dos motores
│   └── benchmark.sh
├── src/spleeter_gui/
│   ├── __main__.py
│   ├── core/                  # sem nenhum import de Qt widgets
│   │   ├── models.py          # Job, JobStatus, ModelInfo, CommandSpec
│   │   ├── paths.py           # pastas de dados locais
│   │   ├── preflight.py       # espaço em disco e memória antes de iniciar
│   │   ├── estimate.py        # estimativa de tempo pelas execuções registradas
│   │   ├── timecode.py        # leitura e exibição de tempos
│   │   ├── probe.py           # duração e metadados via ffprobe
│   │   ├── storage.py         # histórico em SQLite
│   │   ├── audio.py           # decodificar, mixar e exportar stems (usa NumPy)
│   │   ├── player.py          # reprodução em um único callback (usa sounddevice)
│   │   ├── pitch.py           # mudança de tom em tempo real (usa pylibrb)
│   │   └── chords.py          # estimativa de acordes a partir dos stems (usa NumPy)
│   ├── engines/
│   │   ├── base.py            # contrato dos motores
│   │   ├── spleeter.py
│   │   └── demucs.py
│   ├── runner.py              # fila e QProcess
│   └── ui/                    # janela, opções, histórico, mixer
└── tests/
```

### Contrato dos motores

Adicionar um motor deve exigir só um arquivo em `engines/`, um ambiente em `engines/<motor>/` e o registro dele. A GUI monta os formulários a partir de `ModelInfo` e das opções declaradas.

```python
class Engine(Protocol):
    id: str
    label: str
    codecs: tuple[str, ...]
    lossless_codecs: tuple[str, ...]

    def is_installed(self) -> bool: ...
    def models(self) -> list[ModelInfo]: ...  # nome, stems, custo relativo
    def options(self) -> list[OptionSpec]: ...  # opções próprias, viram campos no formulário
    def is_model_cached(self, model: str) -> bool: ...
    def estimate_peak_mb(self, job: Job) -> float: ...
    def estimate_seconds(
        self, job: Job
    ) -> float | None: ...  # usado se o histórico não tem registro
    def required_disk_bytes(self, job: Job) -> int: ...
    def prepare_commands(self, job: Job, workdir: Path) -> list[CommandSpec]: ...  # antes do motor
    def build_command(self, job: Job, workdir: Path) -> CommandSpec: ...  # função pura: argv + env
    def finish_commands(self, job: Job, workdir: Path) -> list[CommandSpec]: ...  # depois do motor
    def progress_parser(self, job: Job) -> Callable[[str], float | None]: ...  # um por job
    def collect_outputs(
        self, job: Job, workdir: Path
    ) -> dict[str, Path]: ...  # stem -> arquivo, deixando a pasta final limpa
```

`OptionSpec.affects_speed` marca as opções que mudam a velocidade: execuções com outro valor não entram na estimativa de tempo.

Fluxo de um job: `queued → running → done | failed | cancelled`. A `JobQueue` registra o job no histórico antes de iniciá-lo, para que uma queda possa ser limpa depois. O runner cria a pasta temporária, executa em sequência, com `nice`, as etapas de `prepare_commands`, `build_command` e `finish_commands`, repassa o log e o progresso, e no sucesso move o resultado para `<destino>/<música>-<modelo>/`. Se a pasta já existir, acrescenta um sufixo numérico em vez de sobrescrever.

### Dados locais

- Modelos: `~/.local/share/spleeter-gui/models/{spleeter,demucs}`, via `MODEL_PATH` (Spleeter) e `TORCH_HOME` (Demucs).
- Histórico: `~/.local/share/spleeter-gui/history.db`.
- Temporários: pasta dentro do destino escolhido, para que a movimentação final seja atômica e o espaço livre verificado seja o do disco certo.

## Armadilhas conhecidas

Confirmadas na Fase 0 ou no `--help` e nos metadados da imagem `jeffreyca/spleeter-web-backend:latest`:

- **O Spleeter corta a música em 600 s por padrão, sem erro.** Confirmado: um arquivo de 731,7 s gerou stems de 600 s com saída 0. Sempre envie `--duration`, calculado com `ffprobe` como `duração_total - offset`.
- **Instale `spleeter==2.4.2` com `--no-deps`.** Os metadados exigem `tensorflow==2.12.1` e `typer<0.4`; o `spleeter-web` funciona com TensorFlow 2.15.1 e typer 0.20.0, instalando sem dependências. O TensorFlow 2.16+ não serve, porque removeu as APIs Estimator.
- **Mantenha `httpx==0.19.0`.** O `spleeter-web` registra incompatibilidade a partir da 0.20; veja deezer/spleeter#808.
- **`-p` aceita `spleeter:{2,4,5}stems` e as variantes `-16kHz`.** Padrão de 11 kHz; o nome com `-16kHz` preserva até 16 kHz.
- **O padrão de nome de saída inclui subpasta.** Com uma pasta por job, use `-f "{instrument}.{codec}"`, como faz `api/separators/spleeter_separator.py`.
- **A CLI do Spleeter não informa percentual.** Mostre tempo decorrido e uma estimativa baseada no histórico de velocidade da máquina, sem inventar um percentual preciso.

Resultados da Fase 0:

- **`tensorflow-cpu==2.15.1` roda o Spleeter nesta CPU.**
- **O Demucs usa `torch==2.8.0`, `torchaudio==2.8.0` e `soundfile==0.14.0`, não as versões 2.9.1 do `spleeter-web`.** A partir da 2.9, o torchaudio grava via `torchcodec`, que ignora `--int24` e `--float32`. Com a 2.8, WAV (16 bits, 24 bits e float), FLAC e MP3 saem corretos.
- **Sem `MKL_DYNAMIC=FALSE`, o Demucs usa só 2 threads**, mesmo com `OMP_NUM_THREADS=4` e `MKL_NUM_THREADS=4`.
- **O Demucs grava em `<saída>/<modelo>/`**, mesmo com `--filename "{stem}.{ext}"`.
- **Encerre o grupo de processos ao cancelar.** Nos testes com `SIGTERM`, não sobrou `ffmpeg` nem outro processo, mesmo encerrando só o pai. Mas o Spleeter pode deixar arquivos parciais durante a gravação, por isso a saída fica em pasta temporária.
- **A RAM do Spleeter cresce com a duração:** 11,1 GB com 5 stems em um arquivo de 12 minutos. Com `--mwf` nessa duração não foi medido, para não arriscar esgotar os 15 GB.
- **Tempos variam muito por temperatura:** a mesma separação de 731,7 s levou de 108 s a 190 s. Compare opções alternando a ordem e repetindo.
- **`QProgressBar` em modo indeterminado gasta cerca de 12% de uma thread.** Use `ui/activity_bar.py`.
- **A razão tempo/duração de um trecho curto superestima a de uma música inteira**, porque a carga inicial pesa mais. Não use trechos para estimar o tempo.
- **Um motor sobrevive à morte da GUI**, porque tem sessão própria. Ao abrir, `recover_interrupted` encerra o motor e limpa a pasta, mas só se o histórico tiver job em `running`.
- **O `ffmpeg` pode sobrar quando o motor morre sozinho.** Por isso o runner envia `SIGKILL` ao grupo sempre que o processo termina, e não só ao cancelar.
- **Sem um `wakeup_fd`, o Python não trata sinais dentro do laço do Qt.** Veja `exit_on_signals`. Use `app.exit()`: no Qt 6, `quit()` tenta fechar as janelas antes, o que abriria a confirmação de fechamento.
- **Adicione o lote inteiro à fila de uma vez.** Um job que falha já ao iniciar esvazia a fila na hora e emitiria `drained` antes de os outros entrarem.
- **Não importe `numpy` nem `sounddevice` em nada que `main_window.py` carrega ao abrir.** `test_isolation.py` verifica isso.
- **A reprodução não normaliza.** Ela limita os picos, e a exportação reduz o volume quando a soma passa de 0 dBFS. Nesse caso, a reprodução fica um pouco mais alta.
- **`latency="high"` dá só um bloco de buffer aqui (46 ms).** Peça a latência em segundos quando precisar de folga.
- **O custo do Rubber Band medido fora do app engana.** O motor `finer` usou 16% de uma thread isolado e 36% dentro do callback, e o `faster` passou de 6% para 14%. Meça com o `Player` real.
- **A mudança de tom sob carga e calor ainda pode falhar.** Com a CPU acima de 85 °C e outros processos ativos, sobram algumas falhas de áudio mesmo com o buffer de 0,5 s. No tom original não houve nenhuma.
- **Trocar o tom com o Rubber Band em andamento gera um pico de volume.** Por isso o tom só muda em silêncio, com o mesmo fade de um salto de posição.
- **Modelos de acordes com sétima são marcados em quase todo trecho.** Quatro notas acumulam mais energia que três, então E vira E7. Por isso só há tríades maiores e menores.
- **`pylibrb` pode imprimir `nanobind: leaked` ao fechar o Python.** A saída continua com código 0 e nada indica um problema de uso.
- **Testar som sem tocar nas caixas:** crie um `module-null-sink` e rode o app com `PIPEWIRE_NODE=<nome>`. O monitor dele pode ser gravado com `parec`.
- **Com a tela bloqueada, a placa de som some.** O sistema retira o acesso a `/dev/snd` da sessão inativa, `aplay -l` não acha placa e o PipeWire mostra só `auto_null`. Não é defeito do app.
- **A CLI do Demucs não tem início nem duração.** O trecho é cortado antes com `ffmpeg`.
- **O Demucs só grava WAV, FLAC e MP3.** OGG, M4A e WMA são convertidos depois, e o WAV intermediário conta no espaço exigido.
- **O Demucs mostra uma barra `tqdm` por sub-modelo e por shift.** `htdemucs_ft` com `--shifts 2` tem 8 barras, e o progresso só é correto contando todas.
- **`--segment` é inteiro e vai até 7 nos modelos transformer**, que foram treinados com 7,8 s. Um trecho menor aumenta o tempo.
- **Scripts com `pipefail` não podem usar `comando | grep -q`.** O `grep` encerra antes, o comando recebe `SIGPIPE` e a linha falha mesmo com a correspondência encontrada.
- **Modelos `*_q` do Demucs ficam indisponíveis:** dependem de `diffq`, que não está instalado.

## Referência: repositório spleeter-web

`../spleeter-web` é apenas referência. **Não importe nada dele nem modifique seus arquivos.**

- `api/separators/spleeter_separator.py` e `demucs_separator.py`: uso dos motores.
- `requirements.txt`: versões que funcionam em conjunto.
- `config/*.json`: configurações de stems (o pacote `spleeter` já traz as próprias em `spleeter/resources/`).

A imagem Docker dele ocupa 28 GB no total. Remova-a somente com a confirmação do usuário e depois de a Fase 0 passar.

## Comandos

```bash
uv sync                          # ambiente da GUI
./scripts/install.sh             # instala tudo e cria o atalho no menu
./scripts/uninstall.sh           # desinstala; pergunta antes de apagar modelos e histórico
./scripts/setup_engines.sh       # só os ambientes dos motores
uv run python -m spleeter_gui    # abrir o app
uv run pytest                    # testes, sem os lentos
uv run pytest -m slow            # testes que executam um modelo real
uv run ruff check . && uv run ruff format .
```

## Convenções

- Código, identificadores e commits em inglês; textos da interface e documentação em português do Brasil.
- Python com type hints; ruff para lint e formatação; pytest e pytest-qt para testes.
- Operações demoradas nunca rodam na thread da interface.
- Argumentos sempre em lista (`argv`), nunca em string de shell: nomes de música têm espaços e caracteres especiais.
- `core/` e `build_command` são testáveis sem Qt widgets e sem baixar modelos. Testes que executam um modelo real levam a marca `@pytest.mark.slow`.
- Registre medições em `docs/BENCHMARKS.md`; não cite tempos de separação sem medir nesta máquina.
- Sem dependência nova sem motivo claro: disco e tempo de abertura são requisitos do projeto.
