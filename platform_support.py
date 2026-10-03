"""Platform state/cache locations and native macOS display pixel modes."""
import os
import sys
from pathlib import Path


def state_directory(*, platform=None, home=None):
    platform = platform or sys.platform
    home = Path(home or Path.home())
    if platform == 'darwin':
        return home/'Library/Application Support/GogglesHDMI-Next'
    return Path(os.environ.get('LOCALAPPDATA',str(home)))/'GogglesHDMI-Next'


def cache_directory(*, platform=None, home=None):
    if (platform or sys.platform) == 'darwin':
        return Path(home or Path.home())/'Library/Caches/GogglesHDMI-Next'
    return state_directory(platform=platform,home=home)


def mac_display_pixels(screen):
    """Map Qt's display ID name to CGDisplayMode's actual pixel dimensions."""
    import ctypes
    import re
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
    cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
    for name in ('CGDisplayModeGetPixelWidth','CGDisplayModeGetPixelHeight'):
        fn = getattr(cg,name);fn.argtypes = [ctypes.c_void_p];fn.restype = ctypes.c_size_t
    cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
    # Qt Cocoa screen name is the localized display name, not its CG ID.
    # Match CG display bounds against Qt geometry, never guess DPI scaling.
    class Point(ctypes.Structure):
        _fields_ = [('x',ctypes.c_double),('y',ctypes.c_double)]
    class Size(ctypes.Structure):
        _fields_ = [('width',ctypes.c_double),('height',ctypes.c_double)]
    class Rect(ctypes.Structure):
        _fields_ = [('origin',Point),('size',Size)]
    cg.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32)]
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32];cg.CGDisplayBounds.restype = Rect
    ids = (ctypes.c_uint32*32)();count = ctypes.c_uint32()
    if cg.CGGetActiveDisplayList(32,ids,ctypes.byref(count)):
        return None
    geometry = screen.geometry()
    matches = []
    for identifier in ids[:count.value]:
        rect = cg.CGDisplayBounds(identifier)
        if (round(rect.origin.x),round(rect.origin.y),round(rect.size.width),round(rect.size.height)) == (
                geometry.x(),geometry.y(),geometry.width(),geometry.height()):
            matches.append(identifier)
    if len(matches) != 1:
        return None
    mode = cg.CGDisplayCopyDisplayMode(matches[0])
    if not mode:
        return None
    try:
        return cg.CGDisplayModeGetPixelWidth(mode),cg.CGDisplayModeGetPixelHeight(mode)
    finally:
        cg.CGDisplayModeRelease(mode)
