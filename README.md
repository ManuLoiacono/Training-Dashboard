# MANU///LOGS

Dashboard personal de rendimiento deportivo. Corre local en `http://localhost:5000` y
cruza cuatro fuentes de datos en una sola vista:

| Dominio | De dónde sale |
|---|---|
| **Gimnasio** | SQLite local — se carga desde el tab ENTRENO |
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
```

Si algo dice `[X]`, mirá la sección correspondiente más abajo. El dashboard **funciona
igual** aunque falten fuentes: cada una tiene su fallback y los tabs sin datos muestran
un estado vacío en vez de romperse.

---

## Instalación

### 1. Dependencias

```bash
pip install flask flask-cors python-dotenv sqlalchemy garminconnect pdfplumber
pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

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

---

## Cargar entrenamientos — tab ENTRENO

Diseñado para usar en el celular, en el gimnasio, con **3 taps**:

1. Elegís (o creás) la sesión del día
2. Elegís el ejercicio
3. Cargás reps y peso

El número de serie se calcula solo. Las series se pueden editar o borrar después.
El catálogo de ejercicios es tuyo: agregás uno nuevo desde el mismo tab y queda guardado
con su grupo muscular.

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
server.py          Backend Flask — todos los endpoints
models.py           SQLAlchemy: Ejercicio, Sesion, Serie
dashboard_v3.html   Frontend completo (HTML + CSS + JS en un archivo)
garmin_client.py    Cliente de Garmin Connect
garmin_setup.py     Auth de Garmin (correr una vez)
antro_parser.py     Parser de PDFs ISAK
migrate_sheets.py   Script one-shot de migración Sheets → SQLite (ya ejecutado)
manu_logs.db        Base de datos (no se commitea)
pdfs/               Informes antropométricos (no se commitean)
```

Nada de esto se sube al repo: `credentials.json`, `.env`, `*.db`, `pdfs/`.
Son datos personales o secretos.

### Configuración opcional (`.env`)

```
DATABASE_URL=sqlite:///manu_logs.db
SPREADSHEET_ID=...
CREDENTIALS_FILE=credentials.json
```

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
