"""Read Linux evdev button presses from a configured input device."""

import os
import select
import struct


INPUT_EVENT = struct.Struct("@llHHi")
EV_SYN, EV_KEY = 0, 1
SYN_REPORT, SYN_DROPPED = 0, 3


def watch_keys(path, mapping, stop, on_press, on_error):
    while not stop.is_set():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                pending = bytearray()
                dropped = False
                while not stop.is_set():
                    ready, _, _ = select.select([fd], [], [], 0.2)
                    if not ready:
                        continue
                    chunk = os.read(fd, INPUT_EVENT.size * 32)
                    if not chunk:
                        raise OSError("Input device disconnected")
                    pending.extend(chunk)
                    while len(pending) >= INPUT_EVENT.size:
                        _, _, kind, code, value = INPUT_EVENT.unpack_from(pending)
                        del pending[:INPUT_EVENT.size]
                        if kind == EV_SYN and code in (SYN_DROPPED, SYN_REPORT):
                            dropped = code == SYN_DROPPED
                        elif not dropped and kind == EV_KEY and value == 1 and code in mapping:
                            on_press(mapping[code])
            finally:
                os.close(fd)
        except OSError as error:
            on_error(error)
            stop.wait(1)
