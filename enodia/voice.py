"""Text-to-speech for Enodia.

Derived from ligeia 0.1 (2019, same author) and rewritten to call the speech
engines with argument lists instead of shell strings: the text Enodia speaks
includes the SSIDs of nearby networks, which anyone around can choose.

Two engines are supported. `ESpeak` (espeak-ng, or the classic espeak binary)
speaks through its own audio output and is the default. `PicoTTS` (SVOX Pico,
through `pico2wave` on Debian and Ubuntu or `pico-tts` from AUR on Arch) can only
produce audio files, so speaking means writing a temporary WAV and playing it
with the first WAV player found (`paplay`, `pw-play` or `aplay`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import wave
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

PLAYERS: tuple[str, ...] = ("paplay", "pw-play", "aplay")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Speaker(Protocol):
    """What the monitor needs from a voice: `say`, and nothing else.

    Both `VoiceController` (speaks now) and `BackgroundVoice` (queues) are one.
    """

    def say(
        self,
        text: str,
        *,
        lang: str = ...,
        silent: bool = ...,
        optional: bool = ...,
        status: bool = ...,
    ) -> None: ...


class VoiceError(RuntimeError):
    """A speech engine or audio player failed."""


class VoiceUnavailable(VoiceError):
    """A speech engine or audio player is not installed."""


def split_into_sentences(text: str) -> list[str]:
    """Split text where '.', '!' or '?' is followed by whitespace."""
    return [sentence for sentence in _SENTENCE_END.split(text.strip()) if sentence]


def say_print(text: str, silent: bool, silent_text: str = "Say") -> None:
    """Print the text about to be said and whether it is only printed."""
    print(f"{silent_text} (Silent: {silent}) > {text}")


def _which(*names: str) -> str | None:
    """Path of the first installed binary among `names`, or None."""
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run `argv` (never through a shell) and translate failures into VoiceError."""
    try:
        return subprocess.run(list(argv), check=True, **kwargs)
    except FileNotFoundError as exc:
        raise VoiceUnavailable(f"{argv[0]} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        message = f"{argv[0]} exited with status {exc.returncode}"
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        if stderr.strip():
            message = f"{message}: {stderr.strip()}"
        raise VoiceError(message) from exc


def find_player() -> str | None:
    """Path of the first available WAV player, or None."""
    return _which(*PLAYERS)


def play_wav(path: str | Path) -> None:
    """Play a WAV file with the first available player."""
    player = find_player()
    if player is None:
        raise VoiceUnavailable(f"no audio player found (tried: {', '.join(PLAYERS)})")
    _run([player, str(path)])


def _terminated(text: str) -> str:
    """The text with a final newline, which espeak-ng needs to say a trailing number.

    Read from stdin, espeak-ng holds a number back until it knows the digits
    are over, and at end of file without a newline it drops them: "mark 3"
    comes out as "mark", "signal quality is 100" as "signal quality is". Words
    survive, digits do not. A newline closes the number.
    """
    return text if text.endswith("\n") else text + "\n"


class ESpeak:
    """eSpeak NG, falling back to the classic `espeak` binary.

    Text goes in through stdin, so nothing in it can be read as an option, and
    always with a final newline, without which a trailing number is lost.
    """

    binaries = ("espeak-ng", "espeak")

    def __init__(
        self,
        pitch: int = 30,
        amplitude: int = 200,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.pitch = pitch
        self.amplitude = amplitude
        self.extra_args = list(extra_args)

    @staticmethod
    def voice_for(lang: str) -> str:
        """eSpeak names voices by language code: 'es-ES' -> 'es'."""
        return lang.split("-")[0].lower()

    def _argv(self, lang: str) -> list[str]:
        binary = _which(*self.binaries)
        if binary is None:
            raise VoiceUnavailable("espeak-ng is not installed")
        return [
            binary,
            "-v",
            self.voice_for(lang),
            "-p",
            str(self.pitch),
            "-a",
            str(self.amplitude),
            *self.extra_args,
        ]

    def say(self, text: str, lang: str = "en-US") -> None:
        """Speak `text` through the engine's audio output."""
        _run([*self._argv(lang), "--stdin"], input=_terminated(text), text=True)

    def generate_file(
        self,
        destiny_path: str | Path,
        text: str,
        lang: str = "en-US",
    ) -> subprocess.CompletedProcess:
        """Write `text` as a WAV file at `destiny_path`."""
        argv = [*self._argv(lang), "-w", str(destiny_path), "--stdin"]
        return _run(argv, input=_terminated(text), text=True)


class PicoTTS:
    """SVOX Pico through either of its command line front ends.

    `pico2wave` (Debian and Ubuntu, package libttspico-utils) writes a WAV file
    itself. `pico-tts` (Arch, AUR package pico-tts) reads the text on stdin and
    writes raw 16-bit mono PCM at 16 kHz on stdout, which is wrapped into a WAV
    here. `say` plays the file right after writing it.
    """

    binaries = ("pico2wave", "pico-tts")
    languages = ("en-US", "en-GB", "de-DE", "es-ES", "fr-FR", "it-IT")
    sample_rate = 16000

    def __init__(self, extra_args: Sequence[str] = ()) -> None:
        self.extra_args = list(extra_args)

    @classmethod
    def lang_for(cls, lang: str) -> str:
        """Closest of the six Pico voices: 'es' -> 'es-ES'; unknown languages -> 'en-US'."""
        if lang in cls.languages:
            return lang
        prefix = lang.split("-")[0].lower()
        for candidate in cls.languages:
            if candidate.startswith(prefix):
                return candidate
        return "en-US"

    def _binary(self) -> str:
        binary = _which(*self.binaries)
        if binary is None:
            raise VoiceUnavailable("neither pico2wave nor pico-tts is installed")
        return binary

    def generate_file(
        self,
        destiny_path: str | Path,
        text: str,
        lang: str = "en-US",
    ) -> subprocess.CompletedProcess:
        """Write `text` as a WAV file at `destiny_path`."""
        binary = self._binary()
        lang = self.lang_for(lang)
        if Path(binary).name == "pico2wave":
            # pico2wave takes the text as a positional argument: strip leading
            # dashes so it can never be parsed as an option.
            text = text.lstrip(" -") or " " + text
            return _run(
                [
                    binary,
                    "--lang",
                    lang,
                    *self.extra_args,
                    "--wave",
                    str(destiny_path),
                    text,
                ]
            )
        # pico-tts: text on stdin, raw PCM on stdout.
        result = _run(
            [binary, "-l", lang, *self.extra_args],
            input=text.encode("utf-8"),
            capture_output=True,
        )
        with wave.open(str(destiny_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(result.stdout)
        return result

    def say(self, text: str, lang: str = "en-US") -> None:
        """Write `text` to a temporary WAV file and play it."""
        with NamedTemporaryFile(suffix=".wav") as wav:
            self.generate_file(wav.name, text, lang)
            play_wav(wav.name)


def default_voice() -> ESpeak | PicoTTS | None:
    """eSpeak NG when installed; else SVOX Pico, which also needs a WAV player;
    else None (print only)."""
    if _which(*ESpeak.binaries):
        return ESpeak()
    if _which(*PicoTTS.binaries) and find_player():
        return PicoTTS()
    return None


class VoiceController:
    """Speaks through a loaded voice, or only prints when there is none or `silent` is set."""

    def __init__(self, voice: Any = None, prefix: str = "Enodia > ") -> None:
        self.selected_voice: Any = None
        self.prefix = prefix
        self.disabled_reason: str | None = None
        if voice is not None:
            self.load_voice(voice)

    def load_voice(self, voice_object: Any) -> None:
        """Load a voice: an engine class (instantiated here) or an instance."""
        self.selected_voice = voice_object() if isinstance(voice_object, type) else voice_object
        self.disabled_reason = None

    @property
    def available(self) -> bool:
        """True if a voice is loaded and has not failed for lack of an engine or player."""
        return self.selected_voice is not None and self.disabled_reason is None

    def say(
        self,
        text: str,
        lang: str = "en-US",
        silent: bool = False,
        say_silent: bool = False,
        print_statement: bool = False,
        split_sentences: bool = True,
        preprocess_text: bool = True,
        silent_text: str = "Say",
        optional: bool = False,
        status: bool = False,
    ) -> None:
        """Say `text` with the loaded voice.

        :param silent: only print, do not speak.
        :param say_silent: do not print the "Say (...) > text" line.
        :param print_statement: print each sentence as it is spoken.
        :param split_sentences: speak sentence by sentence.
        :param preprocess_text: lowercase the text before speaking (Pico reads
            capitals as spelled-out letters).
        :param optional: this utterance may be dropped when speech falls behind.
        :param status: this utterance reports a state, not an event, and is only
            worth saying while it is current.
            Both are ignored here, where speaking is synchronous and nothing is
            queued; `BackgroundVoice` is what honours them.
        """
        if not say_silent:
            say_print(text, silent, silent_text)
        if silent or not text or not self.available:
            return
        if preprocess_text:
            text = text.lower()
        statements = split_into_sentences(text) if split_sentences else [text]
        for statement in statements:
            if print_statement:
                print(f"{self.prefix}{statement}")
            try:
                self.selected_voice.say(text=statement, lang=lang)
            except VoiceUnavailable as exc:
                self.disabled_reason = str(exc)
                print(f"{self.prefix}voice disabled: {exc}")
                return
            except VoiceError as exc:
                print(f"{self.prefix}could not say {statement!r}: {exc}")

    def generate_file(
        self,
        destiny_path: str | Path,
        text: str,
        lang: str = "en-US",
    ) -> subprocess.CompletedProcess:
        """Write `text` as a WAV file with the loaded voice."""
        if self.selected_voice is None:
            raise VoiceUnavailable("no voice loaded")
        return self.selected_voice.generate_file(destiny_path, text, lang)


class BackgroundVoice:
    """Speaks from its own thread, so the scan loop never waits for audio.

    The scan loop is what gives Enodia its resolution along the route, and
    speech is slow: espeak-ng takes nearly four seconds just to say the time.
    Spoken synchronously, a cycle that announces every new network stretches
    from the five seconds asked for to forty or more, and it does so exactly
    where the networks are densest, which is where the route matters most.
    Here `say` only queues, and the scanning goes on.

    The queue is bounded, so falling behind costs speech and never time. An
    utterance marked `optional` -- a network name -- is dropped when the queue
    is full; one that is not makes room by dropping the oldest optional, or
    failing that the oldest utterance of all. The newest is always kept,
    because a stale announcement of the time is worth less than a fresh one.

    An utterance marked `status` -- "Scanning", the signal, the time -- is only
    queued while the voice is idle. A status that arrives while speech is
    behind would be heard late, and the time is the one thing that must not
    be: the notebook is written from it. The next cycle brings a current one.
    Events are never gated this way.
    """

    def __init__(self, controller: VoiceController, maxsize: int = 8) -> None:
        self.controller = controller
        self.maxsize = maxsize
        self.dropped = 0
        self.skipped = 0
        self._speaking = False
        self._queue: deque[tuple[str, str, bool]] = deque()
        self._cv = threading.Condition()
        self._closed = False
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self.controller.available

    @property
    def pending(self) -> int:
        """Utterances waiting to be spoken."""
        with self._cv:
            return len(self._queue)

    @property
    def idle(self) -> bool:
        """Nothing queued and nothing being said: a status said now is heard now."""
        with self._cv:
            return not self._queue and not self._speaking

    def say(
        self,
        text: str,
        lang: str = "en-US",
        silent: bool = False,
        optional: bool = False,
        status: bool = False,
        silent_text: str = "Say",
        **kwargs: Any,
    ) -> None:
        """Queue `text` to be spoken. Prints immediately, returns at once."""
        say_print(text, silent, silent_text)
        if silent or not text or not self.controller.available:
            return
        with self._cv:
            if self._closed:
                return
            if status and (self._queue or self._speaking):
                self.skipped += 1
                return
            if len(self._queue) >= self.maxsize:
                if optional:
                    self.dropped += 1
                    return
                self._make_room()
            self._queue.append((text, lang, optional))
            self._start()
            self._cv.notify()

    def _start(self) -> None:
        """Start the speaking thread on first use. Called holding the lock."""
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._speak_queued, name="enodia-voice", daemon=True
            )
            self._thread.start()

    def _make_room(self) -> None:
        """Drop the oldest optional utterance, or the oldest of all. Called holding the lock."""
        for index, (_, _, optional) in enumerate(self._queue):
            if optional:
                del self._queue[index]
                break
        else:
            self._queue.popleft()
        self.dropped += 1

    def _speak_queued(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if self._closed and not self._queue:
                    return
                text, lang, _ = self._queue.popleft()
                self._speaking = True
                self._cv.notify_all()
            try:
                self.controller.say(text, lang=lang, say_silent=True)
            finally:
                with self._cv:
                    self._speaking = False
                    self._cv.notify_all()

    def close(self, timeout: float = 5.0) -> None:
        """Let what is queued be spoken, then stop the thread."""
        if self._thread is None:
            return
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._queue and time.monotonic() < deadline:
                self._cv.wait(timeout=max(0.0, deadline - time.monotonic()))
            self._closed = True
            self._queue.clear()
            self._cv.notify_all()
        self._thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def generate_file(self, destiny_path: str | Path, text: str, lang: str = "en-US"):
        """Write `text` as a WAV file. Synchronous: nothing is queued."""
        return self.controller.generate_file(destiny_path, text, lang)
