# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pictures drawn at runtime, so the app ships no image files."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap

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


def tray_icon(status: str) -> QIcon:
    """A key glyph with a small status dot in the corner."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    colour = _key_color()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    # Bow of the key: a ring.
    p.drawEllipse(QRectF(6, 14, 26, 26))
    p.setBrush(QColor(0, 0, 0, 0))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setBrush(Qt.GlobalColor.black)
    p.drawEllipse(QRectF(14, 22, 10, 10))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    # Shaft and teeth.
    p.setBrush(colour)
    p.drawRect(QRectF(30, 23, 26, 8))
    p.drawRect(QRectF(44, 31, 6, 10))
    p.drawRect(QRectF(52, 31, 5, 7))

    dot = QColor(STATUS_COLORS.get(status, STATUS_COLORS["none"]))
    p.setBrush(dot)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(38, 42, 20, 20))
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
