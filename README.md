# Spleeter GUI

Aplicativo desktop para Linux que separa músicas em stems (voz, bateria, baixo etc.) e permite mixá-los. Roda **somente na sua máquina**, sem servidor, Docker ou conta.

[![Licença: MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-green.svg)](LICENSE)
![Linux](https://img.shields.io/badge/Linux-X11-FCC624?logo=linux&logoColor=black)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-Qt%206-41CD52?logo=qt&logoColor=white)
![Spleeter](https://img.shields.io/badge/Spleeter-2.4.2-A238FF)
![Demucs](https://img.shields.io/badge/Demucs-4.0.1-EE4C2C)
![TensorFlow](https://img.shields.io/badge/TensorFlow-2.15-FF6F00?logo=tensorflow&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-2.8-EE4C2C?logo=pytorch&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-2-013243?logo=numpy&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?logo=ffmpeg&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white)
![Ruff](https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white)

## Recursos

- **Dois motores:** Spleeter, o padrão, e Demucs, opção avançada bem mais lenta.
- **Fila:** várias músicas em sequência, com reordenação e cancelamento. A falha de uma não interrompe as outras.
- **Opções dos motores:** modelo, trecho, formato (WAV, MP3, FLAC, OGG, M4A, WMA) e opções avançadas de cada um.
- **Mixer:** volume, solo e mudo por stem, forma de onda e exportação da mistura.
- **Mudança de tom:** de 6 semitons abaixo a 6 acima, durante a reprodução e na exportação.
- **Acordes:** mostra o acorde do momento e o seguinte, acompanhando o tom escolhido.
- **Histórico:** reabrir a pasta, abrir no mixer ou repetir uma separação com as mesmas opções.
- **Sem resultado parcial:** falha ou cancelamento não deixa arquivos incompletos no destino.

## Requisitos

- Linux com sessão X11. O projeto foi desenvolvido e testado em uma só máquina, com Ubuntu-like e XFCE.
- CPU com AVX. Tudo roda na CPU; não há suporte a GPU.
- Cerca de 2,4 GB de disco para os ambientes, mais os modelos e as músicas separadas.
- RAM de acordo com a duração: um arquivo de 12 minutos com 5 stems no Spleeter chegou a 11,1 GB.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) e os pacotes do sistema abaixo:

```bash
sudo apt install ffmpeg libportaudio2 libxcb-cursor0
```

## Instalação

```bash
git clone https://github.com/mvsjunior/spleeter-gui.git
cd spleeter-gui
./scripts/install.sh
```

O comando prepara os ambientes Python e cria o atalho no menu de aplicativos. Para deixar o Demucs de fora e economizar cerca de 800 MB, use `./scripts/install.sh --no-demucs`.

O app usa a pasta do projeto ao rodar, então não a apague. Se movê-la, reinstale os ambientes com `./scripts/install.sh`.

## Uso

Abra o **Spleeter GUI** pelo menu de aplicativos.

### Separar

1. Na aba **Separar**, escolha as músicas ou arraste-as para a janela.
2. Escolha o destino, o motor e o modelo.
3. Clique em **Separar**. Enquanto uma música é processada, outras podem ser adicionadas à fila.

O resultado fica em `<destino>/<música>-<modelo>/`, com um arquivo por stem. Cada modelo é baixado da internet no primeiro uso e fica em `~/.local/share/spleeter-gui/models/`.

### Mixar

Abra a aba **Mixer** e escolha uma pasta de stems, ou use **Abrir no mixer** ao fim de uma separação ou na aba **Histórico**. Ao exportar, se a soma passar de 0 dBFS, o volume da mistura é reduzido para evitar distorção. O botão **Fechar** libera a memória ocupada pela música.

Os botões **−** e **+** de **Tom** mudam a música em meio tom, sem alterar a velocidade, e a exportação usa o tom escolhido. Com o tom alterado, volume, mudo e pausa respondem cerca de meio segundo depois, e com a CPU quente e ocupada podem ocorrer pequenas falhas no som.

**Acorde** mostra o acorde atual e o seguinte. É uma estimativa automática, limitada a acordes maiores e menores: uma tétrade como E7 aparece como E, e harmonias mais complexas podem sair erradas. A voz e a bateria ficam fora da análise, que usa os outros stems.

### Modelos

Tempos medidos em um i5-3230M (2 núcleos, 4 threads, sem GPU) com uma música de 4 minutos. Variam muito com o processador e a temperatura. Veja os detalhes em [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

| Motor | Modelo | Stems | Download | Tempo | Pico de RAM |
|---|---|---|---|---|---|
| Spleeter | `2stems` | 2 | 76 MB | 52 s | 2,9 GB |
| Spleeter | `5stems` | 5 | 190 MB | 1 min 44 s | 4,3 GB |
| Demucs | `htdemucs_6s` | 6 | 53 MB | 12 min 36 s | 2,0 GB |
| Demucs | `htdemucs` | 4 | 81 MB | 17 min 43 s | 1,8 GB |
| Demucs | `htdemucs_ft` | 4 | 324 MB | cerca de 1h25, extrapolado de um trecho | 1,8 GB em um trecho |

O Spleeter também tem `4stems` e variantes até 16 kHz. O app mostra uma estimativa antes de iniciar quando já tem como calculá-la, sempre no caso do Demucs, e avisa quando ela passa de 5x a duração do áudio.

## Desinstalação

```bash
./scripts/uninstall.sh
```

O comando remove o atalho, o ícone e os ambientes Python, e pergunta antes de apagar os modelos e o histórico. A pasta do projeto e as músicas separadas continuam onde estão.

## Desenvolvimento

```bash
uv sync                  # ambiente com as ferramentas de desenvolvimento
uv run python -m spleeter_gui
uv run pytest            # testes rápidos
uv run pytest -m slow    # executam modelos reais
uv run ruff check . && uv run ruff format .
```

- [CLAUDE.md](CLAUDE.md): arquitetura, decisões e armadilhas conhecidas.
- [docs/PLANO_IMPLEMENTACAO.md](docs/PLANO_IMPLEMENTACAO.md): plano de implementação.
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md): medições.

## Licença

Distribuído sob a [licença MIT](LICENSE).

Este projeto não é afiliado à Deezer nem à Meta, autoras do Spleeter e do Demucs. Ele não inclui esses motores nem seus modelos: o instalador e o app os obtêm das fontes originais, e cada dependência segue a própria licença, como a LGPL do PySide6.

O código deste projeto é MIT, mas a mudança de tom depende de `pylibrb` e Rubber Band, ambos sob GPL. Quem distribuir o app junto com eles precisa cumprir também os termos da GPL.
