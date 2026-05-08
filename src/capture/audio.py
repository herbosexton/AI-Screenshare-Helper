import threading
import collections
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


class AudioCapture:
    """
    Captures audio from system loopback (WASAPI) and/or microphone.
    Maintains a rolling buffer and calls back with audio chunks for transcription.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    CHUNK_DURATION_SEC = 5
    DTYPE = "float32"

    def __init__(self, config: dict):
        self.config = config
        self.buffer_seconds = config.get("buffer_seconds", 30)
        self.use_system_audio = config.get("system_audio", True)
        self.use_microphone = config.get("microphone", True)

        buffer_size = self.SAMPLE_RATE * self.buffer_seconds
        self._buffer = collections.deque(maxlen=buffer_size)
        self._lock = threading.Lock()

        self._system_stream: Optional[sd.InputStream] = None
        self._mic_stream: Optional[sd.InputStream] = None
        self._running = False
        self._callback: Optional[Callable] = None

        self._chunk_accumulator = np.array([], dtype=self.DTYPE)
        self._chunk_samples = self.SAMPLE_RATE * self.CHUNK_DURATION_SEC

    def start(self, callback: Optional[Callable] = None):
        """Start audio capture. Callback receives numpy arrays of audio data."""
        if self._running:
            return

        self._callback = callback
        self._running = True

        if self.use_system_audio:
            self._start_system_audio()

        if self.use_microphone:
            self._start_microphone()

    def stop(self):
        """Stop all audio capture streams."""
        self._running = False

        if self._system_stream:
            self._system_stream.stop()
            self._system_stream.close()
            self._system_stream = None

        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

    def get_buffer(self) -> np.ndarray:
        """Return the current rolling buffer as a numpy array."""
        with self._lock:
            return np.array(list(self._buffer), dtype=self.DTYPE)

    def get_recent(self, seconds: int = 10) -> np.ndarray:
        """Return the last N seconds of audio from the buffer."""
        samples = min(self.SAMPLE_RATE * seconds, len(self._buffer))
        with self._lock:
            recent = list(self._buffer)[-samples:]
            return np.array(recent, dtype=self.DTYPE)

    @property
    def is_running(self) -> bool:
        return self._running

    def _start_system_audio(self):
        """Start capturing system audio via WASAPI loopback or Stereo Mix."""
        loopback_device = self._find_loopback_device()
        if loopback_device is None:
            print("[AudioCapture] No loopback device found - trying default input for system audio")
            print("[AudioCapture] TIP: Enable 'Stereo Mix' in Windows Sound settings > Recording devices")
            return

        try:
            self._system_stream = sd.InputStream(
                device=loopback_device,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                blocksize=int(self.SAMPLE_RATE * 0.5),
                callback=self._audio_callback,
            )
            self._system_stream.start()
            print(f"[AudioCapture] System audio capturing from device: {loopback_device}")
        except Exception as e:
            print(f"[AudioCapture] Failed to start system audio: {e}")

    def _start_microphone(self):
        """Start capturing microphone input."""
        try:
            self._mic_stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                blocksize=int(self.SAMPLE_RATE * 0.5),
                callback=self._audio_callback,
            )
            self._mic_stream.start()
            print("[AudioCapture] Microphone capturing started")
        except Exception as e:
            print(f"[AudioCapture] Failed to start microphone: {e}")

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback from sounddevice stream - accumulates audio data."""
        if not self._running:
            return

        audio_flat = indata[:, 0] if indata.ndim > 1 else indata.flatten()

        with self._lock:
            self._buffer.extend(audio_flat.tolist())

        self._chunk_accumulator = np.concatenate([self._chunk_accumulator, audio_flat])

        if len(self._chunk_accumulator) >= self._chunk_samples:
            chunk = self._chunk_accumulator[:self._chunk_samples]
            self._chunk_accumulator = self._chunk_accumulator[self._chunk_samples:]

            if self._callback:
                self._callback(chunk)

    @staticmethod
    def _find_loopback_device() -> Optional[int]:
        """Find a WASAPI loopback device or Stereo Mix for system audio capture."""
        try:
            devices = sd.query_devices()
            search_terms = ["loopback", "stereo mix", "what u hear", "wave out", "mix"]

            for i, dev in enumerate(devices):
                name = dev["name"].lower()
                if dev["max_input_channels"] > 0:
                    for term in search_terms:
                        if term in name:
                            print(f"[AudioCapture] Found loopback device: {dev['name']} (index {i})")
                            return i

            hostapis = sd.query_hostapis()
            for api in hostapis:
                if "wasapi" in api["name"].lower():
                    for dev_idx in api["devices"]:
                        dev = devices[dev_idx]
                        if dev["max_input_channels"] > 0:
                            name = dev["name"].lower()
                            for term in search_terms:
                                if term in name:
                                    return dev_idx
        except Exception as e:
            print(f"[AudioCapture] Error finding loopback: {e}")

        return None
