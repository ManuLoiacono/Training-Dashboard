"""
MANU///LOGS — servidor
Corre con: python server.py
Abre en el browser: http://localhost:5000
"""

import json
import os
from datetime import datetime, date as date_type
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import models
from models import (
    get_session, Ejercicio, Sesion, Serie, MensajeParseado, read_gym_as_rows,
)

# ─────────────────────────────────────────────
# Módulos opcionales: Garmin Connect y PDF parser
# ─────────────────────────────────────────────

try:
    import garmin_client
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False

try:
    import telegram_bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

try:
    import antro_parser
    ANTRO_PARSER_AVAILABLE = True
except ImportError:
    ANTRO_PARSER_AVAILABLE = False

# ─────────────────────────────────────────────
# CONFIGURACIÓN — editá estos valores
# ─────────────────────────────────────────────

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1ef2kn8q_8sgT96LsljUixN4ZBFNi0W1MsBuk9-NpZ4o")
CREDENTIALS_FILE = os.environ.get("CREDENTIALS_FILE", "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Nombres de las hojas dentro del Google Sheet
SHEET_GIMNASIO     = "gimnasio"
SHEET_RUNNING      = "running"
SHEET_ANTROPO      = "antropometria"

# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
CORS(app)

def get_sheets_service():
    creds = Credentials.from_service_account_file(
        os.path.join(BASE_DIR, CREDENTIALS_FILE), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()

def read_sheet(sheet_name: str) -> list[dict]:
    """Lee una hoja y devuelve lista de dicts usando la primera fila como headers."""
    service = get_sheets_service()
    result = service.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!A:Z"
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    headers = values[0]
    rows = []
    for row in values[1:]:
        # Pad row si tiene menos columnas que headers
        padded = row + [""] * (len(headers) - len(row))
        rows.append(dict(zip(headers, padded)))
    return rows

# ─────────────────────────────────────────────
# ENDPOINTS DE LA API
# ─────────────────────────────────────────────

@app.route("/")
def index():
    response = send_from_directory(".", "dashboard_v3.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/api/gimnasio")
def api_gimnasio():
    """
    Datos de gimnasio desde SQLite (migrado de Google Sheets).
    Formato flat: fecha | dia | ejercicio | grupo_muscular | serie | reps | peso_kg | notas
    """
    try:
        rows = read_gym_as_rows()
        if not rows:
            # Fallback a Sheets si la DB está vacía (pre-migración)
            rows = read_sheet(SHEET_GIMNASIO)
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/running")
def api_running():
    """
    Datos de running: Garmin Connect API primero, fallback a Google Sheets.
    """
    # Intentar Garmin primero
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            rows = garmin_client.fetch_running_activities(days_back=180)
            if rows:
                return jsonify({"ok": True, "data": rows, "source": "garmin"})
        except Exception as e:
            print(f"[GARMIN] Error: {e}")

    # Fallback a Google Sheets
    try:
        rows = read_sheet(SHEET_RUNNING)
        return jsonify({"ok": True, "data": rows, "source": "sheets"})
    except Exception:
        return jsonify({"ok": True, "data": [], "source": "none"})

@app.route("/api/antropometria")
def api_antropometria():
    """
    Datos antropométricos: parser de PDFs primero, fallback a Google Sheets.
    """
    # Intentar PDF parser primero
    if ANTRO_PARSER_AVAILABLE:
        try:
            rows = antro_parser.parse_all_pdfs()
            if rows:
                return jsonify({"ok": True, "data": rows, "source": "pdf"})
        except Exception as e:
            print(f"[ANTRO] Error: {e}")

    # Fallback a Google Sheets
    try:
        rows = read_sheet(SHEET_ANTROPO)
        return jsonify({"ok": True, "data": rows, "source": "sheets"})
    except Exception:
        return jsonify({"ok": True, "data": [], "source": "none"})

# ─────────────────────────────────────────────
# GYM CRUD ENDPOINTS (SQLite)
# ─────────────────────────────────────────────

@app.route("/api/gym/ejercicios")
def api_gym_ejercicios():
    """Catálogo de ejercicios ordenado por grupo muscular."""
    session = get_session()
    try:
        ejercicios = session.query(Ejercicio).order_by(Ejercicio.grupo_muscular, Ejercicio.nombre).all()
        return jsonify({"ok": True, "data": [e.to_dict() for e in ejercicios]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/ejercicios", methods=["POST"])
def api_gym_ejercicios_create():
    """Agregar un nuevo ejercicio al catálogo."""
    data = request.get_json(force=True)
    nombre = (data.get("nombre") or "").strip().upper()
    grupo = (data.get("grupo_muscular") or "").strip().upper()
    if not nombre or not grupo:
        return jsonify({"ok": False, "error": "nombre y grupo_muscular son requeridos"}), 400

    session = get_session()
    try:
        existing = session.query(Ejercicio).filter(Ejercicio.nombre == nombre).first()
        if existing:
            return jsonify({"ok": False, "error": f"Ejercicio '{nombre}' ya existe"}), 409
        ej = Ejercicio(nombre=nombre, grupo_muscular=grupo)
        session.add(ej)
        session.commit()
        return jsonify({"ok": True, "data": ej.to_dict()}), 201
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/sesiones")
def api_gym_sesiones():
    """Lista de sesiones con metadata. Query param: ?semanas=N (default 12)."""
    semanas = request.args.get("semanas", 12, type=int)
    from datetime import timedelta
    desde = date_type.today() - timedelta(weeks=semanas)

    session = get_session()
    try:
        sesiones = (
            session.query(Sesion)
            .filter(Sesion.fecha >= desde)
            .order_by(Sesion.fecha.desc())
            .all()
        )
        return jsonify({"ok": True, "data": [s.to_dict() for s in sesiones]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/sesiones", methods=["POST"])
def api_gym_sesiones_create():
    """Crear nueva sesión. Body: { fecha, dia_rutina, nombre, notas }."""
    data = request.get_json(force=True)
    fecha_str = data.get("fecha") or date_type.today().isoformat()
    dia = data.get("dia_rutina")
    nombre = (data.get("nombre") or "").strip().upper() or None
    notas = data.get("notas", "")

    try:
        fecha = date_type.fromisoformat(fecha_str)
    except ValueError:
        return jsonify({"ok": False, "error": "fecha inválida (YYYY-MM-DD)"}), 400

    # dia_rutina es opcional: sin valor la sesión queda sin etiquetar
    if dia is not None and str(dia).strip() != "":
        try:
            dia = int(dia)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "dia_rutina debe ser un número"}), 400
    else:
        dia = None

    session = get_session()
    try:
        # Las sesiones creadas desde ENTRENO nacen cerradas: el ciclo
        # abierta/cerrada es del protocolo de Telegram.
        s = Sesion(fecha=fecha, dia_rutina=dia, nombre=nombre, notas=notas)
        session.add(s)
        session.commit()
        return jsonify({"ok": True, "data": s.to_dict()}), 201
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# Cache en proceso de las actividades de fuerza de Garmin. Sin esto, abrir el
# detalle de una sesión dispara una llamada de red por request.
_CACHE_FUERZA = {"datos": None, "vence": 0.0}
_CACHE_FUERZA_SEG = 600


def _actividades_fuerza() -> list[dict]:
    """Actividades de fuerza de Garmin, cacheadas. Ante cualquier falla, []."""
    import time as _time
    if not GARMIN_AVAILABLE:
        return []
    if _CACHE_FUERZA["datos"] is not None and _time.time() < _CACHE_FUERZA["vence"]:
        return _CACHE_FUERZA["datos"]
    try:
        datos = garmin_client.fetch_strength_activities(days_back=180)
    except Exception as e:
        print(f"[GARMIN] No pude traer actividades de fuerza: {e}", flush=True)
        datos = []
    _CACHE_FUERZA["datos"] = datos
    _CACHE_FUERZA["vence"] = _time.time() + _CACHE_FUERZA_SEG
    return datos


def _garmin_de_sesion(sesion) -> dict | None:
    """Datos de la actividad de Garmin linkeada a una sesión, si tiene."""
    if not sesion.garmin_activity_id:
        return None
    for act in _actividades_fuerza():
        if act["activity_id"] == sesion.garmin_activity_id:
            return {
                "activity_id": act["activity_id"],
                "inicio": act["inicio"].isoformat(),
                "duracion_min": act["duracion_min"],
                "fc_prom": act["fc_prom"],
                "fc_max": act["fc_max"],
                "calorias": act["calorias"],
            }
    return None


@app.route("/api/gym/sesiones/<int:sesion_id>")
def api_gym_sesion_detail(sesion_id):
    """Detalle de sesión con todas sus series y ejercicios."""
    session = get_session()
    try:
        s = session.query(Sesion).get(sesion_id)
        if not s:
            return jsonify({"ok": False, "error": "Sesión no encontrada"}), 404
        data = s.to_dict(include_series=True)
        data["garmin"] = _garmin_de_sesion(s)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/series", methods=["POST"])
def api_gym_series_create():
    """Agregar serie a sesión. Body: { sesion_id, ejercicio_id, reps, peso_kg, notas }."""
    data = request.get_json(force=True)
    sesion_id = data.get("sesion_id")
    ejercicio_id = data.get("ejercicio_id")
    reps = data.get("reps")
    peso_kg = data.get("peso_kg", 0)

    if not all([sesion_id, ejercicio_id, reps is not None]):
        return jsonify({"ok": False, "error": "sesion_id, ejercicio_id y reps son requeridos"}), 400

    session = get_session()
    try:
        # Calcular numero_serie automáticamente
        count = (
            session.query(Serie)
            .filter(Serie.sesion_id == sesion_id, Serie.ejercicio_id == ejercicio_id)
            .count()
        )
        serie = Serie(
            sesion_id=int(sesion_id),
            ejercicio_id=int(ejercicio_id),
            numero_serie=count + 1,
            reps=int(reps),
            peso_kg=float(peso_kg),
            notas=data.get("notas", ""),
        )
        session.add(serie)
        session.commit()
        return jsonify({"ok": True, "data": serie.to_dict()}), 201
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/series/<int:serie_id>", methods=["PUT"])
def api_gym_series_update(serie_id):
    """Editar una serie existente."""
    data = request.get_json(force=True)
    session = get_session()
    try:
        serie = session.query(Serie).get(serie_id)
        if not serie:
            return jsonify({"ok": False, "error": "Serie no encontrada"}), 404
        if "reps" in data:
            serie.reps = int(data["reps"])
        if "peso_kg" in data:
            serie.peso_kg = float(data["peso_kg"])
        if "notas" in data:
            serie.notas = data["notas"]
        session.commit()
        return jsonify({"ok": True, "data": serie.to_dict()})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/series/<int:serie_id>", methods=["DELETE"])
def api_gym_series_delete(serie_id):
    """Eliminar una serie."""
    session = get_session()
    try:
        serie = session.query(Serie).get(serie_id)
        if not serie:
            return jsonify({"ok": False, "error": "Serie no encontrada"}), 404
        session.delete(serie)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/gym/ejercicio/<nombre>")
def api_gym_ejercicio_history(nombre):
    """Historial de un ejercicio desde SQLite (reemplaza /api/ejercicio/<nombre>)."""
    nombre_upper = nombre.strip().upper()
    session = get_session()
    try:
        ejercicio = session.query(Ejercicio).filter(Ejercicio.nombre == nombre_upper).first()
        if not ejercicio:
            return jsonify({"ok": False, "error": f"Ejercicio '{nombre}' no encontrado"}), 404

        series = (
            session.query(Serie)
            .join(Sesion)
            .filter(Serie.ejercicio_id == ejercicio.id)
            .order_by(Sesion.fecha, Serie.numero_serie)
            .all()
        )

        # Agrupar por fecha
        sesiones_dict = {}
        for s in series:
            fecha = s.sesion.fecha.isoformat()
            if fecha not in sesiones_dict:
                sesiones_dict[fecha] = []
            sesiones_dict[fecha].append({"reps": s.reps, "peso": s.peso_kg})

        historico = []
        all_sets = []
        for fecha in sorted(sesiones_dict.keys()):
            sets = sesiones_dict[fecha]
            peso_max = max(s["peso"] for s in sets)
            reps_en_max = max(s["reps"] for s in sets if s["peso"] == peso_max)
            volumen = sum(s["reps"] * s["peso"] for s in sets)
            rm_estimado = round(peso_max * (1 + reps_en_max / 30), 1) if peso_max > 0 else 0

            from datetime import timedelta
            d = datetime.strptime(fecha, "%Y-%m-%d")
            week_start = d - timedelta(days=d.weekday())

            historico.append({
                "fecha": fecha,
                "semana": week_start.strftime("%Y-%m-%d"),
                "peso_max": peso_max,
                "reps_en_max": reps_en_max,
                "volumen": round(volumen),
                "1rm_estimado": rm_estimado,
            })
            for s in sets:
                all_sets.append({"fecha": fecha, "reps": s["reps"], "peso": s["peso"],
                                 "vol": s["reps"] * s["peso"]})

        pr_peso_set = max(all_sets, key=lambda s: s["peso"]) if all_sets else None
        pr_vol_set = max(all_sets, key=lambda s: s["vol"]) if all_sets else None
        top5 = sorted(all_sets, key=lambda s: s["vol"], reverse=True)[:5]

        tendencia = ""
        estancado = False
        if len(historico) >= 2:
            recent = historico[-1]["peso_max"]
            idx_4sem = max(0, len(historico) - 5)
            old = historico[idx_4sem]["peso_max"]
            diff = recent - old
            tendencia = f"+{diff}kg" if diff >= 0 else f"{diff}kg"
            if len(historico) >= 3:
                last3 = [h["peso_max"] for h in historico[-3:]]
                estancado = len(set(last3)) == 1

        return jsonify({
            "ok": True,
            "ejercicio": nombre_upper,
            "grupo": ejercicio.grupo_muscular,
            "historico": historico,
            "pr_peso": {"valor": pr_peso_set["peso"], "fecha": pr_peso_set["fecha"]} if pr_peso_set else None,
            "pr_volumen": {"valor": round(pr_vol_set["vol"]), "fecha": pr_vol_set["fecha"]} if pr_vol_set else None,
            "top5_sets": top5,
            "tendencia_4sem": tendencia,
            "estancado": estancado,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ─────────────────────────────────────────────
# BANDEJA DE ENTRADA (mensajes de Telegram)
# ─────────────────────────────────────────────

@app.route("/api/mensajes")
def api_mensajes():
    """Últimos mensajes recibidos por el bot, ya parseados pero sin confirmar."""
    limite = request.args.get("limite", 20, type=int)
    session = get_session()
    try:
        mensajes = (
            session.query(MensajeParseado)
            .order_by(MensajeParseado.recibido_en.desc())
            .limit(limite)
            .all()
        )
        abierta = models.sesion_abierta(session)
        # Lo que el bot está esperando que contestes por Telegram (el grupo
        # muscular de un ejercicio nuevo, o si cerramos la sesión)
        pendiente = models.pregunta_pendiente_actual(session)
        return jsonify({
            "ok": True,
            "data": [m.to_dict() for m in mensajes],
            "bot_activo": TELEGRAM_AVAILABLE and telegram_bot.esta_configurado(),
            "sesion_abierta": abierta.to_dict() if abierta else None,
            "pregunta_pendiente": pendiente.to_dict() if pendiente else None,
        })
    except Exception as e:
        # Sin bandeja el dashboard tiene que seguir andando
        return jsonify({"ok": True, "data": [], "error": str(e)})
    finally:
        session.close()


@app.route("/api/mensajes/<int:mensaje_id>/confirmar", methods=["POST"])
def api_mensaje_confirmar(mensaje_id):
    """
    Escribe las series del mensaje en sesiones/series.
    Los ejercicios sin match quedan afuera y el mensaje pasa a "parcial".
    """
    session = get_session()
    try:
        resultado = models.confirmar_mensaje(session, mensaje_id)
        if not resultado.get("ok"):
            return jsonify({"ok": False, **resultado}), 400
        return jsonify(resultado)
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/mensajes/<int:mensaje_id>/descartar", methods=["POST"])
def api_mensaje_descartar(mensaje_id):
    """Marca el mensaje como descartado. No escribe nada."""
    session = get_session()
    try:
        resultado = models.descartar_mensaje(session, mensaje_id)
        if not resultado.get("ok"):
            return jsonify({"ok": False, **resultado}), 400
        return jsonify(resultado)
    except Exception as e:
        session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        session.close()


# ─────────────────────────────────────────────
# FEATURE ENDPOINTS
# ─────────────────────────────────────────────

SCORE_WEIGHTS = {
    "gimnasio":    0.40,
    "running":     0.35,
    "composicion": 0.25,
}

SCORE_WEIGHTS_WITH_SLEEP = {
    "gimnasio":    0.35,
    "running":     0.30,
    "composicion": 0.20,
    "sueno":       0.15,
}


@app.route("/api/score")
def api_score():
    """
    Score semanal de rendimiento (1-100).
    Gimnasio: volumen actual vs promedio 4 semanas. 100 = al promedio.
    Running: pace actual vs histórico. Menor pace = mayor score.
    Composición: ratio musculo/grasa interpolado.
    Sueño: promedio de sleep score Garmin de la semana.
    """
    from datetime import timedelta

    # --- Gym score ---
    gym_score = None
    gym_weekly_vols = {}
    try:
        rows = read_gym_as_rows() or read_sheet(SHEET_GIMNASIO)
        for r in rows:
            fecha = r.get("fecha", "")
            if not fecha:
                continue
            reps = int(r.get("reps") or 0)
            peso = float(r.get("peso_kg") or 0)
            d = datetime.strptime(fecha, "%Y-%m-%d")
            week_start = d - timedelta(days=d.weekday())
            wk = week_start.strftime("%Y-%m-%d")
            gym_weekly_vols[wk] = gym_weekly_vols.get(wk, 0) + (reps * peso)

        if gym_weekly_vols:
            sorted_weeks = sorted(gym_weekly_vols.keys())
            current_vol = gym_weekly_vols[sorted_weeks[-1]]
            recent = sorted_weeks[-5:-1] if len(sorted_weeks) > 1 else sorted_weeks
            avg_vol = sum(gym_weekly_vols[w] for w in recent) / len(recent) if recent else current_vol
            if avg_vol > 0:
                ratio = current_vol / avg_vol
                gym_score = max(0, min(100, round(ratio * 100)))
    except Exception as e:
        print(f"[SCORE] Gym error: {e}")

    # --- Running score ---
    run_score = None
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            activities = garmin_client.fetch_running_activities(days_back=180)
            if activities:
                paces = [float(a["pace_min_km"]) for a in activities if float(a.get("pace_min_km", 0)) > 0]
                if len(paces) >= 2:
                    avg_pace = sum(paces) / len(paces)
                    current_pace = sum(paces[-3:]) / len(paces[-3:])
                    if avg_pace > 0:
                        ratio = avg_pace / current_pace  # lower pace = higher ratio
                        run_score = max(0, min(100, round(ratio * 100)))
        except Exception as e:
            print(f"[SCORE] Run error: {e}")

    # --- Body comp score ---
    comp_score = None
    if ANTRO_PARSER_AVAILABLE:
        try:
            antro_data = antro_parser.parse_all_pdfs()
            if antro_data:
                last = antro_data[-1]
                muscular = float(last.get("masa_muscular_kg", 0) or 0)
                adiposa = float(last.get("masa_adiposa_kg", 0) or 0)
                if adiposa > 0:
                    ratio = muscular / adiposa
                    # ratio 2.0 = score 70, ratio 2.5 = 85, ratio 3.0 = 100
                    comp_score = max(0, min(100, round((ratio - 1.0) * 50)))
        except Exception as e:
            print(f"[SCORE] Body error: {e}")

    # --- Sleep score ---
    sleep_score = None
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            sleep_data = garmin_client.fetch_sleep_data(days_back=7)
            if sleep_data:
                scores = [int(s.get("score", 0) or 0) for s in sleep_data if int(s.get("score", 0) or 0) > 0]
                if scores:
                    sleep_score = round(sum(scores) / len(scores))
        except Exception as e:
            print(f"[SCORE] Sleep error: {e}")

    # --- Weighted calculation ---
    has_sleep = sleep_score is not None
    weights = SCORE_WEIGHTS_WITH_SLEEP if has_sleep else SCORE_WEIGHTS

    components = {}
    total_weight = 0
    weighted_sum = 0

    if gym_score is not None:
        w = weights["gimnasio"]
        components["gimnasio"] = {"score": gym_score, "peso": w}
        weighted_sum += gym_score * w
        total_weight += w

    if run_score is not None:
        w = weights["running"]
        components["running"] = {"score": run_score, "peso": w}
        weighted_sum += run_score * w
        total_weight += w

    if comp_score is not None:
        w = weights["composicion"]
        components["composicion"] = {"score": comp_score, "peso": w}
        weighted_sum += comp_score * w
        total_weight += w

    if has_sleep:
        w = weights["sueno"]
        components["sueno"] = {"score": sleep_score, "peso": w}
        weighted_sum += sleep_score * w
        total_weight += w

    # Redistribute weights if some sources unavailable
    score_actual = round(weighted_sum / total_weight) if total_weight > 0 else None

    # Historical scores (from gym weekly volumes as proxy)
    historico = []
    if gym_weekly_vols:
        sorted_weeks = sorted(gym_weekly_vols.keys())
        for i, wk in enumerate(sorted_weeks):
            vol = gym_weekly_vols[wk]
            recent = sorted_weeks[max(0, i - 4):i] if i > 0 else [wk]
            avg = sum(gym_weekly_vols[w] for w in recent) / len(recent)
            wk_score = max(0, min(100, round((vol / avg) * 100))) if avg > 0 else 50
            historico.append({"semana": wk, "score": wk_score})

    # Delta vs last week
    delta = None
    if len(historico) >= 2:
        delta = historico[-1]["score"] - historico[-2]["score"]

    return jsonify({
        "ok": score_actual is not None,
        "score_actual": score_actual,
        "delta": delta,
        "componentes": components,
        "historico": historico[-12:],  # last 12 weeks
    })


@app.route("/api/ejercicio/<nombre>")
def api_ejercicio(nombre):
    """
    Historial completo de un ejercicio: peso máx por sesión, volumen semanal,
    1RM estimado (Epley), PRs, tendencia 4 semanas, alerta estancamiento.
    """
    try:
        rows = read_gym_as_rows() or read_sheet(SHEET_GIMNASIO)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    nombre_upper = nombre.strip().upper()
    filtered = [r for r in rows if (r.get("ejercicio") or "").strip().upper() == nombre_upper]

    if not filtered:
        return jsonify({"ok": False, "error": f"Ejercicio '{nombre}' no encontrado"}), 404

    grupo = filtered[0].get("grupo_muscular", "OTRO").upper()

    # Agrupar por fecha (sesión)
    sesiones = {}
    for r in filtered:
        fecha = r.get("fecha", "")
        if not fecha:
            continue
        if fecha not in sesiones:
            sesiones[fecha] = []
        reps = int(r.get("reps") or 0)
        peso = float(r.get("peso_kg") or 0)
        sesiones[fecha].append({"reps": reps, "peso": peso})

    # Calcular historial por sesión
    historico = []
    all_sets = []
    for fecha in sorted(sesiones.keys()):
        sets = sesiones[fecha]
        peso_max = max(s["peso"] for s in sets)
        reps_en_max = max(s["reps"] for s in sets if s["peso"] == peso_max)
        volumen = sum(s["reps"] * s["peso"] for s in sets)
        rm_estimado = round(peso_max * (1 + reps_en_max / 30), 1) if peso_max > 0 else 0

        # Semana ISO
        from datetime import datetime as dt
        d = dt.strptime(fecha, "%Y-%m-%d")
        week_start = d - __import__('datetime').timedelta(days=d.weekday())
        semana = week_start.strftime("%Y-%m-%d")

        historico.append({
            "fecha": fecha,
            "semana": semana,
            "peso_max": peso_max,
            "reps_en_max": reps_en_max,
            "volumen": round(volumen),
            "1rm_estimado": rm_estimado,
        })

        for s in sets:
            all_sets.append({"fecha": fecha, "reps": s["reps"], "peso": s["peso"],
                             "vol": s["reps"] * s["peso"]})

    # PRs
    pr_peso_set = max(all_sets, key=lambda s: s["peso"]) if all_sets else None
    pr_vol_set = max(all_sets, key=lambda s: s["vol"]) if all_sets else None
    top5 = sorted(all_sets, key=lambda s: s["vol"], reverse=True)[:5]

    # Tendencia 4 semanas
    tendencia = ""
    estancado = False
    if len(historico) >= 2:
        recent = historico[-1]["peso_max"]
        idx_4sem = max(0, len(historico) - 5)
        old = historico[idx_4sem]["peso_max"]
        diff = recent - old
        tendencia = f"+{diff}kg" if diff >= 0 else f"{diff}kg"

        # Estancamiento: mismo peso_max en últimas 3+ sesiones
        if len(historico) >= 3:
            last3 = [h["peso_max"] for h in historico[-3:]]
            estancado = len(set(last3)) == 1

    return jsonify({
        "ok": True,
        "ejercicio": nombre_upper,
        "grupo": grupo,
        "historico": historico,
        "pr_peso": {"valor": pr_peso_set["peso"], "fecha": pr_peso_set["fecha"]} if pr_peso_set else None,
        "pr_volumen": {"valor": round(pr_vol_set["vol"]), "fecha": pr_vol_set["fecha"]} if pr_vol_set else None,
        "top5_sets": top5,
        "tendencia_4sem": tendencia,
        "estancado": estancado,
    })

@app.route("/api/correlaciones")
def api_correlaciones():
    """
    Cruza datos de gym, running y antropometría para encontrar correlaciones.
    Correlation 1: Running km/semana vs Gym volumen/semana
    Correlation 2: Masa adiposa vs pace promedio
    Correlation 3: Volumen semanal vs FC reposo (si disponible)
    """
    from datetime import timedelta

    result = {
        "running_vs_gym": [],
        "composicion_vs_pace": [],
        "volumen_vs_fc_reposo": [],
    }

    # --- Gym weekly volumes ---
    gym_weekly = {}
    try:
        rows = read_gym_as_rows() or read_sheet(SHEET_GIMNASIO)
        for r in rows:
            fecha = r.get("fecha", "")
            if not fecha:
                continue
            reps = int(r.get("reps") or 0)
            peso = float(r.get("peso_kg") or 0)
            d = datetime.strptime(fecha, "%Y-%m-%d")
            week_start = d - timedelta(days=d.weekday())
            wk = week_start.strftime("%Y-%m-%d")
            gym_weekly[wk] = gym_weekly.get(wk, 0) + (reps * peso)
    except Exception as e:
        print(f"[CORR] Gym error: {e}")

    # --- Running weekly data ---
    run_weekly = {}
    run_all_paces = []
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            activities = garmin_client.fetch_running_activities(days_back=180)
            for a in activities:
                fecha = a.get("fecha", "")
                if not fecha:
                    continue
                dist = float(a.get("distancia_km", 0) or 0)
                pace = float(a.get("pace_min_km", 0) or 0)
                d = datetime.strptime(fecha, "%Y-%m-%d")
                week_start = d - timedelta(days=d.weekday())
                wk = week_start.strftime("%Y-%m-%d")
                if wk not in run_weekly:
                    run_weekly[wk] = {"dist": 0, "paces": []}
                run_weekly[wk]["dist"] += dist
                if pace > 0:
                    run_weekly[wk]["paces"].append(pace)
                    run_all_paces.append({"fecha": fecha, "pace": pace})
        except Exception as e:
            print(f"[CORR] Run error: {e}")

    # Correlation 1: Running vs Gym
    common_weeks = set(gym_weekly.keys()) & set(run_weekly.keys())
    for wk in sorted(common_weeks):
        result["running_vs_gym"].append({
            "semana": wk,
            "km_running": round(run_weekly[wk]["dist"], 1),
            "vol_gym": round(gym_weekly[wk]),
        })

    # Correlation 2: Body comp vs pace
    if ANTRO_PARSER_AVAILABLE and run_all_paces:
        try:
            antro = antro_parser.parse_all_pdfs()
            for measurement in (antro or []):
                fecha_m = measurement.get("fecha", "")
                adiposa = float(measurement.get("masa_adiposa_kg", 0) or 0)
                if not fecha_m or adiposa <= 0:
                    continue
                # Find average pace in the week of this measurement
                d = datetime.strptime(fecha_m, "%Y-%m-%d")
                week_start = d - timedelta(days=d.weekday())
                wk = week_start.strftime("%Y-%m-%d")
                if wk in run_weekly and run_weekly[wk]["paces"]:
                    avg_pace = sum(run_weekly[wk]["paces"]) / len(run_weekly[wk]["paces"])
                    result["composicion_vs_pace"].append({
                        "fecha": fecha_m,
                        "masa_adiposa": adiposa,
                        "pace_prom_semana": round(avg_pace, 2),
                    })
        except Exception as e:
            print(f"[CORR] Body vs pace error: {e}")

    # Correlation 3: Volume vs resting HR (placeholder — requires Garmin daily stats)
    # Not yet available in garmin_client, included for API completeness

    return jsonify({"ok": True, "data": result})

@app.route("/api/sueno")
def api_sueno():
    """Datos de sueño desde Garmin Connect."""
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            data = garmin_client.fetch_sleep_data(days_back=28)
            return jsonify({"ok": True, "data": data, "source": "garmin"})
        except Exception as e:
            print(f"[SLEEP] Error: {e}")
    return jsonify({"ok": True, "data": [], "source": "none"})

@app.route("/api/all")
def api_all():
    """Endpoint que devuelve todo junto para cargar el dashboard de una sola vez."""
    result = {}
    sources = {}

    # Gimnasio: SQLite primero, fallback a Sheets
    try:
        gym_rows = read_gym_as_rows()
        if gym_rows:
            result["gimnasio"] = gym_rows
            sources["gimnasio"] = "sqlite"
        else:
            result["gimnasio"] = read_sheet(SHEET_GIMNASIO)
            sources["gimnasio"] = "sheets"
    except Exception as e:
        result["gimnasio"] = []
        sources["gimnasio"] = "error"
        print(f"[GYM] Error: {e}")

    # Running: Garmin primero, fallback a Sheets
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            data = garmin_client.fetch_running_activities(days_back=180)
            if data:
                result["running"] = data
                sources["running"] = "garmin"
            else:
                raise ValueError("Sin datos de Garmin")
        except Exception:
            try:
                result["running"] = read_sheet(SHEET_RUNNING)
                sources["running"] = "sheets"
            except Exception:
                result["running"] = []
                sources["running"] = "none"
    else:
        try:
            result["running"] = read_sheet(SHEET_RUNNING)
            sources["running"] = "sheets"
        except Exception:
            result["running"] = []
            sources["running"] = "none"

    # Antropometría: PDF parser primero, fallback a Sheets
    if ANTRO_PARSER_AVAILABLE:
        try:
            data = antro_parser.parse_all_pdfs()
            if data:
                result["antropometria"] = data
                sources["antropometria"] = "pdf"
            else:
                raise ValueError("Sin datos de PDFs")
        except Exception:
            try:
                result["antropometria"] = read_sheet(SHEET_ANTROPO)
                sources["antropometria"] = "sheets"
            except Exception:
                result["antropometria"] = []
                sources["antropometria"] = "none"
    else:
        try:
            result["antropometria"] = read_sheet(SHEET_ANTROPO)
            sources["antropometria"] = "sheets"
        except Exception:
            result["antropometria"] = []
            sources["antropometria"] = "none"

    # Sueño: solo desde Garmin (7 días en /api/all para velocidad, 28 en /api/sueno)
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        try:
            data = garmin_client.fetch_sleep_data(days_back=7)
            result["sueno"] = data
            sources["sueno"] = "garmin" if data else "none"
        except Exception:
            result["sueno"] = []
            sources["sueno"] = "none"
    else:
        result["sueno"] = []
        sources["sueno"] = "none"

    return jsonify({
        "ok": True,
        "data": result,
        "sources": sources,
        "updated_at": datetime.now().isoformat()
    })

# ─────────────────────────────────────────────

DEBUG = True

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  MANU///LOGS - servidor")
    print("="*50)

    # Con dos backends posibles, lo primero que hay que saber es en cual
    # estas escribiendo. Nunca imprimir la URL entera: lleva la password.
    if models.ES_POSTGRES:
        host = models.DATABASE_URL.split("@")[-1].split("/")[0]
        print(f"  Base: Postgres @ {host}")
    else:
        print(f"  Base: SQLite ({os.path.basename(models.DATABASE_URL)})")
    if not models.DB_LISTA:
        print(f"        [X] NO CONECTA: {models.DB_ERROR}")

    print(f"  Sheet ID: {SPREADSHEET_ID}")
    print(f"  Credenciales: {CREDENTIALS_FILE}")

    # Status de Garmin
    if GARMIN_AVAILABLE and garmin_client.is_configured():
        print("  Garmin Connect: [OK] CONFIGURADO")
    elif GARMIN_AVAILABLE:
        print("  Garmin Connect: [X] Corre 'python garmin_setup.py' para conectar")
    else:
        print("  Garmin Connect: [X] pip install garminconnect")

    # Status de PDFs
    if ANTRO_PARSER_AVAILABLE:
        pdfs = antro_parser.parse_all_pdfs()
        print(f"  PDFs antropometria: {len(pdfs)} encontrados")
    else:
        print("  PDFs antropometria: [X] pip install pdfplumber")

    # Bot de Telegram — en su propio thread.
    # Con debug=True el reloader corre este script en DOS procesos (padre
    # observador + hijo que sirve). Solo el hijo tiene WERKZEUG_RUN_MAIN=true.
    # Sin esta guarda hay dos pollers y Telegram devuelve 409 Conflict.
    es_hijo_del_reloader = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    arrancar_bot = es_hijo_del_reloader or not DEBUG

    if TELEGRAM_AVAILABLE and telegram_bot.esta_configurado():
        if arrancar_bot:
            import threading
            threading.Thread(target=telegram_bot.correr, daemon=True).start()
            print("  Telegram: [OK] bot escuchando")
        else:
            print("  Telegram: [OK] configurado (arranca en el proceso hijo)")
    elif TELEGRAM_AVAILABLE:
        print("  Telegram: [X] falta TELEGRAM_BOT_TOKEN / USER_ID en .env")
    else:
        print("  Telegram: [X] pip install requests anthropic")

    print("\n  Abri el dashboard en:")
    print(f"  -> http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=DEBUG, port=5000)
