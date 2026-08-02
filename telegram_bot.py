"""
MANU///LOGS — Bot de Telegram
Recibe mensajes de entrenamiento, los parsea y los deja en la bandeja
de entrada del dashboard. NO escribe todavía en sesiones/series: eso pasa
cuando confirmás desde el tab ENTRENO.

El protocolo de sesión es explícito, para no adivinar fecha ni agrupación:

    "inicio: Push 02/08/26"   abre la sesión
    "BP 75 x 3 x 6,4,3"       cae en la bandeja, atado a esa sesión
    "fin sesión"              la cierra y la linkea con la actividad de Garmin

Si te olvidás de cerrarla, a las horas el bot pregunta.

Usa long polling: no hace falta URL pública ni túnel. Telegram guarda
los mensajes ~24hs, así que si la PC está apagada entran al prender.

Uso:
    python telegram_bot.py          (standalone, para depurar)
    o se levanta solo con server.py en un thread
"""

import json
import os
import time
from datetime import date as date_type
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

import message_parser
import models
from models import MensajeParseado, get_session

try:
    import garmin_client
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False

TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
ALLOWED_USER_ID = (os.environ.get("TELEGRAM_ALLOWED_USER_ID") or "").strip()

API = f"https://api.telegram.org/bot{TOKEN}"
OFFSET_FILE = Path(__file__).parent / ".telegram_offset"

# Cada cuánto revisa si quedó alguna sesión abierta sin cerrar
INTERVALO_WATCHDOG_SEG = 300

AYUDA = (
    "MANU///LOGS\n\n"
    "Para arrancar un entrenamiento:\n"
    "  inicio: Push 02/08/26\n"
    "  inicio sesión: Pierna\n\n"
    "Después mandame los ejercicios como te salga:\n"
    "  BP 75 x 3 x 6,4,3\n"
    "  sentadilla 100 3x5\n"
    "  dominadas 3x8\n"
    "  banca 75 por 6,4 y 3, despues remo 60x10,10,8\n\n"
    "Y para terminar:\n"
    "  fin sesión\n\n"
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


def _texto_resumen(resumen: dict) -> str:
    """Resumen de sesión para el eco del cierre."""
    partes = []
    titulo = resumen["nombre"] or "Sesión"
    partes.append(f"{titulo} — {resumen['fecha']}")
    if resumen["ejercicios"]:
        partes.append("  " + ", ".join(resumen["ejercicios"]))
    partes.append(
        f"  {resumen['total_series']} series · {resumen['volumen_kg']:,} kg".replace(",", ".")
    )
    if resumen["series_pendientes"]:
        partes.append(
            f"  ({resumen['series_pendientes']} series esperando que las "
            f"confirmes en el dashboard)"
        )
    return "\n".join(partes)


def _linkear_garmin(session, sesion) -> str:
    """
    Busca la actividad de fuerza de Garmin que se solapa con la sesión y la
    guarda. Devuelve una línea para el eco, o "" si no hubo match.
    Entrenar sin el reloj es un caso válido: no es un error.
    """
    if not GARMIN_AVAILABLE:
        return ""
    try:
        act = garmin_client.buscar_actividad_fuerza(sesion.iniciada_en, sesion.cerrada_en)
    except Exception as e:
        print(f"[TG] No pude buscar la actividad en Garmin: {e}", flush=True)
        return ""
    if not act:
        return ""

    sesion.garmin_activity_id = act["activity_id"]
    session.commit()
    return (
        f"\nGarmin: {act['duracion_min']:g} min · "
        f"FC {act['fc_prom']}/{act['fc_max']} · {act['calorias']} kcal"
    )


def _manejar_inicio(session, parse, chat_id) -> str:
    """Abre una sesión nueva. Si había otra abierta la cierra."""
    try:
        fecha = date_type.fromisoformat(parse.fecha) if parse.fecha else date_type.today()
    except (ValueError, TypeError):
        fecha = date_type.today()

    nueva, previa = models.abrir_sesion(session, fecha, parse.nombre_sesion)

    lineas = []
    if previa is not None:
        lineas.append(
            f"(cerré la sesión anterior que había quedado abierta: "
            f"{previa.nombre or 'sin nombre'} del {previa.fecha.isoformat()})"
        )
    nombre = nueva.nombre or "sin nombre"
    lineas.append(f"Sesión abierta: {nombre} — {fecha.isoformat()}")
    lineas.append("Mandame los ejercicios. Cuando termines: fin sesión")
    return "\n".join(lineas)


def _manejar_fin(session, chat_id) -> str:
    """Cierra la sesión abierta, la linkea con Garmin y devuelve el resumen."""
    abierta = models.sesion_abierta(session)
    if abierta is None:
        return "No hay ninguna sesión abierta. Para arrancar: inicio: Push"

    models.cerrar_sesion(session, abierta)
    resumen = models.resumen_sesion(session, abierta)
    texto = "Sesión cerrada.\n\n" + _texto_resumen(resumen)
    texto += _linkear_garmin(session, abierta)
    return texto


def procesar_mensaje(texto: str, chat_id: int, message_id: int) -> None:
    """
    Parsea el mensaje, decide qué quiso decir y actúa:
    inicio/fin mueven la sesión, las series caen en la bandeja.
    """
    texto = (texto or "").strip()
    if not texto:
        return

    if texto.startswith("/"):
        comando = texto.split()[0].lower()
        if comando in ("/start", "/help", "/ayuda"):
            enviar(chat_id, AYUDA)
        elif comando in ("/estado", "/sesion"):
            enviar(chat_id, _texto_estado())
        elif comando == "/fin":
            session = get_session()
            try:
                enviar(chat_id, _manejar_fin(session, chat_id))
            finally:
                session.close()
        else:
            enviar(chat_id, "Comando no reconocido. Probá /help")
        return

    session = get_session()
    try:
        abierta = models.sesion_abierta(session)
        pregunta_pendiente = abierta is not None and abierta.pregunta_cierre_en is not None

        # 1) Parsear. Si la API falla, el mensaje igual queda registrado.
        try:
            parse = message_parser.parsear(
                texto,
                sesion_abierta=abierta,
                pregunta_pendiente=pregunta_pendiente,
            )
        except Exception as e:
            session.add(MensajeParseado(
                telegram_message_id=message_id,
                texto_original=texto,
                estado="error",
                tipo="series",
                sesion_id=abierta.id if abierta else None,
                error=f"{type(e).__name__}: {e}",
            ))
            session.commit()
            print(f"[TG] Error parseando {texto!r}: {e}", flush=True)
            enviar(chat_id, f"No pude parsearlo:\n{type(e).__name__}: {e}")
            return

        tipo = (parse.tipo or "series").strip().lower()
        if tipo not in ("series", "inicio", "fin", "respuesta", "otro"):
            tipo = "series"

        respuesta = ""
        estado = "parseado"

        # 2) Actuar según la intención
        if tipo == "inicio":
            respuesta = _manejar_inicio(session, parse, chat_id)
            abierta = models.sesion_abierta(session)
            estado = "aplicado"

        elif tipo == "fin":
            respuesta = _manejar_fin(session, chat_id)
            estado = "aplicado"

        elif tipo == "respuesta":
            estado = "aplicado"
            if not pregunta_pendiente:
                respuesta = "No te entendí. Probá /help"
                tipo = "otro"
            elif (parse.respuesta or "").lower().startswith("s"):
                respuesta = _manejar_fin(session, chat_id)
            else:
                # Sigue entrenando: no volvemos a preguntar por esta sesión
                respuesta = "Dale, la dejo abierta. Avisame con 'fin sesión'."

        elif tipo == "otro":
            respuesta = "No entendí qué querés hacer. Probá /help"
            estado = "aplicado"

        # 3) Las series van a la bandeja (un "inicio" puede traerlas también)
        if parse.ejercicios:
            eco = message_parser.formatear_para_telegram(parse)
            registro = MensajeParseado(
                telegram_message_id=message_id,
                texto_original=texto,
                parse_json=parse.model_dump_json(),
                estado="parseado",
                tipo="series",
                sesion_id=abierta.id if abierta else None,
            )
            session.add(registro)
            session.commit()

            if respuesta:
                respuesta += "\n\n" + eco
            else:
                respuesta = eco
            if abierta is None:
                respuesta += (
                    "\n\n[!] No hay ninguna sesión abierta. Lo guardo igual; "
                    "al confirmarlo va a la sesión del día."
                )
            respuesta += "\n\n(en la bandeja, todavía sin confirmar)"
        else:
            # Mensaje de control: queda como registro, sin series que confirmar
            session.add(MensajeParseado(
                telegram_message_id=message_id,
                texto_original=texto,
                parse_json=parse.model_dump_json(),
                estado=estado,
                tipo=tipo,
                sesion_id=abierta.id if abierta else None,
            ))
            session.commit()

    except Exception as e:
        session.rollback()
        respuesta = f"Error guardando en la base: {e}"
        print(f"[TG] Error de base: {e}", flush=True)
    finally:
        session.close()

    enviar(chat_id, respuesta or "Listo.")


def _texto_estado() -> str:
    """Respuesta a /estado: qué sesión hay abierta y qué tiene adentro."""
    session = get_session()
    try:
        abierta = models.sesion_abierta(session)
        if abierta is None:
            return "No hay ninguna sesión abierta. Para arrancar: inicio: Push"
        resumen = models.resumen_sesion(session, abierta)
        return "Sesión ABIERTA\n\n" + _texto_resumen(resumen)
    finally:
        session.close()


def revisar_sesiones_abiertas() -> None:
    """
    Si una sesión lleva horas abierta, preguntar si la cerramos.
    Pregunta UNA sola vez por sesión: insistir es la forma más rápida de
    que el bot se vuelva molesto y se deje de usar.
    """
    if not ALLOWED_USER_ID:
        return
    session = get_session()
    try:
        for sesion in models.sesiones_para_preguntar_cierre(session):
            nombre = sesion.nombre or "sin nombre"
            desde = sesion.iniciada_en.strftime("%H:%M") if sesion.iniciada_en else ""
            enviar(
                int(ALLOWED_USER_ID),
                f"La sesión {nombre} sigue abierta desde las {desde}. "
                f"¿La cerramos?",
            )
            sesion.pregunta_cierre_en = models.ahora()
            session.commit()
            print(f"[TG] Pregunté por la sesión abierta #{sesion.id}", flush=True)
    except Exception as e:
        session.rollback()
        print(f"[TG] Error revisando sesiones abiertas: {e}", flush=True)
    finally:
        session.close()


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

    ultimo_watchdog = time.monotonic()

    while True:
        try:
            # Revisar sesiones abandonadas entre polleos, sin thread aparte
            if time.monotonic() - ultimo_watchdog >= INTERVALO_WATCHDOG_SEG:
                ultimo_watchdog = time.monotonic()
                revisar_sesiones_abiertas()

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
