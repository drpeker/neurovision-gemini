"""Modelin cagirabilecegi araclar. Yeni arac: DECLARATIONS'a ekle, handle()'a dal ekle."""

import logging
import time

from google.genai import types

from .camera import Camera
from .memory import CATEGORIES, Memory

log = logging.getLogger(__name__)


def _params(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


DECLARATIONS = [
    types.FunctionDeclaration(
        name="look",
        description="Robotun kamerasindan su anki goruntuyu al. Kullanici etrafta ne oldugunu, "
                    "neyi gordugunu sordugunda ya da bir seye bakman gerektiginde kullan.",
    ),
    types.FunctionDeclaration(
        name="remember",
        description="Kullanici ya da ev hakkinda ileride ise yarayacak kalici bir bilgiyi kaydet: "
                    "isim, tercih, aile, esyalarin yeri, rutinler. Gecici seyleri kaydetme. "
                    "Saglik, sifre, banka gibi hassas bilgileri kaydetme.",
        parameters_json_schema=_params({
            "text": {"type": "string", "description": "Tek cumlelik Turkce bilgi, or. 'Kullanicinin kizinin adi Elif.'"},
            "category": {"type": "string", "enum": CATEGORIES},
            "importance": {"type": "integer", "description": "1 (onemsiz) - 5 (cok onemli)"},
        }, ["text", "category"]),
    ),
    types.FunctionDeclaration(
        name="recall",
        description="Hafizada ara: kullanici gecmisten bir sey sordugunda ya da bir bilgiyi "
                    "hatirlaman gerektiginde kullan (or. 'anahtarlarim nerede', 'dun ne konustuk').",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Aranacak anahtar kelimeler"},
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="forget",
        description="Kullanici bir bilgiyi unutmani istediginde o bilgiyi hafizadan sil.",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Silinecek bilgiyi tarif eden kelimeler"},
        }, ["query"]),
    ),
]


class Tools:
    def __init__(self, camera: Camera | None, memory: Memory):
        self._camera = camera
        self._memory = memory

    def handle(self, call: types.FunctionCall) -> types.FunctionResponse:
        args = call.args or {}
        log.info("arac cagrisi: %s %s", call.name, args)
        try:
            if call.name == "look":
                return self._look(call)
            result = getattr(self, f"_{call.name}")(**args)
        except Exception as e:
            log.warning("arac hatasi %s: %s", call.name, e)
            result = {"error": str(e)}
        return types.FunctionResponse(id=call.id, name=call.name, response=result)

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

    def _remember(self, text: str, category: str = "diger", importance: int = 3) -> dict:
        fid = self._memory.remember(text, category, importance)
        return {"result": "kaydedildi" if fid else "zaten biliniyordu"}

    def _recall(self, query: str) -> dict:
        facts = [t for _, _, t in self._memory.recall(query)]
        turns = [f"{time.strftime('%d.%m %H:%M', time.localtime(ts))} "
                 f"{'kullanici' if r == 'user' else 'robot'}: {t}"
                 for ts, r, t in self._memory.search_turns(query)]
        if not facts and not turns:
            return {"result": "hafizada bununla ilgili bir sey yok"}
        return {"facts": facts, "past_conversation": turns}

    def _forget(self, query: str) -> dict:
        removed = self._memory.forget(query)
        return {"removed": removed} if removed else {"result": "eslesen bilgi bulunamadi"}
