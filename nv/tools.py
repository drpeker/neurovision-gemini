"""Modelin cagirabilecegi araclar. Yeni arac: DECLARATIONS'a ekle, handle()'a dal ekle.

Aciklamalar ve donen sonuclar modele gider: duzgun Turkce yazilir.
"""

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
        description="Kameradan şu anki görüntüyü al. Kullanıcı etrafta ne olduğunu ya da neyi "
                    "gördüğünü sorduğunda veya bir şeye bakman gerektiğinde kullan.",
    ),
    types.FunctionDeclaration(
        name="remember",
        description="Kullanıcı ya da ev hakkında ileride işe yarayacak kalıcı bir bilgiyi kaydet: "
                    "isim, tercih, aile, eşyaların yeri, alışkanlıklar. Geçici şeyleri kaydetme. "
                    "Sağlık, şifre, banka gibi hassas bilgileri kaydetme.",
        parameters_json_schema=_params({
            "text": {"type": "string",
                     "description": "Tek cümlelik Türkçe bilgi, örneğin 'Kullanıcının kızının adı Elif.'"},
            "category": {"type": "string", "enum": CATEGORIES},
            "importance": {"type": "integer", "description": "1 (önemsiz) ile 5 (çok önemli) arası"},
        }, ["text", "category"]),
    ),
    types.FunctionDeclaration(
        name="recall",
        description="Hafızada ara. Kullanıcı geçmişten bir şey sorduğunda ya da bir bilgiyi "
                    "hatırlaman gerektiğinde kullan (örneğin 'anahtarlarım nerede', 'dün ne konuştuk').",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Aranacak anahtar kelimeler"},
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="forget",
        description="Kullanıcı bir bilgiyi unutmanı istediğinde o bilgiyi hafızadan sil.",
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
            result = {"error": f"Araç çalışmadı: {e}"}
        return types.FunctionResponse(id=call.id, name=call.name, response=result)

    def _look(self, call: types.FunctionCall) -> types.FunctionResponse:
        jpeg = self._camera.frame() if self._camera else None
        if not jpeg:
            return types.FunctionResponse(id=call.id, name=call.name,
                                          response={"error": "Kamera görüntüsü alınamadı."})
        return types.FunctionResponse(
            id=call.id, name=call.name,
            response={"result": "Kameradan alınan güncel görüntü ekte."},
            parts=[types.FunctionResponsePart(
                inline_data=types.FunctionResponseBlob(mime_type="image/jpeg", data=jpeg))],
        )

    def _remember(self, text: str, category: str = "diğer", importance: int = 3) -> dict:
        fid = self._memory.remember(text, category, importance)
        return {"result": "Kaydedildi." if fid else "Bu bilgi zaten kayıtlı."}

    def _recall(self, query: str) -> dict:
        facts = [t for _, _, t in self._memory.recall(query)]
        turns = [f"{time.strftime('%d.%m %H:%M', time.localtime(ts))} "
                 f"{'kullanıcı' if r == 'user' else 'sen'}: {t}"
                 for ts, r, t in self._memory.search_turns(query)]
        if not facts and not turns:
            return {"result": "Hafızada bununla ilgili bir şey yok."}
        return {"bilgiler": facts, "gecmis_konusmalar": turns}

    def _forget(self, query: str) -> dict:
        removed = self._memory.forget(query)
        return {"silinenler": removed} if removed else {"result": "Eşleşen bilgi bulunamadı."}
