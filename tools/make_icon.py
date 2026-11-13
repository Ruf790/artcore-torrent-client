"""Render the logo from artcore/ui.py into assets/icon.ico and the icon of unfinished files
(".!ac") into assets/incomplete.ico (run once before build)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication
app = QApplication([])
from artcore import theme
theme.load_fonts()
from artcore.ui import app_icon

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def save_ico(render, out: str) -> None:
    """Every size drawn on its own (crisp small icons) and packed into one .ico.
    Without Pillow only the 256 px image is written (Windows scales it down)."""
    from io import BytesIO
    try:
        from PIL import Image
    except ImportError:
        render(256).save(out, "ICO")
        print("saved", out, "(256 px only — pip install pillow for all sizes)")
        return
    imgs = []
    for n in SIZES:
        img = render(n)
        ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        imgs.append(Image.open(BytesIO(bytes(ba))).convert("RGBA"))
    imgs[-1].save(out, format="ICO", sizes=[(n, n) for n in SIZES], append_images=imgs[:-1])
    print("saved", out)


def incomplete(n: int) -> QImage:
    """A sheet of paper with a folded corner, the ArtCore logo and a half-filled progress bar."""
    img = QImage(n, n, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = n / 256
    left, top, right, bottom, fold = 40 * s, 12 * s, 216 * s, 244 * s, 56 * s
    page = QPainterPath()
    page.moveTo(left, top + 10 * s)
    page.quadTo(left, top, left + 10 * s, top)
    page.lineTo(right - fold, top)
    page.lineTo(right, top + fold)
    page.lineTo(right, bottom - 10 * s)
    page.quadTo(right, bottom, right - 10 * s, bottom)
    page.lineTo(left + 10 * s, bottom)
    page.quadTo(left, bottom, left, bottom - 10 * s)
    page.closeSubpath()
    g = QLinearGradient(0, top, 0, bottom)
    g.setColorAt(0, QColor("#ffffff")); g.setColorAt(1, QColor("#eef2f8"))
    p.setPen(QPen(QColor("#9aa5b4"), max(1.0, 6 * s)))
    p.setBrush(g)
    p.drawPath(page)
    corner = QPainterPath()
    corner.moveTo(right - fold, top)
    corner.lineTo(right - fold, top + fold - 8 * s)
    corner.quadTo(right - fold, top + fold, right - fold + 8 * s, top + fold)
    corner.lineTo(right, top + fold)
    corner.closeSubpath()
    p.setBrush(QColor("#d6dde8"))
    p.drawPath(corner)
    # logo
    ls = 120 * s if n >= 32 else 150 * s
    lx, ly = (left + right) / 2 - ls / 2, (60 if n >= 32 else 40) * s
    p.drawPixmap(QRectF(lx, ly, ls, ls).toRect(), app_icon(max(16, int(ls))).pixmap(int(ls), int(ls)))
    # progress bar: unfinished
    bh = max(3.0, 30 * s)
    bar = QRectF(left + 22 * s, bottom - 26 * s - bh, right - left - 44 * s, bh)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#cfd8e3"))
    p.drawRoundedRect(bar, bh / 2, bh / 2)
    p.setBrush(QColor("#0d6efd"))
    p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * 0.6, bh), bh / 2, bh / 2)
    p.end()
    return img


if __name__ == "__main__":
    out = os.path.join(ROOT, "assets", "icon.ico")
    app_icon(256).pixmap(256, 256).save(out, "ICO")       # the exe / window icon, as before
    print("saved", out)
    save_ico(incomplete, os.path.join(ROOT, "assets", "incomplete.ico"))
