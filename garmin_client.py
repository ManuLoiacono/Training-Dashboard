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
