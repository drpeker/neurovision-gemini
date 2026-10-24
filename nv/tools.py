"""Modelin cagirabilecegi araclar. Yeni arac: DECLARATIONS'a ekle, handle()'a dal ekle."""

import logging

from google.genai import types

from .camera import Camera

log = logging.getLogger(__name__)

DECLARATIONS = [
    types.FunctionDeclaration(
        name="look",
        description="Robotun kamerasindan su anki goruntuyu al. Kullanici etrafta ne oldugunu, "
                    "neyi gordugunu sordugunda ya da bir seye bakman gerektiginde kullan.",
    ),
]


class Tools:
    def __init__(self, camera: Camera | None):
        self._camera = camera

    def handle(self, call: types.FunctionCall) -> types.FunctionResponse:
        log.info("arac cagrisi: %s %s", call.name, call.args or {})
        if call.name == "look":
            return self._look(call)
        return types.FunctionResponse(id=call.id, name=call.name,
                                      response={"error": f"bilinmeyen arac: {call.name}"})

    def _look(self, call: types.FunctionCall) -> types.FunctionResponse:
        jpeg = self._camera.frame() if self._camera else None
        if not jpeg:
            return types.FunctionResponse(id=call.id, name=call.name,
                                          response={"error": "kamera goruntusu yok"})
        return types.FunctionResponse(
            id=call.id, name=call.name,
            response={"result": "Kameradan alinan guncel goruntu ekte."},
            parts=[types.FunctionResponsePart(
                inline_data=types.FunctionResponseBlob(mime_type="image/jpeg", data=jpeg))],
        )
