from PySide6.QtCore import QRect, QSize


def place(frame,minimum,areas):
    import importlib.util
    assert importlib.util.find_spec('window_placement'), 'Off-screen window recovery is missing'
    from window_placement import visible_control_rect
    return visible_control_rect(frame,minimum,areas)


def test_disconnected_upper_monitor_returns_window_to_primary():
    area=QRect(0,0,1920,1152)
    result=place(QRect(-6,-929,1098,718),QSize(960,620),[area,QRect(0,1200,1920,550)])
    assert area.contains(result)
    assert result.size()==QSize(1098,718)


def test_valid_negative_monitor_position_is_preserved():
    frame=QRect(-1700,100,1240,780)
    assert place(frame,QSize(960,620),[QRect(0,0,1920,1080),QRect(-1920,0,1920,1080)])==frame


def test_short_secondary_display_uses_primary_that_fits_controls():
    primary=QRect(0,0,1920,1152)
    result=place(QRect(50,1250,1240,780),QSize(960,620),[primary,QRect(0,1200,1920,550)])
    assert primary.contains(result)


def test_oversized_window_is_resized_inside_available_work_area():
    work=QRect(100,50,1280,720)
    result=place(QRect(20,-200,1800,1000),QSize(960,620),[work])
    assert work.contains(result)
    assert result.width()>=960 and result.height()>=620


def test_small_display_still_exposes_title_bar_without_shrinking_below_minimum():
    work=QRect(0,0,800,600)
    result=place(QRect(-2000,-2000,1240,780),QSize(960,620),[work])
    assert work.contains(result.topLeft())
    assert result.width()>=960 and result.height()>=620
