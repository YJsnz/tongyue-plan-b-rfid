"""Offline Windows Chinese speech synthesis for the PC question-answer prototype."""

import base64
import json
import shutil
import subprocess
import sys
import uuid
import wave
from pathlib import Path


_SYNTHESIZE = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$job = [Console]::In.ReadToEnd() | ConvertFrom-Json
$engine = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
try {
    $voice = if ($job.voice) { $job.voice } else {
        ($engine.GetInstalledVoices() | Where-Object {
            $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN'
        } | Select-Object -First 1).VoiceInfo.Name
    }
    if (-not $voice) { throw 'Install an enabled zh-CN Windows speech synthesis voice.' }
    $engine.SelectVoice($voice)
    if ($engine.Voice.Culture.Name -ne 'zh-CN') { throw "Expected a zh-CN voice: $voice" }
    $engine.SetOutputToWaveFile($job.output, $format)
    try { $engine.Speak($job.text) } finally { $engine.SetOutputToNull() }
    @{ voice = $engine.Voice.Name } | ConvertTo-Json -Compress
} finally {
    $engine.Dispose()
}
"""

_PLAY = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System
$path = [Console]::In.ReadToEnd() | ConvertFrom-Json
$player = [System.Media.SoundPlayer]::new($path)
try { $player.PlaySync() } finally { $player.Dispose() }
"""


def _powershell():
    if sys.platform != "win32":
        raise RuntimeError("本地离线中文语音合成目前只支持 Windows System.Speech")
    executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if executable is None:
        raise RuntimeError("找不到 PowerShell，无法运行本地中文语音合成")
    return executable


def _run(script, payload, timeout=90):
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                          input=payload, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)


def _validate(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("本地语音合成输出格式不是 16 kHz 单声道 PCM16")
        frames = audio.getnframes()
        if frames <= 0 or frames * 2 > path.stat().st_size:
            raise ValueError("本地语音合成没有生成完整音频")
        pcm = audio.readframes(frames)
        if not any(pcm):
            raise ValueError("本地语音合成输出了静音文件")
        return frames * 1000 // audio.getframerate()


def synthesize(text, output_dir, *, voice=None):
    """Write a validated, locally synthesized Chinese WAV and return its provenance."""
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise ValueError("语音文本必须为 1 至 500 个字符")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    destination = output_dir / f"answer-{identifier}.wav"
    temporary = output_dir / f".{identifier}.tmp.wav"
    try:
        completed = _run(_SYNTHESIZE, json.dumps({"text": text, "output": str(temporary), "voice": voice},
                                                  ensure_ascii=False))
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "Windows 本地语音合成失败")
        provenance = json.loads(completed.stdout)
        duration_ms = _validate(temporary)
        temporary.replace(destination)
        return {"path": str(destination), "voice": provenance["voice"], "durationMs": duration_ms}
    finally:
        temporary.unlink(missing_ok=True)


def play(path):
    """Play a generated WAV synchronously using the local Windows audio device."""
    source = Path(path).resolve()
    _validate(source)
    completed = _run(_PLAY, json.dumps(str(source)), timeout=120)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Windows 音频播放失败")
