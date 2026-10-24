"""Sohbet bitince dokumu ozetler ve kalici bilgileri cikarir (orta + uzun hafiza)."""

import json
import logging
import time

from google import genai
from google.genai import types

from .memory import CATEGORIES, Memory

log = logging.getLogger(__name__)

PROMPT = """Asagida bir ev robotu ile kullanicinin konusma dokumu var.
1) "summary": Sohbetin 1-3 cumlelik Turkce ozeti (kim ne istedi, ne konusuldu, yarim kalan isler).
2) "facts": Kullanici veya ev hakkinda ILERIDE ISE YARAYACAK kalici bilgiler (isim, tercih, aile,
   esyalarin yeri, rutinler). Gecici seyleri (hava, o anki ruh hali, selamlasma) ekleme.
   Asagidaki "bilinenler" listesinde zaten olan bilgiyi tekrar ekleme.
   Saglik, sifre, banka gibi hassas bilgileri ekleme.
   Her bilgi: {{"text": kisa Turkce cumle, "category": {cats}, "importance": 1-5}}.

Bilinenler:
{known}

Dokum:
{transcript}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "facts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "category": {"type": "string", "enum": CATEGORIES},
                "importance": {"type": "integer"},
            },
            "required": ["text", "category", "importance"],
        }},
    },
    "required": ["summary", "facts"],
}


async def consolidate(memory: Memory, client: genai.Client, model: str) -> bool:
    turns = memory.pending_turns()
    if not turns:
        return False
    transcript = "\n".join(
        f"[{time.strftime('%H:%M', time.localtime(ts))}] {'Kullanici' if role == 'user' else 'Robot'}: {text}"
        for _, ts, role, text in turns)
    known = "\n".join(f"- {t}" for _, _, t in memory.top_facts(60)) or "(yok)"
    resp = await client.aio.models.generate_content(
        model=model,
        contents=PROMPT.format(cats="|".join(CATEGORIES), known=known, transcript=transcript),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_json_schema=SCHEMA, temperature=0.2),
    )
    data = json.loads(resp.text)
    memory.add_episode(data["summary"], turns)
    added = [f["text"] for f in data.get("facts", [])
             if memory.remember(f["text"], f["category"], f["importance"], source="auto")]
    log.info("hafiza: %d satir ozetlendi, %d yeni bilgi %s", len(turns), len(added), added or "")
    return True
