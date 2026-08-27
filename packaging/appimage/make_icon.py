"""Generates a simple placeholder app icon so appimagetool/desktop
integration has something to point at. Run with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


def make_icon(path: str, size: int = 256) -> None:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#1ED760"))  # Spotify green
    gradient.setColorAt(1, QColor("#0A0A0A"))  # Tidal black
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    painter.setPen(QColor("white"))
    font = QFont("Sans Serif", int(size * 0.34), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "S→T")

    painter.end()
    pixmap.save(path, "PNG")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    make_icon(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
