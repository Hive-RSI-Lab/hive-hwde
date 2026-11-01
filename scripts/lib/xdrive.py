"""xdrive.py - drive a GUI program on a private Xvfb with the keyboard only.

KiCad has GUI-only steps (eeschema's non-KiCad schematic import has no
kicad-cli verb). This is the least that runs one headless: our own Xvfb, the
X11 window tree (find a window by its title), focus, and XTEST key presses.
ctypes on libX11/libXtst, so no Python package: the hwde venv stays as
requirements.lock pins it. Keys only, never coordinates: menus move between
KiCad versions and themes, a hotkey bound in user.hotkeys doesn't.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import subprocess
import time

_KEYSYM = {"/": "slash", ".": "period", "-": "minus", "_": "underscore",
           " ": "space", "~": "asciitilde", "+": "plus", "=": "equal",
           ",": "comma", ":": "colon", "(": "parenleft", ")": "parenright",
           "@": "at", "#": "numbersign", "&": "ampersand", "'": "apostrophe"}
_SHIFTED = set("_~+:()@#&ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class XError(RuntimeError):
    pass


def start_xvfb() -> tuple[subprocess.Popen, str]:
    """A private Xvfb; returns (process, ':N')."""
    if not shutil.which("Xvfb"):
        raise XError("Xvfb not on PATH")
    r, w = os.pipe()
    proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "1600x1000x24",
                             "-nolisten", "tcp"], pass_fds=(w,),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    with os.fdopen(r) as f:
        n = f.readline().strip()
    if not n:
        proc.kill()
        raise XError("Xvfb did not come up")
    return proc, f":{n}"


class Display:
    def __init__(self, name: str):
        self.x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.t = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x = self.x
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        x.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.t.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        # an X error (a window gone between QueryTree and FetchName) must not exit us
        self._handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda d, e: 0)
        x.XSetErrorHandler(self._handler)
        self.d = x.XOpenDisplay(name.encode())
        if not self.d:
            raise XError(f"can't open display {name}")
        self.root = x.XDefaultRootWindow(self.d)
        self._net_wm_name = x.XInternAtom(self.d, b"_NET_WM_NAME", 0)
        self._utf8 = x.XInternAtom(self.d, b"UTF8_STRING", 0)

    def title(self, w: int) -> str | None:
        """_NET_WM_NAME (wx/GTK keep the real title there; WM_NAME may be
        just the program name), else WM_NAME."""
        typ, fmt = ctypes.c_ulong(), ctypes.c_int()
        n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        if self.x.XGetWindowProperty(self.d, w, self._net_wm_name, 0, 1024, 0, self._utf8,
                                     ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(n),
                                     ctypes.byref(after), ctypes.byref(data)) == 0 and data.value:
            text = ctypes.string_at(data.value, n.value).decode(errors="replace")
            self.x.XFree(data)
            if text:
                return text
        name = ctypes.c_char_p()
        if self.x.XFetchName(self.d, w, ctypes.byref(name)) and name.value:
            return name.value.decode(errors="replace")
        return None

    def close(self) -> None:
        self.x.XCloseDisplay(self.d)

    def windows(self) -> list[tuple[int, str]]:
        """Every named window under the root, (id, title)."""
        out, todo = [], [self.root]
        while todo:
            w = todo.pop()
            t = self.title(w) if w != self.root else None
            if t:
                out.append((w, t))
            root, parent = ctypes.c_ulong(), ctypes.c_ulong()
            kids, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
            if self.x.XQueryTree(self.d, w, ctypes.byref(root), ctypes.byref(parent),
                                 ctypes.byref(kids), ctypes.byref(n)):
                todo.extend(kids[i] for i in range(n.value))
                if kids:
                    self.x.XFree(kids)
        return out

    def wait_window(self, pred, timeout: float, gone: bool = False) -> int | None:
        """The first window whose title satisfies pred (or, gone=True, wait
        until none does). None on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            hit = [w for w, n in self.windows() if pred(n)]
            if hit and not gone:
                return hit[0]
            if gone and not hit:
                return 0
            time.sleep(0.5)
        return None

    def focus(self, w: int) -> None:
        self.x.XRaiseWindow(self.d, w)
        self.x.XSetInputFocus(self.d, w, 1, 0)  # RevertToPointerRoot, CurrentTime
        self.x.XSync(self.d, 0)
        time.sleep(0.3)

    def _code(self, keysym: str) -> int:
        ks = self.x.XStringToKeysym(keysym.encode())
        code = self.x.XKeysymToKeycode(self.d, ks)
        if not code:
            raise XError(f"no keycode for {keysym!r}")
        return code

    def key(self, combo: str) -> None:
        """'Control_L+s', 'Return', 'Escape'."""
        codes = [self._code(k) for k in combo.split("+")]
        for c in codes:
            self.t.XTestFakeKeyEvent(self.d, c, 1, 0)
        for c in reversed(codes):
            self.t.XTestFakeKeyEvent(self.d, c, 0, 0)
        self.x.XSync(self.d, 0)
        time.sleep(0.3)

    def type(self, text: str) -> None:
        shift = self._code("Shift_L")
        for ch in text:
            c = self._code(_KEYSYM.get(ch, ch))
            if ch in _SHIFTED:
                self.t.XTestFakeKeyEvent(self.d, shift, 1, 0)
            self.t.XTestFakeKeyEvent(self.d, c, 1, 0)
            self.t.XTestFakeKeyEvent(self.d, c, 0, 0)
            if ch in _SHIFTED:
                self.t.XTestFakeKeyEvent(self.d, shift, 0, 0)
            self.x.XSync(self.d, 0)
            time.sleep(0.02)
