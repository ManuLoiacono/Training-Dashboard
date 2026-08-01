# MANU///LOGS — Guía para Claude

Dashboard personal de rendimiento deportivo. Agrega datos de **gimnasio**, **running**,
**composición corporal** y **sueño** en una sola vista local.

Usuario: Manuel (22, Argentina). Entrena a diario. El dashboard se usa **en el celular
dentro del gimnasio**, así que velocidad y simplicidad importan más que features.

---

## Cómo correrlo

```bash
python server.py
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
server.py          Backend Flask — TODOS los endpoints. No hay otro server.
models.py           SQLAlchemy: Ejercicio, Sesion, Serie + helpers. Auto-init al importar.
dashboard_v3.html   Frontend COMPLETO (~3700 líneas): HTML + CSS + JS en un solo archivo.
garmin_client.py    Wrapper de Garmin Connect (running + sueño) vía garth.
garmin_setup.py     Auth de Garmin, se corre una sola vez. Tokens en ~/.garth/
antro_parser.py     Parser de PDFs ISAK (composición corporal) con pdfplumber.
migrate_sheets.py   Script one-shot: migró gimnasio de Google Sheets → SQLite. Ya usado.
telegram_bot.py     Bot de Telegram: long polling, whitelist, deja mensajes en la bandeja.
message_parser.py   Convierte texto libre en series estructuradas usando Claude.
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

`garmin_client`, `antro_parser` y `telegram_bot` se importan en try/except — el server
arranca igual si faltan (`GARMIN_AVAILABLE` / `ANTRO_PARSER_AVAILABLE` / `TELEGRAM_AVAILABLE`).

### Modelo de datos

```
Ejercicio  id, nombre (unique, UPPERCASE), grupo_muscular (UPPERCASE), notas
Sesion     id, fecha (Date), dia_rutina (Int, OPCIONAL), notas
Serie      id, sesion_id → Sesion, ejercicio_id → Ejercicio,
           numero_serie (auto-calculado), reps, peso_kg, notas

MensajeParseado  id, telegram_message_id, texto_original, parse_json,
                 estado (parseado|error|confirmado|descartado), error, recibido_en
```

**`dia_rutina` es opcional a propósito.** Manuel no siempre respeta la rotación 1-2-3, y
un día mal etiquetado es peor que ninguno. Sin valor, la sesión no entra en el gráfico de
distribución por día y en la tabla aparece un `—`. **No poner un default de 1** — el
frontend hacía `parseInt(row.dia) || 1` y eso metía sesiones sin etiquetar en el balde del
DÍA 1; ya está corregido a `|| null`.

`Sesion.series` tiene `cascade="all, delete-orphan"`. Foreign keys activadas por
PRAGMA en SQLite. Nombres de ejercicio y grupo se normalizan a MAYÚSCULAS al crear.

`read_gym_as_rows()` aplana la DB al formato viejo de Sheets
(`fecha | dia | ejercicio | grupo_muscular | serie | reps | peso_kg | notas`) para que
el frontend y los endpoints de análisis no tengan que cambiar.

---

## Endpoints (todos en `server.py`)

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

**Bandeja de Telegram**
- `GET /api/mensajes` — últimos mensajes parseados (`?limite=N`, default 20) + `bot_activo`

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

## Carga por Telegram

Manuel manda un mensaje informal al bot y el sistema lo convierte en series.
Nació porque el tab ENTRENO resultó demasiado engorroso: estuvo **4 meses sin cargar
nada** (última sesión 21/03/2026) y quedaron sesiones abiertas y abandonadas.

**Telegram y no WhatsApp** por una razón técnica concreta: el bot usa **long polling**,
así que el server local no necesita URL pública ni túnel. WhatsApp Cloud API exige un
webhook HTTPS público. Además Telegram encola los updates ~24hs — si la PC está apagada
durante el entrenamiento, los mensajes entran al prender.

### Flujo

```
mensaje → whitelist por user ID → Claude (structured output) → tabla mensajes_parseados
        → eco de confirmación al chat        → panel en el tab ENTRENO
```

**No escribe en `sesiones`/`series` todavía.** Es una bandeja de entrada: primero se
valida que el parser acierte con mensajes reales, después se agrega el botón de confirmar.

### El parser (`message_parser.py`)

Modelo **`claude-haiku-4-5`**. Es extracción contra un catálogo fijo, no razonamiento.

- Usa `client.messages.parse()` con un esquema **Pydantic** (`MensajeParse`) — structured
  outputs garantiza JSON válido, así que no hay regex sobre la salida ni reintentos.
- **Sin `effort` y sin `thinking`**: Haiku 4.5 rechaza `effort`, y para esta tarea no aporta.
- El catálogo de ejercicios se inyecta en el system prompt en cada llamada, con los IDs.
  El modelo devuelve `ejercicio_id`, nunca un nombre libre.
- **Nunca crear un ejercicio automáticamente.** Si no matchea: `ejercicio_id: null`,
  `confianza: "baja"` y lo explica en `ambiguedad`. Esta regla existe porque la entrada
  libre ya había partido un ejercicio en dos (`PB INCL MANC` / `PB INCLINADO MANC.`).
- Costo: ~1000 tokens de entrada + ~150 de salida por mensaje, centavos al mes. El prompt
  caching **no sirve acá** — Haiku necesita 4096 tokens de prefijo y el prompt ronda los 1000.

Probarlo suelto: `python message_parser.py "BP 75 x 3 x 6,4,3"`

### El bot (`telegram_bot.py`)

- Long polling con `requests` contra la Bot API. Sin librería de Telegram: es HTTP plano.
- **Whitelist obligatoria** por `TELEGRAM_ALLOWED_USER_ID`. Los bots son descubribles por
  username; sin el filtro cualquiera escribe en la base y gasta la API key.
- El último `update_id` se guarda en `.telegram_offset` (gitignored) para no reprocesar.
- Corre en un thread lanzado desde `server.py`.

> ⚠️ **Footgun del reloader.** Con `debug=True`, Flask ejecuta el script en **dos
> procesos**. Sin guarda se levantan dos pollers y Telegram devuelve **409 Conflict**.
> La guarda es `os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not DEBUG`. Ojo: **no
> sirve `app.debug`**, que todavía es `False` cuando se evalúa el bloque `__main__`.
> Verificar siempre que aparezca **una sola** línea `[TG] Conectado como`.

### Seguridad

Las credenciales viven en `.env` (gitignored) y se leen de `os.environ` — nunca van
hardcodeadas ni pasan por el chat. Rotación: `/revoke` en @BotFather para el token del bot,
consola de Anthropic para la API key.

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
3. **Toda la lógica de DB va en `models.py`.** `server.py` importa y consulta.
4. **SQLAlchemy core, NO Flask-SQLAlchemy** — mantiene portabilidad para migrar a Supabase.
5. **Todo endpoint maneja datos faltantes con gracia**: devolver `[]` o `{"ok": false, "error": ...}`, nunca crashear.
6. **El dashboard debe funcionar offline** después de cualquier cambio.
7. **No agregar librerías frontend** (ni alternativas a Chart.js) sin preguntar.
8. **No romper tabs existentes** al agregar features.
9. Todo el texto de UI y los comentarios de código van **en español**.

---

## Flujo de trabajo git

Este repo lo edita Claude, en sesiones que no comparten memoria entre sí. El protocolo
existe para que ninguna sesión pise a otra ni trabaje sobre una copia vieja.

**Al empezar cualquier tarea, siempre:**

```bash
git fetch
git status
```

`master` local tiene que estar en el **mismo commit** que `origin/master`. Si está atrás,
`git checkout master && git merge --ff-only origin/master` antes de tocar nada.
Ya pasó una vez que el local quedó 3 meses desactualizado y se documentó un archivo con
el nombre viejo — de ahí viene esta regla.

**Nunca commitear directo en `master`.** Una rama por tarea, creada desde un `master`
fresco:

```bash
git checkout -b feat/nombre-corto master
```

Prefijos: `feat/` · `fix/` · `refactor/` · `docs/`

**Ramas cortas.** Al terminar la tarea: merge a `master`, push, borrar la rama. No dejar
ramas vivas entre sesiones — una rama larga es una divergencia silenciosa esperando pasar.

```bash
git checkout master && git merge feat/nombre-corto
git push origin master
git branch -d feat/nombre-corto
```

**Nunca commitear** `credentials.json`, `.env`, `*.db` ni `pdfs/` — están en `.gitignore`
y contienen datos personales o secretos. Verificar con `git status` antes de `git add -A`.

**Verificar antes de cerrar:** levantar `python server.py` y confirmar que `/` y
`/api/all` devuelven 200, y que el banner muestra Garmin y los PDFs.

---

## Estado actual

Roadmap F-01 a F-04 implementado, migración a SQLite hecha, y carga por Telegram andando
punta a punta (verificada con mensajes reales el 01/08/2026).

- F-01 score semanal · F-02 progresión por ejercicio · F-03 correlaciones cruzadas · F-04 sueño
- Gimnasio en SQLite con CRUD completo y tab ENTRENO
- Bot de Telegram + parser con Claude → bandeja de entrada (todavía sin confirmar)

DB: 22 ejercicios, 9 sesiones, 137 series. Garmin conectado, 2 PDFs parseados.

### Limpieza de datos del 31/07/2026

Tres arreglos, con backup en `manu_logs.db.backup-20260731-234805`:

1. **132 series duplicadas eliminadas** (269 → 137). La migración desde Sheets había
   insertado cada fila dos veces, en las 7 sesiones migradas. **El volumen estaba al
   doble**: 76.063 kg reportados vs 38.474 kg reales. Los PRs de peso y de volumen no
   estaban afectados (son máximos por serie, no sumas), y el score semanal tampoco
   (compara ratios, y ambos lados estaban duplicados).
2. **`PB INCLINADO MANC.` fusionado en `PB INCL MANC`** — mismo ejercicio con el historial
   partido. Se conservó el nombre corto por consistencia con `PM MANC`, `SQ`, `RDL`.
3. **`dia_rutina` pasó a opcional** — requirió recrear la tabla `sesiones`, porque SQLite
   tenía el `NOT NULL` grabado y cambiar el modelo no alcanza.

Si aparecen números de volumen que no cierran con datos viejos, es por (1).

### Sobre el 21/03/2026

Las 7 sesiones migradas tienen fechas concentradas y algunas con muchas series
(la 5 tiene 33). Puede ser un artefacto de la migración desde Sheets, sin confirmar.
**No tocar sin preguntar** — las sesiones 9 y 10 de esa fecha son carga manual real
desde ENTRENO, con pesos que no aparecen en ninguna otra.

---

## Plan — próximos pasos

Estado al 01/08/2026. Los pasos están en orden: **1 es el siguiente**, y 2 depende de
haber usado el bot un tiempo. Cada uno lista las decisiones que **hay que preguntarle a
Manuel** antes de codear — no asumirlas.

### Paso 0 — antes de empezar cualquier cosa

Preguntarle **cómo se portó el bot**. Todo el paso 1 depende de que el parser sea
confiable, y la única forma de saberlo son sus mensajes reales. Para ver qué entró:

```bash
curl -s "http://localhost:5000/api/mensajes?limite=50"
```

Los mensajes guardan `texto_original` junto al parse, así que se puede auditar dónde
falló sin depender de que él se acuerde.

### Paso 1 — botón de confirmar (el siguiente)

Pasar de `mensajes_parseados` a `sesiones` + `series` de verdad. Es lo que convierte la
bandeja en carga real.

**Alcance:**
- `POST /api/mensajes/<id>/confirmar` → crea las series, marca `estado="confirmado"`
- `POST /api/mensajes/<id>/descartar` → `estado="descartado"`, no escribe nada
- Botones en el panel del tab ENTRENO (ya existe, hoy es de solo lectura)
- `numero_serie` se calcula solo, igual que en `POST /api/gym/series`

**Decisiones pendientes — preguntar antes de codear:**

1. **¿Varios mensajes del mismo día son una sesión o varias?** Si manda banca, después
   dominadas y después remo en tres mensajes, lo natural es **una sesión por fecha**,
   reusando la del día si existe. Confirmarlo — afecta cómo quedan agrupados los datos.
2. **¿Qué fecha usa?** La de recepción del mensaje (`recibido_en`) es lo simple, pero si
   carga a la noche lo del día anterior queda mal. ¿Se puede decir "ayer" en el mensaje?
3. **¿Qué pasa con un ejercicio sin match?** Hoy es un callejón sin salida. Opciones:
   bloquear la confirmación hasta resolverlo, o dejar confirmar solo los que matchearon.
   **No crear el ejercicio automáticamente** — eso es lo que partió `PB INCL MANC` en dos.

### Paso 2 — ajustes del parser con datos reales

Recién después de usarlo. Tomar los mensajes donde falló y ajustar el prompt de
`message_parser.py`. Un nit ya detectado: con un conteo de series incoherente
("3 series" pero 4 reps listadas) devuelve `confianza: "alta"` aunque lo avise en
`ambiguedad`; debería ser `"baja"`.

### Paso 3 — alta de ejercicios desde el bot

Hoy `hip thrust` no se puede cargar de ninguna forma vía Telegram. Flujo propuesto: el
bot detecta el sin-match y **pregunta** ("¿lo agrego como HIP THRUST, grupo PIERNA?"),
y recién con el sí explícito lo crea. Mantiene la regla de no inventar, pero destraba
el caso.

### Paso 4 — Supabase (Fase 2)

El plan viejo, y ahora tiene una razón extra: con el bot en un servidor always-on los
mensajes entrarían al instante en vez de esperar a que se prenda la PC. Hoy con polling
llegan cuando arranca `server.py` (Telegram los guarda ~24hs, no se pierde nada).
Por esto es la regla 4 — SQLAlchemy core, nada de Flask-SQLAlchemy.

### Deuda conocida (no urgente)

- **No hay `requirements.txt`.** Las dependencias están listadas en el README pero sin
  fijar versiones.
- **`/api/ejercicio/<nombre>` es un duplicado legacy** de `/api/gym/ejercicio/<nombre>`.
  El frontend usa el segundo. Se puede borrar el primero.
- **Las fechas del 21/03/2026** siguen sin verificar (ver sección de arriba).

---

## Notas operativas

- El backend se llamó `server1.py` por un tiempo y volvió a `server.py` (commit `ff2b2b7`).
  Si ves `server1.py` en algún lado, es una referencia vieja.
- Config por `.env` (plantilla completa en `.env.example`): `SPREADSHEET_ID`,
  `CREDENTIALS_FILE`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`,
  `ANTHROPIC_API_KEY`.
- Gitignored y **nunca commitear**: `credentials.json`, `.env`, `*.db`, `pdfs/`,
  `.telegram_offset`.
- Para chequear que las credenciales estén cargadas **sin exponerlas**, verificar
  presencia y forma (`bool(os.environ.get(...))`, longitud, regex) — nunca imprimir
  el valor ni pedírselo al usuario por chat.
- Tokens de Garmin en `~/.garth/`. Si expiran: `python garmin_setup.py`.
- Los PDFs antropométricos siguen protocolo ISAK; el parser extrae ~25 variables y
  ordena los resultados por fecha.
