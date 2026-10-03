"""Neutral gray theme with subdued chrome and clear active controls."""
STYLE = '''
QWidget { font-family:"Malgun Gothic"; font-size:13px; background:#202020; color:#e6e6e6; }
QLabel, QCheckBox { background:transparent; }
QLabel#brand { font-size:16px; font-weight:600; padding-right:12px; }
QLabel#muted { color:#a0a0a0; }
QWidget#gradingPanel { background:#262626; border-radius:8px; }
QWidget#gradingPage, QWidget#gradingPanel QTabWidget,
QWidget#gradingPanel QStackedWidget, QWidget#gradingPanel QTabBar { background:#262626; }
QPushButton, QToolButton { background:#303030; border:1px solid #414141; padding:7px 10px; border-radius:5px; }
QPushButton:hover, QToolButton:hover { background:#3b3b3b; border-color:#626262; }
QPushButton:pressed, QToolButton:pressed, QPushButton:checked { background:#484848; border-color:#858585; }
QPushButton:focus, QToolButton:focus { border-color:#aaaaaa; }
QPushButton:disabled, QToolButton:disabled, QWidget:disabled { color:#777777; }
QPushButton#primary { background:#dedede; color:#202020; border-color:#dedede; }
QPushButton#primary:hover { background:#ffffff; }
QPushButton#primary:disabled { background:#333333; color:#777777; border-color:#414141; }
QPushButton#statusSummary { background:transparent; border:0; padding:4px 0; text-align:left; color:#a8a8a8; font-size:12px; }
QPushButton#statusSummary:hover { color:#eeeeee; }
QToolButton::menu-indicator { image:none; }
QLineEdit, QComboBox, QTextEdit, QDoubleSpinBox { background:#282828; border:1px solid #454545; padding:5px; border-radius:4px; selection-background-color:#555555; selection-color:#ffffff; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color:#909090; }
QComboBox QAbstractItemView { background:#282828; color:#e6e6e6; selection-background-color:#484848; selection-color:#ffffff; }
QMenu { background:#282828; border:1px solid #494949; padding:5px; }
QMenu::item { padding:8px 26px 8px 18px; border-radius:3px; }
QMenu::item:selected { background:#414141; }
QMenu::separator { height:1px; background:#464646; margin:5px; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:transparent; color:#999999; padding:9px 18px; border-bottom:2px solid #393939; }
QTabBar::tab:selected { color:#eeeeee; border-bottom:2px solid #cccccc; }
QTabBar::tab:hover { color:#ffffff; background:#2d2d2d; }
QSlider { min-height:22px; }
QSlider::groove:horizontal { height:4px; background:#444444; border-radius:2px; }
QSlider::sub-page:horizontal { background:#b5b5b5; border-radius:2px; }
QSlider::handle:horizontal { background:#dedede; border:1px solid #eeeeee; width:12px; margin:-5px 0; border-radius:6px; }
QSlider::handle:horizontal:hover { background:#ffffff; }
QSlider::handle:horizontal:disabled { background:#666666; border-color:#777777; }
QSlider::sub-page:horizontal:disabled { background:#555555; }
QCheckBox { spacing:7px; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #777777; border-radius:3px; background:#282828; }
QCheckBox::indicator:checked { background:#dddddd; border:3px solid #777777; }
QCheckBox::indicator:hover { border-color:#bbbbbb; }
QScrollBar:vertical { background:#242424; width:10px; margin:0; }
QScrollBar::handle:vertical { background:#555555; min-height:25px; border-radius:4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QToolTip { background:#383838; color:#eeeeee; border:1px solid #666666; padding:5px; }
'''
