"""Qt control panel for the local RFID reading demo."""

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QProcess, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parents[1]
SIMULATOR = ROOT / "tools" / "rfid_simulator.py"


class JobSignals(QObject):
    finished = Signal(str, object, str)


class HttpJob(QRunnable):
    def __init__(self, action, callback):
        super().__init__()
        self.action = action
        self.callback = callback
        self.signals = JobSignals()

    def run(self):
        try:
            result = self.callback()
            self.signals.finished.emit(self.action, result, "")
        except Exception as error:
            # Keep request URLs, question text, and environment values out of logs.
            self.signals.finished.emit(self.action, None, str(error))


def request_json(base_url, method, path, data=None, timeout=6):
    payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(base_url.rstrip("/") + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            code = detail.get("code", "请求失败") if isinstance(detail, dict) else "请求失败"
        except (UnicodeError, ValueError):
            code = "请求失败"
        raise RuntimeError(f"HTTP {error.code}: {code}") from None
    except (TimeoutError, URLError, OSError) as error:
        reason = getattr(error, "reason", None)
        if isinstance(reason, TimeoutError) or isinstance(error, TimeoutError):
            raise RuntimeError("连接超时，请确认电脑服务已启动") from None
        raise RuntimeError("无法连接电脑服务，请检查地址、端口和服务状态") from None
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("服务返回的数据不是有效 JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("服务返回的数据格式不正确")
    return result


def request_bytes(base_url, path, timeout=12):
    request = Request(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), headers={"Accept": "audio/wav"})
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.headers.get_content_type() != "audio/wav":
                raise RuntimeError("阅读器没有返回 WAV 音频")
            return response.read()
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            code = detail.get("code", "音频读取失败") if isinstance(detail, dict) else "音频读取失败"
        except (UnicodeError, ValueError):
            code = "音频读取失败"
        raise RuntimeError(f"HTTP {error.code}: {code}") from None
    except (TimeoutError, URLError, OSError) as error:
        reason = getattr(error, "reason", None)
        if isinstance(reason, TimeoutError) or isinstance(error, TimeoutError):
            raise RuntimeError("读取音频超时") from None
        raise RuntimeError("无法从电脑服务读取音频") from None


class DebugStation(QMainWindow):
    def __init__(self, auto_connect=True):
        super().__init__()
        self.setWindowTitle("童阅智伴 · 本地调试台")
        self.resize(1120, 760)
        self.setMinimumSize(920, 620)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.pending_action = ""
        self.reading = {"active": False}
        self.answer_audio_url = ""
        self.audio_path = None
        self.server_process = QProcess(self)
        self.server_process.setWorkingDirectory(str(ROOT))
        self.server_process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.server_process.readyReadStandardOutput.connect(self._server_stdout)
        self.server_process.readyReadStandardError.connect(self._server_stderr)
        self.server_process.started.connect(lambda: self._log("电脑 RFID 服务进程已启动"))
        self.server_process.finished.connect(self._server_finished)
        self.server_process.errorOccurred.connect(self._server_error)

        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_player.playbackStateChanged.connect(self._playback_changed)
        self.audio_player.errorOccurred.connect(lambda _error, message: self._log("音频播放错误：" + message))

        self._build_ui()
        self._style_ui()
        if auto_connect:
            QTimer.singleShot(300, self.refresh_status)

    def _build_ui(self):
        root = QWidget(self)
        layout = QHBoxLayout(root)
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        server_group = QGroupBox("服务与连接")
        server_form = QFormLayout(server_group)
        self.address_input = QLineEdit("http://127.0.0.1:8765")
        self.port_input = QLineEdit("8765")
        self.packs_input = QLineEdit(str(ROOT / "output" / "rfid-semantic-qa-demo" / "packs"))
        browse_packs = QPushButton("选择")
        browse_packs.clicked.connect(self._choose_packs)
        packs_row = QHBoxLayout()
        packs_row.addWidget(self.packs_input, 1)
        packs_row.addWidget(browse_packs)
        self.state_file = Path(tempfile.gettempdir()) / "tongyue-demo-reader.json"
        self.tts_directory = Path(tempfile.gettempdir()) / "tongyue-debug-tts"
        server_form.addRow("服务地址", self.address_input)
        server_form.addRow("服务端口", self.port_input)
        server_form.addRow("StoryPack", packs_row)
        self.start_button = QPushButton("启动电脑服务")
        self.stop_button = QPushButton("停止本台启动的服务")
        self.refresh_button = QPushButton("连接 / 刷新状态")
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.refresh_status)
        server_buttons = QHBoxLayout()
        server_buttons.addWidget(self.start_button)
        server_buttons.addWidget(self.stop_button)
        server_form.addRow(server_buttons)
        server_form.addRow(self.refresh_button)
        controls_layout.addWidget(server_group)

        bind_group = QGroupBox("模拟 RFID 绑定")
        bind_form = QFormLayout(bind_group)
        self.uid_input = QLineEdit("0004A12B")
        self.book_select = QComboBox()
        self.bind_button = QPushButton("模拟扫描并绑定 UID")
        self.bind_button.clicked.connect(self.bind_tag)
        bind_form.addRow("测试 UID", self.uid_input)
        bind_form.addRow("绑定绘本", self.book_select)
        bind_form.addRow(self.bind_button)
        controls_layout.addWidget(bind_group)

        reading_group = QGroupBox("阅读操作")
        reading_layout = QVBoxLayout(reading_group)
        self.tap_button = QPushButton("模拟刷卡开始 / 继续")
        self.tap_button.clicked.connect(self.simulate_tap)
        reading_layout.addWidget(self.tap_button)
        segment_buttons = QHBoxLayout()
        self.previous_button = QPushButton("上一段")
        self.next_button = QPushButton("下一段")
        self.previous_button.clicked.connect(lambda: self.move_segment(-1))
        self.next_button.clicked.connect(lambda: self.move_segment(1))
        segment_buttons.addWidget(self.previous_button)
        segment_buttons.addWidget(self.next_button)
        reading_layout.addLayout(segment_buttons)
        self.play_story_button = QPushButton("播放正文")
        self.play_story_button.clicked.connect(self.play_story_audio)
        reading_layout.addWidget(self.play_story_button)
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("输入针对当前段的问题")
        self.ask_button = QPushButton("提问并查看证据")
        self.ask_button.clicked.connect(self.ask_question)
        reading_layout.addWidget(self.question_input)
        reading_layout.addWidget(self.ask_button)
        self.play_answer_button = QPushButton("播放答案音频")
        self.play_answer_button.clicked.connect(self.play_answer_audio)
        self.play_answer_button.setEnabled(False)
        reading_layout.addWidget(self.play_answer_button)
        self.finish_button = QPushButton("结束阅读并刷新历史")
        self.finish_button.clicked.connect(self.finish_reading)
        reading_layout.addWidget(self.finish_button)
        controls_layout.addWidget(reading_group)
        controls_layout.addStretch(1)

        activity = QWidget()
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("尚未连接")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(98)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("操作记录、连接状态与错误会显示在这里")
        activity_layout.addWidget(QLabel("设备与阅读状态"))
        activity_layout.addWidget(self.status_label)
        activity_layout.addWidget(QLabel("实时日志"))
        activity_layout.addWidget(self.log_view, 1)

        splitter.addWidget(controls)
        splitter.addWidget(activity)
        splitter.setSizes([370, 750])
        layout.addWidget(splitter)
        self.setCentralWidget(root)
        self.network_buttons = [
            self.refresh_button, self.bind_button, self.tap_button, self.previous_button,
            self.next_button, self.play_story_button, self.ask_button, self.play_answer_button, self.finish_button,
        ]

    def _style_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f7f8f4; color: #19243b; font-size: 14px; }
            QGroupBox { border: 1px solid #dce6dc; border-radius: 12px; margin-top: 10px; padding: 12px 9px 9px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #d8e0dc; border-radius: 8px; padding: 8px; }
            QPushButton { background: #e8f5eb; border: 0; border-radius: 8px; padding: 9px 11px; color: #176b3b; font-weight: 600; }
            QPushButton:hover { background: #d8efdf; }
            QPushButton:disabled { color: #8b9891; background: #edf0ed; }
        """)

    def _log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")

    def _base_url(self):
        return self.address_input.text().strip().rstrip("/")

    def _choose_packs(self):
        selected = QFileDialog.getExistingDirectory(self, "选择 StoryPack 目录", self.packs_input.text())
        if selected:
            self.packs_input.setText(selected)

    def _start_job(self, action, callback):
        if self.pending_action:
            self._log("当前操作尚未完成，请稍候")
            return
        self.pending_action = action
        self.status_label.setText(action + "…")
        for button in self.network_buttons:
            button.setEnabled(False)
        job = HttpJob(action, callback)
        job.signals.finished.connect(self._job_finished)
        self.pool.start(job)

    def _job_finished(self, action, result, error):
        self.pending_action = ""
        for button in self.network_buttons:
            button.setEnabled(True)
        if error:
            self.status_label.setText("操作失败：" + error)
            self._log(action + "失败：" + error)
            self._update_segment_buttons()
            return
        try:
            self._handle_result(action, result)
        except Exception as error:
            self.status_label.setText("显示结果失败：" + str(error))
            self._log(action + "结果无法显示：" + str(error))
        self._update_segment_buttons()

    def _handle_result(self, action, result):
        if action == "连接 / 刷新状态":
            self._populate_snapshot(result)
            self._log("已连接：" + result["device"].get("deviceId", "未知设备"))
        elif action == "模拟扫描并绑定 UID":
            self._populate_snapshot(result)
            self._log("模拟标签已绑定到《" + result["bookTitle"] + "》")
        elif action == "模拟刷卡":
            status = result["tap"].get("status", "unknown")
            if status == "unbound":
                self._log("UID 尚未绑定，请先选择绘本并执行“模拟扫描并绑定 UID”")
            else:
                self._populate_snapshot(result["snapshot"])
                self._log("模拟刷卡结果：" + status)
        elif action in ("上一段", "下一段"):
            self._show_reading(result)
            self._log("当前阅读段已更新")
        elif action == "提问":
            self.answer_audio_url = result.get("audioUrl", "")
            self.play_answer_button.setEnabled(bool(self.answer_audio_url))
            answer = result.get("answer", "")
            evidence = result.get("evidence", "")
            if result.get("status") == "answered":
                self.status_label.setText("回答：" + answer + "\n原文证据：" + evidence)
                self._log("问答已返回；证据状态：" + ("已提供" if evidence else "缺少证据"))
                if result.get("speechUnavailable"):
                    self._log("本机语音合成暂不可用，文字回答与原文证据仍可查看")
            else:
                self.status_label.setText("问答状态：" + result.get("status", "未知"))
                self._log("问答未生成可用回答：" + result.get("status", "未知"))
        elif action in ("播放正文", "播放答案音频"):
            self._play_wav(result)
            self._log(action + "音频已载入")
        elif action == "结束阅读并刷新历史":
            self.reading = {"active": False}
            sessions = result.get("sessions", {}).get("items", [])
            self.status_label.setText(f"阅读已结束；服务端可查看 {len(sessions)} 条最近记录")
            self._log("阅读已结束并重新读取服务端历史")
        self._update_segment_buttons()

    def _populate_snapshot(self, snapshot):
        device = snapshot["device"]
        books = snapshot["books"].get("items", [])
        current = self.book_select.currentData()
        previous_session = self.reading.get("sessionId")
        next_reading = snapshot["reading"]
        if not next_reading.get("active") or next_reading.get("sessionId") != previous_session:
            self.answer_audio_url = ""
            self.play_answer_button.setEnabled(False)
        self.book_select.clear()
        for book in books:
            self.book_select.addItem(book.get("title", book.get("bookId", "绘本")), book.get("bookId", ""))
        if current:
            index = self.book_select.findData(current)
            if index >= 0:
                self.book_select.setCurrentIndex(index)
        self.reading = next_reading
        self.status_label.setText("设备：" + device.get("deviceId", "未知") +
                                  "\n状态：电脑 RFID 模拟器\n" + self._reading_text(self.reading))
        self._update_segment_buttons()

    @staticmethod
    def _reading_text(reading):
        if not reading.get("active"):
            return "当前没有活动阅读"
        return (f"当前绘本：《{reading.get('bookTitle', reading.get('bookId', '未知'))}》\n"
                f"第 {reading.get('segmentNumber', 0)} / {reading.get('segmentCount', 0)} 段\n"
                f"{reading.get('text', '')}")

    def _show_reading(self, reading):
        self.reading = reading
        self.status_label.setText(self._reading_text(reading))

    def _update_segment_buttons(self):
        active = bool(self.reading.get("active")) and not bool(self.pending_action)
        number = self.reading.get("segmentNumber", 0)
        count = self.reading.get("segmentCount", 0)
        self.previous_button.setEnabled(active and number > 1)
        self.next_button.setEnabled(active and number < count)
        self.play_story_button.setEnabled(active and not bool(self.pending_action))
        self.finish_button.setEnabled(active and not bool(self.pending_action))
        if not self.answer_audio_url:
            self.play_answer_button.setEnabled(False)

    @staticmethod
    def _snapshot_request(base):
        return {
            "device": request_json(base, "GET", "/v1/device"),
            "books": request_json(base, "GET", "/v1/books"),
            "reading": request_json(base, "GET", "/sim/reading"),
        }

    def refresh_status(self):
        base = self._base_url()
        self._start_job("连接 / 刷新状态", lambda: self._snapshot_request(base))

    def bind_tag(self):
        uid = self.uid_input.text().strip().upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}){2,16}", uid):
            self._log("UID 必须是 4 到 32 位的偶数长度十六进制字符串")
            return
        book_id = self.book_select.currentData()
        book_title = self.book_select.currentText()
        base = self._base_url()
        if not book_id:
            self._log("服务端没有绘本；请先生成 StoryPack 并重启电脑服务")
            return

        def bind():
            scan = request_json(base, "POST", "/v1/scans")
            scan_id = scan.get("scanId", "")
            if not scan_id:
                raise RuntimeError("服务未返回扫描编号")
            tap = request_json(base, "POST", "/sim/tap", {"tagUid": uid})
            detected = request_json(base, "GET", "/v1/scans/" + scan_id)
            if tap.get("status") != "scanned" or detected.get("tagUid") != uid:
                raise RuntimeError("模拟扫描没有识别到输入的 UID")
            request_json(base, "PUT", "/v1/book-bindings/" + uid, {"scanId": scan_id, "bookId": book_id})
            snapshot = self._snapshot_request(base)
            return {**snapshot, "bookTitle": book_title}

        self._start_job("模拟扫描并绑定 UID", bind)

    def simulate_tap(self):
        uid = self.uid_input.text().strip().upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}){2,16}", uid):
            self._log("UID 必須是 4 到 32 位的偶数长度十六进制字符串")
            return
        base = self._base_url()

        def tap():
            result = request_json(base, "POST", "/sim/tap", {"tagUid": uid})
            if result.get("status") == "unbound":
                return {"tap": result}
            return {"tap": result, "snapshot": self._snapshot_request(base)}

        self._start_job("模拟刷卡", tap)

    def move_segment(self, delta):
        action = "上一段" if delta < 0 else "下一段"
        base = self._base_url()

        def select():
            reading = request_json(base, "GET", "/sim/reading")
            ids = reading.get("segmentIds", [])
            index = int(reading.get("segmentNumber", 0)) - 1 + delta
            if not reading.get("active") or index < 0 or index >= len(ids):
                raise RuntimeError("当前没有可切换的相邻段落")
            request_json(base, "POST", "/sim/select-segment", {"segmentId": ids[index]})
            return request_json(base, "GET", "/sim/reading")

        self._start_job(action, select)

    def ask_question(self):
        question = self.question_input.text().strip()
        if not question:
            self._log("请先输入一个问题")
            return
        self.answer_audio_url = ""
        self.play_answer_button.setEnabled(False)
        base = self._base_url()
        self._start_job("提问", lambda: request_json(base, "POST", "/sim/ask", {"question": question}))

    def play_story_audio(self):
        base = self._base_url()
        self._start_job("播放正文", lambda: request_bytes(base, "/sim/audio/current"))

    def play_answer_audio(self):
        if not self.answer_audio_url:
            self._log("当前回答没有可播放的音频")
            return
        path = self.answer_audio_url
        base = self._base_url()
        self._start_job("播放答案音频", lambda: request_bytes(base, path))

    def finish_reading(self):
        base = self._base_url()

        def finish():
            reading = request_json(base, "GET", "/sim/reading")
            if reading.get("active"):
                request_json(base, "POST", "/sim/finish", {"lastSegmentId": reading.get("segmentId", "")})
            sessions = request_json(base, "GET", "/v1/reading-sessions?limit=8")
            return {"sessions": sessions}

        self._start_job("结束阅读并刷新历史", finish)

    def start_server(self):
        if self.server_process.state() != QProcess.ProcessState.NotRunning:
            self._log("本调试台启动的服务已经在运行")
            return
        try:
            port = int(self.port_input.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._log("服务端口必须在 1 到 65535 之间")
            return
        packs = Path(self.packs_input.text().strip())
        if not packs.is_dir():
            self._log("找不到 StoryPack 目录：请先生成 output/rfid-semantic-qa-demo")
            return
        args = ["-X", "utf8", "-B", str(SIMULATOR), "--state", str(self.state_file), "--port", str(port),
                "--packs", str(packs), "--allow-free-questions", "--local-tts-dir", str(self.tts_directory)]
        self.server_process.start(sys.executable, args)
        self._log(f"正在启动电脑服务（端口 {port}）；若端口已占用，可直接连接现有服务")
        QTimer.singleShot(800, self.refresh_status)

    def stop_server(self):
        if self.server_process.state() == QProcess.ProcessState.NotRunning:
            self._log("这台调试台没有启动中的服务；不会停止其他窗口的服务")
            return
        self.server_process.terminate()
        self._log("正在停止本调试台启动的电脑服务")
        QTimer.singleShot(2500, self._kill_server_if_needed)

    def _kill_server_if_needed(self):
        if self.server_process.state() != QProcess.ProcessState.NotRunning:
            self.server_process.kill()

    def _server_stdout(self):
        message = bytes(self.server_process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if message:
            self._log(message)

    def _server_stderr(self):
        message = bytes(self.server_process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if message:
            self._log("服务输出：" + message)

    def _server_finished(self, code, _status):
        self._log(f"电脑服务进程已退出（代码 {code}）")

    def _server_error(self, _error):
        self._log("无法启动电脑服务，请查看服务路径、Python 环境和端口")

    def _play_wav(self, audio):
        if not audio.startswith(b"RIFF"):
            raise RuntimeError("返回的文件不是 WAV")
        self._remove_audio_file()
        path = Path(tempfile.gettempdir()) / f"tongyue-debug-{uuid4().hex}.wav"
        path.write_bytes(audio)
        self.audio_path = path
        self.audio_player.setSource(QUrl.fromLocalFile(os.fspath(path)))
        self.audio_player.play()

    def _playback_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._log("音频正在播放")
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            QTimer.singleShot(1000, self._remove_audio_file)

    def _remove_audio_file(self):
        if self.audio_path:
            try:
                self.audio_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.audio_path = None

    def closeEvent(self, event):
        if self.server_process.state() != QProcess.ProcessState.NotRunning:
            self.server_process.terminate()
            self.server_process.waitForFinished(1000)
            if self.server_process.state() != QProcess.ProcessState.NotRunning:
                self.server_process.kill()
        self.pool.waitForDone(1000)
        self.audio_player.stop()
        self._remove_audio_file()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = DebugStation(auto_connect="--no-connect" not in sys.argv)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
