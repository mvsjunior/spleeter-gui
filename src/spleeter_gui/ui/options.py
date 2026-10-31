"""The options form, built from what the engine declares."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from spleeter_gui.core.models import ModelInfo, OptionSpec, OptionValue
from spleeter_gui.core.timecode import parse_time
from spleeter_gui.engines.base import Engine
from spleeter_gui.ui.collapsible import Collapsible

BITRATES = ("128k", "192k", "256k", "320k")
OptionWidget = QCheckBox | QLineEdit | QSpinBox | QDoubleSpinBox | QComboBox


class OptionsForm(QWidget):
    model_changed = Signal()
    changed = Signal()  # anything in the form

    def __init__(self, engine: Engine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._option_widgets: dict[str, OptionWidget] = {}

        self._model = QComboBox()
        for info in engine.models():
            self._model.addItem(info.label, info.id)
        self._model.currentIndexChanged.connect(self.model_changed)
        self._model.currentIndexChanged.connect(self.changed)

        self._offset = QLineEdit()
        self._offset.setPlaceholderText("do início")
        self._offset.setToolTip("Em segundos ou no formato min:seg. Exemplo: 1:30")
        self._duration = QLineEdit()
        self._duration.setPlaceholderText("até o fim")
        self._duration.setToolTip("Em segundos ou no formato min:seg. Exemplo: 1:30")
        self._offset.textChanged.connect(self.changed)
        self._duration.textChanged.connect(self.changed)

        self._codec = QComboBox()
        for codec in engine.codecs:
            self._codec.addItem(codec.upper(), codec)
        self._codec.currentIndexChanged.connect(self._update_bitrate)
        self._codec.currentIndexChanged.connect(self.changed)
        self._bitrate = QComboBox()
        for bitrate in BITRATES:
            self._bitrate.addItem(bitrate.replace("k", " kbps"), bitrate)
        self._bitrate.setCurrentIndex(BITRATES.index("320k"))

        form = QFormLayout()
        form.addRow("Modelo:", self._model)
        form.addRow("Início:", self._offset)
        form.addRow("Duração:", self._duration)
        form.addRow("Formato:", self._codec)
        form.addRow("Taxa de bits:", self._bitrate)

        advanced_content = QWidget()
        advanced_form = QFormLayout(advanced_content)
        for spec in engine.options():
            target = advanced_form if spec.advanced else form
            widget = self._make_widget(spec)
            if spec.kind == "bool":
                target.addRow(widget)
            else:
                target.addRow(f"{spec.label}:", widget)
            widget.setToolTip(spec.tooltip)
            self._option_widgets[spec.key] = widget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        if advanced_form.rowCount():
            layout.addWidget(Collapsible("Opções avançadas", advanced_content))
        self._update_bitrate()

    def _make_widget(self, spec: OptionSpec) -> OptionWidget:
        if spec.kind == "bool":
            widget = QCheckBox(spec.label)
            widget.setChecked(bool(spec.default))
            widget.toggled.connect(self.changed)
        elif spec.kind == "int":
            widget = QSpinBox()
            widget.setRange(int(spec.minimum), int(spec.maximum))
            widget.setValue(int(spec.default))
            widget.valueChanged.connect(self.changed)
        elif spec.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(spec.minimum, spec.maximum)
            widget.setSingleStep(0.05)
            widget.setValue(float(spec.default))
            widget.valueChanged.connect(self.changed)
        elif spec.kind == "choice":
            widget = QComboBox()
            for value, label in spec.choices:
                widget.addItem(label, value)
            widget.setCurrentIndex(widget.findData(spec.default))
            widget.currentIndexChanged.connect(self.changed)
        else:
            widget = QLineEdit(str(spec.default))
            widget.textChanged.connect(self.changed)
        return widget

    def model_info(self) -> ModelInfo:
        return self._engine.models()[self._model.currentIndex()]

    def select_model(self, model: str) -> None:
        index = self._model.findData(model)
        if index >= 0:
            self._model.setCurrentIndex(index)

    def set_codec(self, codec: str) -> None:
        self._codec.setCurrentIndex(self._codec.findData(codec))

    def set_section(self, offset: str, duration: str) -> None:
        self._offset.setText(offset)
        self._duration.setText(duration)

    def codec(self) -> str:
        return self._codec.currentData()

    def bitrate(self) -> str:
        return self._bitrate.currentData()

    def is_bitrate_enabled(self) -> bool:
        return self._bitrate.isEnabled()

    def offset(self) -> float:
        return parse_time(self._offset.text()) or 0.0

    def duration(self) -> float | None:
        return parse_time(self._duration.text())

    def engine_options(self) -> dict[str, OptionValue]:
        values: dict[str, OptionValue] = {}
        for key, widget in self._option_widgets.items():
            values[key] = _value_of(widget)
        return values

    def option_widget(self, key: str) -> OptionWidget:
        return self._option_widgets[key]

    def _update_bitrate(self) -> None:
        self._bitrate.setEnabled(self.codec() not in self._engine.lossless_codecs)


def _value_of(widget: OptionWidget) -> OptionValue:
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox | QDoubleSpinBox):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentData()
    return widget.text()
