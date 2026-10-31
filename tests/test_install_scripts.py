import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QIcon

from spleeter_gui.__main__ import ICON_PATH

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path) -> Path:
    """A copy of the scripts in a throwaway folder: uninstall.sh deletes next to itself."""
    root = tmp_path / "pasta com espaço é" / "spleeter-gui"
    (root / "scripts").mkdir(parents=True)
    for name in ("install.sh", "uninstall.sh"):
        shutil.copy(REPO / "scripts" / name, root / "scripts" / name)
    (root / "assets").mkdir()
    shutil.copy(ICON_PATH, root / "assets")
    for venv in (".venv", "engines/spleeter/.venv", "engines/demucs/.venv"):
        (root / venv).mkdir(parents=True)
    (root / "src").mkdir()
    return root


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def run(project: Path, home: Path, script: str, *args: str, answers: str = ""):
    return subprocess.run(
        [str(project / "scripts" / script), *args],
        env={**os.environ, "HOME": str(home)},
        input=answers,
        capture_output=True,
        text=True,
    )


def desktop_file(home: Path) -> Path:
    return home / ".local/share/applications/spleeter-gui.desktop"


def icon_file(home: Path) -> Path:
    return home / ".local/share/icons/hicolor/scalable/apps/spleeter-gui.svg"


def test_menu_entry_points_to_this_folder_even_with_spaces(project, home):
    result = run(project, home, "install.sh", "--only-menu")
    assert result.returncode == 0, result.stderr
    text = desktop_file(home).read_text()
    assert f'Exec="{project}/.venv/bin/python" -m spleeter_gui' in text
    assert "Icon=spleeter-gui" in text
    assert icon_file(home).is_file()


@pytest.mark.skipif(not shutil.which("desktop-file-validate"), reason="tool not installed")
def test_menu_entry_is_valid(project, home):
    run(project, home, "install.sh", "--only-menu")
    result = subprocess.run(
        ["desktop-file-validate", str(desktop_file(home))], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_folder_that_cannot_be_written_in_the_entry_is_refused(tmp_path, home):
    root = tmp_path / "preço 100%" / "app"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "install.sh", root / "scripts")
    result = run(root, home, "install.sh", "--only-menu")
    assert result.returncode == 1
    assert not desktop_file(home).exists()


def test_unknown_option_changes_nothing(project, home):
    result = run(project, home, "install.sh", "--nada")
    assert result.returncode == 2
    assert not desktop_file(home).exists()
    assert not (home / ".local").exists()


def fake_tool(folder: Path, name: str, body: str = "exit 0") -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


def run_full_install(project: Path, home: Path, tools: Path, *args: str):
    """Everything but the downloads: uv and the engines' setup are stand-ins."""
    fake_tool(tools, "uv", f'echo "uv $*" >> "{tools}/calls"')
    fake_tool(project / "scripts", "setup_engines.sh", f'echo "engines $*" >> "{tools}/calls"')
    return subprocess.run(
        [str(project / "scripts" / "install.sh"), *args],
        env={**os.environ, "HOME": str(home), "PATH": f"{tools}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("args", "engines"), [((), "engines all"), (("--no-demucs",), "engines spleeter")]
)
def test_full_install_runs_on_a_machine_with_the_requirements(
    project, home, tmp_path, args, engines
):
    tools = tmp_path / "tools"
    # Many more lines than a pipe holds: a reader that stops early must not count as a failure.
    lines = "\\n".join(f"\\tlib{n}.so => /lib/lib{n}.so" for n in range(50000))
    listing = rf"printf '{lines}\\n\\tlibportaudio.so.2 => /x\\n\\tlibxcb-cursor.so.0 => /y\\n'"
    fake_tool(tools, "ldconfig", listing)
    fake_tool(tools, "ffmpeg")
    result = run_full_install(project, home, tools, *args)
    assert result.returncode == 0, result.stderr
    assert (tools / "calls").read_text().splitlines() == ["uv sync --no-dev", engines]
    assert desktop_file(home).exists()


def test_missing_system_packages_are_named_before_installing_anything(project, home, tmp_path):
    tools = tmp_path / "tools"
    fake_tool(tools, "ldconfig", r"printf '\tlibportaudio.so.2 => /x\n'")
    fake_tool(tools, "ffmpeg")
    result = run_full_install(project, home, tools)
    assert result.returncode == 1
    assert "sudo apt install libxcb-cursor0" in result.stderr
    assert "libportaudio2" not in result.stderr
    assert not (tools / "calls").exists()
    assert not desktop_file(home).exists()


def make_user_data(home: Path) -> tuple[Path, Path]:
    data = home / ".local/share/spleeter-gui"
    (data / "models/spleeter/2stems").mkdir(parents=True)
    (data / "models/spleeter/2stems/modelo").write_text("x")
    (data / "history.db").write_text("x")
    config = home / ".config/spleeter-gui"
    config.mkdir(parents=True)
    (config / "spleeter-gui.conf").write_text("x")
    return data, config


def assert_program_is_gone(project: Path, home: Path) -> None:
    assert not desktop_file(home).exists()
    assert not icon_file(home).exists()
    assert not (project / ".venv").exists()
    assert not (project / "engines/spleeter/.venv").exists()
    assert not (project / "engines/demucs/.venv").exists()
    assert (project / "scripts/install.sh").is_file()


def test_uninstall_without_an_answer_keeps_the_data(project, home):
    run(project, home, "install.sh", "--only-menu")
    data, config = make_user_data(home)
    result = run(project, home, "uninstall.sh")
    assert result.returncode == 0, result.stderr
    assert_program_is_gone(project, home)
    assert (data / "models/spleeter/2stems/modelo").is_file()
    assert (data / "history.db").is_file()
    assert config.is_dir()


@pytest.mark.parametrize("answer", ["n", "", "sim", "x"])
def test_only_a_clear_yes_removes_the_models(project, home, answer):
    data, _ = make_user_data(home)
    run(project, home, "uninstall.sh", answers=f"{answer}\nn\n")
    assert (data / "models").is_dir()


def test_each_question_is_separate(project, home):
    data, config = make_user_data(home)
    run(project, home, "uninstall.sh", answers="s\nn\n")
    assert not (data / "models").exists()
    assert (data / "history.db").is_file()
    assert config.is_dir()


def test_full_uninstall_leaves_nothing_of_the_app(project, home):
    run(project, home, "install.sh", "--only-menu")
    data, config = make_user_data(home)
    (data / "app.lock").write_text("")
    other = home / ".local/share/outro-app/dados"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    result = run(project, home, "uninstall.sh", answers="S\ns\n")
    assert result.returncode == 0, result.stderr
    assert_program_is_gone(project, home)
    assert not data.exists()
    assert not config.exists()
    assert other.is_file()


def test_uninstall_refuses_while_the_app_is_open(project, home):
    run(project, home, "install.sh", "--only-menu")
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to("/bin/sleep")
    process = subprocess.Popen(["bash", "-c", f'exec -a "{python} -m spleeter_gui" sleep 30'])
    try:
        result = run(project, home, "uninstall.sh")
        assert result.returncode == 1
        assert desktop_file(home).exists()
        assert (project / ".venv").exists()
    finally:
        process.kill()
        process.wait()


def test_icon_can_be_drawn(qtbot):
    image = QIcon(str(ICON_PATH)).pixmap(64, 64).toImage()
    assert not image.isNull()
    assert image.pixelColor(32, 32).alpha() == 255
