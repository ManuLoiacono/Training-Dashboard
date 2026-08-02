"""
MANU///LOGS — Garmin Connect client
Importado por server.py para proveer datos de running.
Requiere ejecutar garmin_setup.py una vez para autenticar.
"""

import os
from datetime import datetime, timedelta

GARTH_TOKEN_DIR = os.path.expanduser("~/.garth")


def is_configured() -> bool:
    """Verifica si existen tokens de Garmin en ~/.garth."""
    return os.path.isdir(GARTH_TOKEN_DIR) and len(os.listdir(GARTH_TOKEN_DIR)) > 0


def get_client():
    """Resume sesión de Garmin desde tokens guardados. Retorna None si no está configurado."""
    if not is_configured():
        return None
    try:
        from garminconnect import Garmin

        api = Garmin()
        api.garth.load(GARTH_TOKEN_DIR)
        # garth auto-refresca tokens; re-guardar después de cargar
        api.garth.dump(GARTH_TOKEN_DIR)
        return api
    except Exception as e:
        print(f"[GARMIN] Error al cargar sesión: {e}")
        return None


def fetch_running_activities(days_back: int = 180) -> list[dict]:
    """
    Descarga actividades de running de los últimos N días.
    Retorna lista de dicts en el formato esperado por el dashboard:
    {fecha, distancia_km, tiempo_min, pace_min_km, fc_prom, fc_max, desnivel_m, notas}
    """
    api = get_client()
    if api is None:
        return []

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    try:
        activities = api.get_activities_by_date(
            start_date, end_date, activitytype="running"
        )
    except Exception as e:
        print(f"[GARMIN] Error al descargar actividades: {e}")
        return []

    results = []
    for act in activities:
        transformed = _transform_activity(act)
        if transformed:
            results.append(transformed)

    return sorted(results, key=lambda r: r["fecha"])


def fetch_strength_activities(days_back: int = 30) -> list[dict]:
    """
    Actividades de fuerza ("Fuerza" en el reloj) de los últimos N días.

    El reloj NO registra series ni repeticiones (totalReps viene en 0): lo que
    aporta es la ventana temporal y el costo fisiológico. El contenido del
    entrenamiento sigue viniendo de los mensajes de Telegram.

    Retorna: {activity_id, inicio, fin, duracion_min, fc_prom, fc_max, calorias, nombre}
    con `inicio` y `fin` como datetime en hora local.
    """
    api = get_client()
    if api is None:
        return []

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Sin filtro de tipo: la API devuelve 400 con activitytype="strength_training"
    # (a diferencia de "running", que sí acepta). Se filtra acá por typeKey.
    try:
        activities = api.get_activities_by_date(start_date, end_date)
    except Exception as e:
        print(f"[GARMIN] Error al descargar actividades de fuerza: {e}", flush=True)
        return []

    results = []
    for act in activities:
        if (act.get("activityType") or {}).get("typeKey") != "strength_training":
            continue

        # startTimeLocal viene como "2026-07-25 17:12:56"
        raw = act.get("startTimeLocal") or ""
        try:
            inicio = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        duracion_sec = act.get("duration", 0) or 0
        results.append({
            "activity_id": str(act.get("activityId") or ""),
            "inicio": inicio,
            "fin": inicio + timedelta(seconds=duracion_sec),
            "duracion_min": round(duracion_sec / 60, 1),
            "fc_prom": round(act.get("averageHR") or 0),
            "fc_max": round(act.get("maxHR") or 0),
            "calorias": round(act.get("calories") or 0),
            "nombre": act.get("activityName") or "",
        })

    return sorted(results, key=lambda r: r["inicio"])


def buscar_actividad_fuerza(inicio, fin=None, tolerancia_min: int = 120) -> dict | None:
    """
    La actividad de fuerza que corresponde a una sesión que va de `inicio` a `fin`
    (datetimes locales). Matchea por solape de ventanas, con tolerancia para
    cubrir que el reloj se arranca y se corta a destiempo del bot.

    Si hay varias candidatas, gana la que empieza más cerca del inicio.
    Retorna None si no hay ninguna: entrenar sin el reloj es un caso válido.
    """
    if inicio is None:
        return None
    fin = fin or inicio

    # La ventana de búsqueda tiene que llegar hasta la fecha pedida: una sesión
    # de hace dos semanas no se encuentra mirando solo los últimos días.
    dias_atras = (datetime.now() - inicio).days + 2
    dias_atras = max(3, min(dias_atras, 365))

    margen = timedelta(minutes=tolerancia_min)
    candidatas = []
    for act in fetch_strength_activities(days_back=dias_atras):
        # Solape entre [inicio, fin] y la actividad, con margen a los costados
        if act["fin"] + margen >= inicio and act["inicio"] - margen <= fin:
            candidatas.append(act)

    if not candidatas:
        return None
    return min(candidatas, key=lambda a: abs((a["inicio"] - inicio).total_seconds()))


def fetch_sleep_data(days_back: int = 28) -> list[dict]:
    """
    Descarga datos de sueño de los últimos N días.
    Retorna lista de dicts: {fecha, duracion_hs, score, rem_min, deep_min, light_min, hrv_noche}
    """
    api = get_client()
    if api is None:
        return []

    results = []
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    current = start_date
    errors = 0

    while current <= end_date:
        fecha_str = current.strftime("%Y-%m-%d")
        try:
            sleep = api.get_sleep_data(fecha_str)
            transformed = _transform_sleep(sleep, fecha_str)
            if transformed:
                results.append(transformed)
        except Exception:
            errors += 1
            if errors >= 3:
                break  # Stop early if Garmin API is failing
        current += timedelta(days=1)

    return sorted(results, key=lambda r: r["fecha"])


def _transform_sleep(sleep_data: dict, fecha: str) -> dict | None:
    """Transforma datos de sueño de Garmin al formato del dashboard."""
    if not sleep_data:
        return None

    daily = sleep_data.get("dailySleepDTO", {})
    if not daily:
        return None

    # Duración total en segundos
    duration_sec = daily.get("sleepTimeSeconds", 0) or 0
    if duration_sec <= 0:
        return None

    duration_hs = round(duration_sec / 3600, 1)

    # Sleep score (Garmin calculated)
    score = daily.get("sleepScores", {}).get("overall", {}).get("value", 0) or 0

    # Fases en segundos → minutos
    rem_sec = daily.get("remSleepSeconds", 0) or 0
    deep_sec = daily.get("deepSleepSeconds", 0) or 0
    light_sec = daily.get("lightSleepSeconds", 0) or 0

    # HRV nocturno (si disponible)
    hrv = daily.get("averageHRV", 0) or 0

    return {
        "fecha": fecha,
        "duracion_hs": str(duration_hs),
        "score": str(score),
        "rem_min": str(round(rem_sec / 60)),
        "deep_min": str(round(deep_sec / 60)),
        "light_min": str(round(light_sec / 60)),
        "hrv_noche": str(hrv),
    }


def _transform_activity(act: dict) -> dict | None:
    """Transforma una actividad de Garmin al formato del dashboard."""
    # Garmin retorna distancia en metros, duración en segundos
    distance_m = act.get("distance", 0) or 0
    distance_km = round(distance_m / 1000, 2)

    if distance_km <= 0:
        return None

    duration_sec = act.get("duration", 0) or 0
    duration_min = round(duration_sec / 60, 1)

    # Pace = min/km
    pace = round(duration_min / distance_km, 2) if distance_km > 0 else 0

    # Frecuencia cardíaca
    avg_hr = act.get("averageHR", 0) or 0
    max_hr = act.get("maxHR", 0) or 0

    # Desnivel
    elevation = round(act.get("elevationGain", 0) or 0)

    # Fecha: Garmin retorna startTimeLocal como "2025-09-03 07:30:00"
    start_time = act.get("startTimeLocal", "")
    fecha = start_time[:10] if start_time else ""

    # Notas: usar el nombre de la actividad
    notas = act.get("activityName", "")

    return {
        "fecha": fecha,
        "distancia_km": str(distance_km),
        "tiempo_min": str(duration_min),
        "pace_min_km": str(pace),
        "fc_prom": str(avg_hr),
        "fc_max": str(max_hr),
        "desnivel_m": str(elevation),
        "notas": notas,
    }
