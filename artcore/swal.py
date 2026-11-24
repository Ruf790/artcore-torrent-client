"""Message boxes in the style of the site's SweetAlert2: big round icon, centred Oswald title,
pill buttons, the window behind dimmed.

install() swaps QMessageBox.question / information / warning / critical for these, so every
existing call site gets the new look without being rewritten. For custom buttons or a
"don't ask again" check box use Swal directly (see ask()).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget)

KINDS = {   # kind → (icon, colour key)
    "question": ("fa6s.question", "primary"),
    "info": ("fa6s.info", "info"),
    "success": ("fa6s.check", "success"),
    "warning": ("fa6s.exclamation", "warning"),
    "error": ("fa6s.xmark", "danger"),
    "danger": ("fa6s.trash-can", "danger"),
    "quit": ("fa6s.power-off", "danger"),
}


class _Dim(QWidget):
    """Semi-transparent layer over the parent window while the box is open."""
    def __init__(self, win: QWidget):
        super().__init__(win)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(win.rect())
        self.show()
        self.raise_()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 105))


class _ElideLabel(QLabel):
    """One line, "…" in the middle when it doesn't fit the width it actually gets."""
    def __init__(self, text: str):
        super().__init__()
        self._full = text
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, e):
        from PySide6.QtGui import QFontMetrics
        super().resizeEvent(e)
        self.setText(QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, max(10, self.width())))


class Swal(QDialog):
    def __init__(self, parent, kind: str, title: str, text: str = "",
                 buttons: list[tuple[str, str]] | None = None, default: int = 0, cancel: int | None = None,
                 checkbox: str = "", items: list[tuple[str, str, str]] | None = None, more: str = ""):
        """buttons: [(label, style)] left to right; style "" | "primary" | "danger".
        items: [(icon, name, detail)] shown in a framed list under the text (e.g. the torrents
        being removed); `more` is a last grey line such as "…and 5 more".
        exec() returns the index of the clicked button, or `cancel` on Esc / close."""
        from . import theme
        from .ui import C
        import qtawesome as qta
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title)
        buttons = buttons or [("OK", "primary")]
        self.cancel_index = len(buttons) - 1 if cancel is None else cancel
        self.result_index = self.cancel_index
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 26, 26, 26)
        box = QFrame(); box.setObjectName("swal")
        box.setStyleSheet(f"#swal {{ background: {C['body_bg']}; border: 1px solid {C['border']};"
                          f" border-radius: 16px; }}")
        sh = QGraphicsDropShadowEffect(box)
        sh.setBlurRadius(42); sh.setOffset(0, 10); sh.setColor(QColor(0, 0, 0, 95))
        box.setGraphicsEffect(sh)
        v = QVBoxLayout(box)
        v.setContentsMargins(36, 28, 36, 24)
        v.setSpacing(10)

        icon, tone = KINDS.get(kind, KINDS["info"])
        pm = QPixmap(176, 176); pm.setDevicePixelRatio(2.0); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        col = QColor(C[tone]); soft = QColor(col); soft.setAlpha(38)
        p.setPen(QPen(col, 3)); p.setBrush(soft); p.drawEllipse(QRectF(4, 4, 80, 80))
        qta.icon(icon, color=C[tone]).paint(p, 24, 24, 40, 40)
        p.end()
        ic = QLabel(); ic.setPixmap(pm); ic.setAlignment(Qt.AlignCenter)
        v.addWidget(ic)

        head = QLabel(title); head.setAlignment(Qt.AlignCenter); head.setWordWrap(True)
        head.setStyleSheet(f"font-family: '{theme.TITLE_FONT}'; font-size: 18pt; color: {C['emphasis']};")
        v.addWidget(head)
        if text:
            body = QLabel(text); body.setAlignment(Qt.AlignCenter); body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet(f"color: {C['secondary']}; font-size: 10.5pt;")
            v.addWidget(body)
        if items:
            v.addWidget(self._item_list(items, more, C))
        self.check = None
        if checkbox:
            self.check = QCheckBox(checkbox)
            v.addWidget(self.check, 0, Qt.AlignCenter)
        row = QHBoxLayout(); row.setSpacing(10); row.addStretch()
        self.buttons: list[QPushButton] = []
        for i, (label, style) in enumerate(buttons):
            b = QPushButton(label); b.setMinimumWidth(116)
            if style:
                b.setObjectName(style)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, i=i: self._pick(i))
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch()
        v.addSpacing(6)
        v.addLayout(row)
        if 0 <= default < len(self.buttons):
            self.buttons[default].setDefault(True)
            self.buttons[default].setFocus()
        outer.addWidget(box)
        self.setMinimumWidth(600 if items else 500)
        self.setMaximumWidth(680 if items else 640)

    @staticmethod
    def _item_list(items, more: str, C) -> QFrame:
        """Framed list: icon · name (elided in the middle) · grey detail on the right."""
        import qtawesome as qta
        from PySide6.QtGui import QFontMetrics
        fr = QFrame(); fr.setObjectName("swalItems")
        fr.setStyleSheet(f"#swalItems {{ background: {C['tertiary_bg']}; border: 1px solid {C['border']};"
                         f" border-radius: 10px; }} #swalItems QLabel {{ background: transparent; }}")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        for ic, name, detail in items:
            row = QHBoxLayout(); row.setSpacing(8)
            if ic:
                il = QLabel(); il.setPixmap(qta.icon(ic, color=C["secondary"]).pixmap(14, 14))
                row.addWidget(il, 0, Qt.AlignVCenter)
            if len(items) == 1 and not more:          # one torrent: the whole name, wrapped
                nl = QLabel(name.replace(".", ".\u200b").replace("_", "_\u200b").replace("-", "-\u200b"))
                nl.setWordWrap(True)
            else:
                nl = _ElideLabel(name)
            nl.setStyleSheet(f"color: {C['emphasis']};")
            f = nl.font(); f.setPointSizeF(10); f.setBold(True); nl.setFont(f)
            nl.setToolTip(name)
            nl.setMinimumWidth(60)
            row.addWidget(nl, 1)
            if detail:
                dl = QLabel(detail); dl.setStyleSheet(f"color: {C['secondary']}; font-size: 9.5pt;")
                row.addWidget(dl, 0, Qt.AlignRight | Qt.AlignVCenter)
            lay.addLayout(row)
        if more:
            ml = QLabel(more); ml.setStyleSheet(f"color: {C['secondary']}; font-size: 9.5pt;")
            lay.addWidget(ml)
        return fr

    def _pick(self, i: int):
        self.result_index = i
        self.accept()

    def reject(self):
        self.result_index = self.cancel_index
        super().reject()

    def exec(self) -> int:       # noqa: A003 — returns the button index
        win = self.parent().window() if isinstance(self.parent(), QWidget) else None
        dim = _Dim(win) if win is not None and win.isVisible() else None
        self.adjustSize()
        if win is not None and win.isVisible():
            g = win.frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2 - 30)
        try:
            super().exec()
        finally:
            if dim is not None:          # hide now: deleteLater alone waits for the event loop
                dim.hide()
                dim.setParent(None)
                dim.deleteLater()
        return self.result_index

    def checked(self) -> bool:
        return bool(self.check and self.check.isChecked())


def ask(parent, kind: str, title: str, text: str = "", ok: str = "", ok_style: str = "primary",
        cancel: str = "", checkbox: str = "") -> tuple[bool, bool]:
    """Yes / no question → (confirmed, check box ticked)."""
    from .i18n import t
    box = Swal(parent, kind, title, text, [(ok or t("yes"), ok_style), (cancel or t("cancel"), "")],
               default=0, cancel=1, checkbox=checkbox)
    return box.exec() == 0, box.checked()


def _split(title: str, text: str) -> tuple[str, str]:
    """QMessageBox(title=window caption, text=message) → heading + body: a short single-line
    message becomes the heading itself."""
    from . import APP_TITLE
    text = text or ""
    if len(text) <= 70 and "\n" not in text:
        return text, ""
    return (title if title and title != APP_TITLE else ""), text


def install() -> None:
    """Route QMessageBox's static helpers through Swal (call once after QApplication exists)."""
    from .i18n import t
    SB = QMessageBox.StandardButton

    def question(parent, title, text, buttons=SB.Yes | SB.No, default=SB.NoButton):
        head, body = _split(title, text)
        ok, _ = ask(parent, "question", head or title, body, ok=t("yes"), cancel=t("no"))
        return SB.Yes if ok else SB.No

    def make(kind):
        def show(parent, title, text, buttons=SB.Ok, default=SB.NoButton):
            head, body = _split(title, text)
            Swal(parent, kind, head or title, body, [("OK", "primary")], 0, 0).exec()
            return SB.Ok
        return staticmethod(show)

    QMessageBox.question = staticmethod(question)
    QMessageBox.information = make("info")
    QMessageBox.warning = make("warning")
    QMessageBox.critical = make("error")
