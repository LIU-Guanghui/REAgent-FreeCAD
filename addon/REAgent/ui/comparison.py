"""Manual comparison choices and presentation; no modeling or acceptance decisions."""

import json

from REAgent.ui import QtWidgets
from REAgent.ui.panel import plain_label
from REAgent.session.state_snapshots import comparison_contract, tool_message


class ComparisonDialog(QtWidgets.QDialog):
    def __init__(self, parent, facts):
        super().__init__(parent)
        self.contract = comparison_contract()
        self.setWindowTitle("Compare Source and Target")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(plain_label("Source: " + str(facts["source_document"])
                                     + "\nTarget: " + str(facts["target_document"])))
        self.geometry = QtWidgets.QGroupBox("Geometry")
        self.geometry.setCheckable(True)
        self.geometry.setChecked(True)
        grid = QtWidgets.QGridLayout(self.geometry)
        self.measurements, self.booleans = {}, {}
        definitions = self.contract["validation"]["properties"]
        for index, name in enumerate(definitions["measurements"]["items"]["enum"]):
            box = QtWidgets.QCheckBox(name.replace("_", " ").title())
            box.setChecked(name in ("volume", "area", "bounding_box"))
            self.measurements[name] = box
            grid.addWidget(box, index // 2, index % 2)
        self.sampling = QtWidgets.QCheckBox("Approximate surface distances")
        grid.addWidget(self.sampling, 2, 0, 1, 2)
        for index, name in enumerate(definitions["boolean_operations"]["items"]["enum"]):
            box = QtWidgets.QCheckBox({"common": "Shared volume", "source_minus_target": "Missing from Target",
                                      "target_minus_source": "Extra in Target"}[name])
            self.booleans[name] = box
            grid.addWidget(box, 3 + index // 2, index % 2)
        layout.addWidget(self.geometry)
        self.views = QtWidgets.QGroupBox("Paired views")
        self.views.setCheckable(True)
        self.views.setChecked(True)
        grid = QtWidgets.QGridLayout(self.views)
        self.directions = {}
        for index, (name, label) in enumerate(self.contract["view_labels"].items()):
            box = QtWidgets.QCheckBox(label)
            box.setChecked(name in self.contract["default_views"])
            self.directions[name] = box
            grid.addWidget(box, index // 3, index % 3)
        self.section = QtWidgets.QCheckBox("Include half-cutaway")
        self.section_axis = QtWidgets.QComboBox()
        self.section_axis.addItems(self.contract["view"]["properties"]["section_axis"]["enum"])
        grid.addWidget(self.section, 5, 0, 1, 2)
        grid.addWidget(self.section_axis, 5, 2)
        layout.addWidget(self.views)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        compare = QtWidgets.QPushButton("Compare")
        compare.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(compare)
        layout.addLayout(row)
        self.options = self.view_request = None

    def _accept(self):
        options = None
        request = None
        if self.geometry.isChecked():
            options = {"measurements": [name for name, box in self.measurements.items() if box.isChecked()],
                       "boolean_operations": [name for name, box in self.booleans.items() if box.isChecked()],
                       "surface_sampling": self.sampling.isChecked()}
        if self.views.isChecked():
            selected = [name for name in self.contract["default_views"] if self.directions[name].isChecked()]
            selected.extend(name for name, box in self.directions.items()
                            if box.isChecked() and name not in selected)
            query = {"paired": True, "views": selected}
            if self.section.isChecked():
                query.update(include_section=True, section_axis=self.section_axis.currentText())
            request = {"kind": "view", "query": query}
        self.options, self.view_request = options, request
        self.accept()


def comparison_text(value):
    rows = ["Source / Target comparison", ""]
    reference = value.get("reference") or {}
    if reference.get("cross_revision"):
        rows.append("The results belong to different revisions. Compare again for a single current set.")
    rows.append("Applicability: " + str(reference.get("applicability", "unknown")))
    for step in value.get("steps", ()):
        rows.extend(("", "Geometry" if step["comparison_step"] == "geometry" else "Paired views"))
        summary = step["summary"]
        rows.append(tool_message({**step, "result": summary}))
        if step["revisions"]:
            rows.append("Revisions: " + json.dumps(step["revisions"], ensure_ascii=False))
        for name, check in summary.get("checks", {}).items():
            rows.append(name.replace("_", " ").title())
            rows.extend("  " + key.replace("_", " ").title() + ": " + json.dumps(item, ensure_ascii=False)
                        for key, item in check.items())
        if summary.get("image_delivery", {}).get("status") == "prepared":
            rows.append("Open Image to inspect the complete paired board.")
    return "\n".join(rows)
