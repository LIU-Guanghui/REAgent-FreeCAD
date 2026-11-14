"""Named fixed-provider profiles and platform-specific API key persistence."""

import json
import os
import re


from REAgent.application.profiles import (
    config_values, default_profiles, profile_connection, profile_efforts, read_profiles, semantic_inputs, validate_config,
)
from REAgent.ui import QtCore, QtWidgets
from REAgent.ui.credentials import open_key, protect_key


def saved_settings(read_settings):
    """Restore only the current format, without probing or creating a worker."""
    profiles, name = read_profiles(read_settings())
    if name not in profiles:
        return None
    row = profiles[name]
    config = validate_config(config_values(row))
    credentials = None
    if os.name == "nt" and row.get("protected_key"):
        connection = profile_connection(row["provider"], row["base_url"])
        credentials = {"api_key": open_key(row["protected_key"], connection["credential_binding"])}
    return config, credentials, name


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent, *, read_settings, save_settings, config=None, profile_name=None, lock_model=False):
        super().__init__(parent)
        self.setWindowTitle("Model settings")
        self.config = self.credentials = self.profile_name = None
        self.session_key_only = os.name != "nt"
        self._initial_config, self._lock_model = config, lock_model
        self._save_settings = save_settings
        self._stored_profiles = read_settings()
        self._read_error = ""
        try:
            self._profiles, selected = read_profiles(self._stored_profiles)
        except ValueError as exc:
            self._profiles, selected = {}, ""
            self._read_error = str(exc)
        self._keys = {}
        self._initial_keys = {}
        self._defaults = default_profiles()
        self._original_profiles = {name: dict(row) for name, row in self._profiles.items()}
        if config is not None:
            matching = []
            for name, row in self._profiles.items():
                try:
                    if semantic_inputs(row) == semantic_inputs(config):
                        matching.append(name)
                except ValueError:
                    pass
            selected = profile_name if profile_name in matching else matching[0] if matching else self._unique_name("Current model")
            if not matching:
                self._profiles[selected] = dict(config)
        if not self._profiles:
            selected = "New Profile"
            self._profiles[selected] = dict(self._defaults["Anthropic"])
        self._selected = selected if selected in self._profiles else next(iter(self._profiles))
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        profile_row = QtWidgets.QHBoxLayout()
        self._choices = QtWidgets.QComboBox()
        profile_row.addWidget(self._choices, 1)
        profile_buttons = []
        for title, callback in (("New", self._new_profile), ("Rename", self._rename_profile), ("Delete", self._delete_profile)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(callback)
            profile_row.addWidget(button)
            profile_buttons.append(button)
        form.addRow("Profile", profile_row)
        self._provider = QtWidgets.QComboBox()
        self._provider.addItems(list(self._defaults))
        form.addRow("Provider", self._provider)
        self._credential = QtWidgets.QLineEdit()
        self._credential.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("API key", self._credential)
        self._edits = {"base_url": QtWidgets.QLineEdit()}
        self._edits["base_url"].setToolTip("API root, including any version or proxy prefix; omit the request endpoint.")
        form.addRow("Base URL", self._edits["base_url"])
        self._model = QtWidgets.QLineEdit()
        form.addRow("Model", self._model)
        self._effort = QtWidgets.QComboBox()
        form.addRow("Reasoning effort", self._effort)
        self._edits["context_tokens"] = QtWidgets.QLineEdit()
        form.addRow("Context capacity (tokens)", self._edits["context_tokens"])
        self._edits["output_tokens"] = QtWidgets.QLineEdit()
        form.addRow("Output budget (tokens)", self._edits["output_tokens"])
        self._error = QtWidgets.QLabel(self._read_error)
        self._error.setTextFormat(QtCore.Qt.PlainText)
        self._error.setWordWrap(True)
        layout.addWidget(self._error)
        self._replace_profiles = QtWidgets.QPushButton("Replace unreadable profiles…")
        self._replace_profiles.setVisible(bool(self._read_error))
        self._replace_profiles.clicked.connect(self._prepare_replacement)
        layout.addWidget(self._replace_profiles)
        if lock_model:
            note = QtWidgets.QLabel("This CAD task keeps its model settings. Its API key can be updated while paused.")
            note.setWordWrap(True)
            layout.addWidget(note)
        for widget in (self._choices, self._provider, self._effort, *profile_buttons):
            widget.setEnabled(not lock_model)
        for widget in (self._model, *self._edits.values()):
            widget.setReadOnly(lock_model)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        self._save_button = buttons.button(QtWidgets.QDialogButtonBox.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_choices()
        self._load_profile()
        self._choices.activated.connect(self._select_profile)
        self._provider.activated.connect(self._select_provider)
        self._edits["base_url"].textEdited.connect(self._address_changed)
        self._model.textEdited.connect(lambda _text: self._model_changed())
        self.setMinimumWidth(580)

    def accept(self):
        try:
            if self._read_error:
                raise ValueError(self._read_error)
            self._capture()
            saved, selected_config = [], None
            for name, row in self._profiles.items():
                original = self._original_profiles.get(name)
                values = config_values(row)
                if name == self._selected and self._lock_model:
                    if semantic_inputs(row) != semantic_inputs(self._initial_config):
                        raise ValueError("The CAD task keeps its original model settings.")
                    config = self._initial_config
                elif name == self._selected or original is None or values != config_values(original):
                    config = validate_config(values)
                else:
                    config = None
                raw = self._keys.get(name)
                if name == self._selected and not raw:
                    raise ValueError("Enter the API key for the selected profile.")
                if raw and (not raw.isascii() or re.search(r"[\x00-\x20\x7f]", raw)):
                    raise ValueError("API keys must contain ASCII characters without spaces or control characters.")
                item = {"name": name, **dict(row)}
                item.update(context_tokens=values["context_tokens"], output_tokens=values["output_tokens"])
                if os.name == "nt":
                    if raw is not None and (original is None or raw != self._initial_keys.get(name)
                                            or raw and not row.get("protected_key")):
                        item.pop("protected_key", None)
                        if raw:
                            connection = profile_connection(row["provider"], row["base_url"])
                            item["protected_key"] = protect_key(raw, connection["credential_binding"])
                else:
                    item.pop("protected_key", None)
                saved.append(item)
                if name == self._selected:
                    selected_config = config
            self._save_settings(self._stored_profiles, json.dumps({"selected": self._selected, "items": saved}, ensure_ascii=False))
            self.config = selected_config
            self.credentials = {"api_key": self._keys[self._selected]}
            self.profile_name = self._selected
        except (ValueError, TypeError, OverflowError) as exc:
            self._error.setText(str(exc))
            return
        except RuntimeError:
            self._error.setText("Model profiles could not be saved. The settings have not been applied.")
            return
        super().accept()

    def take_credentials(self):
        credentials, self.credentials = self.credentials, None
        return credentials

    def clear_credentials(self):
        self._credential.clear()
        self._keys.clear()
        self._initial_keys.clear()
        if self.credentials is not None:
            self.credentials.clear()
            self.credentials = None

    def reject(self):
        self.clear_credentials()
        super().reject()

    def _unique_name(self, stem):
        name, index = stem, 2
        while name in self._profiles:
            name, index = "%s %s" % (stem, index), index + 1
        return name

    def _refresh_choices(self):
        self._choices.clear()
        self._choices.addItems(list(self._profiles))
        self._choices.setCurrentText(self._selected)

    def _capture(self):
        row = self._profiles[self._selected]
        row.update({field: edit.text().strip() for field, edit in self._edits.items()})
        row.update(model=self._model.text().strip(), provider=self._provider.currentText(),
                   reasoning_effort=self._effort.currentText())
        self._keys[self._selected] = self._credential.text()

    def _load_profile(self):
        row = self._profiles[self._selected]
        self._provider.setCurrentText(row["provider"])
        self._model.setText(row["model"])
        self._set_efforts(row["reasoning_effort"])
        for field, edit in self._edits.items():
            edit.setText(str(row[field]))
        raw = self._keys.get(self._selected)
        if raw is None:
            raw = ""
            try:
                if os.name == "nt" and row.get("protected_key"):
                    connection = profile_connection(row["provider"], row["base_url"])
                    raw = open_key(row["protected_key"], connection["credential_binding"])
            except ValueError as exc:
                self._error.setText(str(exc))
        self._credential.setText(raw)
        self._initial_keys.setdefault(self._selected, raw)

    def _set_efforts(self, selected):
        choices = profile_efforts(self._provider.currentText(), self._model.text().strip())
        self._effort.clear()
        self._effort.addItems(list(choices))
        if selected not in choices:
            self._effort.addItem(selected)
        self._effort.setCurrentText(selected)

    def _model_changed(self):
        self._set_efforts(self._effort.currentText())

    def _select_profile(self, index):
        name = self._choices.itemText(index)
        self._capture()
        self._selected = name
        self._load_profile()

    def _new_profile(self):
        if self._lock_model:
            return
        self._capture()
        if len(self._profiles) >= 100:
            self._error.setText("Delete an unused profile before adding another.")
            return
        name = self._unique_name("New Profile")
        current = self._profiles[self._selected]
        self._profiles[name] = {**self._defaults[current["provider"]], "base_url": current["base_url"],
                                "model": current["model"]}
        self._selected = name
        self._refresh_choices()
        self._load_profile()

    def _rename_profile(self):
        if self._lock_model:
            return
        name, accepted = QtWidgets.QInputDialog.getText(self, "Rename profile", "Profile name", text=self._selected)
        name = name.strip()
        if not accepted or name == self._selected:
            return
        if not name or len(name) > 100 or name in self._profiles:
            self._error.setText("Use a unique profile name of 1–100 characters.")
            return
        self._capture()
        self._profiles[name] = self._profiles.pop(self._selected)
        self._keys[name] = self._keys.pop(self._selected, "")
        self._selected = name
        self._refresh_choices()

    def _delete_profile(self):
        if self._lock_model:
            return
        if len(self._profiles) == 1:
            self._error.setText("Keep at least one profile.")
            return
        del self._profiles[self._selected]
        self._keys.pop(self._selected, None)
        self._selected = next(iter(self._profiles))
        self._refresh_choices()
        self._load_profile()

    def _select_provider(self, _index):
        if self._lock_model:
            return
        self._profiles[self._selected] = dict(self._defaults[self._provider.currentText()])
        self._keys[self._selected] = ""
        self._load_profile()

    def _address_changed(self, text):
        row = self._profiles[self._selected]
        try:
            current = profile_connection(self._provider.currentText(), text)
            previous = profile_connection(row["provider"], row["base_url"])
            if current["endpoint"] == previous["endpoint"]:
                return
        except ValueError:
            pass
        self._credential.clear()
        self._keys[self._selected] = ""
        row.pop("protected_key", None)

    def _prepare_replacement(self):
        if not self._read_error:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Replace unreadable profiles",
            "The saved profiles cannot be read. Prepare a replacement from the selected profile? "
            "Save will replace the unreadable value with the profiles in this form. "
            "Nothing is changed until Save; Cancel keeps the saved value.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._capture()
        self._profiles = {self._selected: self._profiles[self._selected]}
        self._keys = {self._selected: self._keys[self._selected]}
        self._read_error = ""
        self._refresh_choices()
        self._replace_profiles.setEnabled(False)
        self._save_button.setText("Replace and save")
        self._error.setText("Replacement prepared. Save replaces unreadable profiles; Cancel keeps the saved value.")
