"""Generate a local Chinese RFID demo pack and labelled fixtures using Windows speech synthesis."""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tarfile
import wave
from pathlib import Path

from storypack import compile_pack, read_json, write_json


SYNTHESIZE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$batch = [Console]::In.ReadToEnd() | ConvertFrom-Json
$engine = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
try {
    $default = $engine.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN'
    } | Select-Object -First 1
    if (-not $batch.voice -and -not $default) { throw 'Install a Chinese Windows speech synthesis voice.' }
    $voice = if ($batch.voice) { $batch.voice } else { $default.VoiceInfo.Name }
    $fixtureVoice = if ($batch.fixtureVoice) { $batch.fixtureVoice } else { $voice }
    foreach ($job in $batch.jobs) {
        $name = if ($job.fixture) { $fixtureVoice } else { $voice }
        $engine.SelectVoice($name)
        if ($engine.Voice.Culture.Name -ne 'zh-CN') { throw "Expected a zh-CN voice: $name" }
        $engine.Rate = $job.rate
        $engine.SetOutputToWaveFile($job.output, $format)
        try { $engine.Speak($job.text) } finally { $engine.SetOutputToNull() }
    }
    @{ referenceVoice = $voice; fixtureVoice = $fixtureVoice } | ConvertTo-Json -Compress
} finally {
    $engine.Dispose()
}
"""

PROMPTS = {
    "questionReady": "我准备好了，请问吧。",
    "unsupported": "我还不会回答这个问题，我们一起翻书找找吧。",
    "repeatQuestion": "请再说一遍你的问题。",
    "needsPositionConfirmation": "请先用按键确认你读到的段落。",
}


def combine(destination, sources):
    with wave.open(str(destination), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for source in sources:
            if source is None:
                output.writeframesraw(bytes(32000))
            else:
                with wave.open(str(source), "rb") as clip:
                    if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, 16000):
                        raise ValueError(f"Unexpected voice format: {source.name}")
                    output.writeframesraw(clip.readframes(clip.getnframes()))


def duration_ms(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() * 1000 / audio.getframerate()


def generate(output, voice, fixture_voice, locator):
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if sys.platform != "win32" or powershell is None:
        raise ValueError("Generate this demo on Windows with a zh-CN speech synthesis voice")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    authoring, fixtures = output / "authoring", output / "fixtures"
    authoring.mkdir()
    fixtures.mkdir()
    book = read_json(Path(__file__).parent / "examples" / "bear-friends.json")
    book["version"] = "demo-1"
    book["rights"] = "童阅智伴原创文本。音频由本机 Windows 中文语音合成，仅用于开发联调。"
    jobs = []

    def speech(folder, filename, text, *, fixture=False, rate=0):
        jobs.append({"output": str(folder / filename), "text": text, "fixture": fixture, "rate": rate})

    for segment in book["segments"]:
        speech(authoring, segment["audio"], segment["text"])
    book["voiceQuestions"] = []
    for index, faq in enumerate(book["faq"], 1):
        speech(authoring, faq["audio"], faq["answer"])
        for alias_index, question in enumerate(faq["questions"], 1):
            audio = f"q{index}-{alias_index}.wav"
            speech(authoring, audio, question)
            book["voiceQuestions"].append({"question": question, "audio": audio})
        speech(fixtures, f"question-{index}.wav", faq["questions"][0], fixture=True, rate=1)
    book["voicePrompts"] = {}
    for status, text in PROMPTS.items():
        audio = f"prompt-{status}.wav"
        speech(authoring, audio, text)
        book["voicePrompts"][status] = audio
    speech(fixtures, "question-unknown.wav", "小熊最喜欢什么颜色？", fixture=True, rate=1)
    speech(fixtures, "question-free.wav", "小熊出门的时候带了什么东西？", fixture=True, rate=1)
    speech(fixtures, "read-s1.wav", book["segments"][0]["text"], fixture=True, rate=1)
    speech(fixtures, "read-s2.wav", book["segments"][1]["text"], fixture=True, rate=-1)
    fixture_rates = (-2, -1, 0, 1, 2)
    for index, segment in enumerate(book["segments"]):
        speech(fixtures, f"position-{index + 1:02}-clip.wav", segment["text"], fixture=True,
               rate=fixture_rates[index % len(fixture_rates)])

    command = base64.b64encode(SYNTHESIZE.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                               input=json.dumps({"voice": voice, "fixtureVoice": fixture_voice, "jobs": jobs}),
                               capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    if completed.returncode:
        raise ValueError(f"Windows speech synthesis failed: {completed.stderr.strip()}")
    provenance = json.loads(completed.stdout)
    provenance.update({"synthetic": True, "bookId": book["bookId"], "version": book["version"],
                       "intendedUse": "Local software integration; not child speech or board performance evidence"})
    write_json(output / "DEMO-INFO.json", provenance)
    source = authoring / "bear-friends.json"
    write_json(source, book)
    pack = compile_pack(source, output / "packs", locator)
    combine(fixtures / "read-repeat.wav", [fixtures / "read-s1.wav", None, fixtures / "read-s1.wav", None,
                                         fixtures / "read-s2.wav", None])
    combine(fixtures / "silence.wav", [None, None])
    position_cases = []
    for index, segment in enumerate(book["segments"]):
        recording = f"position-{index + 1:02}.wav"
        clip = fixtures / f"position-{index + 1:02}-clip.wav"
        combine(fixtures / recording, [clip, None])
        position_cases.append({"recording": recording, "expected": [["matched", segment["segmentId"]]],
                               "positionChecks": [[int(duration_ms(fixtures / recording) - 200),
                                                   segment["segmentId"]]]})
    write_json(fixtures / "cases-g2.json", position_cases)
    first_ms = duration_ms(fixtures / "read-s1.wav")
    second_ms = duration_ms(fixtures / "read-s2.wav")
    write_json(fixtures / "cases.json", [
        {"recording": "read-repeat.wav", "expected": [["matched", "s1"], ["repeated", "s1"], ["matched", "s2"]],
         "positionChecks": [[int(first_ms + 900), "s1"], [int(2 * first_ms + 1900), "s1"],
                            [int(2 * first_ms + second_ms + 2900), "s2"]]},
        {"recording": "silence.wav", "expected": []},
    ])
    archive = output / "bear-friends-demo-pack.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(pack, arcname="packs/bear-friends")
        tar.add(output / "DEMO-INFO.json", arcname="DEMO-INFO.json")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="A new directory for the generated pack and fixtures")
    parser.add_argument("--voice", help="Installed zh-CN reference voice")
    parser.add_argument("--fixture-voice", help="Optional different installed zh-CN voice for test recordings")
    parser.add_argument("--locator", type=Path, help="Host locate_wav v2 or newer for compiling acoustic banks")
    args = parser.parse_args()
    try:
        print(generate(args.output, args.voice, args.fixture_voice, args.locator))
    except (OSError, ValueError, KeyError, wave.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f"Demo StoryPack: {error}\n")


if __name__ == "__main__":
    main()
