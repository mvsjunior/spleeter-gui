# Benchmarks

Medições da Fase 0, feitas em 2026-10-30 nesta máquina (i5-3230M, 2 núcleos / 4 threads, 15 GB de RAM, sem GPU), com `scripts/benchmark.sh`.

- **Razão:** tempo de separação dividido pela duração do áudio. Abaixo de 1x, é mais rápido que a música.
- **Pico de RAM:** maior processo da árvore (`time -v`), não a soma dos processos.
- **Áudio:** "Nada é Impossível - Quatro Por Um" (MP3, 243,9 s), um trecho de 30 s dela e um arquivo de 731,7 s com a música repetida 3 vezes.
- **Execuções:** cada linha é uma única execução, salvo indicação contrária, com os modelos já baixados.

## Como ler estes tempos

A CPU sofreu limitação térmica durante toda a rodada: chegou a 99 °C, e o clock variou entre 1,2 e 3 GHz (máximo nominal de 3,2 GHz). O mesmo caso variou bastante entre repetições: no trecho, `sp-2stems` com 4 threads foi de 13,3 s a 25,85 s; na música, `sp-4stems` com 4 threads foi de 91,67 s a 103,24 s. Trate diferenças pequenas como ruído. Com melhor refrigeração, os tempos podem cair.

## Tamanho em disco

| Item | Tamanho |
|---|---|
| Ambiente do Spleeter | 1,4 GB |
| Ambiente do Demucs | 819 MB |
| Ambiente temporário da GUI (PySide6-Essentials 6.12.0) | 246 MB |
| Modelos do Spleeter (2, 4 e 5 stems, 11 kHz e 16 kHz) | 417 MB |
| Modelos do Demucs (`htdemucs`, `htdemucs_6s`, `htdemucs_ft`) | 454 MB |

## Spleeter, música inteira (243,9 s, 4 threads)

| Modelo | Tempo | Razão | Pico de RAM |
|---|---|---|---|
| 2 stems | 51,63 s | 0,21x | 2,9 GB |
| 2 stems + `--mwf` | 83,09 s | 0,34x | 4,5 GB |
| 2 stems 16 kHz | 63,08 s | 0,26x | 3,5 GB |
| 2 stems 16 kHz + `--mwf` | 91,08 s | 0,37x | 4,5 GB |
| 4 stems | 91,67 s | 0,38x | 4,2 GB |
| 4 stems + `--mwf` | 146,86 s | 0,60x | 7,0 GB |
| 4 stems 16 kHz | 111,87 s | 0,46x | 5,3 GB |
| 4 stems 16 kHz + `--mwf` | 169,83 s | 0,70x | 7,0 GB |
| 5 stems | 104,1 s | 0,43x | 4,3 GB |
| 5 stems + `--mwf` | 175,52 s | 0,72x | 8,2 GB |
| 5 stems 16 kHz | 142,96 s | 0,59x | 5,3 GB |
| 5 stems 16 kHz + `--mwf` | 209,95 s | 0,86x | 8,2 GB |

## Spleeter, trecho de 30 s (4 threads)

| Modelo | Tempo | Razão | Pico de RAM |
|---|---|---|---|
| 2 stems | 16,2 s | 0,54x | 1,1 GB |
| 2 stems + `--mwf` | 19,26 s | 0,64x | 1,3 GB |
| 2 stems 16 kHz | 15,96 s | 0,53x | 1,3 GB |
| 2 stems 16 kHz + `--mwf` | 19,59 s | 0,65x | 1,3 GB |
| 4 stems | 25,67 s | 0,85x | 1,6 GB |
| 4 stems + `--mwf` | 29,89 s | 0,99x | 1,8 GB |
| 4 stems 16 kHz | 31,14 s | 1,04x | 1,8 GB |
| 4 stems 16 kHz + `--mwf` | 34,63 s | 1,15x | 1,8 GB |
| 5 stems | 28,74 s | 0,96x | 1,6 GB |
| 5 stems + `--mwf` | 37,18 s | 1,24x | 1,9 GB |
| 5 stems 16 kHz | 33,62 s | 1,12x | 1,8 GB |
| 5 stems 16 kHz + `--mwf` | 41,27 s | 1,37x | 2,0 GB |

A razão do trecho é maior que a da música porque a carga inicial pesa mais em um áudio curto. **Não use a razão do trecho para estimar uma música inteira.**

## Spleeter, arquivo longo (731,7 s, 4 threads)

| Caso | Tempo | Razão | Pico de RAM | Duração dos stems |
|---|---|---|---|---|
| 2 stems, com `--duration` | 124,71 s | 0,17x | 6,9 GB | 731,7 s |
| 5 stems, com `--duration` | 312,64 s | 0,43x | 11,1 GB | 731,7 s |
| 2 stems, sem `--duration` | não medido | - | - | **600,0 s** (saída 0, sem erro) |

O corte silencioso em 600 s está confirmado. A RAM cresce com a duração: 5 stems usaram 11,1 GB em cerca de 12 minutos, de 15 GB no total. `--mwf` em arquivo longo **não foi medido**, para não arriscar esgotar a memória; na música de 4 minutos, ele quase dobrou o pico em 5 stems.

## Demucs (4 threads, WAV)

| Modelo | Áudio | Tempo | Razão | Pico de RAM |
|---|---|---|---|---|
| `htdemucs` | música | 1063,14 s (17 min 43 s) | 4,36x | 1,8 GB |
| `htdemucs_6s` | música | 756,46 s (12 min 36 s) | 3,10x | 2,0 GB |
| `htdemucs` | trecho | 146,31 s | 4,87x | 1,2 GB |
| `htdemucs_6s` | trecho | 107,81 s | 3,59x | 1,4 GB |
| `htdemucs_ft` | trecho | 627,3 s | 20,88x | 1,8 GB |

`htdemucs_ft` não foi medido na música inteira: pela razão do trecho, levaria cerca de 1h25. `htdemucs_6s` foi mais rápido que `htdemucs` nas duas medições, mas o motivo não foi investigado.

## 2 ou 4 threads

| Caso | 2 threads | 4 threads |
|---|---|---|
| Spleeter 2 stems, trecho, média de 3 | 16,76 s | 18,64 s |
| Spleeter 4 stems, trecho, média de 3 | 24,30 s | 26,05 s |
| Spleeter 2 stems, música | 49,23 s | 51,63 s e 57,48 s |
| Spleeter 4 stems, música | 101,42 s | 91,67 s e 103,24 s |
| Spleeter 2 stems, arquivo de 731,7 s, média de 3 | 112,57 s | 116,78 s |
| Spleeter 4 stems, arquivo de 731,7 s | 217,37 s | 233,14 s |
| Demucs `htdemucs`, trecho, média de 2 | 161,97 s | 152,54 s |

**Padrões definidos:**

- **Spleeter: 2 threads.** Não há ganho consistente com 4, e o uso de CPU é menor (na música, cerca de 180–195% contra 230–320%).
- **Demucs: 4 threads.** Foram cerca de 6% mais rápidas nessas duas repetições.

Para o Demucs usar 4 threads de verdade, defina `MKL_DYNAMIC=FALSE` junto de `OMP_NUM_THREADS=4` e `MKL_NUM_THREADS=4`. Sem isso, o MKL se limita aos 2 núcleos físicos.

## Pela interface

Arquivo de 731,7 s com nome acentuado, 2 stems, janela real em X11:

| Medida | Resultado |
|---|---|
| Duração dos stems | 731,7 s, sem corte |
| Tempo | 158,95 s e 194,46 s |
| Mesma separação pela CLI, logo depois | 189,58 s |
| Maior pausa da interface | 0,74 s |
| Memória do processo da GUI | 88 MB |
| Abertura da janela, sem medir a inicialização do `uv` | 0,33 s |

A interface não é a causa da variação: a CLI levou de 108,04 s a 189,58 s no mesmo caso, conforme a temperatura. Estes tempos foram medidos antes da troca da barra animada, que sozinha consumia cerca de 12% de uma thread (3,5 s de CPU em 30 s).

## Formatos de saída do Demucs

Confirmados com `ffprobe` na saída da CLI, usando torch e torchaudio 2.8.0 com soundfile:

| Opção | Resultado |
|---|---|
| padrão | WAV `pcm_s16le` |
| `--int24` | WAV `pcm_s24le` |
| `--float32` | WAV `pcm_f32le` |
| `--flac` | FLAC 16 bits |
| `--mp3` | MP3 320 kbps |

O formato quase não altera o tempo (de 142,24 s a 155,66 s no trecho). A saída fica em `<destino>/<modelo>/` mesmo com `--filename "{stem}.{ext}"`.

Com torch 2.9.1, a gravação exige `torchcodec`, e testes diretos com `torchcodec==0.9.1` ignoraram a profundidade de bits: WAV sempre em s16 e FLAC sempre em 24 bits.

## Cancelamento

Envio de `SIGTERM` durante a decodificação, a separação e a gravação (no Demucs, só as duas primeiras), ao grupo de processos ou apenas ao processo pai:

| Cenário | Sobreviventes | Tempo até limpar | Arquivos restantes |
|---|---|---|---|
| Spleeter, decodificação, grupo | nenhum | 0,05 s | nenhum |
| Spleeter, separação, grupo | nenhum | 0,34 s | nenhum |
| Spleeter, gravação, grupo | nenhum | 0,18 s | nenhum |
| Spleeter, decodificação, só o pai | nenhum | 0,17 s | nenhum |
| Spleeter, separação, só o pai | nenhum | 0,47 s | nenhum |
| Spleeter, gravação, só o pai | nenhum | 0,25 s | `vocals.wav`, `accompaniment.wav` |
| Demucs, decodificação, grupo | nenhum | 0,06 s | nenhum |
| Demucs, separação, grupo | nenhum | 0,09 s | nenhum |
| Demucs, decodificação, só o pai | nenhum | 0,27 s | nenhum |

Não sobraram órfãos, mas o encerramento só pelo pai deixou arquivos parciais em um cenário. O Spleeter mantém 5 processos com esse nome durante a execução. A Fase 3 deve encerrar o grupo inteiro e descartar a pasta temporária.

## Fila, cancelamento e queda do app

Spleeter real, 2 stems, janela real em X11. Fila de 3 músicas, duas delas de 731,7 s:

| Caso | Resultado |
|---|---|
| Motor morto com `SIGKILL` enquanto o `ffmpeg` dele rodava | Job falhou; sem `ffmpeg` nem processo do grupo; fila seguiu |
| Cancelar enquanto o `ffmpeg` rodava | Clique em 0,6 ms; processos encerrados em 0,13 s; fila seguiu |
| Terceira música (40 s) | Concluída em 14,8 s |
| Ao terminar | Nenhum `ffmpeg`, nenhuma pasta temporária; só o resultado concluído no destino |

Com o app real (`python -m spleeter_gui`):

| Caso | Resultado |
|---|---|
| `kill -9` na GUI durante a separação | O motor continua rodando, porque tem sessão própria |
| Reabrir depois disso | Motor encerrado, pasta temporária removida, job marcado como interrompido |
| `SIGTERM` ou `SIGINT` durante a separação | App sai, sem motor, `ffmpeg` nem pasta temporária; job registrado como cancelado |

Abertura até a janela aparecer, contando a partida do Python e sem o `uv`: de 0,43 s a 0,47 s em 4 execuções, com 94 MB de memória. A medição anterior, de 0,33 s, usou outro método, então os dois valores não são diretamente comparáveis.

## Estimativa de tempo

A mesma música de 243,9 s, 2 stems, três vezes seguidas pela interface:

| Execução | Estimativa | Tempo real |
|---|---|---|
| 1 | nenhuma, sem registro | 48,35 s |
| 2 | 48,3 s | 56,7 s |
| 3 | 52,5 s | 111,6 s |

A estimativa só vale para a máquina fria: o aquecimento dobrou o tempo da terceira execução. Por isso a interface diz "cerca de" e deixa de mostrar a estimativa quando ela é ultrapassada.

## Mixer

Medido com 5 stems reais de uma música de 243,9 s, em janela real. O som foi para uma saída virtual do PipeWire, cuja gravação permitiu comparar o que saiu com o que foi exportado. Nada disso substitui ouvir.

| Medida | Resultado |
|---|---|
| Memória com 5 stems carregados | 535 MB no processo, 91 MB antes de carregar |
| Memória depois de fechar a música | 124 MB |
| Carregar os 5 stems | 3,62 s |
| Primeira abertura do mixer, com os imports de NumPy e PortAudio | 0,31 s |
| CPU na reprodução simples | 2,1% de uma thread |
| Exportar a mistura em WAV | 0,87 s |

Teste de estresse, com cerca de 30 mudanças de controle por segundo, saltos de posição e janela redimensionada:

| Situação | Duração | Mudanças | Falhas de áudio | Callback, mediana / máximo |
|---|---|---|---|---|
| Máquina livre | 60 s | 1874 | 0 | 0,23 ms / 8,45 ms |
| Spleeter com 5 stems ao mesmo tempo | 60 s | 1847 | 0 | 0,21 ms / 12,14 ms |

Cada bloco tem 46,4 ms. O uso de CPU nesses dois casos (15% e 12% de uma thread) inclui o script que movia os controles.

Reprodução gravada comparada com a mistura exportada, em 35 s:

| Caso | Diferença de nível | Resíduo |
|---|---|---|
| Sem normalização | 1,0000 | −84,5 dB |
| Exportação normalizada em 0,2 dB | 1,0211, a própria normalização | −33,5 dB sem igualar o nível |

Sem cortes de 20 ms ou mais em nenhum dos casos. Quando a soma passa de 0 dBFS, a exportação reduz o volume, e a reprodução só limita os picos, por isso fica um pouco mais alta. O caso normalizado não foi medido com o nível igualado.

Abertura do app, depois do mixer: de 0,41 s a 0,44 s em 4 execuções, com 94 MB. O mixer não pesa enquanto não é usado.

## Demucs pela interface

Áudio real, janela real em X11, tudo escolhido pelo formulário, rodando em sequência e com a CPU aquecendo:

| Caso | Áudio | Tempo | Estimativa antes de iniciar | Pico de RAM |
|---|---|---|---|---|
| `htdemucs`, WAV 24 bits | 10 s | 40,6 s | 43 s | 1,16 GB |
| `htdemucs`, OGG, `--two-stems vocals` | 10 s | 45,0 s | 43 s | 1,15 GB |
| `htdemucs`, M4A | 10 s | 53,1 s | 43 s | 1,16 GB |
| `htdemucs`, WMA | 10 s | 48,2 s | 43 s | 1,16 GB |
| `htdemucs`, FLAC, `--clip-mode clamp` | 10 s | 57,8 s | 43 s | 1,16 GB |
| `htdemucs`, `-j 2` | 10 s | 54,5 s | 43 s | 1,59 GB |
| `htdemucs`, float, `--shifts 2`, `--overlap 0.1`, `--segment 5` | 10 s | 178,8 s | 72 s | 1,25 GB |
| `htdemucs_6s`, MP3 192 kbps, trecho de música de 4 min | 12 s | 46,4 s | 37 s | 1,16 GB |
| `htdemucs_6s`, `--two-stems guitar` | 10 s | 48,0 s | 31 s | 1,21 GB |
| `htdemucs_ft`, trecho | 3 s | 134,4 s | 62 s | 1,68 GB |

As estimativas da tabela vêm da versão que ainda ignorava `--segment`; depois disso, a de `--segment 5` sobe para cerca de 112 s. Os picos são amostrados a cada 0,5 s na árvore de processos, o que os torna aproximados. Em todos os casos, a duração e o formato dos stems saíram certos, e o progresso nunca voltou atrás.

Em áudio muito curto, a estimativa fica abaixo do real, porque a carga dos modelos pesa mais: `htdemucs_ft` com 3 s levou mais que o dobro do previsto. A partir de 5% de progresso, a janela passa a calcular o tempo pelo próprio progresso.

`-j 2` não mostrou ganho de tempo nesta medição curta e usou cerca de 40% mais memória.

| Outros casos | Resultado |
|---|---|
| Cancelar com o `torch` em execução | Encerrado em 0,33 s, sem processo nem pasta temporária |
| 6 stems no mixer, 8 s mudando volumes | 0 falhas de áudio; exportação em MP3 concluída |
| `--two-stems guitar` com `htdemucs` | Recusado antes de iniciar |

## Instalação e abertura

Instalação do zero em uma cópia limpa do projeto, com outra pasta pessoal e o cache do `uv` já cheio. Por isso, o tempo da instalação em si não representa uma primeira instalação com downloads.

| Item | Tamanho |
|---|---|
| Ambiente da GUI, sem ferramentas de desenvolvimento | 304 MB |
| Ambiente do Spleeter | 1,3 GB |
| Ambiente do Demucs | 796 MB |

Tempo até a janela aparecer, contando a partida do Python e sem o `uv`, com a versão final:

| Situação | Tempo |
|---|---|
| Máquina ocupada (navegador em uso, carga em torno de 10, CPU a 90 °C), com e sem ícone | de 0,65 s a 2,38 s em 21 execuções, a maioria perto de 2 s |

As variantes com e sem ícone e nome do app não mostraram diferença. A última medição com a máquina livre é de antes do ícone: de 0,41 s a 0,44 s, com 94 MB. Com a versão final, o processo usa 97 MB. Não foi medida a primeira abertura depois de ligar o computador, sem os arquivos no cache do sistema.

No primeiro uso pela interface, o modelo `2stems` do Spleeter foi baixado e usado na separação de 10 s de áudio em 136,1 s, a maior parte de download. O `htdemucs` também foi baixado pela interface, mas a separação logo depois foi interrompida pelo limite de tempo do teste.

## Medições com áudio sintético

Serviram só para provar que cada configuração funciona: todas terminaram com saída 0, com a quantidade e a duração corretas de stems. **Não use estes tempos como referência:** a primeira execução de cada modelo incluiu download, e o Demucs "de 4 threads" rodou com 2, porque `MKL_DYNAMIC=FALSE` ainda não era usado. Por isso, os valores não estão reproduzidos aqui.

## Qualidade

O usuário ouviu os resultados da música inteira e os aprovou de forma geral: "O resultado ficou bom." Não houve comparação registrada entre modelos, então os padrões seguem definidos pelo tempo.
