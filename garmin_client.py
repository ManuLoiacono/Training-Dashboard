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
