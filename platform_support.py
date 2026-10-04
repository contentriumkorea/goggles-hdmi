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
    return mac_display_info(screen)['pixels']


def mac_display_info(screen,*,snapshot=None):
    """Match the same NSScreen points/flip Qt uses, then query its CG mode."""
    if snapshot is None:
        from PySide6.QtWidgets import QApplication
        if QApplication.platformName()!='cocoa':return {'pixels':None,'reason':'native_query_failed'}
    try:
        entries = _mac_display_snapshot() if snapshot is None else snapshot
        geometry = screen.geometry()
        key = (geometry.x(),geometry.y(),geometry.width(),geometry.height())
        matches = [entry for entry in entries if entry['geometry']==key]
        if len(matches)!=1:
            return {'pixels':None,'reason':'screen_ambiguous' if matches else 'screen_unmatched'}
        entry = matches[0]
        return {'pixels':entry['pixels'],'identifier':entry['identifier'],
                'reason':'ok' if entry['pixels'] else 'mode_unavailable'}
    except (OSError,AttributeError,ValueError,TypeError,RuntimeError):
        return {'pixels':None,'reason':'native_query_failed'}


def mac_window_state(window):
    """Read actual NSWindow flags on the UI thread; no IDs or titles returned."""
    from PySide6.QtWidgets import QApplication
    if QApplication.platformName()!='cocoa':return {}
    import ctypes
    try:
        objc=ctypes.CDLL('/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.argtypes=[ctypes.c_char_p];objc.sel_registerName.restype=ctypes.c_void_p
        address=ctypes.cast(objc.objc_msgSend,ctypes.c_void_p).value
        pointer=ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)(address)
        integer=ctypes.CFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p,ctypes.c_void_p)(address)
        boolean=ctypes.CFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)(address)
        native=pointer(int(window.winId()),objc.sel_registerName(b'window'))
        if not native:return {}
        return {'native_fullscreen':bool(integer(native,objc.sel_registerName(b'styleMask')) & (1<<14)),
                'native_visible':bool(boolean(native,objc.sel_registerName(b'isVisible'))),
                'native_minimized':bool(boolean(native,objc.sel_registerName(b'isMiniaturized')))}
    except (OSError,ValueError,TypeError,AttributeError,RuntimeError):return {}


def _mac_display_snapshot():
    """UI-thread AppKit lookup; identifiers never enter support reports."""
    import ctypes
    ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes=[ctypes.c_char_p];objc.objc_getClass.restype=ctypes.c_void_p
    objc.sel_registerName.argtypes=[ctypes.c_char_p];objc.sel_registerName.restype=ctypes.c_void_p
    address = ctypes.cast(objc.objc_msgSend,ctypes.c_void_p).value
    def send(result,*arguments):
        return ctypes.CFUNCTYPE(result,ctypes.c_void_p,ctypes.c_void_p,*arguments)(address)
    pointer=send(ctypes.c_void_p)
    integer=send(ctypes.c_ulong)
    display_number=send(ctypes.c_uint32)
    indexed=send(ctypes.c_void_p,ctypes.c_ulong)
    keyed=send(ctypes.c_void_p,ctypes.c_void_p)
    def selector(name):return objc.sel_registerName(name.encode('ascii'))
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
    cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
    for name in ('CGDisplayModeGetPixelWidth','CGDisplayModeGetPixelHeight'):
        fn = getattr(cg,name);fn.argtypes = [ctypes.c_void_p];fn.restype = ctypes.c_size_t
    cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
    class Point(ctypes.Structure):
        _fields_ = [('x',ctypes.c_double),('y',ctypes.c_double)]
    class Size(ctypes.Structure):
        _fields_ = [('width',ctypes.c_double),('height',ctypes.c_double)]
    class Rect(ctypes.Structure):
        _fields_ = [('origin',Point),('size',Size)]
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32];cg.CGDisplayBounds.restype = Rect
    cg.CGMainDisplayID.restype=ctypes.c_uint32
    primary_height=cg.CGDisplayBounds(cg.CGMainDisplayID()).size.height
    screens=pointer(objc.objc_getClass(b'NSScreen'),selector('screens'))
    frame_of=send(Rect)
    cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFStringCreateWithCString.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype=ctypes.c_void_p
    cf.CFRelease.argtypes=[ctypes.c_void_p]
    key=cf.CFStringCreateWithCString(None,b'NSScreenNumber',0x08000100)
    if not key:raise OSError('Native display key unavailable')
    entries=[]
    try:
        for index in range(min(32,integer(screens,selector('count')))):
            screen=indexed(screens,selector('objectAtIndex:'),index)
            rect=frame_of(screen,selector('frame'))
            description=pointer(screen,selector('deviceDescription'))
            number=keyed(description,selector('objectForKey:'),key)
            identifier=display_number(number,selector('unsignedIntValue'))
            mode=cg.CGDisplayCopyDisplayMode(identifier)
            pixels=None
            if mode:
                try:
                    width,height=cg.CGDisplayModeGetPixelWidth(mode),cg.CGDisplayModeGetPixelHeight(mode)
                    if width>0 and height>0:pixels=(width,height)
                finally:cg.CGDisplayModeRelease(mode)
            from PySide6.QtCore import QRectF
            geometry=QRectF(rect.origin.x,primary_height-rect.origin.y-rect.size.height,
                            rect.size.width,rect.size.height).toRect()
            entries.append({'identifier':identifier,'pixels':pixels,
                'geometry':(geometry.x(),geometry.y(),geometry.width(),geometry.height())})
        return entries
    finally:
        if key:cf.CFRelease(key)
