# MANU///LOGS — Guía de configuración

## Lo que vas a tener al final
Un dashboard que corre en `http://localhost:5000` y se actualiza automáticamente cada vez que cargás datos en Google Sheets.

---

## PASO 1 — Crear el Google Sheet nuevo

1. Andá a [sheets.google.com](https://sheets.google.com) y creá un archivo nuevo
2. Renombralo como `MANU_LOGS` (o como quieras)
3. Creá estas 3 hojas (tabs en la parte de abajo):

### Hoja: `gimnasio`
La primera fila tiene que ser exactamente así (copiá y pegá):
```
fecha	dia	ejercicio	grupo_muscular	serie	reps	peso_kg	notas
```

**Ejemplo de cómo cargar:**
```
2025-09-02	1	Press Banca	Pecho	1	7	70	
2025-09-02	1	Press Banca	Pecho	2	6	70	
2025-09-02	1	Press Banca	Pecho	3	5	70	
2025-09-02	1	Dominadas	Espalda	1	10	0	
```

**Grupos musculares sugeridos:** Pecho, Espalda, Pierna, Hombro, Bicep, Tricep, Core

### Hoja: `running`
Primera fila:
```
fecha	distancia_km	tiempo_min	pace_min_km	fc_prom	fc_max	desnivel_m	notas
```

**Ejemplo:**
```
2025-09-03	8.2	44.1	5.22	156	178	45	Fartlek
2025-09-06	5.0	25.5	5.10	162	181	20	Ritmo
```

### Hoja: `antropometria`
Primera fila:
```
fecha	peso_kg	masa_muscular_kg	masa_adiposa_kg	masa_osea_kg	masa_residual_kg	masa_piel_kg	cintura_cm	brazo_relajado_cm	brazo_flex_cm	metabolismo_basal	gasto_total
```

**Cargá tu primera medición (datos del informe de feb 2026):**
```
2026-02-13	77.50	38.93	17.83	8.78	8.26	3.71	80.00	34.70	38.50	1755	2809
```

---

## PASO 2 — Configurar Google Sheets API (gratis)

### 2.1 Crear proyecto en Google Cloud

1. Andá a [console.cloud.google.com](https://console.cloud.google.com)
2. Hacé click en el selector de proyectos (arriba a la izquierda) → **"Nuevo proyecto"**
3. Nombre: `manu-logs` → **Crear**
4. Asegurate de que el proyecto nuevo esté seleccionado

### 2.2 Habilitar la API de Google Sheets

1. En el menú izquierdo: **APIs y servicios** → **Biblioteca**
2. Buscá **"Google Sheets API"**
3. Hacé click → **Habilitar**

### 2.3 Crear credenciales (cuenta de servicio)

1. En el menú: **APIs y servicios** → **Credenciales**
2. Click en **"+ Crear credenciales"** → **"Cuenta de servicio"**
3. Nombre: `manu-logs-reader` → **Crear y continuar**
4. Rol: **Visualizador** → **Continuar** → **Listo**
5. Hacé click en la cuenta de servicio recién creada
6. Andá a la pestaña **"Claves"**
7. **"Agregar clave"** → **"Crear clave nueva"** → **JSON** → **Crear**
8. Se descarga un archivo `.json` → **renombralo `credentials.json`**

### 2.4 Compartir el Sheet con la cuenta de servicio

1. Abrí el archivo `credentials.json` descargado
2. Buscá el campo `"client_email"` — algo como:
   `manu-logs-reader@manu-logs-123456.iam.gserviceaccount.com`
3. Andá a tu Google Sheet → **"Compartir"** (arriba a la derecha)
4. Pegá ese email → permiso **"Lector"** → **Enviar**

---

## PASO 3 — Configurar el proyecto local

### 3.1 Estructura de carpetas
```
manu-logs/
├── dashboard.html      ← el dashboard
├── server.py           ← el servidor
├── credentials.json    ← el que descargaste de Google Cloud
└── README.md           ← esta guía
```

### 3.2 Obtener el ID del Spreadsheet

La URL de tu Sheet es algo como:
```
https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit
```
El ID es la parte larga entre `/d/` y `/edit`. Abrí `server.py` y reemplazá:
```python
SPREADSHEET_ID = "TU_SPREADSHEET_ID_ACA"
```

### 3.3 Instalar dependencias Python

Abrí una terminal en la carpeta `manu-logs/` y corré:
```bash
pip install flask flask-cors google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

---

## PASO 4 — Correr el dashboard

```bash
python server.py
```

Abrí el browser en: **http://localhost:5000**

Cada vez que cargues datos en el Sheet y recargues el browser, el dashboard se actualiza.

---

## TIPS para cargar datos desde el celular

- Guardá la URL del Google Sheet como acceso directo en la pantalla de inicio
- Usá siempre el formato de fecha `AAAA-MM-DD` (ej: `2025-09-02`)
- Cada serie es una fila — tarda ~10 segundos por ejercicio pero después podés ver todo en el dashboard
- Para dominadas o fondos sin lastre, poné `0` en `peso_kg`

---

## PRÓXIMO PASO — Garmin Connect

Una vez que esto funcione, el siguiente paso es un script que descarga tus actividades de Garmin automáticamente cada vez que corrés `server.py`. La API es gratuita para uso personal.
