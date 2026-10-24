"""Gemini Live oturumu: ses gonder/al, araclar, kesintisiz yeniden baglanma."""

import asyncio
import logging

from google import genai
from google.genai import types

from .audio import MIC_RATE, Mic, Speaker
from .camera import Camera
from .tools import DECLARATIONS, Tools
from .vad import SpeechGate

log = logging.getLogger(__name__)

AUDIO_MIME = f"audio/pcm;rate={MIC_RATE}"


class LiveBrain:
    def __init__(self, cfg: dict, api_key: str, mic: Mic, speaker: Speaker,
                 camera: Camera | None, tools: Tools):
        self._cfg = cfg
        self._client = genai.Client(api_key=api_key)
        self._mic = mic
        self._speaker = speaker
        self._camera = camera
        self._tools = tools
        v = cfg["vad"]
        self._gate = SpeechGate(v["aggressiveness"], v["preroll_ms"], v["hangover_ms"])
        self._frame_on_speech = cfg["camera"]["enabled"] and cfg["camera"]["frame_on_speech"]
        self._handle: str | None = None  # oturum devam belirteci (2 saat gecerli)
        self._heard: list[str] = []
        self._said: list[str] = []

    def _config(self) -> types.LiveConnectConfig:
        g = self._cfg["gemini"]
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=g["system_instruction"].strip(),
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=g["voice"]))),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    prefix_padding_ms=300, silence_duration_ms=700)),
            # Sikistirma: oturum suresi sinirsiz; resumption: baglanti yenilenince baglam korunur
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()),
            session_resumption=types.SessionResumptionConfig(handle=self._handle),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
            tools=[types.Tool(function_declarations=DECLARATIONS)],
        )

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                await self._session()
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("oturum hatasi: %s; %d sn sonra yeniden baglaniyor", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _session(self) -> None:
        model = self._cfg["gemini"]["model"]
        async with self._client.aio.live.connect(model=model, config=self._config()) as s:
            log.info("Gemini Live baglandi (%s, %s)", model,
                     "devam" if self._handle else "yeni oturum")
            renew = asyncio.Event()
            tasks = [asyncio.create_task(self._send(s)), asyncio.create_task(self._recv(s, renew)),
                     asyncio.create_task(renew.wait())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t.exception():
                        raise t.exception()
            finally:
                for t in tasks:
                    t.cancel()
        log.info("baglanti yenileniyor")

    async def _send(self, s) -> None:
        self._gate.reset()
        while True:
            frame = await self._mic.frames.get()
            if self._speaker.busy:
                # Yari cift yonlu: robot konusurken kendi sesini dinleme
                if self._gate.active:
                    await s.send_realtime_input(audio_stream_end=True)
                self._gate.reset()
                continue

            frames, event = self._gate.feed(frame)
            if event == "start":
                log.info("konusma algilandi")
                jpeg = self._camera.frame() if self._frame_on_speech and self._camera else None
                if jpeg:
                    await s.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
            for f in frames:
                await s.send_realtime_input(audio=types.Blob(data=f, mime_type=AUDIO_MIME))
            if event == "end":
                await s.send_realtime_input(audio_stream_end=True)

    async def _recv(self, s, renew: asyncio.Event) -> None:
        while True:
            async for msg in s.receive():
                await self._on_message(s, msg, renew)

    async def _on_message(self, s, msg: types.LiveServerMessage, renew: asyncio.Event) -> None:
        upd = msg.session_resumption_update
        if upd and upd.resumable and upd.new_handle:
            self._handle = upd.new_handle

        if msg.go_away:
            log.info("sunucu baglantiyi kapatacak (kalan %s), yenileniyor", msg.go_away.time_left)
            renew.set()

        if msg.tool_call:
            replies = [self._tools.handle(fc) for fc in msg.tool_call.function_calls or []]
            await s.send_tool_response(function_responses=replies)

        sc = msg.server_content
        if not sc:
            return
        if sc.interrupted:
            self._speaker.clear()
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    self._speaker.play(part.inline_data.data)
        if sc.input_transcription and sc.input_transcription.text:
            self._heard.append(sc.input_transcription.text)
        if sc.output_transcription and sc.output_transcription.text:
            self._said.append(sc.output_transcription.text)
        if sc.turn_complete:
            if self._heard:
                log.info("kullanici: %s", "".join(self._heard).strip())
            if self._said:
                log.info("robot: %s", "".join(self._said).strip())
            self._heard.clear()
            self._said.clear()
