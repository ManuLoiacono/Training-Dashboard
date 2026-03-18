"""
MANU///LOGS — servidor local
Corre con: python server.py
Abre en el browser: http://localhost:5000
"""

import json
import os
from datetime import datetime
from flask import Flask, jsonify, send_file, send_from_directory
from flask_cors import CORS
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ─────────────────────────────────────────────
# Módulos opcionales: Garmin Connect y PDF parser
# ─────────────────────────────────────────────

try:
    import garmin_client
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False

try:
    import antro_parser
    ANTRO_PARSER_AVAILABLE = True
except ImportError:
    ANTRO_PARSER_AVAILABLE = False

# ─────────────────────────────────────────────
# CONFIGURACIÓN — editá estos valores
# ─────────────────────────────────────────────

SPREADSHEET_ID = "1ef2kn8q_8sgT96LsljUixN4ZBFNi0W1MsBuk9-NpZ4o"
# El ID está en la URL de tu Sheet:
# https://docs.google.com/spreadsheets/d/[ESTE_ES_EL_ID]/edit

CREDENTIALS_FILE = "credentials.json"
# Archivo de credenciales que descargás de Google Cloud Console
# (ver README.md para instrucciones paso a paso)

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
    Lee la hoja 'gimnasio' y devuelve los datos procesados.
    Estructura esperada del sheet:
    fecha | dia | ejercicio | grupo_muscular | serie | reps | peso_kg | notas
    """
    try:
        rows = read_sheet(SHEET_GIMNASIO)
        return jsonify({ "ok": True, "data": rows })
    except Exception as e:
        return jsonify({ "ok": False, "error": str(e) }), 500

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

@app.route("/api/all")
def api_all():
    """Endpoint que devuelve todo junto para cargar el dashboard de una sola vez."""
    result = {}
    sources = {}

    # Gimnasio: siempre de Google Sheets
    try:
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

    return jsonify({
        "ok": True,
        "data": result,
        "sources": sources,
        "updated_at": datetime.now().isoformat()
    })

# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  MANU///LOGS - servidor local")
    print("="*50)
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

    print("\n  Abri el dashboard en:")
    print(f"  -> http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
