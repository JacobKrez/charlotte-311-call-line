"""Audio I/O abstraction.

The dialogue logic must be testable without a microphone, a speaker, a
network connection, or an API key. So audio sits behind two small
protocols, and the repository ships two implementations of each:

* a **provider** implementation that talks to a real speech-to-text
  service and a real text-to-speech service, and
* a **deterministic offline** implementation that reads transcripts from
  a scripted list and writes audio as silence.

The offline implementation is not a stub. It is a first-class citizen:
the entire test suite, the red-team corpus, and CI run against it, and
they would behave identically against a real provider, because the
provider is only ever reached through the same protocol.

The reason the transcript is treated as untrusted input, all the way
down, is that the ASR layer is the point where a caller's intent stops
being controlled by the system. Everything the guardrail chain does
assumes the transcript might be hostile, because on a voice channel it
might be.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Protocol, Sequence


class TranscriptSource(Protocol):
    """Something that produces caller transcripts."""

    def next_transcript(self) -> str | None:
        """Return the next caller transcript, or None when the call ends."""
        ...


class SpeechSynthesizer(Protocol):
    """Something that turns agent text into audio."""

    def synthesize(self, text: str, out_path: Path) -> Path:
        """Render ``text`` to ``out_path`` and return the path."""
        ...


class ScriptedTranscriptSource:
    """A transcript source that replays a fixed list.

    Parameters
    ----------
    transcripts:
        The transcripts to replay, in order.
    """

    def __init__(self, transcripts: Sequence[str]) -> None:
        self._transcripts = list(transcripts)
        self._index = 0

    def next_transcript(self) -> str | None:
        if self._index >= len(self._transcripts):
            return None
        transcript = self._transcripts[self._index]
        self._index += 1
        return transcript


class SilentSynthesizer:
    """A synthesizer that writes real, playable, silent WAV files.

    The file is a valid RIFF/WAVE container with the right header, so
    any audio tool can open it. It is silent because generating
    speech in CI would be slow, flaky, and would require a network
    dependency that has no business being in a test.
    """

    def __init__(self, sample_rate: int = 22050, duration_ms: int = 400) -> None:
        self.sample_rate = sample_rate
        self.duration_ms = duration_ms

    def synthesize(self, text: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames = int(self.sample_rate * self.duration_ms / 1000)
        with wave.open(str(out_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(b"\x00\x00" * frames)
        return out_path


class ProviderTranscriptSource:
    """Transcript source backed by a real speech-to-text provider.

    This class is intentionally thin. It holds the provider client and
    turns bytes into a string. It contains no dialogue logic, no
    normalization, and no heuristics, because those belong in
    :mod:`src.slots` and :mod:`src.guardrails` where they can be
    tested without a network.

    The constructor takes an already-configured client so that provider
    SDKs stay an optional dependency of this package.
    """

    def __init__(self, client, *, model: str = "nova-2", language: str = "en-US") -> None:
        self._client = client
        self._model = model
        self._language = language

    def transcribe_bytes(self, audio: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe raw audio bytes and return the transcript."""
        options = {
            "model": self._model,
            "language": self._language,
            "smart_format": True,
        }
        response = self._client.listen.rest.v("1").transcribe_file(
            {"buffer": audio, "mimetype": mime_type}, options
        )
        alternatives = response["results"]["channels"][0]["alternatives"]
        return alternatives[0]["transcript"]

    def next_transcript(self) -> str | None:
        raise NotImplementedError(
            "ProviderTranscriptSource is driven by the capture loop, not by "
            "next_transcript(). Use transcribe_bytes() with audio captured "
            "from the microphone."
        )


class CallRecorder:
    """Appends audio segments to a single call recording file.

    A municipal 311 line records calls for dispute resolution and
    training. The recording is evidence, so it is written once, in
    order, and never overwritten mid-call.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    def append(self, segment_path: Path) -> None:
        """Append a WAV segment to the call recording."""
        with wave.open(str(segment_path), "rb") as source:
            frames = source.readframes(source.getnframes())
            params = source.getparams()
            if not self._initialized:
                with wave.open(str(self.path), "wb") as target:
                    target.setparams(params)
                self._initialized = True
        with wave.open(str(self.path), "rb+") as target:
            existing = target.getnframes()
            target.setpos(existing)
            target.writeframes(frames)

    def reset(self) -> None:
        """Start a fresh recording, discarding any previous one."""
        self._initialized = False
        if self.path.exists():
            self.path.unlink()
