# MANU///LOGS

Dashboard personal de rendimiento deportivo. Corre local en `http://localhost:5000` y
cruza cuatro fuentes de datos en una sola vista:

| Dominio | De dónde sale |
|---|---|
| **Gimnasio** | SQLite local — por mensaje de Telegram o desde el tab ENTRENO |
| **Running** | Garmin Connect (automático) |
| **Composición corporal** | PDFs de antropometría ISAK en `pdfs/` |
| **Sueño** | Garmin Connect (automático) |

Tabs: `OVERVIEW · GIMNASIO · RUNNING · COMPOSICIÓN · ENTRENO · SUEÑO · ANÁLISIS`

---

## Arrancar

```bash
python server.py
```

Abrí **http://localhost:5000**.

El banner de arranque te dice el estado de cada fuente:

```
  Garmin Connect: [OK] CONFIGURADO
  PDFs antropometria: 2 encontrados
  Telegram: [OK] bot escuchando
```

Si algo dice `[X]`, mirá la sección correspondiente más abajo. El dashboard **funciona
igual** aunque falten fuentes: cada una tiene su fallback y los tabs sin datos muestran
un estado vacío en vez de romperse.

---

## Instalación

### 1. Dependencias

```bash
pip install flask flask-cors python-dotenv sqlalchemy garminconnect pdfplumber
pip install anthropic requests
pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

Si el server no arranca con `ModuleNotFoundError`, corré el primer renglón de nuevo:
`python-dotenv`, `sqlalchemy` y `anthropic` son los que suelen faltar.

Los últimos dos renglones (Google) solo hacen falta si vas a usar el fallback a Google
Sheets — ver la sección *Google Sheets* al final.

### 2. Base de datos

No hay que hacer nada. `models.py` crea `manu_logs.db` y sus tablas automáticamente la
primera vez que arranca el server.

### 3. Garmin Connect (running + sueño)

Una sola vez:

```bash
python garmin_setup.py
```

Guarda los tokens en `~/.garth/`. Se auto-refrescan solos; si algún día expiran, volvé a
correr ese mismo comando.

### 4. PDFs de antropometría

Tirá los informes ISAK en la carpeta `pdfs/`. El parser los detecta solos, extrae ~25
variables de cada uno y los ordena por fecha. No hay que registrarlos en ningún lado.

Para probar el parseo de un PDF suelto:

```bash
python antro_parser.py pdfs/informe.pdf
```

### 5. Bot de Telegram

Tres valores en el `.env` (la plantilla está en `.env.example`):

| Variable | De dónde sale |
|---|---|
| `TELEGRAM_BOT_TOKEN` | hablale a **@BotFather** → `/newbot` |
| `TELEGRAM_ALLOWED_USER_ID` | hablale a **@userinfobot**, te da tu ID numérico |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

El bot arranca solo junto con `server.py`. Usa long polling, así que **no hace falta URL
pública, ni túnel, ni abrir puertos** — tu PC le pregunta a Telegram, no al revés. Si la
PC está apagada mientras entrenás, Telegram guarda los mensajes ~24hs y entran al prender.

`TELEGRAM_ALLOWED_USER_ID` no es opcional: los bots son descubribles por su username, y
sin ese filtro cualquiera podría escribir en tu base y gastarte la API key.

---

## Cargar entrenamientos

### Por Telegram (lo más rápido)

Arrancás la sesión, mandás los ejercicios como te salga, y la cerrás:

```
inicio: Push 02/08/26
BP 75 x 3 x 6,4,3
dominadas 3x8
remo 60x10,10,8
fin sesión
```

El mensaje de inicio acepta lo que quieras: `inicio sesión: Pierna`, `arranco pull`,
con fecha o sin fecha. Las fechas son **día/mes** (`02/08/26` es el 2 de agosto) y
también entiende "hoy" y "ayer". Si no ponés fecha, es hoy.

Los ejercicios no tienen sintaxis que recordar:

```
BP 75 x 3 x 6,4,3
sentadilla 100 3x5
dominadas 3x8
PB 70x8 75x6 80x4
hoy banca 75 por 6, 4 y 3, después dominadas 3x8 y remo 60x10,10,8
```

Lo interpreta un modelo de Claude contra **tu** catálogo de ejercicios, así que entiende
tus abreviaturas (`BP` → `PB`), español o inglés, y varios ejercicios en un mismo mensaje.

El bot te contesta con **lo que entendió**, para que veas el error al toque:

```
[OK] PB - 3 series @ 75kg
   reps: 6, 4, 3   vol: 975 kg
```

Si un ejercicio no está en tu catálogo **no lo inventa**: te pregunta de qué grupo
muscular es y lo agrega con lo que le contestes.

```
VOS:  hip thrust 80 3x10
BOT:  [SIN MATCH] ?? hip thrust — 3 series @ 80kg
         reps: 10, 10, 10   vol: 2.400 kg
      (en la bandeja, todavía sin confirmar)

      "hip thrust" no está en el catálogo. ¿De qué grupo muscular es?
      (BICEP / CORE / ESPALDA / HOMBRO / PECHO / PIERNA / TRICEP)
      Si no lo querés agregar, contestame "no".

VOS:  pierna
BOT:  Listo, agregué HIP THRUST (PIERNA).
      Ya lo completé en 1 mensaje de la bandeja; confirmalo en el dashboard.
```

El grupo lo escribís vos a propósito: si lo adivinara el bot, tarde o temprano te mete
un grupo inventado en el catálogo y con eso se arman todos los gráficos. El nombre sale
de lo que escribiste, en mayúsculas.

Si en un mismo mensaje hay dos ejercicios nuevos, te pregunta de a uno: el segundo
recién cuando resolvés el primero. Y si no contestás, la pregunta se vence sola a las
12 horas — un "sí" al otro día casi seguro es sobre otra cosa.

Al mandar `fin sesión` te devuelve el resumen y, si tenías el reloj puesto, lo cruza con
la actividad de **Fuerza** de Garmin:

```
Sesión cerrada.

PUSH — 2026-08-02
  PB, DOMINADAS, REMO
  9 series · 2.775 kg
Garmin: 73 min · FC 126/168 · 610 kcal
```

Otros comandos: `/estado` te dice qué sesión hay abierta (y si quedó algo esperando tu
respuesta), `/fin` la cierra, `/help` te recuerda todo esto.

**Si te olvidás de cerrarla**, a las 5 horas el bot te pregunta si la damos por
terminada. Pregunta una sola vez, no te va a insistir. Y si te olvidás del `inicio`,
el mensaje no se pierde: al confirmarlo va a la sesión del día.

Los mensajes caen en una **bandeja** arriba del tab ENTRENO. **No se guardan como series
hasta que apretás CONFIRMAR** ahí — primero los revisás. Se guarda el texto original tal
cual lo escribiste, así siempre podés comparar contra lo que el parser entendió.

Si un mensaje traía un ejercicio que no está en tu catálogo, al confirmarlo se guardan
**los que sí matchearon** y queda marcado como parcial. Cuando después le contestás al
bot el grupo muscular, ese mensaje se completa solo y vuelve a tener el botón
CONFIRMAR: al apretarlo entran únicamente las series que faltaban, las ya guardadas no
se duplican (aparecen con un ✓).

Para probar el parser sin Telegram:

```bash
python message_parser.py "BP 75 x 3 x 6,4,3"
```

### Por el tab ENTRENO

Diseñado para usar en el celular, en el gimnasio, con **3 taps**:

1. Elegís (o creás) la sesión del día
2. Elegís el ejercicio
3. Cargás reps y peso

El número de serie se calcula solo. Las series se pueden editar o borrar después.
El catálogo de ejercicios es tuyo: agregás uno nuevo desde el mismo tab y queda guardado
con su grupo muscular.

El **día de rutina es opcional**. Si no seguís la rotación 1-2-3 a rajatabla, dejalo
vacío: la sesión se guarda igual y simplemente no entra en el gráfico de distribución
por día. Es preferible a etiquetarla mal.

---

## Qué calcula

**Score semanal (1-100)** — ponderado entre gimnasio (35%), running (30%), composición
(20%) y sueño (15%). Si falta alguna fuente, los pesos se redistribuyen entre las que sí
están.

**Por ejercicio** — peso máximo por sesión, volumen, 1RM estimado (fórmula de Epley),
PRs de peso y de volumen, top 5 series, tendencia a 4 semanas y alerta de estancamiento
(mismo peso máximo tres sesiones seguidas).

**Correlaciones** — km de running vs volumen de gimnasio por semana, y masa adiposa vs
pace promedio.

---

## Estructura

```
server.py           Backend Flask — todos los endpoints
models.py           SQLAlchemy: Ejercicio, Sesion, Serie, MensajeParseado
dashboard_v3.html   Frontend completo (HTML + CSS + JS en un archivo)
telegram_bot.py     Bot de Telegram (long polling)
message_parser.py   Convierte los mensajes en series usando Claude
garmin_client.py    Cliente de Garmin Connect
garmin_setup.py     Auth de Garmin (correr una vez)
antro_parser.py     Parser de PDFs ISAK
migrate_sheets.py   Script one-shot de migración Sheets → SQLite (ya ejecutado)
manu_logs.db        Base de datos (no se commitea)
pdfs/               Informes antropométricos (no se commitean)
```

Nada de esto se sube al repo: `credentials.json`, `.env`, `*.db`, `pdfs/`,
`.telegram_offset`. Son datos personales o secretos.

### Configuración (`.env`)

Copiá `.env.example` a `.env` y completá lo que uses:

```
DATABASE_URL=sqlite:///manu_logs.db
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...
ANTHROPIC_API_KEY=...
SPREADSHEET_ID=...
CREDENTIALS_FILE=credentials.json
```

Si alguna credencial se te escapa a algún lado, rotala: `/revoke` en @BotFather para el
token del bot, y la consola de Anthropic para la API key.

---

## Google Sheets (legacy)

El proyecto **arrancó** leyendo todo de Google Sheets. El gimnasio ya migró a SQLite,
pero Sheets sigue funcionando como fallback para running y antropometría si Garmin o los
PDFs no están disponibles.

Si querés usarlo, hace falta una service account de Google Cloud:

1. Creá un proyecto en [console.cloud.google.com](https://console.cloud.google.com)
2. **APIs y servicios → Biblioteca** → habilitá **Google Sheets API**
3. **Credenciales → Crear credenciales → Cuenta de servicio**, rol *Visualizador*
4. En la cuenta creada: pestaña **Claves → Agregar clave → JSON**. Guardalo como
   `credentials.json` en la raíz del proyecto
5. Abrí ese JSON, copiá el `client_email` y compartí tu Google Sheet con ese mail como
   **Lector**
6. Poné el ID del spreadsheet (la parte larga de la URL entre `/d/` y `/edit`) en el
   `.env` como `SPREADSHEET_ID`

Las hojas tienen que llamarse `gimnasio`, `running` y `antropometria`, con estos headers
en la primera fila:

```
gimnasio       fecha  dia  ejercicio  grupo_muscular  serie  reps  peso_kg  notas
running        fecha  distancia_km  tiempo_min  pace_min_km  fc_prom  fc_max  desnivel_m  notas
antropometria  fecha  peso_kg  masa_muscular_kg  masa_adiposa_kg  masa_osea_kg  masa_residual_kg
               masa_piel_kg  cintura_cm  brazo_relajado_cm  brazo_flex_cm  metabolismo_basal  gasto_total
```

Formato de fecha siempre `AAAA-MM-DD`.

---

## Desarrollo

`CLAUDE.md` tiene el contexto técnico completo: arquitectura, endpoints, modelo de datos,
sistema de diseño y las reglas del proyecto. Leelo antes de tocar código.
