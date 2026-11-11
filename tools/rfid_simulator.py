"""Local HTTP simulator for the Hi3516 + RFID integration draft."""

import argparse
import gzip
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import tarfile
import threading
import time
import wave
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from storypack import StoryPack, identifier, read_json
from storypack_import import MAX_ARCHIVE_BYTES, PackConflict, install
from reading_trace import STATUSES, append_position, validated_alignment

POSITION_REVIEW_STATUSES = {"needs_review", "position_mismatch", "confirmed"}
MAX_POSITION_REVIEWS = 512


class ApiError(Exception):
    def __init__(self, status, code):
        self.status = status
        self.code = code


class StateWriteError(OSError):
    def __init__(self, message, restart=False):
        super().__init__(message)
        self.code = "STORAGE_RESTART_REQUIRED" if restart else "STORAGE_UNAVAILABLE"


class Reader:
    def __init__(self, state_path, scan_timeout, packs=None, locator=None, recordings=None, *, device_prefix="sim",
                 trusted_clock=True, ctc_acoustic=None, allow_free_questions=False):
        self.state_path = state_path
        self.scan_timeout = scan_timeout
        self.packs = packs
        self.locator = locator
        self.recordings = recordings
        self.trusted_clock = trusted_clock
        self.ctc_acoustic = ctc_acoustic
        self.allow_free_questions = allow_free_questions
        self.import_lock = threading.Lock()
        self.storage_needs_restart = False
        self.scan = None
        self.active = None
        if state_path.exists():
            self.state = self.load_state(state_path)
        else:
            self.save({"deviceId": f"{device_prefix}-{uuid4().hex}", "bindings": {}, "sessions": [],
                       "positionReviews": []})

    @staticmethod
    def load_state(path):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise StateWriteError(f"Cannot read reader state {path}: {error}") from error
        if (not isinstance(state, dict) or not isinstance(state.get("deviceId"), str) or not state["deviceId"]
                or not isinstance(state.get("bindings"), dict) or not isinstance(state.get("sessions"), list)):
            raise StateWriteError(f"Invalid reader state: {path}")
        for uid, book in state["bindings"].items():
            if (not re.fullmatch(r"(?:[0-9A-F]{2}){1,16}", uid) or not isinstance(book, str)
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", book)):
                raise StateWriteError(f"Invalid book binding in {path}")
        seen = set()
        sessions_by_id = {}
        for session in state["sessions"]:
            session_id = session.get("sessionId") if isinstance(session, dict) else None
            if (not isinstance(session_id, str) or
                    not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", session_id) or session_id in seen):
                raise StateWriteError(f"Missing or duplicate reading session ID in {path}")
            seen.add(session_id)
            sessions_by_id[session_id] = session
        reviews = state.get("positionReviews", [])
        if not isinstance(reviews, list) or len(reviews) > MAX_POSITION_REVIEWS:
            raise StateWriteError(f"Invalid position review list in {path}")
        review_keys = set()
        for review in reviews:
            key = Reader.position_review_key(review) if isinstance(review, dict) else None
            if (key is None or key in review_keys or
                    not Reader.valid_position_review(review, state["deviceId"], sessions_by_id)):
                raise StateWriteError(f"Invalid position review in {path}")
            review_keys.add(key)
        return state

    @staticmethod
    def position_review_key(review):
        if not all(isinstance(review.get(field), str) for field in ("deviceId", "sessionId", "eventId")):
            return None
        return review["deviceId"], review["sessionId"], review["eventId"]

    @staticmethod
    def valid_position_review(review, device_id, sessions_by_id):
        if (not isinstance(review, dict) or review.get("deviceId") != device_id or
                not isinstance(review.get("sessionId"), str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", review["sessionId"]) or
                not isinstance(review.get("eventId"), str) or
                not re.fullmatch(r"[a-f0-9]{32}", review["eventId"]) or
                not isinstance(review.get("status"), str) or
                review["status"] not in POSITION_REVIEW_STATUSES or
                not isinstance(review.get("note"), str) or len(review["note"]) > 256 or
                type(review.get("updatedAtMs")) is not int or
                not 0 <= review["updatedAtMs"] <= 9007199254740991):
            return False
        session = sessions_by_id.get(review["sessionId"])
        trajectory = session.get("trajectory") if isinstance(session, dict) else None
        return (isinstance(trajectory, list) and any(
            isinstance(entry, dict) and entry.get("eventId") == review["eventId"] for entry in trajectory))

    def require_storage(self):
        if self.storage_needs_restart:
            raise StateWriteError("State directory could not be synchronized. Check storage and restart the reader.",
                                  restart=True)

    def save(self, state):
        self.require_storage()
        directory = None
        temporary = None
        published = False
        try:
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(self.state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.state_path.parent,
                                             prefix=f".{self.state_path.name}.", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump(state, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state_path)
            published = True
            if directory is not None:
                os.fsync(directory)
        except OSError as error:
            self.storage_needs_restart = published
            raise StateWriteError(f"Cannot save reader state {self.state_path}: {error}", restart=published) from error
        finally:
            if temporary is not None and not published:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if directory is not None:
                try:
                    os.close(directory)
                except OSError:
                    pass
        self.state = state

    def scan_open(self):
        return self.scan is not None and time.monotonic() < self.scan["deadline"]

    def dispatch(self, method, target, data):
        path = target.path
        if method == "GET" and path == "/v1/device":
            return {"deviceId": self.state["deviceId"], "firmwareVersion": "pc-simulator", "protocolVersion": 1}
        if method == "GET" and path == "/v1/books":
            return {"items": self.books()}
        if method == "POST" and path == "/v1/scans":
            self.require_storage()
            self.scan = {"scanId": uuid4().hex, "deadline": time.monotonic() + self.scan_timeout}
            return {"scanId": self.scan["scanId"]}
        if method == "GET" and path.startswith("/v1/scans/"):
            if not self.scan_open() or path != f"/v1/scans/{self.scan['scanId']}":
                return {"status": "expired"}
            if "tagUid" in self.scan:
                return {"status": "detected", "tagUid": self.scan["tagUid"]}
            return {"status": "waiting"}
        if method == "PUT" and path.startswith("/v1/book-bindings/"):
            return self.bind(path.rsplit("/", 1)[-1], data)
        if method == "GET" and path == "/v1/reading-sessions":
            query = parse_qs(target.query, keep_blank_values=True)
            limit = query.get("limit", [None])[0]
            if limit is not None and not re.fullmatch(r"[1-8]", limit):
                raise ApiError(400, "INVALID_LIMIT")
            return self.sessions(query.get("after", [""])[0], int(limit) if limit is not None else None)
        if method == "GET" and path == "/v1/position-reviews":
            self.require_storage()
            return {"items": self.state.get("positionReviews", [])}
        if method == "PUT" and path.startswith("/v1/position-reviews/"):
            parts = path.removeprefix("/v1/position-reviews/").split("/")
            if len(parts) != 2:
                raise ApiError(404, "NOT_FOUND")
            return self.save_position_review(parts[0], parts[1], data)
        if method == "POST" and path == "/sim/tap":
            return self.tap(data.get("tagUid"))
        if method == "POST" and path == "/sim/finish":
            return self.finish(data.get("lastSegmentId"))
        if method == "POST" and path == "/sim/select-segment":
            return self.select_segment(data.get("segmentId"))
        if method == "POST" and path == "/sim/ask":
            result = self.ask(data.get("question"))
            self.remember_answer(result, "text")
            return result
        if method == "POST" and path == "/sim/ask-recording":
            result = self.ask_recording(data.get("recording"))
            self.remember_answer(result, "voice")
            return result
        if method == "POST" and path == "/sim/locate":
            return self.locate(data.get("recording"))
        raise ApiError(404, "NOT_FOUND")

    def books(self):
        if self.packs is None:
            return [{"bookId": "bear-friends", "title": "小熊找朋友"}, {"bookId": "forest-seed", "title": "神奇的种子"}]
        books = []
        for directory in sorted(self.packs.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", directory.name):
                continue
            try:
                manifest = read_json(directory / "book.json")
                title = manifest["title"]
                if (manifest["formatVersion"] == 1 and manifest["bookId"] == directory.name
                        and isinstance(title, str) and title.strip()):
                    books.append({"bookId": directory.name, "title": title, "version": identifier(manifest["version"])})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return books

    def receive_pack(self, stream, size):
        if self.packs is None:
            raise ApiError(404, "PACK_IMPORT_UNAVAILABLE")
        if size <= 0:
            raise ApiError(400, "INVALID_BODY_LENGTH")
        if size > MAX_ARCHIVE_BYTES:
            raise ApiError(413, "PACK_TOO_LARGE")
        if not self.import_lock.acquire(blocking=False):
            raise ApiError(409, "PACK_IMPORT_BUSY")
        try:
            with tempfile.TemporaryFile(dir=self.packs) as source:
                deadline = time.monotonic() + 90
                while size:
                    if time.monotonic() >= deadline:
                        raise ApiError(408, "PACK_UPLOAD_TIMEOUT")
                    chunk = stream.read1(min(size, 65536))
                    if not chunk:
                        raise ApiError(400, "PACK_UPLOAD_INCOMPLETE")
                    source.write(chunk)
                    size -= len(chunk)
                source.seek(0)
                return install(source, self.packs)
        except PackConflict as error:
            raise ApiError(409, "PACK_ALREADY_EXISTS") from error
        except TimeoutError as error:
            raise ApiError(408, "PACK_UPLOAD_TIMEOUT") from error
        except (EOFError, ValueError, TypeError, KeyError, tarfile.TarError, gzip.BadGzipFile, zlib.error,
                sqlite3.Error, wave.Error) as error:
            raise ApiError(422, "PACK_INVALID") from error
        finally:
            self.import_lock.release()

    @staticmethod
    def check_uid(uid):
        if not isinstance(uid, str) or not re.fullmatch(r"(?:[0-9A-F]{2}){1,16}", uid):
            raise ApiError(400, "INVALID_TAG_UID")

    def bind(self, uid, data):
        self.require_storage()
        self.check_uid(uid)
        book = data.get("bookId")
        if not isinstance(book, str) or not book:
            raise ApiError(400, "INVALID_BOOK_ID")
        bindings = self.state["bindings"]
        scan_matches = self.scan_open() and self.scan["scanId"] == data.get("scanId")
        if bindings.get(uid) == book:
            if scan_matches and self.scan.get("tagUid") == uid:
                self.scan = None
            return {"tagUid": uid, "bookId": book}
        if not scan_matches:
            raise ApiError(409, "SCAN_EXPIRED")
        if self.scan.get("tagUid") != uid:
            raise ApiError(409, "SCAN_TAG_MISMATCH")
        if uid in bindings:
            raise ApiError(409, "TAG_ALREADY_BOUND")
        self.load_pack(book)
        self.save({**self.state, "bindings": {**bindings, uid: book}})
        self.scan = None
        return {"tagUid": uid, "bookId": book}

    def load_pack(self, book):
        if self.packs is None:
            if book not in ("bear-friends", "forest-seed"):
                raise ApiError(404, "PACK_NOT_FOUND")
            return None
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", book):
            raise ApiError(400, "INVALID_BOOK_ID")
        if not (self.packs / book).is_dir():
            raise ApiError(404, "PACK_NOT_FOUND")
        try:
            return StoryPack(self.packs, book)
        except (OSError, KeyError, ValueError, TypeError) as error:
            raise ApiError(409, "PACK_INVALID") from error

    def tap(self, uid):
        self.require_storage()
        self.check_uid(uid)
        if self.scan_open():
            self.scan.setdefault("tagUid", uid)
            return {"status": "scanned", "tagUid": self.scan["tagUid"]}
        book = self.state["bindings"].get(uid)
        if book is None:
            return {"status": "unbound", "tagUid": uid}
        if self.active is not None:
            if self.active["tagUid"] != uid:
                raise ApiError(409, "SESSION_ACTIVE")
            return {"status": "alreadyReading", "bookId": book}
        pack = self.load_pack(book)
        self.active = {"sessionId": uuid4().hex, "tagUid": uid, "bookId": book, "started": time.monotonic(), "pack": pack,
                       "segmentId": "s1", "positionSource": "manual", "positionUncertain": False, "answers": [],
                       "trajectory": [], "trajectoryDropped": 0,
                       "traceStats": {"repeated": 0, "relocated": 0, "uncertain": 0}, "wordIndex": None, "ctcTracker": None}
        result = {"status": "started", "bookId": book}
        if pack is not None:
            result.update(self.select_segment(pack.segments[0]["segmentId"]))
        return result

    def select_segment(self, segment_id):
        if self.active is None:
            raise ApiError(409, "NO_ACTIVE_SESSION")
        pack = self.active["pack"]
        if pack is None:
            if segment_id not in ("s1", "s2"):
                raise ApiError(400, "INVALID_SEGMENT_ID")
            result = {"segmentId": segment_id}
        else:
            segment = next((item for item in pack.segments if item["segmentId"] == segment_id), None)
            if segment is None:
                raise ApiError(400, "INVALID_SEGMENT_ID")
            result = segment.copy()
        self.active["segmentId"] = segment_id
        self.active["positionSource"] = "manual"
        self.active["positionUncertain"] = False
        self.active["wordIndex"] = None
        self.active["ctcTracker"] = None
        append_position(self.active, "manual", "selected", self.session_offset())
        return result

    def session_offset(self):
        return max(0, int((time.monotonic() - self.active["started"]) * 1000))

    def recording_source(self, recording):
        if self.active is None:
            raise ApiError(409, "NO_ACTIVE_SESSION")
        if (self.locator is None and self.ctc_acoustic is None) or self.active["pack"] is None:
            raise ApiError(409, "LOCATOR_UNAVAILABLE")
        if not isinstance(recording, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}\.wav", recording):
            raise ApiError(400, "INVALID_RECORDING")
        source = (self.recordings / recording).resolve()
        if not source.is_relative_to(self.recordings) or not source.is_file() or source.stat().st_size > 3_000_000:
            raise ApiError(400, "INVALID_RECORDING")
        return source

    @staticmethod
    def locate_events(command):
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=8, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise ApiError(422, "LOCATOR_FAILED") from error
        lines = run.stdout.splitlines()
        if not lines or not lines[0].startswith("endMs\t"):
            raise ApiError(422, "LOCATOR_FAILED")
        return lines[1:]

    def locate(self, recording):
        source = self.recording_source(recording)
        if self.ctc_acoustic is not None:
            from ctc_acoustic import read_pcm_wav
            from ctc_alignment import CtcBookTracker
            from reading_probe import ctc_events
            if self.active["ctcTracker"] is None:
                self.active["ctcTracker"] = CtcBookTracker(self.active["pack"].text_index(), self.ctc_acoustic)
            events = [self.apply_alignment_event(event) for event in ctc_events(
                self.active["pack"], read_pcm_wav(source), self.ctc_acoustic, self.active["ctcTracker"])]
        else:
            events = [self.apply_locator_event(line) for line in self.locate_events(self.locator_command(str(source)))]
        return {"segmentId": self.active["segmentId"], "events": events}

    def locator_command(self, source):
        pack = self.active["pack"]
        references = pack.reference_args("segments")
        return [str(self.locator), source, f"--start={self.active['segmentId']}", *references]

    @staticmethod
    def parse_locator_event(line):
        end_ms, status, segment_id, distance, margin = line.split("\t")
        return {"endMs": float(end_ms), "status": status, "segmentId": segment_id,
                "distance": float(distance) if distance else None, "margin": float(margin) if margin else None}

    def apply_locator_event(self, line):
        event = self.parse_locator_event(line)
        status, segment_id = event["status"], event["segmentId"]
        if status not in STATUSES or not math.isfinite(event["endMs"]) or event["endMs"] < 0:
            raise ValueError("Invalid locator position event")
        if status in ("matched", "relocated", "repeated"):
            if not any(segment["segmentId"] == segment_id for segment in self.active["pack"].segments):
                raise ValueError("Locator returned a segment outside the current book")
            self.active["segmentId"] = segment_id
            self.active["positionSource"] = "audio"
            self.active["positionUncertain"] = False
            self.active["wordIndex"] = None
        else:
            self.active["positionUncertain"] = True
        append_position(self.active, "mfcc", status, self.session_offset())
        return event

    def apply_alignment_event(self, event):
        if self.active is None:
            raise ApiError(409, "NO_ACTIVE_SESSION")
        word = validated_alignment(self.active["pack"], event)
        if event["status"] == "uncertain":
            self.active["positionUncertain"] = True
        else:
            self.active.update(segmentId=event["segmentId"], positionSource="audio",
                               positionUncertain=False, wordIndex=word["wordIndex"])
        if event["final"]:
            append_position(self.active, "ctc", event["status"], self.session_offset(), word)
        return event

    def voice_prompt(self, status):
        result = {"status": status, "segmentId": self.active["segmentId"]}
        audio = self.active["pack"].voice_prompts.get(status)
        if audio:
            result["audio"] = audio
        return result

    def ask_recording(self, recording):
        if self.locator is None and self.ctc_acoustic is None:
            raise ApiError(409, "LOCATOR_UNAVAILABLE")
        source = self.recording_source(recording)
        if self.active["positionUncertain"]:
            return self.voice_prompt("needsPositionConfirmation")
        if not self.active["pack"].voice_questions:
            return self.free_voice_answer(source) if self.allow_free_questions else self.voice_prompt("unsupported")
        if self.locator is None:
            return self.free_voice_answer(source) if self.allow_free_questions else self.voice_prompt("unsupported")
        return self.answer_voice_events(self.locate_events(self.question_command(str(source))), source)

    def question_command(self, source):
        pack = self.active["pack"]
        references = pack.reference_args("questions")
        flags = ["--endpoint-only"] if source == "-" else []
        return [str(self.locator), source, *flags, *references]

    def answer_voice_events(self, lines, recording=None):
        if len(lines) != 1:
            if self.allow_free_questions and recording is not None:
                return self.free_voice_answer(recording)
            return self.voice_prompt("repeatQuestion")
        match = self.parse_locator_event(lines[0])
        question = self.active["pack"].voice_questions.get(match["segmentId"])
        if (match["status"] != "matched" or question is None or match["distance"] is None or
                not 0 <= match["distance"] <= 5 or (match["margin"] is not None and not match["margin"] >= 1.5)):
            if self.allow_free_questions and recording is not None:
                return self.free_voice_answer(recording)
            return self.voice_prompt("unsupported")
        answer = self.ask(question["question"])
        if answer["status"] == "answered":
            return answer
        return self.voice_prompt(answer["status"])

    def free_voice_answer(self, recording):
        if not self.allow_free_questions or self.ctc_acoustic is None:
            return self.voice_prompt("unsupported")
        try:
            from free_question import transcribe_question
            transcript = transcribe_question(recording, self.ctc_acoustic)
            return self.ask(transcript) if transcript else self.voice_prompt("unsupported")
        except (ImportError, OSError, ValueError):
            return self.voice_prompt("unsupported")

    def ask(self, question):
        if self.active is None:
            raise ApiError(409, "NO_ACTIVE_SESSION")
        if not isinstance(question, str) or not question.strip():
            raise ApiError(400, "INVALID_QUESTION")
        if self.active["positionUncertain"]:
            return {"status": "needsPositionConfirmation", "segmentId": self.active["segmentId"]}
        pack = self.active["pack"]
        if pack is None:
            return {"status": "unsupported"}
        try:
            result = pack.answer(self.active["segmentId"], question)
            if result.get("status") == "unsupported" and self.allow_free_questions:
                return pack.answer_free(self.active["segmentId"], question)
            return result
        except (ValueError, sqlite3.Error) as error:
            raise ApiError(409, "PACK_INVALID") from error

    def remember_answer(self, result, source):
        if self.active is None or result.get("status") != "answered":
            return
        # Store only the approved question alias and answer, never the raw question or audio path.
        entry = {field: result[field] for field in ("segmentId", "question", "answer", "evidence")}
        if any(not isinstance(value, str) or not value or len(value.encode("utf-16-le")) > 4096 for value in entry.values()):
            return
        entry.update(source=source, offsetMs=int((time.monotonic() - self.active["started"]) * 1000))
        answers = self.active["answers"]
        answers.append(entry)
        if len(answers) > 20:
            del answers[0]

    def finish(self, segment):
        self.require_storage()
        if self.active is None:
            raise ApiError(409, "NO_ACTIVE_SESSION")
        if segment is not None:
            self.select_segment(segment)
        pack = self.active["pack"]
        ended_at = time.time_ns() // 1_000_000
        session = {
            "sessionId": self.active["sessionId"],
            "bookId": self.active["bookId"],
            "packVersion": pack.book["version"] if pack is not None else "1",
            "endedAtMs": ended_at if self.trusted_clock and ended_at >= 1577836800000 else None,
            "durationMs": int((time.monotonic() - self.active["started"]) * 1000),
            "lastSegmentId": self.active["segmentId"],
            "positionSource": self.active["positionSource"],
            "positionUncertain": self.active["positionUncertain"],
        }
        if self.active["answers"]:
            session["answers"] = self.active["answers"][:]
        if self.active["trajectory"]:
            session.update(trajectory=self.active["trajectory"][:],
                           trajectoryDropped=self.active["trajectoryDropped"], traceStats=self.active["traceStats"].copy())
        if self.active["wordIndex"] is not None:
            session["lastWordIndex"] = self.active["wordIndex"]
        self.save({**self.state, "sessions": [*self.state["sessions"], session]})
        self.active = None
        return session

    def sessions(self, cursor, limit=None):
        self.require_storage()
        sessions = self.state["sessions"]
        start = 0
        if cursor:
            start = next((i + 1 for i, session in enumerate(sessions) if session["sessionId"] == cursor), 0)
            if not start:
                raise ApiError(400, "INVALID_CURSOR")
        end = len(sessions) if limit is None else start + limit
        items = sessions[start:end]
        result = {"items": items, "nextCursor": items[-1]["sessionId"] if items else cursor}
        if limit is not None:
            result["hasMore"] = end < len(sessions)
        return result

    def save_position_review(self, session_id, event_id, data):
        self.require_storage()
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", session_id) or
                not re.fullmatch(r"[a-f0-9]{32}", event_id)):
            raise ApiError(400, "INVALID_POSITION_REVIEW")
        sessions = {session["sessionId"]: session for session in self.state["sessions"]}
        session = sessions.get(session_id)
        trajectory = session.get("trajectory") if isinstance(session, dict) else None
        if not isinstance(trajectory, list) or not any(
                isinstance(entry, dict) and entry.get("eventId") == event_id for entry in trajectory):
            raise ApiError(404, "POSITION_TRACE_NOT_FOUND")
        candidate = {"deviceId": self.state["deviceId"], "sessionId": session_id, "eventId": event_id,
                     "status": data.get("status"), "note": data.get("note"),
                     "updatedAtMs": data.get("updatedAtMs")}
        if not self.valid_position_review(candidate, self.state["deviceId"], sessions):
            raise ApiError(400, "INVALID_POSITION_REVIEW")
        reviews = self.state.get("positionReviews", [])
        key = self.state["deviceId"], session_id, event_id
        previous = next((item for item in reviews if self.position_review_key(item) == key), None)
        if previous is not None and candidate["updatedAtMs"] <= previous["updatedAtMs"]:
            return previous
        if previous is None and len(reviews) >= MAX_POSITION_REVIEWS:
            raise ApiError(409, "POSITION_REVIEW_LIMIT")
        updated = [item for item in reviews if self.position_review_key(item) != key]
        updated.append(candidate)
        self.save({**self.state, "positionReviews": updated})
        return candidate


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def handle_request(self):
        try:
            self.authorize()
            size = int(self.headers.get("Content-Length", "0"))
            target = urlsplit(self.path)
            if self.command == "POST" and target.path == "/v1/storypacks":
                if self.headers.get_content_type() != "application/gzip" or self.headers.get("Transfer-Encoding"):
                    raise ApiError(415, "INVALID_PACK_UPLOAD")
                result = self.server.reader.receive_pack(self.rfile, size)
            else:
                if size < 0 or size > 8192:
                    raise ApiError(400, "INVALID_BODY_LENGTH")
                data = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(data, dict):
                    raise ApiError(400, "INVALID_BODY")
                result = self.server.reader.dispatch(self.command, target, data)
            status = 200
        except (ValueError, UnicodeError):
            status, result = 400, {"code": "INVALID_REQUEST"}
        except ApiError as error:
            status, result = error.status, {"code": error.code}
        except StateWriteError as error:
            self.log_error("%s", error)
            status, result = 503, {"code": error.code}
        except OSError as error:
            self.log_error("%s", error)
            status, result = 500, {"code": "IO_ERROR"}
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorize(self):
        pass

    do_GET = do_POST = do_PUT = handle_request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="JSON file for simulated device data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scan-timeout", type=int, default=30, help="Scan timeout in seconds")
    parser.add_argument("--packs", type=Path, help="Directory of compiled StoryPacks")
    parser.add_argument("--locator", type=Path, help="Native locate_wav executable")
    parser.add_argument("--ctc-model", type=Path, help="Optional pinned PC CTC model for reading and question transcription")
    parser.add_argument("--allow-free-questions", action="store_true",
                        help="PC-only: allow conservative text/CTC questions answered with an exact page excerpt")
    parser.add_argument("--recordings", type=Path, help="Directory of local WAV files for simulated read-aloud input")
    args = parser.parse_args()
    if args.scan_timeout < 1:
        parser.error("--scan-timeout must be positive")
    if (args.locator or args.ctc_model or args.recordings) and not ((args.locator or args.ctc_model) and args.recordings and args.packs):
        parser.error("--recordings, --packs and --locator or --ctc-model are required for audio location")
    if args.locator and (not args.locator.is_file() or not args.recordings.is_dir()):
        parser.error("Locator executable or recordings directory does not exist")
    acoustic = None
    if args.ctc_model:
        try:
            from ctc_acoustic import ZipformerCtc
            acoustic = ZipformerCtc(args.ctc_model)
        except (OSError, ValueError, ImportError) as error:
            parser.error(str(error))
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.reader = Reader(args.state, args.scan_timeout, args.packs,
                               args.locator.resolve() if args.locator else None,
                               args.recordings.resolve() if args.recordings else None, ctc_acoustic=acoustic,
                               allow_free_questions=args.allow_free_questions)
        print(f"RFID simulator: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
