"""Yerel konusma kapisi: sessizlikte sunucuya ses gitmez."""

from collections import deque

import webrtcvad

from .audio import FRAME_MS, MIC_RATE


class SpeechGate:
    """20 ms kareleri alir; konusma suresince gonderilecek kareleri dondurur.

    feed() -> (gonderilecek_kareler, olay); olay "start", "end" ya da None.
    """

    def __init__(self, aggressiveness: int, preroll_ms: int, hangover_ms: int,
                 min_speech_ms: int = 140):
        self._vad = webrtcvad.Vad(aggressiveness)
        self._preroll = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        # Son 200 ms'nin en az min_speech_ms'si konusma olmali: tik, oksuruk, kapi sesi gecmesin
        self._recent = deque(maxlen=10)
        self._min_voiced = max(1, min_speech_ms // FRAME_MS)
        self._hangover = max(1, hangover_ms // FRAME_MS)
        self._silent = 0
        self.active = False

    def reset(self) -> None:
        self._preroll.clear()
        self._recent.clear()
        self._silent = 0
        self.active = False

    def feed(self, frame: bytes) -> tuple[list[bytes], str | None]:
        voiced = self._vad.is_speech(frame, MIC_RATE)
        self._recent.append(voiced)

        if not self.active:
            self._preroll.append(frame)
            if sum(self._recent) >= self._min_voiced:
                self.active = True
                self._silent = 0
                frames = list(self._preroll)
                self._preroll.clear()
                return frames, "start"
            return [], None

        self._silent = 0 if voiced else self._silent + 1
        if self._silent >= self._hangover:
            self.active = False
            self._recent.clear()
            return [frame], "end"
        return [frame], None
