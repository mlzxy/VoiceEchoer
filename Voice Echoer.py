import json
import os.path as osp
import os
from threading import Thread
from PyQt5.QtWidgets import QMainWindow
from sys import platform
import sys
import keyboard
import time
from PyQt5 import QtGui, QtWidgets, QtCore

import subprocess
from pyee import BaseEventEmitter
from queue import Queue
import sounddevice as sd
import soundfile as sf
from collections import deque
import io
import numpy as np
import base64
from time import localtime, strftime


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


bus = BaseEventEmitter()


def iconFromBase64(base64):
    pixmap = QtGui.QPixmap()
    pixmap.loadFromData(QtCore.QByteArray.fromBase64(base64))
    icon = QtGui.QIcon(pixmap)
    return icon


ICON = resource_path("tap.png")
with open(ICON, "rb") as image_file:
    bs = image_file.read()
    B64_ICON = base64.b64encode(bs)
    _icon_local_path = osp.expanduser("~/.echo.icon.png")
    with open(_icon_local_path, "wb") as fo:
        fo.write(bs)
    ICON = _icon_local_path
APP_NAME = "Voice Echoer"


config_path = osp.expanduser("~/.echo.conf")


def log(msg, mode="a"):
    with open(osp.expanduser("~/.echo.log"), mode) as f:
        print(msg, file=f)


log("", mode="w")


def load_config(default={"save_folder": osp.expanduser("~/Desktop/Echo.Recordings")}):
    if osp.exists(config_path):
        return json.load(open(config_path))
    else:
        return default


def save_config(o):
    with open(config_path, "w") as f:
        f.write(json.dumps(o, indent=4))


config = load_config()


recording_status = {"stopsign": False, "talking": False}
SAMPLE_RATE = 44100


record_button_name = "F7" if platform == "darwin" else "shift"
talk_button_name = "F9" if platform == "darwin" else "shift"
help_message = f"Holding '{record_button_name}' to record and '{talk_button_name}' to play, just that simple 😊. Recordings will be saved at {config['save_folder']}"


if platform == "darwin":
    # code from https://gist.github.com/ii0/827093c5c2b9a1ccebc9810f879b002e
    from Foundation import NSUserNotification
    from Foundation import NSUserNotificationCenter
    from Foundation import NSUserNotificationDefaultSoundName
    from optparse import OptionParser

    def send_noti(title, message):
        notification = NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is not None:
            center.deliverNotification_(notification)
        else:
            print("fail to get notification center", title, message)

    def send_folder_noti(msg, title=""):
        send_noti(title or "Save Folder Changed", msg)

    def send_help_noti():
        send_noti("How to Use Echo", help_message)

    def send_record_noti(msg):
        send_noti("Start to Record", msg)


else:
    from notifypy import Notify

    def send_folder_noti(msg, title=""):
        folder_noti = Notify()
        folder_noti.title = title or "Save Folder Changed"
        folder_noti.application_name = APP_NAME
        folder_noti.icon = ICON
        folder_noti.message = msg
        folder_noti.send()

    def send_help_noti():
        help_noti = Notify()
        help_noti.title = "How to Use Echo"

        help_noti.message = help_message
        help_noti.icon = ICON
        help_noti.application_name = APP_NAME
        help_noti.send(block=False)

    def send_record_noti(msg):
        record_noti = Notify()
        record_noti.application_name = APP_NAME
        record_noti.title = "Start to Record!"
        record_noti.message = msg
        record_noti.icon = ICON
        record_noti.send()


memory = {"record": None, "target": ""}

send_help_noti()


def ensure_dir(d):
    if not osp.exists(d):
        os.makedirs(d)


def random_time_id():
    return strftime("%Y-%m-%d_%H_%M_%S", localtime())


def tgo(func):
    thread = Thread(target=func)
    thread.daemon = True
    thread.start()
    return thread


@bus.on("start-record")
def start_record():
    def tfunc():
        print("recording")
        mic_queue = Queue()

        CHANNELS = 2
        target = osp.join(config["save_folder"], random_time_id() + ".wav")
        recording_status["stopsign"] = False
        send_record_noti(f"Saving to {target}")

        def recording_callback(indata, frames, time, status):
            print("receive recording raw audio")
            mic_queue.put(indata.copy())

        ensure_dir(osp.dirname(target))
        with sf.SoundFile(target, mode="x", samplerate=SAMPLE_RATE, channels=2) as file:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                callback=recording_callback,
            ):
                while not recording_status["stopsign"]:
                    file.write(mic_queue.get())

        memory["record"], _ = sf.read(target)
        memory["target"] = target
        print("stop recording")

    tgo(tfunc)


@bus.on("end-record")
def end_record():
    recording_status["stopsign"] = True


@bus.on("start-talk")
def start_talk():
    def tfunc():
        print("talking")
        if memory["record"] is not None:
            recording_status["talking"] = True
            while recording_status["talking"]:
                sd.play(memory["record"], SAMPLE_RATE)
                sd.wait()
                # time.sleep(0.3)
        print("stop talking")

    tgo(tfunc)


@bus.on("end-talk")
def end_talk():
    if memory["record"] is not None:
        recording_status["talking"] = False
        sd.stop()


class SystemTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, icon, parent=None):
        self.event_folder_click = None
        self.event_help_click = None
        self.event_exit_click = None
        self.parent = parent

        QtWidgets.QSystemTrayIcon.__init__(self, icon, parent)

        menu = QtWidgets.QMenu(parent)
        self._folder_action = menu.addAction("Choose Save Folder", self.on_folder_click)
        self._open_folder_action = menu.addAction(
            "Tell me Current Folder", self.on_open_folder_click
        )
        self._help_action = menu.addAction("Help", self.on_help_click)
        self._exit_action = menu.addAction("Exit", self.on_exit_click)
        self.setContextMenu(menu)
        self.show()

    def on_open_folder_click(self):
        ensure_dir(config["save_folder"])
        send_folder_noti(config["save_folder"], title="Current Folder")

    def on_folder_click(self):
        folder_path = QtWidgets.QFileDialog.getExistingDirectory(
            self.parent, "Select Folder to Save Recordings"
        )
        if folder_path:
            config["save_folder"] = folder_path
            save_config({"save_folder": folder_path})
            send_folder_noti(
                f"Now you recordings will be saved automatically into {folder_path}"
            )

    def on_help_click(self):
        send_help_noti()

    def on_exit_click(self):
        sys.exit(0)


class MyWidget(QtWidgets.QWidget):
    pass


class SystemTrayApp:
    def __init__(self):
        self._app = QtWidgets.QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._widget = MyWidget()
        self._icon = SystemTrayIcon(iconFromBase64(B64_ICON), self._widget)

    def run(self):
        code = self._app.exec_()
        sys.exit(code)


app = SystemTrayApp()


def ui_thread():
    app.run()


############################ Key Listener for Different OS #################################
osx_record_down = (20, 10)
osx_record_up = (20, 11)

osx_talk_down = (19, 10)
osx_talk_up = (19, 11)

if platform == "darwin":
    # the OSX implementation is modified from
    # https://github.com/feeluown/FeelUOwn/blob/602fe097305474441dadd8c0fb223538b1f72cc2/feeluown/global_hotkey_mac.py

    darwin_env = {"status": "still"}

    import threading

    import Quartz
    from AppKit import NSSystemDefined
    from AppKit import NSKeyUp, NSEvent, NSBundle

    def keyboard_tap_callback(proxy, type_, event, refcon):
        NSBundle.mainBundle().infoDictionary()["NSAppTransportSecurity"] = dict(
            NSAllowsArbitraryLoads=True
        )
        if type_ < 0 or type_ > 0x7FFFFFFF:
            print("Unkown mac event")
            key_thread()
            print("restart mac key board event loop")
            return event
        try:
            # 这段代码如果运行在非主线程，它会有如下输出，根据目前探索，
            # 这并不影响它的运行，我们暂时可以忽略它。
            # Python pid(11)/euid(11) is calling TIS/TSM in non-main thread environment.
            # ERROR : This is NOT allowed.
            key_event = NSEvent.eventWithCGEvent_(event)
        except:  # noqa
            print("mac event cast error")
            return event
        key_code = (key_event.data1() & 0xFFFF0000) >> 16
        key_state = (key_event.data1() & 0xFF00) >> 8
        key = (key_code, key_state)
        should_recording = key == osx_record_down
        should_talking = key == osx_talk_down

        should_stop_recording = key == osx_record_up
        should_stop_talking = key == osx_talk_up

        if should_recording:
            if darwin_env["status"] == "still":
                darwin_env["status"] = "recording"
                bus.emit("start-record")
        else:
            if darwin_env["status"] == "recording" and should_stop_recording:
                darwin_env["status"] = "still"
                bus.emit("end-record")

        if should_talking:
            if darwin_env["status"] == "still":
                darwin_env["status"] = "talking"
                bus.emit("start-talk")
        else:
            if darwin_env["status"] == "talking" and should_stop_talking:
                darwin_env["status"] = "still"
                bus.emit("end-talk")

        return event

    def key_thread():
        print("try to load mac hotkey event loop")

        # Set up a tap, with type of tap, location, options and event mask

        def create_tap():
            return Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,  # Session level is enough for our needs
                Quartz.kCGHeadInsertEventTap,  # Insert wherever, we do not filter
                Quartz.kCGEventTapOptionDefault,
                # NSSystemDefined for media keys
                Quartz.CGEventMaskBit(NSSystemDefined),
                keyboard_tap_callback,
                None,
            )

        tap = create_tap()
        if tap is None:
            print(
                "Error occurred when trying to listen global hotkey. "
                "trying to popup a prompt dialog to ask for permission."
            )
            # we do not use pyobjc-framework-ApplicationServices directly, since it
            # causes segfault when call AXIsProcessTrustedWithOptions function
            import objc

            AS = objc.loadBundle(
                "CoreServices",
                globals(),
                "/System/Library/Frameworks/ApplicationServices.framework",
            )
            objc.loadBundleFunctions(
                AS, globals(), [("AXIsProcessTrustedWithOptions", b"Z@")]
            )
            objc.loadBundleVariables(
                AS, globals(), [("kAXTrustedCheckOptionPrompt", b"@")]
            )
            trusted = AXIsProcessTrustedWithOptions(
                {kAXTrustedCheckOptionPrompt: True}
            )  # noqa
            if not trusted:
                log(
                    "Have popuped a prompt dialog to ask for accessibility."
                    "You can restart feeluown after you grant access to it."
                )
                time.sleep(3)
                sys.exit(1)
            else:
                log(
                    "Have already grant accessibility, "
                    "but we still can not listen global hotkey,"
                    "theoretically, this should not happen."
                )
                time.sleep(3)
                tap = create_tap()
                if tap is None:
                    sys.exit(1)

        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), run_loop_source, Quartz.kCFRunLoopDefaultMode
        )
        # Enable the tap
        Quartz.CGEventTapEnable(tap, True)
        # and run! This won't return until we exit or are terminated.
        Quartz.CFRunLoopRun()
        print("mac hotkey event loop exit")
        return []

    class MacGlobalHotkeyManager:
        def __init__(self):
            self._t = None
            self._started = False

        def start(self):
            # mac event loop 最好运行在主线程中，但是测试发现它也可以运行
            # 在非主线程。但不能运行在子进程中。
            self._t = threading.Thread(
                target=key_thread, name="MacGlobalHotkeyListener"
            )
            self._t.daemon = True
            self._t.start()
            self._started = True

        def stop(self):
            # FIXME: 经过测试发现，这个 stop 函数并不会正常工作。
            # 现在是将 thread 为 daemon thread，让线程在程序退出时停止。
            if self._started:
                loop = Quartz.CFRunLoopGetCurrent()
                Quartz.CFRunLoopStop(loop)
                self._t.join()


else:

    def key_thread(parent: Thread = None):
        status = "still"
        recording_flags = deque(maxlen=4)
        talking_flags = deque(maxlen=4)

        while True:
            if parent is not None:
                if not parent.is_alive():
                    break

            if keyboard.is_pressed("s"):
                print(f"status = {status}")
            # rewrite this by emit an event from on_press shift / alt, which does not requires permission on osx
            recording_flags.append(keyboard.is_pressed("shift"))
            talking_flags.append(keyboard.is_pressed("alt"))

            should_recording = all(list(recording_flags))
            should_talking = all(list(talking_flags))

            if should_recording & should_talking:  # should not happen
                time.sleep(0.25)
                continue

            if should_recording:
                if status == "still":
                    status = "recording"
                    bus.emit("start-record")
            else:
                if status == "recording":
                    status = "still"
                    bus.emit("end-record")

            if should_talking:
                if status == "still":
                    status = "talking"
                    bus.emit("start-talk")
            else:
                if status == "talking":
                    status = "still"
                    bus.emit("end-talk")
            time.sleep(0.25)


################################################################


if __name__ == "__main__":
    tgo(key_thread)
    ui_thread()
