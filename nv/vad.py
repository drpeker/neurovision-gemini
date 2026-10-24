"""Yerel konusma kapisi: sessizlikte sunucuya ses gitmez."""

from collections import deque

import webrtcvad

from .audio import FRAME_MS, MIC_RATE


class SpeechGate:
    """20 ms kareleri alir; konusma suresince gonderilecek kareleri dondurur.

    feed() -> (gonderilecek_kareler, olay); olay "start", "end" ya da None.
    """

    def __init__(self, aggressiveness: int, preroll_ms: int, hangover_ms: int):
        self._vad = webrtcvad.Vad(aggressiveness)
        self._preroll = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self._recent = deque(maxlen=5)
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
            # Son 5 karenin 3'u konusma ise basla (tek tik/gurultu tetiklemesin)
            if sum(self._recent) >= 3:
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
