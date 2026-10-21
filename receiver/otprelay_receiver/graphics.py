# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pictures drawn at runtime, so the app ships no image files."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QIcon, QPainter, QPainterPath,
                           QPixmap)

STATUS_COLORS = {
    "connected": "#2ecc71",
    "connecting": "#f0a30a",
    "reconnecting": "#f0a30a",
    "offline": "#e74c3c",
    "unpaired": "#e74c3c",
    "none": "#95a5a6",
}


def _key_color() -> QColor:
    """A colour that stays visible in both light and dark trays."""
    app = QGuiApplication.instance()
    if app is not None:
        return app.palette().windowText().color()
    return QColor("#dddddd")


def _key_path() -> QPainterPath:
    """The key from assets/icon.svg, on the same 108x108 grid."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addEllipse(QRectF(18, 34, 36, 36))   # bow, outer edge
    path.addEllipse(QRectF(28, 44, 16, 16))   # the hole in it
    shaft = QPainterPath()
    shaft.addRect(QRectF(46, 46, 42, 12))     # shaft
    shaft.addRect(QRectF(64, 58, 10, 16))     # long tooth
    shaft.addRect(QRectF(79, 58, 9, 11))      # short tooth
    return path.united(shaft)


def tray_icon(status: str) -> QIcon:
    """The key glyph with a status dot, drawn to suit light and dark trays."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # The key occupies the upper left so the dot has a clear corner of its own.
    p.save()
    p.scale(size / 108 * 0.92, size / 108 * 0.92)
    p.translate(-6, 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_key_color())
    p.drawPath(_key_path())
    p.restore()

    dot = QColor(STATUS_COLORS.get(status, STATUS_COLORS["none"]))
    p.setBrush(dot)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(size - 22, size - 22, 20, 20))
    p.end()
    return QIcon(pixmap)


def qr_pixmap(text: str, target: int = 260) -> QPixmap:
    """Render text as a QR code, drawn module by module."""
    import qrcode

    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    scale = max(1, target // modules)
    side = modules * scale

    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.white)
    p = QPainter(pixmap)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(Qt.GlobalColor.black)
    for y, row in enumerate(matrix):
        for x, filled in enumerate(row):
            if filled:
                p.drawRect(x * scale, y * scale, scale, scale)
    p.end()
    return pixmap
