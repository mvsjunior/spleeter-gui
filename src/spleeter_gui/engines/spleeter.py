"""Spleeter, called through its CLI. Measurements are in docs/BENCHMARKS.md."""

from collections.abc import Callable
from pathlib import Path

from spleeter_gui.core.models import CommandSpec, Job, ModelInfo, OptionSpec
from spleeter_gui.core.paths import MODELS_DIR
from spleeter_gui.core.preflight import required_bytes
from spleeter_gui.engines.base import ENGINES_DIR

CODECS = ("wav", "mp3", "ogg", "m4a", "wma", "flac")
LOSSLESS_CODECS = ("wav", "flac")
DEFAULT_FILENAME_FORMAT = "{instrument}.{codec}"
# More threads brought no consistent gain on this CPU, only more load and heat.
THREADS = "2"

_STEMS_2 = ("vocals", "accompaniment")
_STEMS_4 = ("vocals", "drums", "bass", "other")
_STEMS_5 = ("vocals", "drums", "bass", "piano", "other")

MODELS = (
    ModelInfo("2stems", "2 stems (voz e acompanhamento)", _STEMS_2, 1.0, 76),
    ModelInfo("4stems", "4 stems (voz, bateria, baixo e outros)", _STEMS_4, 1.8, 152),
    ModelInfo("5stems", "5 stems (voz, bateria, baixo, piano e outros)", _STEMS_5, 2.0, 190),
    ModelInfo("2stems-16kHz", "2 stems, até 16 kHz", _STEMS_2, 1.2, 76),
    ModelInfo("4stems-16kHz", "4 stems, até 16 kHz", _STEMS_4, 2.2, 152),
    ModelInfo("5stems-16kHz", "5 stems, até 16 kHz", _STEMS_5, 2.8, 190),
)

OPTIONS = (
    OptionSpec(
        "mwf",
        "Filtro de Wiener multicanal (--mwf)",
        "bool",
        False,
        "Mais lento e usa bem mais memória.",
        affects_speed=True,
    ),
    OptionSpec("verbose", "Modo detalhado", "bool", False, "Mostra mais detalhes no log."),
    OptionSpec(
        "filename_format",
        "Padrão de nome (-f)",
        "text",
        DEFAULT_FILENAME_FORMAT,
        "Variáveis: {filename}, {foldername}, {instrument} e {codec}.",
        advanced=True,
    ),
)

# Peak RSS is about this base plus a rate per second of audio, by model, without and with
# --mwf. From docs/BENCHMARKS.md; only 2 and 5 stems were measured on a long file.
_BASE_MB = 925.0
_MB_PER_SECOND = {
    "2stems": (8.1, 14.5),
    "4stems": (13.5, 24.8),
    "5stems": (14.0, 29.6),
    "2stems-16kHz": (10.7, 14.6),
    "4stems-16kHz": (17.8, 24.8),
    "5stems-16kHz": (17.8, 29.8),
}


class SpleeterEngine:
    id = "spleeter"
    label = "Spleeter"
    codecs = CODECS
    lossless_codecs = LOSSLESS_CODECS

    def __init__(
        self,
        env_dir: Path = ENGINES_DIR / "spleeter" / ".venv",
        models_dir: Path = MODELS_DIR / "spleeter",
    ) -> None:
        self._executable = env_dir / "bin" / "spleeter"
        self._models_dir = models_dir

    def is_installed(self) -> bool:
        return self._executable.is_file()

    def models(self) -> list[ModelInfo]:
        return list(MODELS)

    def options(self) -> list[OptionSpec]:
        return list(OPTIONS)

    def is_model_cached(self, model: str) -> bool:
        # The 16 kHz variants share the weights of the plain models.
        return (self._models_dir / model.removesuffix("-16kHz") / ".probe").is_file()

    def estimate_peak_mb(self, job: Job) -> float:
        plain, with_mwf = _MB_PER_SECOND[job.model]
        rate = with_mwf if job.options.get("mwf") else plain
        return _BASE_MB + rate * job.effective_duration

    def estimate_seconds(self, job: Job) -> float | None:
        return None  # varies too much with the machine's heat to say without a record

    def required_disk_bytes(self, job: Job) -> int:
        stems = len(_model_info(job.model).stems)
        return required_bytes(job.effective_duration, stems, job.codec, job.bitrate)

    def prepare_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        return []

    def finish_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        return []

    def build_command(self, job: Job, workdir: Path) -> CommandSpec:
        _model_info(job.model)
        if job.codec not in CODECS:
            raise ValueError(f"Formato não suportado: {job.codec}")
        argv = [
            str(self._executable),
            "separate",
            "-p",
            f"spleeter:{job.model}",
            "-o",
            str(workdir),
            "-f",
            _checked_filename_format(job),
            "-c",
            job.codec,
        ]
        if job.codec not in LOSSLESS_CODECS:
            argv += ["-b", job.bitrate]
        if job.offset:
            argv += ["-s", _seconds(job.offset)]
        # Always sent: without it, Spleeter silently cuts the audio at 600 s.
        argv += ["-d", _seconds(job.effective_duration)]
        if job.options.get("mwf"):
            argv.append("--mwf")
        if job.options.get("verbose"):
            argv.append("--verbose")
        # Absolute, so that a name starting with "-" is not read as an option.
        argv.append(str(job.input_path.absolute()))
        return CommandSpec(
            argv=argv,
            env={
                "MODEL_PATH": str(self._models_dir),
                "OMP_NUM_THREADS": THREADS,
                "MKL_NUM_THREADS": THREADS,
                "TF_NUM_INTRAOP_THREADS": THREADS,
            },
        )

    def progress_parser(self, job: Job) -> Callable[[str], float | None]:
        return lambda line: None  # the CLI does not report a percentage

    def collect_outputs(self, job: Job, workdir: Path) -> dict[str, Path]:
        """Maps each stem to its file. A missing stem means the separation is incomplete."""
        name_format = _filename_format(job)
        outputs = {}
        for stem in _model_info(job.model).stems:
            relative = _stem_path(name_format, job, stem)
            path = workdir / relative
            if not path.is_file():
                raise FileNotFoundError(f"Stem não gerado: {stem} ({relative})")
            outputs[stem] = path
        return outputs


def _model_info(model: str) -> ModelInfo:
    for info in MODELS:
        if info.id == model:
            return info
    raise ValueError(f"Modelo desconhecido: {model}")


def _filename_format(job: Job) -> str:
    return str(job.options.get("filename_format") or DEFAULT_FILENAME_FORMAT)


def _checked_filename_format(job: Job) -> str:
    """Rejects a format that would overwrite stems or write outside the job's folder."""
    name_format = _filename_format(job)
    try:
        relative = Path(_stem_path(name_format, job, "vocals"))
    except (KeyError, IndexError, ValueError):
        raise ValueError(f"Padrão de nome inválido: {name_format}") from None
    if "{instrument}" not in name_format:
        raise ValueError("O padrão de nome precisa conter {instrument}.")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("O padrão de nome precisa ficar dentro da pasta de saída.")
    return name_format


def _stem_path(name_format: str, job: Job, stem: str) -> str:
    return name_format.format(
        filename=job.input_path.stem,
        foldername=job.input_path.parent.name,
        instrument=stem,
        codec=job.codec,
    )


def _seconds(value: float) -> str:
    return f"{value:.6f}"
