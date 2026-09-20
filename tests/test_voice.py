"""Tests for the speech engines. No audio is produced: subprocess calls are captured."""

import subprocess
import threading
import wave

import pytest

from enodia import voice
from enodia.voice import (
    BackgroundVoice,
    ESpeak,
    PicoTTS,
    VoiceController,
    VoiceError,
    VoiceUnavailable,
    default_voice,
    split_into_sentences,
)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)


@pytest.fixture
def run(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(voice.subprocess, "run", recorder)
    return recorder


@pytest.fixture
def engines(monkeypatch):
    """Pretend every engine and player is installed."""
    monkeypatch.setattr(voice.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_split_into_sentences():
    assert split_into_sentences("Now connected to home. Signal is fine! Really?") == [
        "Now connected to home.",
        "Signal is fine!",
        "Really?",
    ]
    assert split_into_sentences("  ") == []
    assert split_into_sentences("no punctuation") == ["no punctuation"]


def test_espeak_say_uses_stdin_and_arg_list(run, engines):
    ESpeak().say('hello"; rm -rf ~; "', lang="es-ES")
    argv, kwargs = run.calls[0]
    assert argv[0] == "/usr/bin/espeak-ng"
    assert argv[1:] == ["-v", "es", "-p", "30", "-a", "200", "--stdin"]
    assert kwargs["input"] == 'hello"; rm -rf ~; "\n'
    assert "shell" not in kwargs


def test_espeak_generate_file(run, engines, tmp_path):
    ESpeak(pitch=50, amplitude=100, extra_args=["-s", "150"]).generate_file(
        tmp_path / "x.wav", "hi"
    )
    argv, kwargs = run.calls[0]
    assert argv[1:] == [
        "-v",
        "en",
        "-p",
        "50",
        "-a",
        "100",
        "-s",
        "150",
        "-w",
        str(tmp_path / "x.wav"),
        "--stdin",
    ]
    assert kwargs["input"] == "hi\n"


def test_espeak_falls_back_to_classic_binary(run, monkeypatch):
    monkeypatch.setattr(
        voice.shutil, "which", lambda name: "/usr/bin/espeak" if name == "espeak" else None
    )
    ESpeak().say("hi")
    assert run.calls[0][0][0] == "/usr/bin/espeak"


def test_missing_engine_raises(monkeypatch, run):
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    with pytest.raises(VoiceUnavailable):
        ESpeak().say("hi")
    with pytest.raises(VoiceUnavailable):
        PicoTTS().generate_file("x.wav", "hi")
    assert run.calls == []


def test_run_translates_missing_binary(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(voice.subprocess, "run", missing)
    with pytest.raises(VoiceUnavailable):
        voice._run(["nope"])


def test_pico_generate_file_arg_list(run, engines, tmp_path):
    PicoTTS().generate_file(tmp_path / "x.wav", '--wave /etc/passwd "$(reboot)"', lang="es-ES")
    argv, kwargs = run.calls[0]
    assert argv == [
        "/usr/bin/pico2wave",
        "--lang",
        "es-ES",
        "--wave",
        str(tmp_path / "x.wav"),
        'wave /etc/passwd "$(reboot)"',
    ]
    assert "shell" not in kwargs


def test_pico_text_made_only_of_dashes(run, engines):
    PicoTTS().generate_file("x.wav", "---")
    assert run.calls[0][0][-1] == " ---"


def test_pico_language_mapping():
    assert PicoTTS.lang_for("es-ES") == "es-ES"
    assert PicoTTS.lang_for("es") == "es-ES"
    assert PicoTTS.lang_for("EN") == "en-US"
    assert PicoTTS.lang_for("pt-BR") == "en-US"


def test_pico_say_generates_then_plays(run, engines):
    PicoTTS(extra_args=["--verbose"]).say("hello")
    assert [argv[0] for argv, _ in run.calls] == ["/usr/bin/pico2wave", "/usr/bin/paplay"]
    pico_argv = run.calls[0][0]
    assert "--verbose" in pico_argv
    wav = pico_argv[pico_argv.index("--wave") + 1]
    assert wav.endswith(".wav")
    assert run.calls[1][0] == ["/usr/bin/paplay", wav]


def test_play_wav_without_player(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    with pytest.raises(VoiceUnavailable):
        voice.play_wav("x.wav")


def test_engine_failure_raises_voice_error(monkeypatch, engines):
    def failing(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(voice.subprocess, "run", failing)
    with pytest.raises(VoiceError) as info:
        ESpeak().say("hi")
    assert not isinstance(info.value, VoiceUnavailable)


class FakeVoice:
    def __init__(self):
        self.spoken = []

    def say(self, text, lang="en-US"):
        self.spoken.append((text, lang))

    def generate_file(self, destiny_path, text, lang="en-US"):
        return ("file", str(destiny_path), text, lang)


def test_controller_silent_only_prints(capsys):
    fake = FakeVoice()
    VoiceController(fake).say("Now connected to", silent=True)
    assert fake.spoken == []
    assert "Say (Silent: True) > Now connected to" in capsys.readouterr().out


def test_controller_speaks_each_sentence_lowercased(capsys):
    fake = FakeVoice()
    VoiceController(fake).say("Hello World. New network found!", lang="es-ES", print_statement=True)
    assert fake.spoken == [("hello world.", "es-ES"), ("new network found!", "es-ES")]
    out = capsys.readouterr().out
    assert "Say (Silent: False) > Hello World. New network found!" in out
    assert "Enodia > hello world." in out


def test_controller_options(capsys):
    fake = FakeVoice()
    VoiceController(fake).say(
        "Keep. Case.", preprocess_text=False, split_sentences=False, say_silent=True
    )
    assert fake.spoken == [("Keep. Case.", "en-US")]
    assert capsys.readouterr().out == ""


def test_controller_empty_text_is_not_spoken():
    fake = FakeVoice()
    VoiceController(fake).say("", say_silent=True)
    assert fake.spoken == []


def test_controller_without_voice_is_print_only(capsys):
    controller = VoiceController()
    controller.say("hello")
    assert not controller.available
    assert "hello" in capsys.readouterr().out


def test_controller_load_voice_class_or_instance():
    controller = VoiceController()
    controller.load_voice(FakeVoice)
    assert isinstance(controller.selected_voice, FakeVoice)
    fake = FakeVoice()
    controller.load_voice(fake)
    assert controller.selected_voice is fake
    assert controller.available
    assert controller.generate_file("out.wav", "hi", lang="es-ES") == (
        "file",
        "out.wav",
        "hi",
        "es-ES",
    )


def test_controller_generate_file_without_voice():
    with pytest.raises(VoiceUnavailable):
        VoiceController().generate_file("out.wav", "hi")


def test_controller_disables_voice_when_engine_is_missing(capsys):
    class Missing:
        def say(self, text, lang="en-US"):
            raise VoiceUnavailable("pico2wave is not installed")

    controller = VoiceController(Missing())
    controller.say("hello")
    controller.say("again")
    assert not controller.available
    assert controller.disabled_reason == "pico2wave is not installed"
    assert capsys.readouterr().out.count("voice disabled") == 1
    controller.load_voice(FakeVoice())
    assert controller.available


def test_controller_keeps_going_after_engine_error(capsys):
    class Flaky:
        def __init__(self):
            self.calls = 0

        def say(self, text, lang="en-US"):
            self.calls += 1
            raise VoiceError("pico2wave exited with status 1")

    flaky = Flaky()
    controller = VoiceController(flaky)
    controller.say("one. two.")
    assert flaky.calls == 2
    assert controller.available
    assert capsys.readouterr().out.count("could not say") == 2


def test_pico_prefers_pico2wave_when_both_exist(run, engines, tmp_path):
    PicoTTS().generate_file(tmp_path / "x.wav", "hi")
    assert run.calls[0][0][0] == "/usr/bin/pico2wave"


def test_pico_tts_binary_wraps_pcm_into_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(
        voice.shutil, "which", lambda name: "/usr/bin/pico-tts" if name == "pico-tts" else None
    )
    pcm = b"\x01\x02" * 800
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=pcm, stderr=b"")

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    out = tmp_path / "x.wav"
    PicoTTS(extra_args=["-x"]).generate_file(out, "Señal --no-es-una-opción", lang="es")
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/pico-tts", "-l", "es-ES", "-x"]
    assert kwargs["input"] == "Señal --no-es-una-opción".encode()
    assert kwargs["capture_output"] is True
    assert "shell" not in kwargs
    with wave.open(str(out)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (
            1,
            2,
            16000,
            800,
        )
        assert wav.readframes(800) == pcm


def test_pico_tts_say_plays_the_wav(monkeypatch):
    monkeypatch.setattr(
        voice.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("pico-tts", "pw-play") else None,
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=b"\x00\x00" * 16, stderr=b"")

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    PicoTTS().say("hola", lang="es-ES")
    assert calls[0] == ["/usr/bin/pico-tts", "-l", "es-ES"]
    assert calls[1][0] == "/usr/bin/pw-play"
    assert calls[1][1].endswith(".wav")


def test_engine_failure_includes_stderr(monkeypatch, engines):
    def failing(argv, **kwargs):
        raise subprocess.CalledProcessError(2, argv, stderr=b"pico error -40: cannot open file")

    monkeypatch.setattr(voice.subprocess, "run", failing)
    with pytest.raises(VoiceError, match="status 2: pico error -40"):
        PicoTTS().generate_file("x.wav", "hi")


def test_default_voice_prefers_espeak(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert isinstance(default_voice(), ESpeak)


def test_default_voice_falls_back_to_pico(monkeypatch):
    monkeypatch.setattr(
        voice.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("pico-tts", "paplay") else None,
    )
    assert isinstance(default_voice(), PicoTTS)


def test_default_voice_pico_needs_a_player(monkeypatch):
    monkeypatch.setattr(
        voice.shutil, "which", lambda name: "/usr/bin/pico2wave" if name == "pico2wave" else None
    )
    assert default_voice() is None


def test_default_voice_none(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda name: None)
    assert default_voice() is None


# --- BackgroundVoice: la voz no debe frenar el ciclo de escaneo -----------------


class BlockingController(VoiceController):
    """Controller that records what it speaks and holds each utterance until released."""

    def __init__(self):
        super().__init__()
        self.said = []
        self.gate = threading.Event()
        self.speaking = threading.Event()

    @property
    def available(self):
        return True

    def say(self, text, lang="en-US", **kwargs):
        self.speaking.set()
        self.gate.wait(timeout=5)
        self.said.append(text)


def queued(voice, controller, text, **kwargs):
    """Queue an utterance and wait until the speaking thread has picked the first one up."""
    voice.say(text, **kwargs)
    controller.speaking.wait(timeout=5)


def test_background_voice_speaks_in_order_and_returns_at_once():
    controller = BlockingController()
    voice = BackgroundVoice(controller)
    queued(voice, controller, "first")
    voice.say("second")
    assert voice.pending == 1  # "first" ya salio de la cola: say() no espera al audio
    controller.gate.set()
    voice.close()
    assert controller.said == ["first", "second"]


def test_background_voice_drops_optional_utterances_when_it_falls_behind():
    controller = BlockingController()
    voice = BackgroundVoice(controller, maxsize=3)
    queued(voice, controller, "first")
    for index in range(5):
        voice.say(f"network {index}", optional=True)
    assert (voice.pending, voice.dropped) == (3, 2)
    controller.gate.set()
    voice.close()
    assert controller.said == ["first", "network 0", "network 1", "network 2"]


def test_background_voice_makes_room_for_what_must_be_said():
    controller = BlockingController()
    voice = BackgroundVoice(controller, maxsize=2)
    queued(voice, controller, "first")
    voice.say("network a", optional=True)
    voice.say("network b", optional=True)
    voice.say("17 hours, 45 minutes, 0 seconds")  # la hora entra; el nombre mas viejo cae
    assert (voice.pending, voice.dropped) == (2, 1)
    controller.gate.set()
    voice.close()
    assert controller.said == ["first", "network b", "17 hours, 45 minutes, 0 seconds"]


def test_background_voice_keeps_the_newest_when_nothing_is_optional():
    controller = BlockingController()
    voice = BackgroundVoice(controller, maxsize=2)
    queued(voice, controller, "first")
    voice.say("a")
    voice.say("b")
    voice.say("c")
    controller.gate.set()
    voice.close()
    assert controller.said == ["first", "b", "c"]  # "a", la mas vieja, se descarto


def test_background_voice_silent_and_empty_are_never_queued(capsys):
    voice = BackgroundVoice(BlockingController())
    voice.say("hidden", silent=True)
    voice.say("")
    assert voice.pending == 0
    assert "Say (Silent: True) > hidden" in capsys.readouterr().out


def test_background_voice_without_an_engine_starts_no_thread():
    voice = BackgroundVoice(VoiceController())  # sin voz cargada
    voice.say("nobody hears this")
    assert voice.pending == 0 and not voice.available
    voice.close()  # no hay hilo que cerrar


def test_background_voice_ignores_speech_after_close():
    controller = BlockingController()
    voice = BackgroundVoice(controller)
    controller.gate.set()
    queued(voice, controller, "first")
    voice.close()
    voice.say("too late")
    assert voice.pending == 0
    assert controller.said == ["first"]


def test_background_voice_generate_file_is_synchronous(monkeypatch, tmp_path):
    controller = VoiceController(ESpeak())
    monkeypatch.setattr(voice, "_which", lambda *names: "/usr/bin/espeak-ng")
    calls = []
    monkeypatch.setattr(voice, "_run", lambda argv, **kw: calls.append(argv))
    BackgroundVoice(controller).generate_file(tmp_path / "out.wav", "hello")
    assert calls and "-w" in calls[0]


def test_status_is_skipped_while_the_voice_is_busy():
    # "Scanning", the signal and the time report a state: said late they are
    # wrong, and the next cycle brings a current one. Events still queue.
    controller = BlockingController()
    voice = BackgroundVoice(controller)
    queued(voice, controller, "first")  # being spoken, and held
    assert not voice.idle
    voice.say("17 hours, 45 minutes, 17 seconds", status=True)
    voice.say("New network found")
    voice.say("Scanning", status=True)
    assert (voice.pending, voice.skipped) == (1, 2)
    controller.gate.set()
    voice.close()
    assert controller.said == ["first", "New network found"]


def test_status_is_said_when_the_voice_is_idle():
    controller = BlockingController()
    controller.gate.set()
    voice = BackgroundVoice(controller)
    assert voice.idle
    voice.say("17 hours, 45 minutes, 17 seconds", status=True)
    voice.close()
    assert controller.said == ["17 hours, 45 minutes, 17 seconds"] and voice.skipped == 0
    assert voice.idle


def test_idle_is_false_while_speaking_even_with_an_empty_queue():
    controller = BlockingController()
    voice = BackgroundVoice(controller)
    queued(voice, controller, "first")
    assert voice.pending == 0 and not voice.idle  # la cola esta vacia, pero se esta hablando
    controller.gate.set()
    voice.close()


def test_espeak_gets_a_final_newline_so_a_trailing_number_is_said(run, engines):
    # Read from stdin without a final newline, espeak-ng drops a trailing number:
    # "mark 3" came out as "mark". Measured, not guessed.
    from enodia.voice import _terminated

    assert _terminated("mark 3") == "mark 3\n"
    assert _terminated("mark 3\n") == "mark 3\n"
    ESpeak().say("mark 3")
    assert run.calls[-1][1]["input"] == "mark 3\n"
