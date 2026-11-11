"""Render the logo from artcore/ui.py into assets/icon.ico (run once before build)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from artcore import theme
theme.load_fonts()
from artcore.ui import app_icon
out = os.path.join(ROOT, "assets", "icon.ico")
app_icon(256).pixmap(256, 256).save(out, "ICO")
print("saved", out)
