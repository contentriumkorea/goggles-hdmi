"""Recover the control window using Qt logical work areas, without moving HDMI."""
from PySide6.QtCore import QRect


def visible_control_rect(frame, minimum, work_areas):
    """Keep valid placement, including negative monitor coordinates; otherwise fit."""
    areas = [area for area in work_areas if not area.isEmpty()]
    if not areas or any(area.contains(frame) for area in areas):
        return QRect(frame)
    fitting = [area for area in areas if area.width()>=minimum.width() and area.height()>=minimum.height()]
    def overlap(area):
        intersection = area.intersected(frame)
        return intersection.width()*intersection.height()
    # The caller lists the primary screen first, used when every overlap is zero.
    area = max(fitting or areas,key=overlap)
    width = max(minimum.width(),min(frame.width(),area.width()-24))
    height = max(minimum.height(),min(frame.height(),area.height()-24))
    return QRect(area.left()+max(0,(area.width()-width)//2),
                 area.top()+max(0,(area.height()-height)//2),width,height)
