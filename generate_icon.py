import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QBrush, QLinearGradient, QPolygonF
from PyQt6.QtCore import Qt, QPointF, QRectF

app = QApplication(sys.argv)
size = 1024
image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
image.fill(Qt.GlobalColor.transparent)

painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)

# Gradient background
grad = QLinearGradient(0, 0, size, size)
grad.setColorAt(0, QColor("#1e3a8a"))  # Blue-900
grad.setColorAt(1, QColor("#0f172a"))  # Slate-900
painter.setBrush(QBrush(grad))
painter.setPen(Qt.PenStyle.NoPen)
painter.drawRoundedRect(0, 0, size, size, 224, 224)

# Candlesticks
painter.setPen(Qt.PenStyle.NoPen)

# Red candle
painter.setBrush(QBrush(QColor("#ef4444")))
# Wick
painter.drawRect(QRectF(size*0.3 - 8, size*0.4, 16, size*0.4))
# Body
painter.drawRoundedRect(QRectF(size*0.3 - 48, size*0.5, 96, size*0.2), 12, 12)

# Green candle
painter.setBrush(QBrush(QColor("#22c55e")))
# Wick
painter.drawRect(QRectF(size*0.7 - 8, size*0.15, 16, size*0.5))
# Body
painter.drawRoundedRect(QRectF(size*0.7 - 48, size*0.25, 96, size*0.3), 12, 12)

# Line chart / Arrow
pen = QPen(QColor("#22c55e"))
pen.setWidth(48)
pen.setCapStyle(Qt.PenCapStyle.RoundCap)
pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
painter.setPen(pen)
painter.drawLine(int(size*0.15), int(size*0.8), int(size*0.5), int(size*0.6))
painter.drawLine(int(size*0.5), int(size*0.6), int(size*0.85), int(size*0.2))

# Arrow head
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QBrush(QColor("#22c55e")))
arrow = QPolygonF()
arrow.append(QPointF(size*0.85 + 24, size*0.2 - 24))
arrow.append(QPointF(size*0.85 - 80, size*0.2 - 24))
arrow.append(QPointF(size*0.85 + 24, size*0.2 + 80))
painter.drawPolygon(arrow)

painter.end()
image.save("icon.png")

print("Icon generated as icon.png")
