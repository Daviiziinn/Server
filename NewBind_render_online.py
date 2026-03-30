# -*- coding: utf-8 -*-
"""
NewBind único: key system compacto + app principal no mesmo arquivo.
Gerado automaticamente a partir dos arquivos enviados pelo usuário.
"""

import ctypes
import json
import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import keyboard
import psutil
from pynput import mouse
from pynput.mouse import Button, Controller
from PySide6.QtCore import Qt, QTimer, QEasingCurve, QPropertyAnimation, QRect, QObject, QEvent, Property, Signal, QPoint
from PySide6.QtGui import QKeyEvent, QColor, QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
)



def resource_path(relative_path: str) -> Path:
    try:
        base_path = Path(getattr(sys, "_MEIPASS"))
    except Exception:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path

try:
    import win32gui
    import win32process
except Exception:
    win32gui = None
    win32process = None


# ============================================================
# HOTKEY ENGINE (processo separado)
# ============================================================
ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class InputBackend:
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0

    VK_W = 0x57
    VK_A = 0x41
    VK_S = 0x53
    VK_D = 0x44
    VK_P = 0x50

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT

    @classmethod
    def get_scan_code(cls, vk: int) -> int:
        return cls.user32.MapVirtualKeyW(vk, cls.MAPVK_VK_TO_VSC)

    @classmethod
    def send_key(cls, vk: int, key_up: bool = False):
        scan = cls.get_scan_code(vk)
        if not scan:
            raise RuntimeError(f"Não foi possível obter scan code da tecla VK={vk}")

        flags = cls.KEYEVENTF_SCANCODE
        if key_up:
            flags |= cls.KEYEVENTF_KEYUP

        inp = INPUT()
        inp.type = cls.INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        result = cls.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if result != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    @classmethod
    def key_down(cls, vk: int):
        cls.send_key(vk, False)

    @classmethod
    def key_up(cls, vk: int):
        cls.send_key(vk, True)


class HotkeyEngine:
    def __init__(self):
        self.enabled = False
        self.macroAtivo = False
        self.pauseEmAndamento = False
        self.reativarPermitido = False
        self.shiftSegurandoMovimento = False
        self.shiftAguardandoLButton = False
        self.macroCanceladoPorC = False
        self.wMacroAtivo = False
        self.left_mouse_pressed = False
        self.right_mouse_pressed = False
        self.last_shift_time = 0.0
        self.shift_cooldown = 0.20
        self.space_delay_ms = 750
        self.macroEstavaAtivoAntesDoSpace = False
        self.macroEstavaAtivoAntesDoLClick = False
        self.hooks = []
        self.mouse_listener = None
        self.closing = False
        self._space_timer = None
        self.on_state_change = None
        self.on_schedule = None
        self.on_cancel_schedule = None

    def emit_state(self):
        if callable(self.on_state_change):
            try:
                self.on_state_change(
                    {
                        "enabled": self.enabled,
                        "macroAtivo": self.macroAtivo,
                        "pauseEmAndamento": self.pauseEmAndamento,
                        "space_delay_ms": self.space_delay_ms,
                    }
                )
            except Exception:
                pass

    def schedule(self, delay_ms: int, callback):
        if callable(self.on_schedule):
            return self.on_schedule(delay_ms, callback)
        return None

    def cancel_schedule(self, handle):
        if handle is not None and callable(self.on_cancel_schedule):
            self.on_cancel_schedule(handle)

    def is_any_move_pressed(self):
        return keyboard.is_pressed("w") or keyboard.is_pressed("a") or keyboard.is_pressed("s") or keyboard.is_pressed("d")

    def SoltarPW(self):
        for vk in (InputBackend.VK_P, InputBackend.VK_W):
            try:
                InputBackend.key_up(vk)
            except Exception:
                pass
        self.macroAtivo = False
        self.wMacroAtivo = False
        self.emit_state()

    def SoltarTudo(self):
        self.SoltarPW()
        self.pauseEmAndamento = False
        self.reativarPermitido = False
        self.shiftSegurandoMovimento = False
        self.shiftAguardandoLButton = False
        self.macroCanceladoPorC = False
        self.macroEstavaAtivoAntesDoSpace = False
        self.macroEstavaAtivoAntesDoLClick = False
        self.left_mouse_pressed = False
        self.right_mouse_pressed = False
        self.emit_state()

    def ativar_macro_hold(self):
        if not self.enabled or self.pauseEmAndamento or self.macroAtivo or self.left_mouse_pressed:
            return
        try:
            InputBackend.key_down(InputBackend.VK_P)
            InputBackend.key_down(InputBackend.VK_W)
            self.macroAtivo = True
            self.wMacroAtivo = True
            self.reativarPermitido = True
            self.macroCanceladoPorC = False
            self.emit_state()
        except Exception:
            self.SoltarPW()

    def cancelar_macro(self):
        self.SoltarPW()
        self.reativarPermitido = False
        self.shiftSegurandoMovimento = False
        self.shiftAguardandoLButton = False
        self.macroCanceladoPorC = True
        self.macroEstavaAtivoAntesDoLClick = False
        self.emit_state()

    def desativar_sistema_temporariamente_space(self):
        if self.pauseEmAndamento:
            return
        self.macroEstavaAtivoAntesDoSpace = self.macroAtivo
        self.pauseEmAndamento = True
        self.SoltarPW()
        self.emit_state()
        if self._space_timer is not None:
            self.cancel_schedule(self._space_timer)
            self._space_timer = None
        self._space_timer = self.schedule(self.space_delay_ms, self.reativar_sistema_apos_space)

    def reativar_sistema_apos_space(self):
        self._space_timer = None
        if self.closing:
            return
        self.pauseEmAndamento = False
        if self.enabled and self.macroEstavaAtivoAntesDoSpace and not self.is_any_move_pressed() and not self.left_mouse_pressed:
            self.ativar_macro_hold()
        self.macroEstavaAtivoAntesDoSpace = False
        self.emit_state()

    def setup_hooks(self):
        if self.hooks or self.mouse_listener is not None:
            return
        self.hooks.append(keyboard.hook_key("left shift", self.on_left_shift, suppress=False))
        self.hooks.append(keyboard.hook_key("a", self.on_a_down_up))
        self.hooks.append(keyboard.hook_key("s", self.on_s_down_up))
        self.hooks.append(keyboard.hook_key("d", self.on_d_down_up))
        self.hooks.append(keyboard.hook_key("w", self.on_w_down_up))
        self.hooks.append(keyboard.hook_key("space", self.on_space))
        self.hooks.append(keyboard.hook_key("c", self.on_c))
        self.mouse_listener = mouse.Listener(on_click=self.on_mouse_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    def on_left_shift(self, event):
        if self.closing or event.event_type != "down" or not self.enabled:
            return
        now = time.time()
        if now - self.last_shift_time < self.shift_cooldown:
            return
        self.last_shift_time = now
        if self.pauseEmAndamento:
            return
        if self.macroAtivo:
            self.cancelar_macro()
            return
        if self.is_any_move_pressed():
            self.shiftSegurandoMovimento = True
            self.reativarPermitido = True
            self.emit_state()
            return
        self.ativar_macro_hold()

    def _handle_asd(self, event):
        if self.closing or not self.enabled:
            return
        if event.event_type == "down":
            if self.macroAtivo:
                self.cancelar_macro()
        elif event.event_type == "up":
            if self.shiftSegurandoMovimento and not self.is_any_move_pressed() and not self.pauseEmAndamento and not self.left_mouse_pressed:
                self.shiftSegurandoMovimento = False
                self.emit_state()
                self.ativar_macro_hold()

    def on_a_down_up(self, event):
        self._handle_asd(event)

    def on_s_down_up(self, event):
        self._handle_asd(event)

    def on_d_down_up(self, event):
        self._handle_asd(event)

    def on_w_down_up(self, event):
        if self.closing or not self.enabled:
            return
        if event.event_type == "up":
            if self.shiftSegurandoMovimento and not self.is_any_move_pressed() and not self.pauseEmAndamento and not self.left_mouse_pressed:
                self.shiftSegurandoMovimento = False
                self.emit_state()
                self.ativar_macro_hold()

    def on_space(self, event):
        if self.closing:
            return
        if event.event_type == "down" and not self.pauseEmAndamento and self.macroAtivo:
            self.desativar_sistema_temporariamente_space()

    def on_c(self, event):
        if self.closing or not self.enabled or event.event_type != "down":
            return
        if self.macroAtivo:
            self.cancelar_macro()

    def on_mouse_click(self, x, y, button, pressed):
        if self.closing or not self.enabled:
            return
        try:
            left = button == mouse.Button.left
            right = button == mouse.Button.right
        except Exception:
            return
        if left:
            self.left_mouse_pressed = pressed
            if pressed:
                self.macroEstavaAtivoAntesDoLClick = self.macroAtivo
                if self.macroAtivo:
                    self.SoltarPW()
                    self.emit_state()
            else:
                if self.enabled and self.macroEstavaAtivoAntesDoLClick and not self.pauseEmAndamento and not self.is_any_move_pressed():
                    self.ativar_macro_hold()
                self.macroEstavaAtivoAntesDoLClick = False
                self.emit_state()
        elif right:
            self.right_mouse_pressed = pressed

    def set_enabled(self, value: bool):
        self.enabled = bool(value)
        if not self.enabled:
            self.SoltarTudo()
        self.emit_state()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.enabled = False
        if self._space_timer is not None:
            self.cancel_schedule(self._space_timer)
            self._space_timer = None
        self.SoltarTudo()
        for hook in self.hooks:
            try:
                keyboard.unhook(hook)
            except Exception:
                pass
        self.hooks.clear()
        try:
            if self.mouse_listener is not None:
                self.mouse_listener.stop()
        except Exception:
            pass
        self.mouse_listener = None


class WorkerTimerManager:
    def __init__(self):
        self._timers = {}
        self._counter = 0
        self._lock = threading.Lock()

    def schedule(self, delay_ms, callback):
        with self._lock:
            self._counter += 1
            handle = self._counter
        timer = threading.Timer(delay_ms / 1000.0, callback)
        timer.daemon = True
        with self._lock:
            self._timers[handle] = timer
        timer.start()
        return handle

    def cancel(self, handle):
        with self._lock:
            timer = self._timers.pop(handle, None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def cancel_all(self):
        with self._lock:
            handles = list(self._timers.keys())
        for handle in handles:
            self.cancel(handle)


class HotkeyWorker:
    def __init__(self, command_queue: mp.Queue, status_queue: mp.Queue):
        self.command_queue = command_queue
        self.status_queue = status_queue
        self.timer_manager = WorkerTimerManager()
        self.engine = HotkeyEngine()
        self.engine.on_state_change = self._push_state
        self.engine.on_schedule = self.timer_manager.schedule
        self.engine.on_cancel_schedule = self.timer_manager.cancel

    def _push_state(self, state):
        try:
            self.status_queue.put_nowait(state)
        except Exception:
            pass

    def run(self):
        self.engine.setup_hooks()
        self.engine.emit_state()
        running = True
        while running:
            try:
                command = self.command_queue.get(timeout=0.20)
            except queue.Empty:
                continue
            except Exception:
                break
            if not isinstance(command, dict):
                continue
            action = command.get("action")
            if action == "enable":
                self.engine.set_enabled(True)
            elif action == "disable":
                self.engine.set_enabled(False)
            elif action == "toggle":
                self.engine.set_enabled(not self.engine.enabled)
            elif action == "shutdown":
                running = False
            elif action == "set_delay":
                try:
                    delay = int(command.get("value", self.engine.space_delay_ms))
                    if delay >= 50:
                        self.engine.space_delay_ms = delay
                        self.engine.emit_state()
                except Exception:
                    pass
        self.timer_manager.cancel_all()
        self.engine.shutdown()


def hotkey_worker_entry(command_queue: mp.Queue, status_queue: mp.Queue):
    worker = HotkeyWorker(command_queue, status_queue)
    worker.run()


class HotkeyProcessManager:
    def __init__(self):
        self.command_queue = None
        self.status_queue = None
        self.process = None
        self.last_state = {
            "enabled": False,
            "macroAtivo": False,
            "pauseEmAndamento": False,
            "space_delay_ms": 750,
        }

    def start(self):
        if self.process is not None and self.process.is_alive():
            return
        self.command_queue = mp.Queue()
        self.status_queue = mp.Queue()
        self.process = mp.Process(target=hotkey_worker_entry, args=(self.command_queue, self.status_queue), daemon=True)
        self.process.start()

    def send(self, action, value=None):
        self.start()
        payload = {"action": action}
        if value is not None:
            payload["value"] = value
        try:
            self.command_queue.put_nowait(payload)
        except Exception:
            pass

    def enable(self):
        self.send("enable")

    def disable(self):
        self.send("disable")

    def toggle(self):
        current = bool(self.last_state.get("enabled", False))
        if current:
            self.disable()
        else:
            self.enable()

    def poll_state(self):
        if self.status_queue is None:
            return self.last_state
        while True:
            try:
                self.last_state = self.status_queue.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
        return self.last_state

    def is_enabled(self):
        return bool(self.poll_state().get("enabled", False))

    def shutdown(self):
        try:
            if self.command_queue is not None:
                self.command_queue.put_nowait({"action": "shutdown"})
        except Exception:
            pass
        if self.process is not None:
            self.process.join(timeout=1.5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1.0)
        self.process = None
        self.command_queue = None
        self.status_queue = None
        self.last_state = {
            "enabled": False,
            "macroAtivo": False,
            "pauseEmAndamento": False,
            "space_delay_ms": 750,
        }


# ============================================================
# UI HELPERS + EMULATOR DETECTION
# ============================================================
EMULATOR_NAMES = {
    "hd-player.exe": "BlueStacks",
    "bluestacks.exe": "BlueStacks",
    "bluestackshelper.exe": "BlueStacks",
    "hd-frontend.exe": "BlueStacks",
    "dnplayer.exe": "LDPlayer",
    "ldplayer.exe": "LDPlayer",
    "ldplayer9.exe": "LDPlayer",
    "memu.exe": "MEmu",
    "memuc.exe": "MEmu",
    "nox.exe": "Nox",
    "noxvmhandle.exe": "Nox",
    "androidemulator.exe": "Android Emulator",
    "gameloop.exe": "GameLoop",
    "appmarket.exe": "GameLoop",
    "aow_exe.exe": "GameLoop",
}
EMULATOR_WINDOW_HINTS = ["BlueStacks", "LDPlayer", "MEmu", "Nox", "GameLoop", "Android Emulator"]


class AnimatedButton(QPushButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._base_geo = None
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._base_geo is None:
            self._base_geo = self.geometry()
        rect = self._base_geo if self._base_geo is not None else self.geometry()
        self._animate_to(QRect(rect.x(), rect.y() - 1, rect.width(), rect.height()))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._base_geo is not None:
            self._animate_to(self._base_geo)

    def mousePressEvent(self, event):
        if self._base_geo is None:
            self._base_geo = self.geometry()
        rect = self._base_geo if self._base_geo is not None else self.geometry()
        self._animate_to(QRect(rect.x(), rect.y() + 1, rect.width(), rect.height()), 75)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._base_geo is not None:
            hovered = self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint()))
            target = QRect(self._base_geo.x(), self._base_geo.y() - 1 if hovered else self._base_geo.y(), self._base_geo.width(), self._base_geo.height())
            self._animate_to(target, 90)

    def _animate_to(self, rect: QRect, duration: int | None = None):
        self._anim.setDuration(duration if duration is not None else 130)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()


class PulseButton(AnimatedButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._pulse_value = 0.0
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(24)
        self._shadow.setOffset(0, 8)
        self._shadow.setColor(QColor(124, 77, 255, 70))
        self.setGraphicsEffect(self._shadow)
        self._pulse_anim = QPropertyAnimation(self, b"pulseValue", self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(1700)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_anim.start()

    def getPulseValue(self):
        return self._pulse_value

    def setPulseValue(self, value):
        self._pulse_value = float(value)
        if not hasattr(self, "_shadow") or self._shadow is None:
            return
        blur = 24 + (10 * self._pulse_value)
        alpha = 70 + int(20 * self._pulse_value)
        self._shadow.setBlurRadius(blur)
        self._shadow.setColor(QColor(124, 77, 255, alpha))

    pulseValue = Property(float, getPulseValue, setPulseValue)


class GlassCard(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 95))
        self.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
            title_label.setMinimumHeight(34)
            title_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            layout.addWidget(title_label)


class StatusPill(QFrame):
    def __init__(self, label: str, value: str, positive: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.setFixedHeight(36)
        self.setMinimumWidth(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        left_wrap = QWidget()
        left_layout = QHBoxLayout(left_wrap)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.dot = QLabel("•")
        self.dot.setObjectName("pillDotOn" if positive else "pillDotOff")
        self.dot.setFixedWidth(10)
        self.dot.setAlignment(Qt.AlignCenter)
        self.text = QLabel(label)
        self.text.setObjectName("pillText")
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        left_layout.addWidget(self.dot)
        left_layout.addWidget(self.text)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("pillValue")
        self.value_label.setFixedWidth(88)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(left_wrap, 1)
        layout.addWidget(self.value_label, 0)

    def set_value(self, value: str):
        self.value_label.setText(value)

    def set_active(self, active: bool):
        self.dot.setObjectName("pillDotOn" if active else "pillDotOff")
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)


class KeyCaptureFilter(QObject):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
            key_name = normalize_key_event(event)
            if key_name:
                self.callback(key_name)
                return True
        return False


def normalize_key_event(event: QKeyEvent) -> str:
    key = event.key()
    special = {
        Qt.Key_Space: "space",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
        Qt.Key_Tab: "tab",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Escape: "esc",
        Qt.Key_Shift: "shift",
        Qt.Key_Control: "ctrl",
        Qt.Key_Alt: "alt",
        Qt.Key_Meta: "win",
        Qt.Key_Up: "up",
        Qt.Key_Down: "down",
        Qt.Key_Left: "left",
        Qt.Key_Right: "right",
        Qt.Key_Delete: "delete",
        Qt.Key_Insert: "insert",
        Qt.Key_Home: "home",
        Qt.Key_End: "end",
        Qt.Key_PageUp: "pageup",
        Qt.Key_PageDown: "pagedown",
        Qt.Key_CapsLock: "capslock",
    }
    if key in special:
        return special[key]
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return f"f{key - Qt.Key_F1 + 1}"
    text = event.text().strip().lower()
    return text if text else ""


def find_emulator_process():
    candidates = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in EMULATOR_NAMES:
                candidates.append((proc.info["pid"], EMULATOR_NAMES[name], name))
        except Exception:
            pass
    return candidates[0] if candidates else (None, None, None)


def find_main_window_for_pid(pid: int):
    if not win32gui or not win32process:
        return None
    result = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid != pid:
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            if not title or width < 200 or height < 120:
                return True
            result.append((hwnd, width * height))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None
    if not result:
        return None
    result.sort(key=lambda item: item[1], reverse=True)
    return result[0][0]


def find_window_by_title_hint():
    if not win32gui:
        return None, None
    found = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return True
            for hint in EMULATOR_WINDOW_HINTS:
                if hint.lower() in title.lower():
                    rect = win32gui.GetWindowRect(hwnd)
                    width = rect[2] - rect[0]
                    height = rect[3] - rect[1]
                    if width >= 200 and height >= 120:
                        found.append((hwnd, title, width * height))
                        break
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None, None
    if not found:
        return None, None
    found.sort(key=lambda item: item[2], reverse=True)
    return found[0][0], found[0][1]


def get_emulator_status():
    pid, emulator_name, process_name = find_emulator_process()
    hwnd = None
    fallback_title = None
    if pid:
        hwnd = find_main_window_for_pid(pid)
    if hwnd is None:
        hwnd, fallback_title = find_window_by_title_hint()
    if not pid and not hwnd:
        return {"state": "fechado", "label": "Fechado", "process": "Não detectado", "window": "Não detectada"}
    if hwnd and win32gui:
        try:
            title = (win32gui.GetWindowText(hwnd) or fallback_title or emulator_name or "Emulador").strip()
            if win32gui.IsIconic(hwnd):
                return {"state": "minimizado", "label": "Minimizado", "process": process_name or "Detectado", "window": title}
            foreground = win32gui.GetForegroundWindow()
            if hwnd == foreground:
                return {"state": "aberto", "label": "Aberto", "process": process_name or "Detectado", "window": title}
            return {"state": "segundo_plano", "label": "Segundo plano", "process": process_name or "Detectado", "window": title}
        except Exception:
            pass
    return {"state": "detectado", "label": "Detectado", "process": process_name or "Detectado", "window": fallback_title or emulator_name or "Emulador"}


class TitleBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_pos = None
        self.setObjectName("titleBar")
        self.setFixedHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        self.left_spacer = QWidget()
        self.left_spacer.setFixedWidth(72)
        self.left_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.brand = QLabel("NewBind")
        self.brand.setObjectName("topBrand")
        self.brand.setAlignment(Qt.AlignCenter)
        self.brand.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.actions = QWidget()
        self.actions.setFixedWidth(72)
        actions_layout = QHBoxLayout(self.actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)

        self.min_btn = QPushButton("–")
        self.min_btn.setFocusPolicy(Qt.NoFocus)
        self.min_btn.setObjectName("titleBtn")
        self.min_btn.setFixedSize(28, 28)
        self.min_btn.clicked.connect(self.window.showMinimized)

        self.close_btn = QPushButton("×")
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setObjectName("titleBtnClose")
        self.close_btn.setFixedSize(34, 34)
        self.close_btn.clicked.connect(self.window.close)

        actions_layout.addWidget(self.min_btn)
        actions_layout.addWidget(self.close_btn)

        layout.addWidget(self.left_spacer)
        layout.addWidget(self.brand, 1)
        layout.addWidget(self.actions)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        event.accept()


class RoundedContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rootContainer")


# ============================================================
# REMAP PAGE (PySide6 UI + lógica funcional)
# ============================================================
class RemapPage(QWidget):
    HOTKEY_RESERVED_KEYS = {"left shift", "shift", "c"}
    MOUSE_BUTTONS = {"left", "right", "middle", "x1", "x2", "button4", "button5"}
    capture_input_signal = Signal(str)
    capture_hotkey_signal = Signal(str)
    capture_position_signal = Signal(int, int)

    def __init__(self, hotkey_manager: HotkeyProcessManager):
        super().__init__()
        self.hotkey_manager = hotkey_manager
        self.macro_ativo = False
        self.permitido_pelo_emulador = False
        self.key_map = {}
        self.capturando_campo = None
        self.capturando_tecla_posicao = False
        self.capturando_posicao = False
        self.hotkeys_registradas = {}
        self.teclas_pressionadas = set()
        self.mouse_map_pressionado = set()
        self.keyboard_hook_captura = None
        self.mouse_listener = None
        self.encerrando = False
        self.mouse_controller = Controller()
        self.atalho_posicao = ""
        self.posicao_salva = None
        self.hook_atalho_posicao = None
        self.tecla_posicao_pressionada = False
        self.hotkey_btn = None
        self.main_toggle = None
        self.current_key_label = None
        self.current_position_label = None
        self.hotkey_note = None
        self.hotkey_status = None
        self.emulator_dot = None
        self.emulator_state_label = None
        self.emulator_process_label = None
        self.emulator_window_label = None
        self.table = None
        self.original_input = None
        self.new_input = None
        self.position_input = None
        self.capture_status_label = None
        self.summary_macro_pill = None
        self.summary_emulator_pill = None
        self.summary_remaps_pill = None
        self.remap_count_label = None
        self.empty_state_label = None
        self.capture_filter = KeyCaptureFilter(self._finish_hotkey_capture)
        self.capture_input_signal.connect(self._finish_input_capture)
        self.capture_hotkey_signal.connect(self._finish_hotkey_capture)
        self.capture_position_signal.connect(self._finish_position_capture)
        self._last_capture_key = ""
        self._last_capture_time = 0.0

        appdata = os.getenv("APPDATA") or "."
        self.pasta_config = os.path.join(appdata, "MacroFF")
        os.makedirs(self.pasta_config, exist_ok=True)
        self.arquivo_config = os.path.join(self.pasta_config, "macro_config.json")

        self.carregar_config()
        self._build_ui()
        self.iniciar_hooks()
        self.atualizar_lista()
        self.atualizar_interface()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._poll_hotkey_status)
        self.status_timer.start(300)

        self.emulator_timer = QTimer(self)
        self.emulator_timer.timeout.connect(self.refresh_emulator_status)
        self.emulator_timer.start(800)
        self.refresh_emulator_status()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 10, 18, 18)
        root.setSpacing(14)

        header_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("NewBind")
        title.setObjectName("pageTitle")
        title_col.addWidget(title)

        self.main_toggle = PulseButton("PAUSADO")
        self.main_toggle.setObjectName("mainToggle")
        self.main_toggle.setCursor(Qt.PointingHandCursor)
        self.main_toggle.setFixedSize(142, 46)
        self.main_toggle.clicked.connect(self.toggle_macro)

        header_row.addLayout(title_col)
        header_row.addStretch()
        header_row.addWidget(self.main_toggle)
        root.addLayout(header_row)

        scroll = QScrollArea()
        scroll.setObjectName("mainScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        overview_card = GlassCard("Visão geral")
        overview_card.setFixedHeight(98)
        content_layout.addWidget(overview_card)
        overview_layout = overview_card.layout()
        overview_layout.setSpacing(12)
        overview_top = QHBoxLayout()
        overview_top.setSpacing(10)
        self.summary_macro_pill = StatusPill("Sistema", "Pausado", False)
        self.summary_emulator_pill = StatusPill("Emulador", "Fechado", False)
        self.summary_remaps_pill = StatusPill("Remaps", "0 ativos", False)
        overview_top.addWidget(self.summary_macro_pill)
        overview_top.addWidget(self.summary_emulator_pill)
        overview_top.addWidget(self.summary_remaps_pill)
        overview_layout.addLayout(overview_top)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(16)
        top_layout.setAlignment(Qt.AlignTop)
        content_layout.addLayout(top_layout)

        left_col_widget = QWidget()
        left_col_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        left_col = QVBoxLayout(left_col_widget)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(14)
        left_col.setAlignment(Qt.AlignTop)

        remap_card = GlassCard("Adicionar remap")
        remap_card.setMinimumHeight(262)
        remap_card.layout().setContentsMargins(16, 14, 16, 16)
        remap_card.layout().setSpacing(10)
        left_col.addWidget(remap_card)
        remap_layout = remap_card.layout()
        remap_layout.addWidget(self._label("Original"))
        self.original_input = self._input("Tecla ou botão do mouse")
        self.original_input.setFixedHeight(38)
        self.original_input.mousePressEvent = lambda e, field=self.original_input: self.start_input_capture(field)
        remap_layout.addWidget(self.original_input)
        remap_layout.addWidget(self._label("Nova"))
        self.new_input = self._input("Nova tecla")
        self.new_input.setFixedHeight(38)
        self.new_input.mousePressEvent = lambda e, field=self.new_input: self.start_input_capture(field)
        remap_layout.addWidget(self.new_input)
        remap_buttons = QHBoxLayout()
        remap_buttons.setSpacing(10)
        add_btn = self._button("Adicionar", primary=True)
        add_btn.setFixedHeight(36)
        add_btn.clicked.connect(self.add_remap)
        clear_btn = self._button("Limpar")
        clear_btn.setFixedHeight(36)
        clear_btn.clicked.connect(self.clear_inputs)
        remap_buttons.addWidget(add_btn)
        remap_buttons.addWidget(clear_btn)
        remap_layout.addLayout(remap_buttons)

        self.emulator_dot = QLabel("•")
        self.emulator_dot.setObjectName("emulatorDotOff")
        self.emulator_state_label = QLabel("Fechado")
        self.emulator_process_label = QLabel("Processo: Não detectado")
        self.emulator_window_label = QLabel("Janela: Não detectada")
        for _w in (self.emulator_dot, self.emulator_state_label, self.emulator_process_label, self.emulator_window_label):
            _w.hide()
        left_col.addStretch()

        right_col_widget = QWidget()
        right_col_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        right_col = QVBoxLayout(right_col_widget)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(14)
        right_col.setAlignment(Qt.AlignTop)

        self.hotkey_status = StatusPill("Estado", "Desligado", False)
        self.hotkey_status.hide()
        self.hotkey_btn = self._button("Ativar", primary=True)
        self.hotkey_btn.hide()
        self.hotkey_note = self._muted("Hotkey desligado.")
        self.hotkey_note.hide()

        position_card = GlassCard("Atalho de posição")
        position_card.setFixedWidth(350)
        position_card.setMinimumHeight(268)
        position_card.setMaximumHeight(268)
        right_col.addWidget(position_card)
        position_layout = position_card.layout()
        position_layout.setSpacing(8)
        position_layout.addWidget(self._label("Tecla do clique na posição"))
        self.position_input = self._input("Ex: F ou botão do mouse")
        self.position_input.setFixedHeight(38)
        self.position_input.mousePressEvent = lambda e: self.start_hotkey_capture()
        self.position_input.installEventFilter(self.capture_filter)
        position_layout.addWidget(self.position_input)
        if self.atalho_posicao:
            self.position_input.setText(self.atalho_posicao)
        capture_hotkey_btn = self._button("Capturar posição", primary=True)
        capture_hotkey_btn.setFixedHeight(38)
        capture_hotkey_btn.clicked.connect(self.start_position_capture)
        position_layout.addWidget(capture_hotkey_btn)
        info = QFrame()
        info.setObjectName("infoBox")
        info.setMinimumHeight(64)
        info.setMaximumHeight(64)
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 8, 14, 8)
        info_layout.setSpacing(2)
        self.current_key_label = self._muted("Tecla atual: NÃO DEFINIDA")
        self.current_position_label = self._muted("Posição: NÃO DEFINIDA")
        info_layout.addWidget(self.current_key_label)
        info_layout.addWidget(self.current_position_label)
        position_layout.addWidget(info)

        top_layout.addWidget(left_col_widget, 1)
        top_layout.addWidget(right_col_widget, 0)

        list_card = GlassCard("Lista de remaps")
        list_card.setFixedHeight(346)
        content_layout.addWidget(list_card)
        list_head = QHBoxLayout()
        list_head.setSpacing(10)
        self.remap_count_label = self._muted("0 remaps ativos")
        self.remap_count_label.setObjectName("listCountText")
        list_head.addWidget(self.remap_count_label)
        list_head.addStretch()
        list_card.layout().addLayout(list_head)
        self.table = QTableWidget(0, 2)
        self.table.setObjectName("mappingTable")
        self.table.setMinimumHeight(190)
        self.table.setHorizontalHeaderLabels(["Original", "Nova tecla"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.itemSelectionChanged.connect(self.fill_inputs_from_selection)
        list_card.layout().addWidget(self.table)
        self.empty_state_label = self._muted("Nenhum remap cadastrado ainda.")
        self.empty_state_label.setObjectName("emptyStateText")
        list_card.layout().addWidget(self.empty_state_label)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        buttons = [
            ("Editar", self.edit_selected_remap),
            ("Remover", self.remove_selected_remap),
            ("Limpar tudo", self.clear_all_remaps),
        ]
        for text, callback in buttons:
            btn = self._button(text)
            btn.setFixedHeight(32)
            btn.clicked.connect(callback)
            action_row.addWidget(btn)
        list_card.layout().addLayout(action_row)
        content_layout.addSpacing(2)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        return label

    def _input(self, placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setObjectName("textField")
        field.setReadOnly(True)
        field.setFocusPolicy(Qt.ClickFocus)
        return field

    def _button(self, text: str, primary: bool = False) -> QPushButton:
        btn = AnimatedButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setObjectName("primaryButton" if primary else "secondaryButton")
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def _set_feedback(self, text: str):
        if self.capture_status_label is not None:
            self.capture_status_label.setText(text)

    def _capture_ready(self, key_name: str) -> bool:
        key_name = (key_name or "").strip().lower()
        if not key_name:
            return False
        now = time.time()
        if key_name == self._last_capture_key and (now - self._last_capture_time) < 0.18:
            return False
        self._last_capture_key = key_name
        self._last_capture_time = now
        return True

    def _is_single_key(self, key_name: str) -> bool:
        key_name = (key_name or "").strip().lower()
        if not key_name:
            return False
        invalid_parts = ['+', ',', '  ']
        return not any(part in key_name for part in invalid_parts)

    def _normalize_mapping_key(self, key_name: str) -> str:
        return self._normalize_mouse_button_name((key_name or "").strip().lower())

    def fill_inputs_from_selection(self):
        row = self.table.currentRow()
        if row < 0:
            return
        original_item = self.table.item(row, 0)
        new_item = self.table.item(row, 1)
        if not original_item or not new_item:
            return
        self.original_input.setText(original_item.text().strip().lower())
        self.new_input.setText(new_item.text().strip().lower())

    def salvar_config(self):
        try:
            with open(self.arquivo_config, "w", encoding="utf-8") as f:
                json.dump({
                    "key_map": self.key_map,
                    "atalho_posicao": self.atalho_posicao,
                    "posicao_salva": list(self.posicao_salva) if self.posicao_salva else None,
                }, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print("Erro ao salvar config:", e)

    def carregar_config(self):
        if not os.path.exists(self.arquivo_config):
            return
        try:
            with open(self.arquivo_config, "r", encoding="utf-8") as f:
                dados = json.load(f)
            self.key_map = dados.get("key_map", {})
            self.atalho_posicao = (dados.get("atalho_posicao") or "").strip().lower()
            posicao = dados.get("posicao_salva")
            if isinstance(posicao, list) and len(posicao) == 2:
                self.posicao_salva = (int(posicao[0]), int(posicao[1]))
            else:
                self.posicao_salva = None
        except Exception as e:
            print("Erro ao carregar config:", e)
            self.key_map = {}
            self.atalho_posicao = ""
            self.posicao_salva = None

    def _reset_capture_placeholders(self):
        if self.original_input is not None:
            self.original_input.setPlaceholderText("Tecla ou botão do mouse")
        if self.new_input is not None:
            self.new_input.setPlaceholderText("Nova tecla")
        if self.position_input is not None:
            self.position_input.setPlaceholderText("Ex: F ou botão do mouse")

    def cancel_all_capture_modes(self):
        self.capturando_campo = None
        self.capturando_tecla_posicao = False
        self.capturando_posicao = False
        self._reset_capture_placeholders()
        self._set_feedback("Pronto para cadastrar remaps e atalhos.")

    def start_input_capture(self, field: QLineEdit):
        self.cancel_all_capture_modes()
        self.capturando_campo = field
        field.clear()
        if field is self.original_input:
            field.setPlaceholderText("Pressione 1 tecla ou 1 botão do mouse...")
            self._set_feedback("Capturando tecla original: toque uma tecla ou um botão do mouse.")
        else:
            field.setPlaceholderText("Pressione 1 tecla...")
            self._set_feedback("Capturando nova tecla: use apenas uma tecla do teclado.")
        field.setFocus()

    def _normalize_mouse_button_name(self, button_name: str) -> str:
        mapping = {
            "button4": "x1",
            "button5": "x2",
            "xbutton1": "x1",
            "xbutton2": "x2",
            "back": "x1",
            "forward": "x2",
        }
        return mapping.get((button_name or "").strip().lower(), (button_name or "").strip().lower())

    def _finish_input_capture(self, key_name: str):
        if not self.capturando_campo:
            return
        key_name = self._normalize_mapping_key(key_name)
        if not self._is_single_key(key_name):
            self._set_feedback("Entrada ignorada: use apenas uma tecla por vez.")
            return
        if self.capturando_campo is self.new_input and key_name in self.MOUSE_BUTTONS:
            QMessageBox.information(self, "Aviso", "A nova tecla aceita apenas uma tecla do teclado.")
            self.new_input.clear()
            self.new_input.setPlaceholderText("Pressione 1 tecla...")
            self._set_feedback("A nova tecla não aceita botão do mouse.")
            return
        field = self.capturando_campo
        field.setText(key_name)
        target_name = "tecla original" if field is self.original_input else "nova tecla"
        self.cancel_all_capture_modes()
        self._set_feedback(f"Captura concluída: {target_name} = {key_name.upper()}.")

    def start_hotkey_capture(self):
        self.cancel_all_capture_modes()
        self.capturando_tecla_posicao = True
        self.position_input.setText("")
        self.position_input.setPlaceholderText("Pressione 1 tecla ou 1 botão do mouse...")
        self.position_input.setFocus()
        self._set_feedback("Capturando atalho de posição: use uma tecla ou um botão do mouse.")

    def _finish_hotkey_capture(self, key_name: str):
        if not self.capturando_tecla_posicao:
            return
        key_name = (key_name or "").strip().lower()
        if not key_name:
            return
        self.atalho_posicao = key_name
        self.position_input.setText(key_name)
        self.cancel_all_capture_modes()
        self.current_key_label.setText(f"Tecla atual: {key_name.upper()}")
        self.salvar_config()
        self.registrar_atalho_posicao()
        self.atualizar_status_posicao()
        self._set_feedback(f"Atalho de posição definido como {key_name.upper()}.")

    def start_position_capture(self):
        if not self.atalho_posicao:
            QMessageBox.warning(self, "Aviso", "Defina primeiro a tecla do atalho da posição.")
            return
        self.cancel_all_capture_modes()
        self.capturando_posicao = True
        self._set_feedback("Capturando posição: a janela será minimizada, depois clique com o botão esquerdo.")
        window = self.window()
        if window is not None:
            window.showMinimized()
        self.atualizar_status_posicao()

    def restaurar_janela(self):
        window = self.window()
        if window is not None:
            try:
                window.showNormal()
                window.raise_()
                window.activateWindow()
            except Exception:
                pass

    def iniciar_hooks(self):
        if self.keyboard_hook_captura is None:
            self.keyboard_hook_captura = keyboard.hook(self._global_keyboard_event, suppress=False)
        if self.mouse_listener is None:
            self.mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
            self.mouse_listener.daemon = True
            self.mouse_listener.start()
        self.registrar_atalho_posicao()

    def _global_keyboard_event(self, event):
        if event.event_type != "down":
            return
        key_name = self._normalize_mapping_key(event.name or "")
        if not self._capture_ready(key_name):
            return
        if self.capturando_campo:
            self.capture_input_signal.emit(key_name)
            return
        if self.capturando_tecla_posicao:
            self.capture_hotkey_signal.emit(key_name)

    def _finish_position_capture(self, x: int, y: int):
        self.posicao_salva = (x, y)
        self.capturando_posicao = False
        self.salvar_config()
        self._after_position_capture()

    def _on_mouse_click(self, x, y, button, pressed):
        nome = self._normalize_mouse_button_name(getattr(button, "name", ""))
        if self.capturando_posicao and pressed and nome == "left":
            self.capture_position_signal.emit(int(x), int(y))
            return
        if self.capturando_campo and pressed:
            if nome:
                self.capture_input_signal.emit(nome)
            return
        if self.capturando_tecla_posicao and pressed:
            if nome:
                self.capture_hotkey_signal.emit(nome)
            return
        self.processar_mouse_remap(x, y, button, pressed)

    def _after_position_capture(self):
        self.current_position_label.setText(f"Posição: ({self.posicao_salva[0]}, {self.posicao_salva[1]})")
        self.atualizar_status_posicao()
        self.restaurar_janela()
        self._set_feedback(f"Posição salva em ({self.posicao_salva[0]}, {self.posicao_salva[1]}).")

    def soltar_teclas_remapeadas(self):
        for tecla in list(self.teclas_pressionadas):
            try:
                keyboard.release(tecla)
            except Exception:
                pass
        self.teclas_pressionadas.clear()

    def soltar_mouse_remapeado(self):
        for tecla in list(self.mouse_map_pressionado):
            try:
                keyboard.release(tecla)
            except Exception:
                pass
        self.mouse_map_pressionado.clear()

    def limpar_estado_teclas(self):
        self.soltar_teclas_remapeadas()
        self.soltar_mouse_remapeado()
        self.tecla_posicao_pressionada = False

    def remover_hotkeys(self):
        for hook in list(self.hotkeys_registradas.values()):
            try:
                keyboard.unhook(hook)
            except Exception:
                pass
        self.hotkeys_registradas.clear()
        self.soltar_teclas_remapeadas()

    def processar_remap_teclado(self, event, nova):
        try:
            if event.event_type == "down":
                if nova not in self.teclas_pressionadas:
                    keyboard.press(nova)
                    self.teclas_pressionadas.add(nova)
            elif event.event_type == "up":
                keyboard.release(nova)
                self.teclas_pressionadas.discard(nova)
        except Exception as e:
            print(f"Erro no remap para {nova}: {e}")

    def registrar_hotkeys(self):
        self.remover_hotkeys()
        if not self.macro_ativo or not self.permitido_pelo_emulador:
            return
        hotkey_ativo = self.hotkey_manager.is_enabled()
        for original, nova in self.key_map.items():
            original_normalizado = (original or "").strip().lower()
            if original_normalizado in self.MOUSE_BUTTONS:
                continue
            if hotkey_ativo and original_normalizado in self.HOTKEY_RESERVED_KEYS:
                continue
            try:
                hook = keyboard.hook_key(original_normalizado, lambda e, destino=nova: self.processar_remap_teclado(e, destino), suppress=True)
                self.hotkeys_registradas[original_normalizado] = hook
            except Exception as e:
                print(f"Erro ao registrar {original_normalizado} -> {nova}: {e}")

    def remover_atalho_posicao(self):
        if self.hook_atalho_posicao is not None:
            try:
                keyboard.unhook(self.hook_atalho_posicao)
            except Exception:
                pass
            self.hook_atalho_posicao = None
        self.tecla_posicao_pressionada = False

    def registrar_atalho_posicao(self):
        self.remover_atalho_posicao()
        tecla = (self.atalho_posicao or "").strip().lower()
        if not tecla:
            return
        try:
            self.hook_atalho_posicao = keyboard.hook_key(tecla, self.processar_atalho_posicao, suppress=False)
        except Exception as e:
            print(f"Erro ao registrar atalho de posição {tecla}: {e}")
            self.hook_atalho_posicao = None

    def processar_atalho_posicao(self, event):
        if not self.macro_ativo or not self.posicao_salva or self.capturando_posicao:
            return
        try:
            if event.event_type == "down":
                if self.tecla_posicao_pressionada:
                    return
                self.tecla_posicao_pressionada = True
                posicao_anterior = self.mouse_controller.position
                self.mouse_controller.position = self.posicao_salva
                self.mouse_controller.click(Button.left, 1)
                self.mouse_controller.position = posicao_anterior
            elif event.event_type == "up":
                self.tecla_posicao_pressionada = False
        except Exception as e:
            print(f"Erro ao usar posição salva: {e}")
            self.tecla_posicao_pressionada = False

    def processar_mouse_remap(self, x, y, button, pressed):
        if self.capturando_posicao:
            return
        if not self.macro_ativo or not self.permitido_pelo_emulador:
            return
        nome = self._normalize_mouse_button_name(getattr(button, "name", ""))
        if nome not in self.key_map:
            return
        destino = self.key_map[nome]
        try:
            if pressed:
                if destino not in self.mouse_map_pressionado:
                    keyboard.press(destino)
                    self.mouse_map_pressionado.add(destino)
            else:
                keyboard.release(destino)
                self.mouse_map_pressionado.discard(destino)
        except Exception as e:
            print(f"Erro no remap do mouse {nome} -> {destino}: {e}")

    def _set_table_item(self, row: int, col: int, value: str):
        item = QTableWidgetItem(value)
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, col, item)

    def atualizar_lista(self):
        self.table.setRowCount(0)
        ordered_items = sorted(self.key_map.items())
        for row, (original, nova) in enumerate(ordered_items):
            self.table.insertRow(row)
            self._set_table_item(row, 0, original.upper())
            self._set_table_item(row, 1, nova.upper())
        total = len(ordered_items)
        if self.remap_count_label is not None:
            self.remap_count_label.setText(f"{total} remap{'s' if total != 1 else ''} ativo{'s' if total != 1 else ''}")
        if self.summary_remaps_pill is not None:
            self.summary_remaps_pill.set_value(f"{total} ativo{'s' if total != 1 else ''}")
            self.summary_remaps_pill.set_active(total > 0)
        if self.empty_state_label is not None:
            self.empty_state_label.setVisible(total == 0)
        if self.table is not None:
            self.table.setVisible(total > 0)

    def atualizar_status_posicao(self):
        tecla = self.atalho_posicao.upper() if self.atalho_posicao else "NÃO DEFINIDA"
        if self.posicao_salva:
            posicao = f"({self.posicao_salva[0]}, {self.posicao_salva[1]})"
        elif self.capturando_posicao:
            posicao = "CLIQUE COM O BOTÃO ESQUERDO"
        else:
            posicao = "NÃO DEFINIDA"
        self.current_key_label.setText(f"Tecla atual: {tecla}")
        self.current_position_label.setText(f"Posição: {posicao}")

    def atualizar_interface(self):
        self.main_toggle.setText("ATIVO" if self.macro_ativo else "PAUSADO")
        self.main_toggle.setProperty("active", self.macro_ativo)
        self.main_toggle.style().unpolish(self.main_toggle)
        self.main_toggle.style().polish(self.main_toggle)
        self.atualizar_status_posicao()
        hotkey_on = self.hotkey_manager.is_enabled()
        if self.hotkey_status is not None:
            self.hotkey_status.set_value("Ligado" if hotkey_on else "Desligado")
            self.hotkey_status.set_active(hotkey_on)
        if self.hotkey_btn is not None:
            self.hotkey_btn.setText("Desativar" if hotkey_on else "Ativar")
        if self.hotkey_note is not None:
            if hotkey_on:
                self.hotkey_note.setText("Hotkey ativo: Left Shift e C ficam reservadas.")
            else:
                self.hotkey_note.setText("Hotkey desligado.")
        if self.summary_macro_pill is not None:
            macro_ok = self.macro_ativo and self.permitido_pelo_emulador
            if macro_ok:
                macro_text = "Ativo"
            elif self.macro_ativo and not self.permitido_pelo_emulador:
                macro_text = "Aguardando"
            else:
                macro_text = "Pausado"
            self.summary_macro_pill.set_value(macro_text)
            self.summary_macro_pill.set_active(macro_ok)
        self.atualizar_lista()

    def add_remap(self):
        original = self.original_input.text().strip().lower()
        nova = self.new_input.text().strip().lower()
        if not original or not nova:
            QMessageBox.information(self, "Aviso", "Preencha a tecla original e a nova tecla.")
            return
        if "+" in original or "+" in nova or "," in original or "," in nova:
            QMessageBox.information(self, "Aviso", "Cadastre apenas 1 tecla por vez em cada campo.")
            return
        if len(original.split()) > 2 and original not in self.MOUSE_BUTTONS:
            QMessageBox.information(self, "Aviso", "Use apenas 1 tecla ou 1 botão por vez no campo original.")
            return
        if nova in self.MOUSE_BUTTONS:
            QMessageBox.information(self, "Aviso", "A nova tecla precisa ser uma tecla do teclado.")
            return
        if original == nova:
            QMessageBox.information(self, "Aviso", "A tecla original e a nova tecla não podem ser iguais.")
            return
        self.key_map[original] = nova
        self.atualizar_lista()
        self.salvar_config()
        if self.macro_ativo:
            self.registrar_hotkeys()
        self.clear_inputs()
        self.atualizar_interface()
        self._set_feedback(f"Remap salvo: {original.upper()} → {nova.upper()}.")

    def clear_inputs(self):
        self.original_input.clear()
        self.new_input.clear()
        self.original_input.setPlaceholderText("Tecla ou botão do mouse")
        self.new_input.setPlaceholderText("Nova tecla")
        self._set_feedback("Campos limpos. Pronto para um novo cadastro.")

    def toggle_macro(self):
        self.macro_ativo = not self.macro_ativo
        if self.macro_ativo and self.permitido_pelo_emulador:
            self.registrar_hotkeys()
            self.registrar_atalho_posicao()
            self._set_feedback("Sistema ativo e pronto para aplicar remaps.")
        else:
            self.remover_hotkeys()
            self.remover_atalho_posicao()
            self.limpar_estado_teclas()
            if self.macro_ativo and not self.permitido_pelo_emulador:
                self._set_feedback("Sistema ligado, mas aguardando o emulador ficar em primeiro plano.")
            else:
                self._set_feedback("Sistema pausado.")
        self.atualizar_interface()

    def toggle_hotkey_status(self):
        self.hotkey_manager.toggle()
        QTimer.singleShot(180, self.refresh_after_hotkey_change)

    def refresh_after_hotkey_change(self):
        self.hotkey_manager.poll_state()
        if self.macro_ativo and self.permitido_pelo_emulador:
            self.registrar_hotkeys()
        self.atualizar_interface()

    def _poll_hotkey_status(self):
        self.hotkey_manager.poll_state()
        self.atualizar_interface()

    def get_selected_original(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return item.text().strip().lower()

    def edit_selected_remap(self):
        original = self.get_selected_original()
        if not original:
            return
        nova = self.key_map.get(original, "")
        self.original_input.setText(original)
        self.new_input.setText(nova)
        self.key_map.pop(original, None)
        self.atualizar_lista()
        self.salvar_config()
        if self.macro_ativo:
            self.registrar_hotkeys()
        self.atualizar_interface()
        self._set_feedback(f"Remap removido: {original.upper()}.")
        self._set_feedback(f"Editando remap de {original.upper()}. Ajuste e salve novamente.")

    def remove_selected_remap(self):
        original = self.get_selected_original()
        if not original:
            return
        self.key_map.pop(original, None)
        self.atualizar_lista()
        self.salvar_config()
        if self.macro_ativo:
            self.registrar_hotkeys()
        self.atualizar_interface()
        self._set_feedback(f"Remap removido: {original.upper()}.")

    def clear_all_remaps(self):
        if QMessageBox.question(self, "Confirmar", "Apagar todos os remaps?") != QMessageBox.Yes:
            return
        self.key_map.clear()
        self.atualizar_lista()
        self.remover_hotkeys()
        self.limpar_estado_teclas()
        self.salvar_config()
        if self.macro_ativo:
            self.registrar_hotkeys()
        self.atualizar_interface()
        self._set_feedback("Todos os remaps foram apagados.")

    def set_permitido_pelo_emulador(self, permitido: bool):
        permitido = bool(permitido)
        mudou = self.permitido_pelo_emulador != permitido
        self.permitido_pelo_emulador = permitido
        if permitido:
            if mudou and not self.macro_ativo:
                self.macro_ativo = True
            if self.macro_ativo:
                self.registrar_hotkeys()
                self.registrar_atalho_posicao()
        else:
            self.remover_hotkeys()
            self.remover_atalho_posicao()
            self.limpar_estado_teclas()
            if mudou:
                self.macro_ativo = False
        self.atualizar_interface()

    def refresh_emulator_status(self):
        status = get_emulator_status()
        state = status["state"]
        if self.emulator_state_label is not None:
            self.emulator_state_label.setText(status["label"])
        if self.emulator_process_label is not None:
            self.emulator_process_label.setText(f"Processo: {status['process']}")
        if self.emulator_window_label is not None:
            self.emulator_window_label.setText(f"Janela: {status['window']}")
        state_styles = {
            "fechado": ("emulatorDotOff", "emulatorStateClosed"),
            "detectado": ("emulatorDotWarn", "emulatorStateWarn"),
            "minimizado": ("emulatorDotWarn", "emulatorStateWarn"),
            "segundo_plano": ("emulatorDotPurple", "emulatorStatePurple"),
            "aberto": ("emulatorDotOn", "emulatorStateOn"),
        }
        dot_style, text_style = state_styles.get(state, ("emulatorDotOff", "emulatorStateClosed"))
        if self.emulator_dot is not None:
            self.emulator_dot.setObjectName(dot_style)
            self.emulator_dot.style().unpolish(self.emulator_dot)
            self.emulator_dot.style().polish(self.emulator_dot)
        if self.emulator_state_label is not None:
            self.emulator_state_label.setObjectName(text_style)
            self.emulator_state_label.style().unpolish(self.emulator_state_label)
            self.emulator_state_label.style().polish(self.emulator_state_label)
        emulator_active = state == "aberto"
        self.set_permitido_pelo_emulador(emulator_active)
        if self.summary_emulator_pill is not None:
            self.summary_emulator_pill.set_value(status["label"])
            self.summary_emulator_pill.set_active(emulator_active)
        if emulator_active:
            self.hotkey_manager.enable()
        else:
            self.hotkey_manager.disable()

    def shutdown(self):
        self.encerrando = True
        self.macro_ativo = False
        self.capturando_posicao = False
        self.remover_hotkeys()
        self.remover_atalho_posicao()
        self.limpar_estado_teclas()
        self.salvar_config()
        try:
            if self.keyboard_hook_captura is not None:
                keyboard.unhook(self.keyboard_hook_captura)
        except Exception:
            pass
        try:
            if self.mouse_listener is not None:
                self.mouse_listener.stop()
        except Exception:
            pass
        self.keyboard_hook_captura = None
        self.mouse_listener = None


class LicenseToast(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("licenseToast")
        self.setFixedSize(328, 98)
        self.hide()

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity_effect)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.dot = QLabel("•")
        self.dot.setStyleSheet("color:#9d6bff;font-size:20px;background:transparent;")
        self.title_label = QLabel("Licença ativa")
        self.title_label.setStyleSheet("color:white;font-size:14px;font-weight:800;background:transparent;")
        top.addWidget(self.dot)
        top.addWidget(self.title_label, 1)

        self.msg_label = QLabel("Sua key dura 0 dias")
        self.msg_label.setWordWrap(True)
        self.msg_label.setStyleSheet("color:#cfdcff;font-size:12px;background:transparent;")

        layout.addLayout(top)
        layout.addWidget(self.msg_label)

        self.setStyleSheet("""
        #licenseToast {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #101a31, stop:1 #0a1427);
            border: 1px solid #6d3cff;
            border-radius: 18px;
        }
        """)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.fade_anim.setDuration(240)

        self.pos_anim = QPropertyAnimation(self, b"pos", self)
        self.pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.pos_anim.setDuration(260)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_animated)

    def show_message(self, title: str, message: str, accent: str = "#6d3cff", duration_ms: int = 5200):
        parent = self.parentWidget()
        if parent is None:
            return
        self.title_label.setText(title)
        self.msg_label.setText(message)
        self.dot.setStyleSheet(f"color:{accent};font-size:20px;background:transparent;")
        self.setStyleSheet(f"""
        #licenseToast {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #101a31, stop:1 #0a1427);
            border: 1px solid {accent};
            border-radius: 18px;
        }}
        """)
        target_x = max(16, parent.width() - self.width() - 20)
        target_y = 70
        start = QPoint(target_x, target_y - 18)
        end = QPoint(target_x, target_y)
        self.move(start)
        self.show()
        self.raise_()
        self.fade_anim.stop()
        self.pos_anim.stop()
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.pos_anim.setStartValue(start)
        self.pos_anim.setEndValue(end)
        self.fade_anim.start()
        self.pos_anim.start()
        self.hide_timer.start(duration_ms)

    def hide_animated(self):
        start = self.pos()
        end = QPoint(start.x(), start.y() - 12)
        self.fade_anim.stop()
        self.pos_anim.stop()
        self.fade_anim.setStartValue(max(0.0, self.opacity_effect.opacity()))
        self.fade_anim.setEndValue(0.0)
        self.pos_anim.setStartValue(start)
        self.pos_anim.setEndValue(end)
        self.fade_anim.finished.connect(self._finish_hide_once)
        self.fade_anim.start()
        self.pos_anim.start()

    def _finish_hide_once(self):
        try:
            self.fade_anim.finished.disconnect(self._finish_hide_once)
        except Exception:
            pass
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NewBind")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(1288, 758)
        self.hotkey_manager = HotkeyProcessManager()
        self.hotkey_manager.start()

        central = QWidget()
        central.setObjectName("outerTransparent")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        self.container = RoundedContainer()
        shadow_layout = QVBoxLayout(self.container)
        shadow_layout.setContentsMargins(0, 0, 0, 0)
        shadow_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        shadow_layout.addWidget(self.title_bar)

        body = QWidget()
        body.setObjectName("bodyArea")
        shadow_layout.addWidget(body)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(22, 18, 22, 18)
        body_layout.setSpacing(18)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 20, 14, 22)
        sidebar_layout.setSpacing(14)
        logo_label = QLabel()
        logo_label.setObjectName("brandLogo")
        logo_label.setFixedHeight(150)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_path = resource_path("newbind_logo.png")
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
        else:
            pix = QPixmap(r"/mnt/data/newbind_logo.png")
        if not pix.isNull():
            logo_label.setPixmap(pix.scaled(250, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        sidebar_layout.addWidget(logo_label)
        sidebar_layout.addSpacing(12)
        dash_btn = AnimatedButton("Painel")
        dash_btn.setObjectName("navButtonActive")
        dash_btn.setMinimumHeight(44)
        sidebar_layout.addWidget(dash_btn)
        sidebar_layout.addStretch()

        license_data = load_local_license()
        _, license_value, expired = build_sidebar_license_info(license_data.get("plan"), license_data.get("expires_at"))
        self.sidebar_license_card = QFrame()
        self.sidebar_license_card.setObjectName("sidebarLicenseCard")
        sidebar_license_layout = QVBoxLayout(self.sidebar_license_card)
        sidebar_license_layout.setContentsMargins(14, 12, 14, 12)
        sidebar_license_layout.setSpacing(4)
        self.sidebar_license_title = QLabel("Tempo da key")
        self.sidebar_license_title.setObjectName("sidebarLicenseTitle")
        self.sidebar_license_value = QLabel(license_value)
        self.sidebar_license_value.setWordWrap(True)
        self.sidebar_license_value.setObjectName("sidebarLicenseValueExpired" if expired else "sidebarLicenseValue")
        sidebar_license_layout.addWidget(self.sidebar_license_title)
        sidebar_license_layout.addWidget(self.sidebar_license_value)
        sidebar_layout.addWidget(self.sidebar_license_card)

        content_frame = QFrame()
        content_frame.setObjectName("contentFrame")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.remap_page = RemapPage(self.hotkey_manager)
        content_layout.addWidget(self.remap_page)
        body_layout.addWidget(sidebar)
        body_layout.addWidget(content_frame, 1)
        outer.addWidget(self.container)
        self.setStyleSheet(STYLESHEET)
        self.license_toast = LicenseToast(self.container)
        QTimer.singleShot(900, self.show_license_toast_if_available)

    def show_license_toast_if_available(self):
        data = load_local_license()
        plan = data.get("plan")
        expires_at = data.get("expires_at")
        _, value_txt, expired = build_sidebar_license_info(plan, expires_at)
        if hasattr(self, "sidebar_license_title"):
            self.sidebar_license_title.setText("Tempo da key")
        if hasattr(self, "sidebar_license_value"):
            self.sidebar_license_value.setText(value_txt)
            self.sidebar_license_value.setObjectName("sidebarLicenseValueExpired" if expired else "sidebarLicenseValue")
            self.sidebar_license_value.style().unpolish(self.sidebar_license_value)
            self.sidebar_license_value.style().polish(self.sidebar_license_value)
        msg = build_license_text(plan, expires_at)
        if not msg:
            return
        accent = "#ef4444" if "expirou" in msg.lower() else "#7c4dff"
        title = "Licença expirada" if "expirou" in msg.lower() else "Licença ativa"
        self.license_toast.show_message(title, msg, accent=accent)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "license_toast") and self.license_toast.isVisible():
            x = max(16, self.container.width() - self.license_toast.width() - 20)
            self.license_toast.move(x, 70)

    def closeEvent(self, event):
        try:
            self.remap_page.shutdown()
        finally:
            self.hotkey_manager.shutdown()
        super().closeEvent(event)


STYLESHEET = """
QMainWindow, QWidget {
    color: #f4f6fb;
    font-family: Segoe UI;
    font-size: 13px;
    background: transparent;
}
QPushButton { outline: none; }
QPushButton:focus { outline: none; }
#rootContainer { background: #030812; border-radius: 24px; border: 1px solid #0b1530; }
#titleBar { background: #061124; border-top-left-radius: 24px; border-top-right-radius: 24px; border-bottom: 1px solid #0f1d36; }
#bodyArea { background: #030812; border-bottom-left-radius: 24px; border-bottom-right-radius: 24px; }
#topBrand { color: white; font-size: 15px; font-weight: 800; letter-spacing: 0.4px; background: transparent; }
#titleBtn, #titleBtnClose {
    background: #0b162b; color: #eef3ff; border: 1px solid #192746; border-radius: 10px;
    font-size: 13px; font-weight: 700; padding: 0px; margin: 0px; outline: none;
}
#titleBtn:focus, #titleBtnClose:focus, #primaryButton:focus, #secondaryButton:focus, #mainToggle:focus, #navButton:focus, #navButtonActive:focus {
    outline: none;
    border: 1px solid transparent;
}
#titleBtn:hover { background: #13203b; border: 1px solid #233456; }
#titleBtnClose:hover { background: #261221; border: 1px solid #4c2740; color: #ffffff; }
#sidebar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #041126, stop:1 #071326); border-radius: 24px; border: 1px solid #0f1d36; }
#sidebarLicenseCard { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(18, 27, 52, 0.95), stop:1 rgba(12, 21, 42, 0.98)); border: 1px solid #182957; border-radius: 16px; }
#sidebarLicenseTitle { background: transparent; color: #9bb2db; font-size: 11px; font-weight: 700; }
#sidebarLicenseValue { background: transparent; color: white; font-size: 20px; font-weight: 900; }
#sidebarLicenseValueExpired { background: transparent; color: #ff7b7b; font-size: 20px; font-weight: 900; }
#contentFrame { background: transparent; border: none; }
#brandLogo { background: transparent; margin-bottom: 4px; }
#pageTitle { font-size: 28px; font-weight: 800; color: white; background: transparent; }
#pageSubtitle { color: #93a5ca; font-size: 13px; background: transparent; }
#card { background: qlineargradient(x1:0, y1:0, x2:0.9, y2:1, stop:0 #08162b, stop:1 #071120); border: 1px solid #10203b; border-radius: 24px; }
#cardTitle { font-size: 15px; font-weight: 800; color: white; background: #051022; border-radius: 14px; padding-left: 14px; }
#fieldLabel { color: #f2f6ff; font-size: 12px; font-weight: 700; background: transparent; }
#mutedText { color: #8ea0c8; font-size: 12px; background: transparent; }
#feedbackText { color: #dce7ff; font-size: 12px; background: #0b1528; border: 1px solid #172641; border-radius: 14px; padding: 10px 12px; }
#listCountText { color: #b8c5e8; font-size: 12px; font-weight: 700; background: transparent; }
#emptyStateText { color: #91a4ca; font-size: 12px; background: #08142a; border: 1px dashed #1d2d4a; border-radius: 14px; padding: 12px; }
#textField {
    background: #111d35; border: 1px solid #1d2a44; border-radius: 14px; padding: 0 14px;
    color: white; selection-background-color: #7c4dff;
}
#textField:focus { border: 1px solid #8f63ff; }
#pill { background: #0a1427; border: 1px solid #16243a; border-radius: 14px; }
#pillText { color: #b8c5e8; font-weight: 500; font-size: 12px; background: transparent; }
#pillValue { color: white; font-weight: 800; font-size: 12px; background: transparent; }
#pillDotOn { color: #8f63ff; font-size: 18px; min-width: 10px; background: transparent; }
#pillDotOff { color: #5e6f96; font-size: 18px; min-width: 10px; background: transparent; }
#infoBox { background: #0c1730; border: 1px solid #192845; border-radius: 18px; }
#emulatorRow { background: #0a1427; border: 1px solid #16243a; border-radius: 12px; }
#emulatorLeftText { color: #b8c5e8; font-size: 12px; background: transparent; }
#emulatorDotOn { color: #37d67a; font-size: 18px; background: transparent; }
#emulatorDotOff { color: #8ea0c8; font-size: 18px; background: transparent; }
#emulatorDotWarn { color: #ffb703; font-size: 18px; background: transparent; }
#emulatorDotPurple { color: #7c4dff; font-size: 18px; background: transparent; }
#emulatorStateOn { color: #37d67a; font-weight: 800; background: transparent; }
#emulatorStateClosed { color: #ffffff; font-weight: 800; background: transparent; }
#emulatorStateWarn { color: #ffb703; font-weight: 800; background: transparent; }
#emulatorStatePurple { color: #7c4dff; font-weight: 800; background: transparent; }
#primaryButton, #secondaryButton, #mainToggle, #navButton, #navButtonActive {
    border-radius: 16px; padding: 10px 16px; font-weight: 800; border: none;
}
#primaryButton, #mainToggle {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7738f2, stop:1 #a45af0); color: white;
}
#primaryButton:hover, #mainToggle:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8347ff, stop:1 #b26dff);
}
#secondaryButton, #navButton { background: #111d35; color: #edf3ff; }
#secondaryButton:hover, #navButton:hover { background: #172542; }
#navButtonActive { background: #1b1644; color: white; border: 1px solid #5230a9; text-align: left; padding-left: 14px; }
#navButtonActive:hover { background: #241d5a; }
#navButton { text-align: left; padding-left: 14px; }
#mappingTable {
    background: #08142a; border: 1px solid #12213c; border-radius: 16px; gridline-color: #12213c; color: white;
    selection-background-color: #1b2950; selection-color: #ffffff;
}
QHeaderView::section { background: #09162c; color: #ffffff; border: none; border-bottom: 1px solid #13233f; padding: 11px; font-weight: 800; }
QTableWidget::item { padding: 11px; background: #08142a; border-bottom: 1px solid #13233f; }
QTableWidget::item:selected { background: #152447; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 10px 2px 14px 2px; }
QScrollBar::handle:vertical { background: #5423b4; min-height: 34px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #7c4dff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; height: 0px; }
"""



import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from PySide6.QtCore import Qt, QTimer, QRect, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
RUN_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LICENSE_FILE = RUN_DIR / "license_local.json"
CONFIG_FILE = RUN_DIR / "newbind_server_config.json"
DEFAULT_API_URL = "https://SEU-SERVICO.onrender.com"

def get_api_url() -> str:
    env_url = (os.environ.get("NEWBIND_API_URL") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            value = (data.get("api_url") or "").strip()
            if value:
                return value.rstrip("/")
    except Exception:
        pass
    return DEFAULT_API_URL

API_URL = get_api_url()

BG = "#0b0b10"
WINDOW_BG = "#111119"
CARD = "#171726"
CARD_2 = "#1d1d2d"
BORDER = "#2a2a3f"
TEXT = "#f5f7ff"
MUTED = "#9ea2b8"
PURPLE = "#7c4dff"
PURPLE_HOVER = "#8b63ff"
RED = "#ef4444"
RED_HOVER = "#ff5a5a"
ENTRY_BG = "#10101a"
SUCCESS = "#35c48b"
WARNING = "#f59e0b"


class LicenseAnimatedButton(QPushButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._base_geo = None
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setFocusPolicy(Qt.NoFocus)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._base_geo is None:
            self._base_geo = self.geometry()
        rect = self._base_geo if self._base_geo is not None else self.geometry()
        self._animate_to(QRect(rect.x(), rect.y() - 1, rect.width(), rect.height()))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._base_geo is not None:
            self._animate_to(self._base_geo)

    def mousePressEvent(self, event):
        if self._base_geo is None:
            self._base_geo = self.geometry()
        rect = self._base_geo if self._base_geo is not None else self.geometry()
        self._animate_to(QRect(rect.x(), rect.y() + 1, rect.width(), rect.height()), 75)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._base_geo is not None:
            hovered = self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint()))
            target = QRect(
                self._base_geo.x(),
                self._base_geo.y() - 1 if hovered else self._base_geo.y(),
                self._base_geo.width(),
                self._base_geo.height(),
            )
            self._animate_to(target, 90)

    def _animate_to(self, rect: QRect, duration: int | None = None):
        self._anim.setDuration(duration if duration is not None else 130)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()


class LicenseTitleBar(QFrame):
    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self.window = window
        self.drag_pos = None
        self.setObjectName("titleBar")
        self.setFixedHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        self.left_spacer = QWidget()
        self.left_spacer.setFixedWidth(72)
        self.left_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.brand = QLabel("NewBind")
        self.brand.setObjectName("topBrand")
        self.brand.setAlignment(Qt.AlignCenter)
        self.brand.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.actions = QWidget()
        self.actions.setFixedWidth(72)
        actions_layout = QHBoxLayout(self.actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)

        self.min_btn = QPushButton("–")
        self.min_btn.setObjectName("titleBtn")
        self.min_btn.setFixedSize(28, 28)
        self.min_btn.setFocusPolicy(Qt.NoFocus)
        self.min_btn.clicked.connect(self.window.showMinimized)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("titleBtnClose")
        self.close_btn.setFixedSize(34, 34)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.clicked.connect(self.window.close)

        actions_layout.addWidget(self.min_btn)
        actions_layout.addWidget(self.close_btn)

        layout.addWidget(self.left_spacer)
        layout.addWidget(self.brand, 1)
        layout.addWidget(self.actions)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        event.accept()


class LicenseRoundedContainer(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rootContainer")


class LicenseCardFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)


def get_device_id() -> str:
    host = socket.gethostname()
    mac = uuid.getnode()
    return f"{host}-{mac}"


def load_local_license() -> dict:
    try:
        if LICENSE_FILE.exists():
            data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def load_local_key() -> str | None:
    data = load_local_license()
    key = (data.get("key") or "").strip()
    return key or None


def save_local_key(key: str, plan: str | None = None, expires_at: str | None = None) -> None:
    payload = {"key": key}
    if plan:
        payload["plan"] = plan
    if expires_at:
        payload["expires_at"] = expires_at
    LICENSE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_local_key() -> None:
    try:
        if LICENSE_FILE.exists():
            LICENSE_FILE.unlink()
    except Exception:
        pass


def parse_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _normalize_plan_text(plan: str | None) -> str | None:
    plan = (plan or "").strip().lower()
    mapping = {
        "daily": "1 dia",
        "weekly": "7 dias",
        "monthly": "30 dias",
        "lifetime": "Permanente",
        "permanent": "Permanente",
        "test_1m": "1 minuto",
        "test_5m": "5 minutos",
        "test_10m": "10 minutos",
    }
    return mapping.get(plan)


def build_license_text(plan: str | None, expires_at: str | None) -> str:
    plan_txt = _normalize_plan_text(plan)
    expires_dt = parse_dt(expires_at)
    if plan_txt == "Permanente" or (plan_txt and expires_dt is None):
        return "Sua key é permanente"
    if not expires_dt:
        return f"Sua key é de {plan_txt}" if plan_txt else "Licença ativa"
    now = datetime.now(timezone.utc)
    diff = int((expires_dt - now).total_seconds())
    if diff <= 0:
        return "Sua key expirou"
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60
    if days > 0:
        return "Sua key dura 1 dia" if days == 1 else f"Sua key dura {days} dias"
    if hours > 0:
        return "Sua key dura 1 hora" if hours == 1 else f"Sua key dura {hours} horas"
    if minutes <= 1:
        return "Sua key dura menos de 1 minuto"
    return f"Sua key dura {minutes} minutos"


def build_sidebar_license_info(plan: str | None, expires_at: str | None):
    plan_txt = _normalize_plan_text(plan)
    expires_dt = parse_dt(expires_at)
    if plan_txt == "Permanente" or (plan_txt and expires_dt is None):
        return ("Tempo da key", "Permanente", False)
    if not expires_dt:
        return ("Tempo da key", plan_txt or "Sem dados", False)
    diff = int((expires_dt - datetime.now(timezone.utc)).total_seconds())
    if diff <= 0:
        return ("Tempo da key", "Expirada", True)
    days = diff // 86400
    hours = (diff % 86400) // 3600
    minutes = (diff % 3600) // 60
    if days > 0:
        value = "1 dia" if days == 1 else f"{days} dias"
    elif hours > 0:
        value = "1 hora" if hours == 1 else f"{hours} horas"
    elif minutes <= 1:
        value = "< 1 minuto"
    else:
        value = f"{minutes} minutos"
    return ("Tempo da key", value, False)


def launch_newbind() -> tuple[bool, str]:
    candidates = [
        "NewBind.exe",
        "NewBind Full.exe",
        "NewBind_ajuste_final_fit.exe",
        "NewBind_polido_final.exe",
        "NewBind.exe",
        "NewBind Full.py",
        "NewBind_ajuste_final_fit.py",
        "NewBind_polido_final.py",
    ]
    for name in candidates:
        path = RUN_DIR / name
        if not path.exists():
            continue
        try:
            if path.suffix.lower() == ".exe":
                subprocess.Popen([str(path)], cwd=str(RUN_DIR))
            else:
                python_cmd = sys.executable if not getattr(sys, "frozen", False) else "python"
                subprocess.Popen([python_cmd, str(path)], cwd=str(RUN_DIR))
            return True, f"Abrindo {path.name}"
        except Exception as e:
            return False, f"Falha ao abrir {path.name}: {e}"
    return False, "Não encontrei o app NewBind na mesma pasta."


class LicenseWindow(QMainWindow):
    TARGET_WIDTH = 430
    TARGET_HEIGHT = 360

    def __init__(self):
        super().__init__()
        self._shown_once = False
        self.setObjectName("licenseWindow")
        self.setWindowTitle("NewBind")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(self.TARGET_WIDTH, self.TARGET_HEIGHT)
        self.setMinimumWidth(self.TARGET_WIDTH)
        self.setMaximumWidth(self.TARGET_WIDTH)
        self.setMinimumHeight(340)

        self.root = LicenseRoundedContainer()
        self.setCentralWidget(self.root)

        root_layout = QVBoxLayout(self.root)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)

        self.title_bar = LicenseTitleBar(self)
        root_layout.addWidget(self.title_bar)

        body = QWidget()
        root_layout.addWidget(body, 1)

        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 18)
        body_layout.setSpacing(0)

        self.card = LicenseCardFrame()
        body_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        card_layout.setSpacing(0)

        accent = QFrame()
        accent.setObjectName("accentBar")
        accent.setFixedHeight(4)
        card_layout.addWidget(accent)
        card_layout.addSpacing(16)

        title = QLabel("ATIVAÇÃO DE LICENÇA")
        title.setObjectName("cardTitle")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        card_layout.addSpacing(6)

        subtitle = QLabel("Digite sua key para continuar")
        subtitle.setObjectName("cardSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(18)

        key_label = QLabel("Sua key")
        key_label.setObjectName("fieldLabel")
        card_layout.addWidget(key_label)

        card_layout.addSpacing(8)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Cole sua key aqui")
        self.key_input.returnPressed.connect(self.confirm_key)
        card_layout.addWidget(self.key_input)

        card_layout.addSpacing(14)

        self.status = QLabel("AGUARDANDO KEY")
        self.status.setObjectName("statusIdle")
        self.status.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.status)

        card_layout.addSpacing(16)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.confirm_btn = LicenseAnimatedButton("CONFIRMAR")
        self.confirm_btn.setObjectName("primaryButton")
        self.confirm_btn.clicked.connect(self.confirm_key)

        self.cancel_btn = LicenseAnimatedButton("CANCELAR")
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.clicked.connect(self.close)

        btn_row.addWidget(self.confirm_btn)
        btn_row.addWidget(self.cancel_btn)
        card_layout.addLayout(btn_row)

        card_layout.addSpacing(10)

        self.note = QLabel("Se já existir uma licença válida, o app abre direto.")
        self.note.setObjectName("noteLabel")
        self.note.setAlignment(Qt.AlignCenter)
        self.note.setWordWrap(True)
        card_layout.addWidget(self.note)

        self._apply_styles()

        saved_key = load_local_key()
        if saved_key:
            self.key_input.setText(saved_key)
            QTimer.singleShot(120, self.validate_saved_key)

    def _apply_styles(self):
        self.setStyleSheet(
            f"""
            #rootContainer {{
                background: {WINDOW_BG};
                border: 1px solid {BORDER};
                border-radius: 22px;
            }}
            #titleBar {{
                background: transparent;
                border: none;
            }}
            #topBrand {{
                color: {TEXT};
                font-size: 17px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }}
            QPushButton#titleBtn, QPushButton#titleBtnClose {{
                background: #161624;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 14px;
                font-size: 16px;
                font-weight: 700;
                outline: none;
            }}
            QPushButton#titleBtn:hover {{ background: #1f1f30; border-color: #383856; }}
            QPushButton#titleBtnClose:hover {{ background: #28171c; border-color: #5a2934; }}
            #cardFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {CARD}, stop:1 {CARD_2});
                border: 1px solid {BORDER};
                border-radius: 20px;
            }}
            #accentBar {{
                background: {PURPLE};
                border: none;
                border-radius: 2px;
            }}
            #cardTitle {{
                color: {TEXT};
                font-size: 18px;
                font-weight: 800;
            }}
            #cardSubtitle {{
                color: {MUTED};
                font-size: 11px;
            }}
            #fieldLabel {{
                color: {TEXT};
                font-size: 11px;
                font-weight: 700;
            }}
            QLineEdit {{
                background: {ENTRY_BG};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 12px;
                padding: 12px 14px;
                font-size: 12px;
                selection-background-color: {PURPLE};
            }}
            QLineEdit:focus {{
                border: 1px solid {PURPLE};
            }}
            QLabel#statusIdle, QLabel#statusLoading, QLabel#statusError, QLabel#statusSuccess, QLabel#statusExpired {{
                font-size: 11px;
                font-weight: 800;
                padding: 10px 12px;
                border-radius: 12px;
                border: 1px solid {BORDER};
            }}
            QLabel#statusIdle {{ background: #12121b; color: {TEXT}; }}
            QLabel#statusLoading {{ background: #191527; color: #cbb8ff; border-color: #4a3a7d; }}
            QLabel#statusError {{ background: #221318; color: #ffb4b4; border-color: #6b2932; }}
            QLabel#statusSuccess {{ background: #12221a; color: #b6f1d7; border-color: #24573f; }}
            QLabel#statusExpired {{ background: #2a1d0b; color: #ffd08a; border-color: #7a5310; }}
            QPushButton#primaryButton, QPushButton#dangerButton {{
                min-height: 46px;
                border-radius: 14px;
                font-size: 12px;
                font-weight: 800;
                border: none;
                outline: none;
            }}
            QPushButton#primaryButton {{
                background: {PURPLE};
                color: white;
            }}
            QPushButton#primaryButton:hover {{ background: {PURPLE_HOVER}; }}
            QPushButton#dangerButton {{
                background: #1b1420;
                color: #f8d7df;
                border: 1px solid #4b2430;
            }}
            QPushButton#dangerButton:hover {{ background: #231821; }}
            QLabel#noteLabel {{
                color: {MUTED};
                font-size: 10px;
            }}
            QPushButton {{ outline: none; }}
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            QTimer.singleShot(0, self._fix_start_geometry)

    def _fix_start_geometry(self):
        self.layout().activate()
        self.centralWidget().layout().activate()
        self.adjustSize()
        final_h = max(self.minimumSizeHint().height() + 8, self.TARGET_HEIGHT)
        self.setFixedSize(self.TARGET_WIDTH, final_h)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    def set_status(self, text: str, kind: str = "idle"):
        mapping = {
            "idle": "statusIdle",
            "loading": "statusLoading",
            "error": "statusError",
            "success": "statusSuccess",
            "expired": "statusExpired",
        }
        self.status.setObjectName(mapping.get(kind, "statusIdle"))
        self.status.setText(text)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.update()

    def validate_saved_key(self):
        key = self.key_input.text().strip()
        if not key:
            return
        self.set_status("VALIDANDO KEY SALVA...", "loading")
        QApplication.processEvents()
        try:
            r = requests.post(
                f"{API_URL}/validate",
                json={"key": key, "device_id": get_device_id()},
                timeout=10,
            )
            data = r.json()
        except Exception:
            self.set_status("FALHA NA CONEXÃO", "error")
            return
        if data.get("ok"):
            try:
                save_local_key(key, data.get("plan"), data.get("expires_at"))
                ok, msg = launch_embedded_newbind(self)
                if ok:
                    self.set_status("ABRINDO NEWBIND...", "success")
                    QTimer.singleShot(200, self.close)
                else:
                    self.set_status(msg.upper(), "error")
            except Exception as e:
                self.set_status(f"ERRO AO ABRIR APP: {e}".upper(), "error")
            return
        error = (data.get("error") or "").lower()
        if "expir" in error:
            remove_local_key()
            self.key_input.clear()
            self.set_status("SUA KEY EXPIROU", "expired")
            QMessageBox.warning(self, "Key expirada", "Sua key expirou. Digite uma nova key para continuar.")
            return
        remove_local_key()
        self.key_input.clear()
        self.set_status("DIGITE UMA KEY VÁLIDA", "error")

    def confirm_key(self):
        key = self.key_input.text().strip()
        if not key:
            self.set_status("DIGITE SUA KEY", "error")
            self.key_input.setFocus()
            return
        self.set_status("CONFIRMANDO KEY...", "loading")
        QApplication.processEvents()
        try:
            r = requests.post(
                f"{API_URL}/activate",
                json={"key": key, "device_id": get_device_id()},
                timeout=10,
            )
            data = r.json()
        except Exception:
            self.set_status("FALHA NA CONEXÃO", "error")
            return
        if data.get("ok"):
            try:
                save_local_key(key, data.get("plan"), data.get("expires_at"))
                self.set_status(build_license_text(data.get("plan"), data.get("expires_at")), "success")
                ok, msg = launch_embedded_newbind(self)
                if ok:
                    QTimer.singleShot(350, self.close)
                else:
                    self.set_status(msg.upper(), "error")
            except Exception as e:
                self.set_status(f"ERRO AO ABRIR APP: {e}".upper(), "error")
            return
        error = (data.get("error") or "KEY INVÁLIDA").strip()
        if "expir" in error.lower():
            remove_local_key()
            self.set_status("SUA KEY EXPIROU", "expired")
            return
        self.set_status(error.upper(), "error")



# ============================================================
# GLUE: abrir o app embutido no mesmo processo
# ============================================================
_EMBEDDED_MAIN_WINDOW = None

def _resolve_icon_path() -> Path | None:
    candidates = [
        resource_path("newbind_app_icon.ico"),
        RUN_DIR / "newbind_app_icon.ico",
        BASE_DIR / "newbind_app_icon.ico",
        Path(__file__).with_name("newbind_app_icon.ico"),
        Path(r"/mnt/data/newbind_app_icon.ico"),
    ]
    for path in candidates:
        try:
            if path.exists():
                return path
        except Exception:
            pass
    return None

def launch_embedded_newbind(parent=None) -> tuple[bool, str]:
    global _EMBEDDED_MAIN_WINDOW
    try:
        if _EMBEDDED_MAIN_WINDOW is not None:
            try:
                _EMBEDDED_MAIN_WINDOW.raise_()
                _EMBEDDED_MAIN_WINDOW.activateWindow()
                return True, "NewBind já está aberto"
            except Exception:
                _EMBEDDED_MAIN_WINDOW = None
        _EMBEDDED_MAIN_WINDOW = MainWindow()
        icon_path = _resolve_icon_path()
        if icon_path is not None:
            try:
                _EMBEDDED_MAIN_WINDOW.setWindowIcon(QIcon(str(icon_path)))
            except Exception:
                pass
        _EMBEDDED_MAIN_WINDOW.show()
        try:
            _EMBEDDED_MAIN_WINDOW.raise_()
            _EMBEDDED_MAIN_WINDOW.activateWindow()
        except Exception:
            pass
        if parent is not None:
            try:
                parent.hide()
            except Exception:
                pass
            QTimer.singleShot(180, parent.close)
        return True, "Abrindo NewBind"
    except Exception as e:
        return False, f"Falha ao abrir o app: {e}"



def is_local_license_still_valid(data: dict | None) -> bool:
    data = data or {}
    key = str(data.get("key") or "").strip()
    if not key:
        return False
    expires_at = data.get("expires_at")
    expires_dt = parse_dt(expires_at)
    if expires_at and expires_dt is not None:
        from datetime import datetime, timezone
        return expires_dt > datetime.now(timezone.utc)
    return False

def validate_key_silently(key: str) -> tuple[bool, dict | None, str | None]:
    key = (key or "").strip()
    if not key:
        return False, None, "empty"
    try:
        r = requests.post(
            f"{API_URL}/validate",
            json={"key": key, "device_id": get_device_id()},
            timeout=10,
        )
        data = r.json()
    except Exception:
        return False, None, "connection"
    if data.get("ok"):
        return True, data, None
    return False, data, (data.get("error") or "invalid")

def main():
    mp.freeze_support()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("newbind.app")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    icon_path = _resolve_icon_path()
    if icon_path is not None:
        try:
            app.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass

    local_data = load_local_license()
    saved_key = str(local_data.get("key") or "").strip()

    # Abre direto no app quando já existir key local ainda válida.
    if is_local_license_still_valid(local_data):
        window = MainWindow()
        if icon_path is not None:
            try:
                window.setWindowIcon(QIcon(str(icon_path)))
            except Exception:
                pass
        window.show()
        globals()['_EMBEDDED_MAIN_WINDOW'] = window
        sys.exit(app.exec())

    # Se a key local existir mas estiver vencida, limpa e cai para a tela de ativação.
    if saved_key:
        expires_dt = parse_dt(local_data.get("expires_at"))
        if expires_dt is not None:
            from datetime import datetime, timezone
            if expires_dt <= datetime.now(timezone.utc):
                remove_local_key()
                saved_key = ""

    # Fallback: quando houver key salva sem expires_at local, tenta validar silenciosamente.
    if saved_key:
        ok, data, error = validate_key_silently(saved_key)
        if ok and data is not None:
            try:
                save_local_key(saved_key, data.get("plan"), data.get("expires_at"))
            except Exception:
                pass
            window = MainWindow()
            if icon_path is not None:
                try:
                    window.setWindowIcon(QIcon(str(icon_path)))
                except Exception:
                    pass
            window.show()
            globals()['_EMBEDDED_MAIN_WINDOW'] = window
            sys.exit(app.exec())
        lowered = (error or "").lower()
        if "expir" in lowered or "invalid" in lowered or "invál" in lowered:
            remove_local_key()

    window = LicenseWindow()
    if icon_path is not None:
        try:
            window.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()