"""
MANU///LOGS - Migracion Google Sheets -> SQLite
Ejecutar UNA sola vez: python migrate_sheets.py
"""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from models import get_session, Ejercicio, Sesion, Serie, init_db

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1ef2kn8q_8sgT96LsljUixN4ZBFNi0W1MsBuk9-NpZ4o")
CREDENTIALS_FILE = os.environ.get("CREDENTIALS_FILE", "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def read_sheet():
    """Lee la hoja 'gimnasio' de Google Sheets."""
    creds = Credentials.from_service_account_file(
        os.path.join(BASE_DIR, CREDENTIALS_FILE), scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()
    result = service.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="gimnasio!A:Z"
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    headers = values[0]
    rows = []
    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        rows.append(dict(zip(headers, padded)))
    return rows


def migrate():
    init_db()
    session = get_session()

    print("\n" + "=" * 50)
    print("  MANU///LOGS - Migracion Sheets -> SQLite")
    print("=" * 50)

    # 1. Leer datos de Sheets
    print("\n[1/4] Leyendo Google Sheets...")
    rows = read_sheet()
    print(f"       {len(rows)} filas encontradas")

    if not rows:
        print("       No hay datos para migrar.")
        return

    # 2. Crear ejercicios
    print("[2/4] Creando catálogo de ejercicios...")
    ejercicios_map = {}  # nombre → Ejercicio obj
    for r in rows:
        nombre = (r.get("ejercicio") or "").strip().upper()
        grupo = (r.get("grupo_muscular") or "OTRO").strip().upper()
        if not nombre:
            continue
        if nombre not in ejercicios_map:
            existing = session.query(Ejercicio).filter(Ejercicio.nombre == nombre).first()
            if existing:
                ejercicios_map[nombre] = existing
            else:
                ej = Ejercicio(nombre=nombre, grupo_muscular=grupo)
                session.add(ej)
                session.flush()
                ejercicios_map[nombre] = ej

    session.commit()
    print(f"       {len(ejercicios_map)} ejercicios")

    # 3. Crear sesiones y series
    print("[3/4] Creando sesiones y series...")
    sesiones_map = {}  # (fecha, dia) → Sesion obj
    n_series = 0

    for r in rows:
        fecha_str = r.get("fecha", "").strip()
        if not fecha_str:
            continue

        nombre = (r.get("ejercicio") or "").strip().upper()
        if not nombre or nombre not in ejercicios_map:
            continue

        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except ValueError:
            print(f"       [WARN] Fecha inválida: {fecha_str}")
            continue

        dia = int(r.get("dia") or r.get("dia_rutina") or 1)
        key = (fecha_str, dia)

        if key not in sesiones_map:
            existing_sesion = (
                session.query(Sesion)
                .filter(Sesion.fecha == fecha, Sesion.dia_rutina == dia)
                .first()
            )
            if existing_sesion:
                sesiones_map[key] = existing_sesion
            else:
                s = Sesion(fecha=fecha, dia_rutina=dia)
                session.add(s)
                session.flush()
                sesiones_map[key] = s

        sesion_obj = sesiones_map[key]
        ejercicio_obj = ejercicios_map[nombre]

        reps = int(r.get("reps") or 0)
        peso = float(r.get("peso_kg") or 0)
        numero_serie = int(r.get("serie") or 1)
        notas = r.get("notas", "")

        serie = Serie(
            sesion_id=sesion_obj.id,
            ejercicio_id=ejercicio_obj.id,
            numero_serie=numero_serie,
            reps=reps,
            peso_kg=peso,
            notas=notas,
        )
        session.add(serie)
        n_series += 1

    session.commit()

    # 4. Resumen
    print("[4/4] Migración completa!")
    print(f"\n  Resumen:")
    print(f"  - {len(ejercicios_map)} ejercicios")
    print(f"  - {len(sesiones_map)} sesiones")
    print(f"  - {n_series} series")
    print("=" * 50 + "\n")

    session.close()


if __name__ == "__main__":
    migrate()
