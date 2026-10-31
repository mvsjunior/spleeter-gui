"""Demucs, called through its CLI. An advanced option: see the times in docs/BENCHMARKS.md."""

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from spleeter_gui.core.models import CommandSpec, Job, ModelInfo, OptionSpec
from spleeter_gui.core.paths import MODELS_DIR
from spleeter_gui.core.preflight import required_bytes
from spleeter_gui.engines.base import ENGINES_DIR

CODECS = ("wav", "mp3", "flac", "ogg", "m4a", "wma")
LOSSLESS_CODECS = ("wav", "flac")
# Without MKL_DYNAMIC=FALSE it uses only 2 threads, whatever the other two variables say.
THREADS = "4"
_NATIVE_FLAGS = {"wav": [], "mp3": ["--mp3"], "flac": ["--flac"]}
_CONVERSION_ENCODERS = {"ogg": "libvorbis", "m4a": "aac", "wma": "wmav2"}
_SECTION_FILE = "section.wav"
_PCM_FLOAT_BYTES_PER_SECOND = 352_800
_DEPTH_FLAGS = {"16": [], "24": ["--int24"], "float": ["--float32"]}
_DEPTH_SIZE = {"16": 1.0, "24": 1.5, "float": 2.0}
_CLIP_MODES = ("rescale", "clamp")

_STEMS_4 = ("vocals", "drums", "bass", "other")
_STEMS_6 = ("vocals", "drums", "bass", "guitar", "piano", "other")

MODELS = (
    ModelInfo("htdemucs", "htdemucs: 4 stems, lento", _STEMS_4, 1.4, 81),
    ModelInfo("htdemucs_6s", "htdemucs_6s: 6 stems, lento", _STEMS_6, 1.0, 53),
    ModelInfo("htdemucs_ft", "htdemucs_ft: 4 stems, muito lento", _STEMS_4, 6.7, 324),
)
_SUB_MODELS = {
    "htdemucs": ("955717e8",),
    "htdemucs_6s": ("5c90dfd2",),
    "htdemucs_ft": ("f7e0c4bc", "d12395a8", "92cfc3b6", "04573f0d"),
}
# Time per second of audio with one shift and the default overlap. htdemucs_ft was only measured
# on a short section, which overstates it a little.
_SPEED_RATIO = {"htdemucs": 4.36, "htdemucs_6s": 3.10, "htdemucs_ft": 20.88}
_DEFAULT_OVERLAP = 0.25
_DEFAULT_SEGMENT = 7.8  # of the transformer models, the only ones offered here
# Peak RSS from 2 measurements per model at most; the rate per second is extrapolated.
_BASE_MB = {"htdemucs": 1115.0, "htdemucs_6s": 1315.0, "htdemucs_ft": 1715.0}
_MB_PER_SECOND = 2.8
_EXTRA_PER_JOB = 0.4  # measured with -j 2 on 10 s of audio only
# The largest whole number the transformer models accept: they were trained on 7.8 s.
_MAX_SEGMENT = 7

OPTIONS = (
    OptionSpec(
        "shifts",
        "Deslocamentos (--shifts)",
        "int",
        1,
        "Melhora um pouco a qualidade. O tempo é multiplicado por este número.",
        advanced=True,
        affects_speed=True,
        minimum=0,
        maximum=20,
    ),
    OptionSpec(
        "overlap",
        "Sobreposição (--overlap)",
        "float",
        _DEFAULT_OVERLAP,
        "Quanto cada trecho processado cobre do anterior. Mais sobreposição leva mais tempo.",
        advanced=True,
        affects_speed=True,
        minimum=0.0,
        maximum=0.9,
    ),
    OptionSpec(
        "segment",
        "Tamanho do trecho (--segment)",
        "int",
        0,
        "Em segundos; 0 usa o padrão do modelo. Trechos menores usam menos memória.",
        advanced=True,
        affects_speed=True,
        minimum=0,
        maximum=_MAX_SEGMENT,
    ),
    OptionSpec(
        "two_stems",
        "Só dois stems (--two-stems)",
        "choice",
        "",
        "Gera o stem escolhido e todo o resto em outro arquivo.",
        advanced=True,
        choices=(
            ("", "Não"),
            ("vocals", "Voz e o resto"),
            ("drums", "Bateria e o resto"),
            ("bass", "Baixo e o resto"),
            ("guitar", "Guitarra e o resto (só htdemucs_6s)"),
            ("piano", "Piano e o resto (só htdemucs_6s)"),
            ("other", "Outros e o resto"),
        ),
    ),
    OptionSpec(
        "clip_mode",
        "Evitar distorção (--clip-mode)",
        "choice",
        "rescale",
        "Reduzir o volume do stem inteiro ou só limitar os picos.",
        advanced=True,
        choices=(("rescale", "Reduzir o volume"), ("clamp", "Limitar os picos")),
    ),
    OptionSpec(
        "depth",
        "Profundidade do WAV",
        "choice",
        "16",
        "Vale só para o formato WAV.",
        advanced=True,
        choices=(("16", "16 bits"), ("24", "24 bits (--int24)"), ("float", "32 bits (--float32)")),
    ),
    OptionSpec(
        "jobs",
        "Tarefas simultâneas (-j)",
        "int",
        1,
        "Usa mais memória, e não mostrou ganho claro de tempo nesta máquina.",
        advanced=True,
        minimum=1,
        maximum=4,
    ),
)

_BAR = re.compile(r"\s*(\d+)%\|")


class _BarProgress:
    """Follows tqdm's bars: one per sub-model and per shift, each from 0 to 100%."""

    def __init__(self, bars: int) -> None:
        self._bars = bars
        self._finished = 0
        self._last = 0

    def __call__(self, line: str) -> float | None:
        match = _BAR.match(line)
        if match is None:
            return None
        percent = int(match.group(1))
        if percent < self._last:
            self._finished += 1
        self._last = percent
        return min((self._finished + percent / 100) / self._bars, 1.0)


class DemucsEngine:
    id = "demucs"
    label = "Demucs (avançado, bem mais lento)"
    codecs = CODECS
    lossless_codecs = LOSSLESS_CODECS

    def __init__(
        self,
        env_dir: Path = ENGINES_DIR / "demucs" / ".venv",
        models_dir: Path = MODELS_DIR / "demucs",
    ) -> None:
        self._executable = env_dir / "bin" / "demucs"
        self._models_dir = models_dir

    def is_installed(self) -> bool:
        return self._executable.is_file()

    def models(self) -> list[ModelInfo]:
        return list(MODELS)

    def options(self) -> list[OptionSpec]:
        return list(OPTIONS)

    def is_model_cached(self, model: str) -> bool:
        folder = self._models_dir / "hub" / "checkpoints"
        return all(any(folder.glob(f"{name}-*.th")) for name in _SUB_MODELS[model])

    def estimate_peak_mb(self, job: Job) -> float:
        peak = _BASE_MB[job.model] + _MB_PER_SECOND * job.effective_duration
        return peak * (1 + _EXTRA_PER_JOB * (_option(job, "jobs") - 1))

    def estimate_seconds(self, job: Job) -> float:
        passes = max(_option(job, "shifts"), 1)
        density = (1 - _DEFAULT_OVERLAP) / (1 - _option(job, "overlap"))
        chunks = _DEFAULT_SEGMENT / (_option(job, "segment") or _DEFAULT_SEGMENT)
        return _SPEED_RATIO[job.model] * passes * density * chunks * job.effective_duration

    def required_disk_bytes(self, job: Job) -> int:
        duration = job.effective_duration
        stems = len(_stems(job))
        needed = required_bytes(duration, stems, job.codec, job.bitrate)
        if job.codec == "wav":
            needed = int(needed * _DEPTH_SIZE[_option(job, "depth")])
        elif job.codec not in _NATIVE_FLAGS:
            needed += required_bytes(duration, stems, "wav", job.bitrate)  # to convert from
        if _has_section(job):
            needed += int(duration * _PCM_FLOAT_BYTES_PER_SECOND)
        return needed

    def prepare_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        """Demucs always takes the whole file, so a section is cut out first."""
        if not _has_section(job):
            return []
        argv = [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-ss", f"{job.offset:.6f}", "-t", f"{job.effective_duration:.6f}",
            "-i", str(job.input_path.absolute()),
            "-vn", "-c:a", "pcm_f32le", str(workdir / _SECTION_FILE),
        ]  # fmt: skip
        return [CommandSpec(argv=argv, env={})]

    def build_command(self, job: Job, workdir: Path) -> CommandSpec:
        _validate(job)
        argv = [
            str(self._executable),
            "-n",
            job.model,
            "-d",
            "cpu",
            "-o",
            str(workdir),
            "--filename",
            "{stem}.{ext}",
            "--shifts",
            str(_option(job, "shifts")),
            "--overlap",
            str(_option(job, "overlap")),
            "--clip-mode",
            _option(job, "clip_mode"),
            "-j",
            str(_option(job, "jobs")),
        ]
        if _option(job, "segment"):
            argv += ["--segment", str(_option(job, "segment"))]
        if _option(job, "two_stems"):
            argv += ["--two-stems", _option(job, "two_stems")]
        if job.codec == "wav":
            argv += _DEPTH_FLAGS[_option(job, "depth")]
        argv += _NATIVE_FLAGS.get(job.codec, [])
        if job.codec == "mp3":
            argv += ["--mp3-bitrate", job.bitrate.removesuffix("k")]
        # Absolute, so that a name starting with "-" is not read as an option.
        source = workdir / _SECTION_FILE if _has_section(job) else job.input_path.absolute()
        argv.append(str(source))
        return CommandSpec(
            argv=argv,
            env={
                "TORCH_HOME": str(self._models_dir),
                "OMP_NUM_THREADS": THREADS,
                "MKL_NUM_THREADS": THREADS,
                "MKL_DYNAMIC": "FALSE",
            },
        )

    def finish_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        """Demucs only writes WAV, FLAC and MP3; the other formats are converted from WAV."""
        encoder = _CONVERSION_ENCODERS.get(job.codec)
        if encoder is None:
            return []
        return [
            CommandSpec(
                argv=[
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(workdir / job.model / f"{stem}.wav"),
                    "-c:a",
                    encoder,
                    "-b:a",
                    job.bitrate,
                    str(workdir / f"{stem}.{job.codec}"),
                ],  # fmt: skip
                env={},
            )
            for stem in _stems(job)
        ]

    def progress_parser(self, job: Job) -> Callable[[str], float | None]:
        return _BarProgress(len(_SUB_MODELS[job.model]) * max(_option(job, "shifts"), 1))

    def collect_outputs(self, job: Job, workdir: Path) -> dict[str, Path]:
        """Leaves one flat file per stem. Demucs always writes into `<workdir>/<model>/`."""
        written = workdir / job.model
        outputs = {}
        for stem in _stems(job):
            path = workdir / f"{stem}.{job.codec}"
            if job.codec in _NATIVE_FLAGS:
                source = written / path.name
                if source.is_file():
                    source.replace(path)
            if not path.is_file():
                raise FileNotFoundError(f"Stem não gerado: {stem} ({path.name})")
            outputs[stem] = path
        shutil.rmtree(written, ignore_errors=True)
        (workdir / _SECTION_FILE).unlink(missing_ok=True)
        return outputs


def _option(job: Job, key: str):
    for spec in OPTIONS:
        if spec.key == key:
            return job.options.get(key, spec.default)
    raise KeyError(key)


def _model_info(model: str) -> ModelInfo:
    for info in MODELS:
        if info.id == model:
            return info
    raise ValueError(f"Modelo desconhecido: {model}")


def _stems(job: Job) -> tuple[str, ...]:
    chosen = _option(job, "two_stems")
    return (chosen, f"no_{chosen}") if chosen else _model_info(job.model).stems


def _has_section(job: Job) -> bool:
    return bool(job.offset) or job.duration is not None


def _validate(job: Job) -> None:
    info = _model_info(job.model)
    job.effective_duration  # noqa: B018 - raises if the section is outside the song
    if job.codec not in CODECS:
        raise ValueError(f"Formato não suportado: {job.codec}")
    chosen = _option(job, "two_stems")
    if chosen and chosen not in info.stems:
        raise ValueError(f"O modelo {job.model} não separa este stem: {chosen}")
    if _option(job, "clip_mode") not in _CLIP_MODES:
        raise ValueError(f"Modo inválido: {_option(job, 'clip_mode')}")
    if _option(job, "depth") not in _DEPTH_FLAGS:
        raise ValueError(f"Profundidade inválida: {_option(job, 'depth')}")
    if _option(job, "shifts") < 0 or _option(job, "jobs") < 1:
        raise ValueError("Número de deslocamentos ou de tarefas inválido.")
    if not 0 <= _option(job, "overlap") < 1:
        raise ValueError(f"Sobreposição inválida: {_option(job, 'overlap')}")
    if not 0 <= _option(job, "segment") <= _MAX_SEGMENT:
        raise ValueError(f"O tamanho do trecho vai até {_MAX_SEGMENT} s neste modelo.")
