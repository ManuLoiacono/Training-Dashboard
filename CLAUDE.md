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
Sesion     id, fecha (Date), dia_rutina (Int, OPCIONAL), nombre (Text, OPCIONAL),
           notas, estado (abierta|cerrada), iniciada_en, cerrada_en,
           pregunta_cierre_en, garmin_activity_id
Serie      id, sesion_id → Sesion, ejercicio_id → Ejercicio,
           numero_serie (auto-calculado), reps, peso_kg, notas

MensajeParseado  id, telegram_message_id, texto_original, parse_json,
                 estado (parseado|error|confirmado|parcial|descartado|aplicado),
                 error, tipo (series|inicio|fin|respuesta|otro),
                 sesion_id → Sesion, recibido_en

PreguntaPendiente  id, tipo (cierre_sesion|alta_ejercicio), pregunta,
                   contexto_json, estado (abierta|respondida|cancelada),
                   creada_en, respondida_en
```

**`nombre` es cómo Manuel piensa el entrenamiento hoy**: PUSH, PULL, PIERNA.
Reemplaza en la práctica a `dia_rutina`, que es la rotación 1-2-3 y quedó para
las sesiones viejas que ya lo tenían. El gráfico de distribución por día sigue
usando `dia_rutina` — no se tocó.

**Los timestamps van en hora LOCAL, no UTC** (`models.ahora()`, no `utcnow`).
Se comparan contra `startTimeLocal` de Garmin, y con UTC un entrenamiento de las
21:00 se guardaba como las 00:00 del día siguiente y el match fallaba. Las filas
escritas antes de este cambio quedaron en UTC: son 3 horas más que la realidad.

**Migraciones**: `create_all()` no toca tablas que ya existen. Las columnas
nuevas se agregan en `_migrar_columnas()` (`models.py`), que corre en cada
import y es idempotente. Todas tienen que ser nullable o traer `DEFAULT` —
es la única forma de que SQLite acepte un `ADD COLUMN` sin recrear la tabla.

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
- `GET /api/mensajes` — últimos mensajes parseados (`?limite=N`, default 20) +
  `bot_activo` + `sesion_abierta` + `pregunta_pendiente` (lo que el bot espera
  que contestes por Telegram; se contesta en el chat, el panel solo lo muestra)
- `POST /api/mensajes/<id>/confirmar` — escribe las series en sesiones/series
- `POST /api/mensajes/<id>/descartar` — no escribe nada

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

### El protocolo de sesión

La sesión se abre y se cierra **explícitamente**. Es una decisión de Manuel, y es
mejor que lo que había planeado antes (deducir la fecha de Garmin y agrupar por día):
declarando el inicio, **no hay nada que adivinar**.

```
"inicio: Push 02/08/26"   abre la sesión (nombre PUSH, fecha 2026-08-02)
"BP 75 x 3 x 6,4,3"       cae en la bandeja, atado a esa sesión
"fin sesión"              la cierra y la linkea con la actividad de Garmin
```

Reglas del ciclo de vida (todo en `models.py`):

- **Una sola sesión abierta a la vez.** Un `inicio` nuevo cierra la anterior.
- **Fechas argentinas**: `02/08/26` es el 2 de agosto. También entiende "hoy" y "ayer".
- **Si te olvidás de cerrarla**, a las `HORAS_PARA_PREGUNTAR_CIERRE` (5) el watchdog
  del bot pregunta si la damos por terminada. **Pregunta una sola vez** — se anota en
  `pregunta_cierre_en`. Insistir es la forma más rápida de que el bot moleste y se
  deje de usar, que es exactamente lo que pasó con el tab ENTRENO.
- **Sin sesión abierta el mensaje no se pierde**: se guarda igual, y al confirmarlo
  cae en la sesión del día (se reusa la que exista, o se crea una).

### Preguntas pendientes

El bot pregunta y espera. Empezó siendo `Sesion.pregunta_cierre_en` (un timestamp,
solo para el cierre) y se generalizó en la tabla **`preguntas_pendientes`** al agregar
el alta de ejercicios, que necesita dos cosas que un timestamp no da: saber **de qué**
es la pregunta (para que el parser interprete la respuesta) y guardar el **contexto**
para ejecutarla (qué alias, de qué mensaje).

- Se atiende **una sola a la vez**: la más vieja abierta. Dos ejercicios sin match en
  un mensaje encolan dos preguntas, y la segunda se hace al resolver la primera.
- **Vencen a las `HORAS_VALIDEZ_PREGUNTA` (12).** Un "sí" doce horas después casi
  seguro es sobre otra cosa. Se cancelan solas dentro de `pregunta_pendiente_actual()`,
  que es el único lugar por el que pasan todas — por eso no hay otro watchdog.
- Un `inicio` o un `fin` cancelan la pregunta de cierre: el evento la volvió irrelevante.
- `pregunta_cierre_en` **sigue existiendo** y es la marca de "ya preguntamos, no
  insistir"; la fila en `preguntas_pendientes` es la que espera la respuesta.

### Alta de ejercicios desde el bot

Un ejercicio fuera del catálogo no se inventa **nunca** (es lo que partió
`PB INCL MANC` en dos). Antes eso era un callejón sin salida: el mensaje quedaba
`parcial` y esas series se perdían. Ahora el bot pregunta:

```
"hip thrust 80 3x10"   → queda en la bandeja SIN MATCH, y el bot pregunta el grupo
"pierna"               → crea HIP THRUST (PIERNA) y completa el mensaje pendiente
```

**El grupo muscular lo escribe Manuel, no lo propone el modelo.** Es su decisión:
proponerlo abarata la interacción pero mete grupos inventados en un catálogo que
ordena todos los gráficos. El nombre sí sale del alias tal cual lo escribió.

Al crear el ejercicio, `_completar_mensajes_con_ejercicio()` rellena el `ejercicio_id`
que faltaba en los mensajes de la bandeja que mencionaban ese alias. Sin eso el alta
serviría a medias: el ejercicio existiría pero las series que lo estrenaron quedarían
afuera igual.

> ⚠️ **Confirmar es re-ejecutable, y por eso cada ejercicio ya escrito queda marcado
> con `confirmado: true` dentro de `parse_json`.** Un mensaje `parcial` que se completa
> vuelve a `parseado` para poder confirmar lo que faltaba; sin esa marca, la segunda
> confirmación **duplicaría** las series que ya estaban.

### Flujo

```
mensaje → whitelist por user ID → Claude (structured output) → clasifica la intención
        → inicio/fin mueven la sesión · las series van a mensajes_parseados
        → eco de confirmación al chat  → panel en el tab ENTRENO → CONFIRMAR
```

**Las series no se escriben solas.** El mensaje queda en la bandeja y recién entra a
`sesiones`/`series` cuando apretás CONFIRMAR en el tab ENTRENO. Es a propósito: el eco
del bot te muestra el error al toque, pero nada toca la base sin que lo mires.

Al confirmar, los ejercicios **sin match se saltean** y el mensaje queda en `parcial`
en vez de `confirmado`. Si no matcheó ninguno, no se escribe nada y falla. **Nunca se
crea un ejercicio automáticamente**: se dan de alta contestándole al bot el grupo
muscular, y ahí el mensaje vuelve a estar confirmable.

### El link con Garmin

Manuel registra "Fuerza" en el reloj: 74 actividades en 180 días. El reloj sabe
**cuándo** entrenó y **cuánto costó** (duración, FC, calorías); lo que no sabe es
**qué** hizo — `totalReps` viene en 0. Los mensajes de Telegram son lo complementario.

Al cerrar la sesión, `buscar_actividad_fuerza()` busca la actividad que se **solapa**
con la ventana inicio→fin (tolerancia 120 min a cada lado) y guarda su ID en
`garmin_activity_id`. Entrenar sin el reloj es un caso válido: sin match no pasa nada.

> ⚠️ La API de Garmin devuelve **400** con `activitytype="strength_training"` en la
> búsqueda, aunque sí devuelve ese `typeKey` en los resultados (con `"running"` anda).
> Por eso `fetch_strength_activities()` trae todo y filtra por `typeKey` en Python.

### El parser (`message_parser.py`)

Modelo **`claude-haiku-4-5`**. Es extracción contra un catálogo fijo, no razonamiento.

- **Clasifica la intención** en `tipo`: `series` · `inicio` · `fin` · `respuesta` · `otro`.
  Un mensaje puede abrir la sesión **y** traer series ("arranco push: BP 75x3x6,4,3").
- **Recibe el estado del bot** (`sesion_abierta`, `pregunta_pendiente`) en el system
  prompt. Sin eso no puede distinguir un "sí" que contesta una pregunta del bot de un
  "sí" suelto — el primero es `respuesta`, el segundo es `otro`. `pregunta_pendiente`
  es el objeto, no un bool: importa **de qué** es. Un `"pierna"` suelto es `otro`, pero
  con un alta esperando es `respuesta` con `respuesta_texto: "PIERNA"`.
- **Un ejercicio que no matchea se devuelve igual**, con `ejercicio_id: null` y sus
  series. Nunca se omite de la lista: esa entrada es la que dispara la pregunta del
  alta. Haiku tendía a devolver `ejercicios: []` cuando el mensaje traía **un solo**
  ejercicio y no matcheaba — las reglas no alcanzaron, lo arregló el ejemplo trabajado
  que está en la regla 2 del prompt.
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

> ⚠️ `INSTRUCCIONES` se arma con `str.format()`, así que **una llave literal en el
> prompt lo rompe** (`KeyError`). Por eso el ejemplo de la regla 2 está escrito como
> lista indentada y no como JSON. Si hace falta JSON de verdad, duplicar: `{{` y `}}`.

### El bot (`telegram_bot.py`)

- Long polling con `requests` contra la Bot API. Sin librería de Telegram: es HTTP plano.
- **Enruta por intención**: `inicio`/`fin` mueven la sesión, las series van a la bandeja.
- **Watchdog de sesiones abandonadas**: cada `INTERVALO_WATCHDOG_SEG` (300s), dentro del
  mismo loop de polling — sin thread aparte. Usa `ALLOWED_USER_ID` como `chat_id`, que
  en un chat privado con el bot son el mismo número.
- Comandos: `/help` · `/estado` (qué sesión hay abierta) · `/fin` (cerrarla a mano).
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
punta a punta con sesiones explícitas, confirmación y alta de ejercicios (03/08/2026).

- F-01 score semanal · F-02 progresión por ejercicio · F-03 correlaciones cruzadas · F-04 sueño
- Gimnasio en SQLite con CRUD completo y tab ENTRENO
- Bot de Telegram + parser con Claude → bandeja de entrada
- Sesiones por Telegram (`inicio` / `fin`), botones de confirmar y link con Garmin
- Alta de ejercicios contestándole al bot el grupo muscular (`preguntas_pendientes`)

DB: 22 ejercicios, 9 sesiones, 137 series. Garmin conectado, 2 PDFs parseados
(verificado contra el banner del server el 03/08/2026 — antes decía 17, era un error).

### Sobre el entorno de trabajo

Este clon **no trae los datos personales** — están todos gitignored. Si arrancás una
sesión y ves la DB vacía, el bot apagado y 0 PDFs, no está roto: faltan `manu_logs.db`,
`.env`, `credentials.json` y `pdfs/`, que Manuel tiene que copiar a mano. Garmin sí
anda igual, porque los tokens viven en `~/.garth/`, fuera del repo.

Dependencias que faltaban en la máquina y hay que instalar aparte del README:
`python-dotenv`, `sqlalchemy` y `anthropic` (ver la deuda de `requirements.txt`).

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

Estado al 03/08/2026. Los pasos 0 a 3 están **hechos**. El siguiente es el 4 (Supabase).
Cada paso lista las decisiones que **hay que preguntarle a Manuel** antes de codear —
no asumirlas.

### Paso 0 — auditar el bot ✅ HECHO

Se revisaron los tres mensajes reales de la bandeja (`BP 75 x 3 x 6,4,3`,
`SQ 100 X 3 X 7,6,3`, `Remo barra 95 x 3 x 8,7,9`): los tres parsearon bien, con los
alias resueltos (`BP` → `PB`, `Remo barra` → `REMO`) y `confianza: alta`.

Para auditar de nuevo, los mensajes guardan `texto_original` junto al parse:

```bash
curl -s "http://localhost:5000/api/mensajes?limite=50"
```

### Paso 1 — sesiones y confirmación ✅ HECHO

Lo planeado era un botón de confirmar con la fecha deducida. **Manuel propuso algo
mejor**: declarar la sesión con `inicio` / `fin sesión`, que elimina las dos preguntas
abiertas del plan viejo — la fecha la dice él y la agrupación es explícita.

Las tres decisiones que estaban pendientes quedaron así:

1. **Agrupación** → una sesión por protocolo `inicio`/`fin`. Sin sesión abierta, cae en
   la sesión del día (se reusa la que exista).
2. **Fecha** → la del mensaje de inicio. Sin `inicio`, la fecha de recepción.
3. **Sin match** → se confirman los que matchearon y el mensaje queda `parcial`.

Se agregó además el link con Garmin y el watchdog de sesiones abandonadas.

### Paso 2 — ajustes del parser ✅ HECHO

El nit detectado ya está corregido: con un conteo de series incoherente ("3 series"
pero 4 reps listadas) ahora devuelve `confianza: "baja"`. La regla en el prompt es más
general — **cualquier cosa anotada en `ambiguedad` implica confianza baja**.

Si aparecen mensajes reales donde el parser falla, este es el lugar para volver.

### Paso 3 — alta de ejercicios desde el bot ✅ HECHO

Ver *Alta de ejercicios desde el bot* más arriba. La decisión que estaba pendiente
la tomó Manuel: **el grupo muscular lo escribe él**, el modelo no lo propone. El bot
pregunta "¿de qué grupo muscular es?" y con esa respuesta crea el ejercicio.

Se generalizó `Sesion.pregunta_cierre_en` en la tabla `preguntas_pendientes`, y al dar
el alta se completan los mensajes de la bandeja que habían quedado sin match.

Lo que se aprendió probándolo: Haiku **omitía** el ejercicio sin match cuando era el
único del mensaje, y sin esa entrada el flujo entero no arranca. Lo arregló un ejemplo
trabajado en el prompt; las reglas en prosa no alcanzaron.

### Paso 4 — Supabase (Fase 2, el siguiente)

El plan viejo, y ahora tiene una razón extra: con el bot en un servidor always-on los
mensajes entrarían al instante en vez de esperar a que se prenda la PC. Hoy con polling
llegan cuando arranca `server.py` (Telegram los guarda ~24hs, no se pierde nada).
Por esto es la regla 4 — SQLAlchemy core, nada de Flask-SQLAlchemy.

### Deuda conocida (no urgente)

- **No hay `requirements.txt`.** Las dependencias están listadas en el README pero sin
  fijar versiones. Ya mordió: en el arranque del 02/08 faltaban `python-dotenv`,
  `sqlalchemy` y `anthropic`, y el server no levantaba.
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
