"""Check the Linux reader's deployment prerequisites without opening peripherals."""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def run_locator(locator):
    completed = subprocess.run([str(locator.resolve()), "--version"], capture_output=True, text=True, timeout=5)
    output = completed.stdout.strip()
    if completed.returncode or output != "tongyue-locate 3":
        raise ValueError(f"Incompatible locator: exit {completed.returncode}, {output}")
    return output


def character_device(path, mode):
    if not stat.S_ISCHR(path.stat().st_mode) or not os.access(path, mode):
        raise ValueError(f"Current user cannot access character device {path}")
    return str(path)


def prerequisites(args):
    checks = {}

    def check(name, inspect):
        try:
            checks[name] = {"ok": True, "value": inspect()}
        except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
            checks[name] = {"ok": False, "error": str(error)}

    def python_runtime():
        if sys.version_info < (3, 11):
            raise ValueError("Python 3.11 or newer is required")
        import sqlite3
        import ssl
        import termios
        return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                "tls": ssl.OPENSSL_VERSION, "uart115200": termios.B115200}

    def alsa_tools():
        tools = {name: shutil.which(name) for name in ("arecord", "aplay")}
        if not all(tools.values()):
            raise ValueError("Both arecord and aplay are required")
        return tools

    def storypacks():
        from storypack import StoryPack
        books = [StoryPack(args.packs, book_id) for book_id in args.book_id]
        return [{"bookId": pack.book["bookId"], "version": pack.book["version"],
                 "segments": len(pack.segments), "voiceQuestions": len(pack.voice_questions)} for pack in books]

    check("runtime", python_runtime)
    check("locator", lambda: run_locator(args.locator))
    check("alsaTools", alsa_tools)
    check("uartNode", lambda: character_device(args.uart, os.R_OK | os.W_OK))
    if args.keys_device:
        check("keysNode", lambda: character_device(args.keys_device, os.R_OK))
    check("storyPacks", storypacks)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locator", type=Path, required=True)
    parser.add_argument("--uart", type=Path, required=True)
    parser.add_argument("--keys-device", type=Path)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--book-id", action="append", required=True, help="Check each named StoryPack")
    args = parser.parse_args()
    checks = prerequisites(args)
    print(json.dumps({"system": platform.system(), "machine": platform.machine(), "kernel": platform.release(),
                      "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
