"""PN532 ISO14443-A UID polling over a POSIX UART (NXP UM0701-02)."""

import os
import select
import termios
import time
import tty


START = b"\x00\x00\xff"
ACK = b"\x00\x00\xff\x00\xff\x00"


class FrameError(OSError):
    pass


def frame(payload):
    size = len(payload)
    if not 1 <= size <= 254:
        raise ValueError("PN532 normal frame needs 1–254 bytes")
    return START + bytes((size, -size & 255)) + payload + bytes((-sum(payload) & 255, 0))


class Pn532:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSAFLUSH, attrs)
            self._write(b"\x55\x55\x00\x00\x00", time.monotonic() + 0.2)
            time.sleep(0.01)
            firmware = self.command(0x02)
            if len(firmware) != 4 or firmware[0] != 0x32:
                raise FrameError("UART target did not identify as PN532")
            self.firmware = f"{firmware[1]}.{firmware[2]}"
            self.command(0x14, b"\x01")  # SAM normal mode.
            self.command(0x32, b"\x05\xff\x01\x01")  # Finite passive activation retries.
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            if hasattr(self, "saved"):
                termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
        finally:
            os.close(self.fd)

    def _write(self, data, deadline):
        while data:
            _, ready, _ = select.select([], [self.fd], [], max(0, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError("PN532 UART write timeout")
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                continue

    def _read(self, length, deadline):
        data = bytearray()
        while len(data) < length:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError("PN532 UART response timeout")
            try:
                chunk = os.read(self.fd, length - len(data))
            except BlockingIOError:
                continue
            if not chunk:
                raise FrameError("PN532 UART disconnected")
            data.extend(chunk)
        return bytes(data)

    def _read_frame(self, deadline):
        prefix = b""
        while prefix != START:
            if time.monotonic() >= deadline:
                raise TimeoutError("PN532 frame preamble timeout")
            prefix = (prefix + self._read(1, deadline))[-3:]
        size, check = self._read(2, deadline)
        if size == 0 and check == 255 and self._read(1, deadline) == b"\0":
            return b""
        if not 1 <= size <= 254 or (size + check) & 255:
            raise FrameError("Invalid PN532 length checksum")
        packet = self._read(size + 2, deadline)
        if packet[-1] or sum(packet[:-1]) & 255:
            raise FrameError("Invalid PN532 data checksum or postamble")
        return packet[:-2]

    def command(self, code, parameters=b""):
        deadline = time.monotonic() + 0.75
        try:
            self._write(frame(bytes((0xD4, code)) + parameters), deadline)
            if self._read_frame(deadline) != b"":
                raise FrameError("Expected PN532 ACK")
            reply = self._read_frame(deadline)
            if reply[:2] != bytes((0xD5, code + 1)):
                raise FrameError(f"Unexpected PN532 response to command 0x{code:02x}")
            return reply[2:]
        except OSError:
            try:
                self._write(ACK, time.monotonic() + 0.1)  # Abort the in-flight PN532 command.
            except OSError:
                pass
            raise

    def poll_uid(self):
        result = self.command(0x4A, b"\x01\x00")
        if result == b"\0":
            return None
        if len(result) < 6 or result[0] != 1:
            raise FrameError("Invalid Type A target response")
        length = result[5]
        if length not in (4, 7, 10) or len(result) < 6 + length:
            raise FrameError("Invalid Type A UID length")
        return result[6:6 + length].hex().upper()


class TapDetector:
    """Emit once for a held UID; require two empty polls before the same UID can tap again."""

    def __init__(self):
        self.held = None
        self.empty = 0

    def update(self, uid):
        if uid is None:
            self.empty += 1
            if self.empty >= 2:
                self.held = None
            return None
        self.empty = 0
        if self.held == uid:
            return None
        self.held = uid
        return uid
