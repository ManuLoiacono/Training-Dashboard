# MANU///LOGS — Guía para Claude

Dashboard personal de rendimiento deportivo. Agrega datos de **gimnasio**, **running**,
**composición corporal** y **sueño** en una sola vista local.

Usuario: Manuel (22, Argentina). Entrena a diario. El dashboard se usa **en el celular
dentro del gimnasio**, así que velocidad y simplicidad importan más que features.

---

## Cómo correrlo

```bash
python server1.py
```

Abre en **http://localhost:5000**. Flask dev server con `debug=True` (auto-reload).
El banner de arranque imprime el estado de Garmin y cuántos PDFs encontró — usalo
para diagnosticar antes de tocar código.

Dependencias (ya instaladas en la máquina de Manuel):
`flask flask-cors python-dotenv sqlalchemy google-api-python-client google-auth
garminconnect pdfplumber`

---

## Arquitectura

```
server1.py          Backend Flask — TODOS los endpoints. No hay otro server.
models.py           SQLAlchemy: Ejercicio, Sesion, Serie + helpers. Auto-init al importar.
dashboard_v3.html   Frontend COMPLETO (~3700 líneas): HTML + CSS + JS en un solo archivo.
garmin_client.py    Wrapper de Garmin Connect (running + sueño) vía garth.
garmin_setup.py     Auth de Garmin, se corre una sola vez. Tokens en ~/.garth/
antro_parser.py     Parser de PDFs ISAK (composición corporal) con pdfplumber.
migrate_sheets.py   Script one-shot: migró gimnasio de Google Sheets → SQLite. Ya usado.
manu_logs.db        SQLite (gitignored).
pdfs/               Informes antropométricos (gitignored, datos personales).
credentials.json    Service account de Google Sheets (gitignored).
```

### Flujo de datos y fallbacks

Cada fuente tiene una cadena de fallback. **Nunca crashear**: si todo falla, devolver `[]`.

| Dominio | Fuente primaria | Fallback |
|---|---|---|
| Gimnasio | SQLite (`read_gym_as_rows()`) | Google Sheets hoja `gimnasio` |
| Running | Garmin Connect | Google Sheets hoja `running` |
| Composición | PDFs en `pdfs/` | Google Sheets hoja `antropometria` |
| Sueño | Garmin Connect | ninguno (devuelve `[]`) |

Google Sheets quedó como **fallback legacy** para running y antropometría. El gimnasio
ya vive en SQLite; el fallback a Sheets solo dispara si la DB está vacía.

`garmin_client` y `antro_parser` se importan en try/except — el server arranca igual
si faltan (`GARMIN_AVAILABLE` / `ANTRO_PARSER_AVAILABLE`).

### Modelo de datos

```
Ejercicio  id, nombre (unique, UPPERCASE), grupo_muscular (UPPERCASE), notas
Sesion     id, fecha (Date), dia_rutina (Int), notas
Serie      id, sesion_id → Sesion, ejercicio_id → Ejercicio,
           numero_serie (auto-calculado), reps, peso_kg, notas
```

`Sesion.series` tiene `cascade="all, delete-orphan"`. Foreign keys activadas por
PRAGMA en SQLite. Nombres de ejercicio y grupo se normalizan a MAYÚSCULAS al crear.

`read_gym_as_rows()` aplana la DB al formato viejo de Sheets
(`fecha | dia | ejercicio | grupo_muscular | serie | reps | peso_kg | notas`) para que
el frontend y los endpoints de análisis no tengan que cambiar.

---

## Endpoints (todos en `server1.py`)

**Lectura agregada**
- `GET /` → sirve `dashboard_v3.html` con headers no-cache
- `GET /api/all` → todo junto (gimnasio, running, antropometria, sueno) + `sources` de dónde salió cada uno. **Es la carga principal del dashboard.**
- `GET /api/gimnasio` · `/api/running` · `/api/antropometria` · `/api/sueno`

**CRUD gimnasio (SQLite)**
- `GET/POST /api/gym/ejercicios` — catálogo
- `GET/POST /api/gym/sesiones` — lista (`?semanas=N`, default 12) / crear
- `GET /api/gym/sesiones/<id>` — detalle con series
- `POST /api/gym/series` · `PUT/DELETE /api/gym/series/<id>`
- `GET /api/gym/ejercicio/<nombre>` — historial, PRs, 1RM, tendencia (desde SQLite)

**Análisis**
- `GET /api/score` — score semanal 1-100, ponderado
- `GET /api/ejercicio/<nombre>` — versión legacy vía `read_gym_as_rows()`; `/api/gym/ejercicio/<nombre>` es la que usa el frontend
- `GET /api/correlaciones` — running vs gym, composición vs pace

Convención de respuesta: `{"ok": bool, "data": ..., "error": str}`.

### Score semanal

Sin sueño: gym 40% · running 35% · composición 25%
Con sueño: gym 35% · running 30% · composición 20% · sueño 15%

Los pesos se **redistribuyen** si falta una fuente (se divide por `total_weight`).
Gym = volumen de la semana vs promedio de las 4 anteriores. Running = pace promedio
histórico / pace reciente. Composición = `(masa_muscular/masa_adiposa - 1.0) * 50`.
1RM estimado con fórmula de Epley: `peso_max * (1 + reps/30)`.

---

## Frontend

`dashboard_v3.html` — **un solo archivo, siempre**. HTML, CSS y JS juntos.

Tabs: `OVERVIEW · GIMNASIO · RUNNING · COMPOSICIÓN · ENTRENO · SUEÑO · ANÁLISIS`
(`switchTab('overview'|'gym'|'running'|'body'|'entreno'|'sleep'|'analysis')`).

**ENTRENO** es el tab de carga de datos, diseñado para el celular con la **regla de 3 taps**:
elegir sesión → ejercicio → cargar serie. Es el único tab que escribe.

Librerías externas: Chart.js 4.4.1 (CDN) y Google Fonts. Nada más.

### Sistema de diseño — NO cambiar sin pedido explícito

```
--bg #0a0a0a   --surface #111111  --surface2 #181818  --border #222222
--accent #c8f135 (lima)  --accent2 #35f1c8 (teal)  --accent3 #f13580 (rosa)
--text #e8e8e8  --muted #555  --muted2 #333
```

Tipografías: **Bebas Neue** (títulos), **DM Mono** (números/datos), **DM Sans** (texto).

---

## Reglas del proyecto

Estas restricciones vienen del usuario y ya causaron correcciones. Respetarlas.

1. **No cambiar el sistema visual** (colores, fuentes) sin instrucción explícita.
2. **Frontend en un solo archivo HTML.** No crear `.css` ni `.js` separados.
3. **Toda la lógica de DB va en `models.py`.** `server1.py` importa y consulta.
4. **SQLAlchemy core, NO Flask-SQLAlchemy** — mantiene portabilidad para migrar a Supabase.
5. **Todo endpoint maneja datos faltantes con gracia**: devolver `[]` o `{"ok": false, "error": ...}`, nunca crashear.
6. **El dashboard debe funcionar offline** después de cualquier cambio.
7. **No agregar librerías frontend** (ni alternativas a Chart.js) sin preguntar.
8. **No romper tabs existentes** al agregar features.
9. Todo el texto de UI y los comentarios de código van **en español**.

---

## Estado actual

Todo lo del roadmap (F-01 a F-04) está implementado y la migración a SQLite está hecha:

- F-01 score semanal · F-02 progresión por ejercicio · F-03 correlaciones cruzadas · F-04 sueño
- Gimnasio migrado de Google Sheets a SQLite, con CRUD completo y tab ENTRENO

DB al día de hoy: 23 ejercicios, 10 sesiones, 269 series. Garmin conectado, 2 PDFs parseados.

**Próximo paso planeado:** migración a Supabase (Fase 2) para acceso desde la nube.
Por eso el punto 4 de las reglas.

`README.md` documenta el setup **original** con Google Sheets y todavía dice `server.py`.
Está desactualizado respecto a SQLite — sirve solo como referencia histórica del setup
de Google Cloud / service account.

---

## Notas operativas

- El archivo se llama `server1.py`, no `server.py`. Varios docstrings viejos siguen diciendo `server.py`.
- Config por variables de entorno vía `.env`: `SPREADSHEET_ID`, `CREDENTIALS_FILE`, `DATABASE_URL`.
- Gitignored y **nunca commitear**: `credentials.json`, `.env`, `*.db`, `pdfs/`.
- Tokens de Garmin en `~/.garth/`. Si expiran: `python garmin_setup.py`.
- Los PDFs antropométricos siguen protocolo ISAK; el parser extrae ~25 variables y
  ordena los resultados por fecha.
