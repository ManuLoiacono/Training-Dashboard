"""
MANU///LOGS — Bot de Telegram
Recibe mensajes de entrenamiento, los parsea y los deja en la bandeja
de entrada del dashboard. NO escribe todavía en sesiones/series.

Usa long polling: no hace falta URL pública ni túnel. Telegram guarda
los mensajes ~24hs, así que si la PC está apagada entran al prender.

Uso:
    python telegram_bot.py          (standalone, para depurar)
    o se levanta solo con server.py en un thread
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

import message_parser
from models import MensajeParseado, get_session

TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
ALLOWED_USER_ID = (os.environ.get("TELEGRAM_ALLOWED_USER_ID") or "").strip()

API = f"https://api.telegram.org/bot{TOKEN}"
OFFSET_FILE = Path(__file__).parent / ".telegram_offset"

AYUDA = (
    "MANU///LOGS\n\n"
    "Mandame los ejercicios como te salga:\n\n"
    "  BP 75 x 3 x 6,4,3\n"
    "  sentadilla 100 3x5\n"
    "  dominadas 3x8\n"
    "  banca 75 por 6,4 y 3, despues remo 60x10,10,8\n\n"
    "Te contesto lo que entendí y queda en la bandeja del dashboard "
    "para que lo confirmes."
)


def esta_configurado() -> bool:
    return bool(TOKEN and ALLOWED_USER_ID)


def _leer_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def _guardar_offset(offset: int) -> None:
    try:
        OFFSET_FILE.write_text(str(offset))
    except OSError as e:
        print(f"[TG] No pude guardar el offset: {e}", flush=True)


def enviar(chat_id: int, texto: str) -> None:
    try:
        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[TG] Error enviando mensaje: {e}", flush=True)


def procesar_mensaje(texto: str, chat_id: int, message_id: int) -> None:
    """Parsea el mensaje, lo guarda en la bandeja y contesta con el eco."""
    texto = (texto or "").strip()
    if not texto:
        return

    if texto.startswith("/"):
        comando = texto.split()[0].lower()
        if comando in ("/start", "/help", "/ayuda"):
            enviar(chat_id, AYUDA)
        else:
            enviar(chat_id, "Comando no reconocido. Probá /help")
        return

    session = get_session()
    try:
        try:
            parse = message_parser.parsear(texto)
            registro = MensajeParseado(
                telegram_message_id=message_id,
                texto_original=texto,
                parse_json=parse.model_dump_json(),
                estado="parseado",
            )
            respuesta = message_parser.formatear_para_telegram(parse)
        except Exception as e:
            registro = MensajeParseado(
                telegram_message_id=message_id,
                texto_original=texto,
                estado="error",
                error=f"{type(e).__name__}: {e}",
            )
            respuesta = f"No pude parsearlo:\n{type(e).__name__}: {e}"
            print(f"[TG] Error parseando {texto!r}: {e}", flush=True)

        session.add(registro)
        session.commit()
        respuesta += "\n\n(guardado en la bandeja, todavía sin confirmar)"
    except Exception as e:
        session.rollback()
        respuesta = f"Error guardando en la base: {e}"
        print(f"[TG] Error de base: {e}", flush=True)
    finally:
        session.close()

    enviar(chat_id, respuesta)


def _manejar_update(update: dict) -> None:
    mensaje = update.get("message") or update.get("edited_message")
    if not mensaje:
        return

    remitente = str((mensaje.get("from") or {}).get("id", ""))
    chat_id = (mensaje.get("chat") or {}).get("id")

    # Whitelist: el bot es descubrible por username, así que sin esto
    # cualquiera podría escribir en la base y gastar la API key.
    if remitente != ALLOWED_USER_ID:
        print(f"[TG] Mensaje ignorado de user_id={remitente}", flush=True)
        return

    texto = mensaje.get("text")
    if not texto:
        enviar(chat_id, "Por ahora solo entiendo texto.")
        return

    print(f"[TG] <- {texto!r}", flush=True)
    procesar_mensaje(texto, chat_id, mensaje.get("message_id"))


def correr(poll_timeout: int = 25) -> None:
    """Loop de long polling. Bloqueante — corrolo en su propio thread."""
    if not esta_configurado():
        print("[TG] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_ALLOWED_USER_ID en .env", flush=True)
        return

    try:
        me = requests.get(f"{API}/getMe", timeout=15).json()
        if not me.get("ok"):
            print(f"[TG] Token rechazado por Telegram: {me}", flush=True)
            return
        print(f"[TG] Conectado como @{me['result'].get('username')}", flush=True)
    except requests.RequestException as e:
        print(f"[TG] No pude conectar con Telegram: {e}", flush=True)
        return

    offset = _leer_offset()
    print(f"[TG] Escuchando (offset={offset})", flush=True)

    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": poll_timeout},
                timeout=poll_timeout + 10,
            )
            data = r.json()
            if not data.get("ok"):
                print(f"[TG] getUpdates devolvió error: {data}", flush=True)
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                _guardar_offset(offset)
                try:
                    _manejar_update(update)
                except Exception as e:
                    print(f"[TG] Error manejando update: {e}", flush=True)

        except requests.Timeout:
            continue
        except requests.RequestException as e:
            print(f"[TG] Error de red: {e}", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"[TG] Error inesperado: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    correr()
