from dataclasses import dataclass
from PySide6.QtCore import QRectF


@dataclass
class Eye:
    x_ratio: float
    y_ratio: float
    width_ratio: float
    height_ratio: float
    corner_radius: float = 28.0
    is_closed: bool = False

    def get_rect(self, widget_width: int, widget_height: int) -> QRectF:
        x = widget_width * self.x_ratio
        y = widget_height * self.y_ratio
        width = widget_width * self.width_ratio
        height = widget_height * self.height_ratio
        return QRectF(x, y, width, height)