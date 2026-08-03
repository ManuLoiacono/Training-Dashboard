"""
MANU///LOGS — SQLAlchemy models
Tablas: ejercicios, sesiones, series, mensajes_parseados.
Auto-init al importar si el .db no existe.
"""

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import (
    Column, Integer, Text, Float, Date, DateTime,
    ForeignKey, create_engine, event, inspect, text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# models.py se importa desde scripts que no siempre cargaron el .env todavia.
# Sin esto, DATABASE_URL cae al default de SQLite en silencio y terminas
# escribiendo en la base equivocada sin que nada avise. Es idempotente.
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _normalizar_database_url(url: str) -> str:
    """
    Deja la URL como la espera SQLAlchemy y resuelve los paths de SQLite.

    Supabase te da la connection string arrancando en `postgresql://`, que
    SQLAlchemy mapea al driver psycopg2 (no instalado). Se reescribe a
    `postgresql+psycopg://` para usar psycopg 3, que es el que anda en 3.13.
    """
    if url.startswith("postgres://"):  # algunos hosts la dan asi
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    # SQLite con path relativo: resolverlo contra el directorio del proyecto,
    # no contra el cwd de quien lo haya arrancado
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        db_path = url.replace("sqlite:///", "")
        url = f"sqlite:///{os.path.join(BASE_DIR, db_path)}"
    return url


DATABASE_URL = _normalizar_database_url(
    os.environ.get("DATABASE_URL", "sqlite:///manu_logs.db")
)

ES_SQLITE = DATABASE_URL.startswith("sqlite")
ES_POSTGRES = DATABASE_URL.startswith("postgresql")

# Postgres administrado corta las conexiones ociosas y la app se pasa horas
# esperando un mensaje de Telegram: sin pre_ping, el primer request despues
# de un rato revienta con "server closed the connection unexpectedly".
_opciones = {"echo": False}
if ES_POSTGRES:
    _opciones["pool_pre_ping"] = True
    _opciones["pool_recycle"] = 900
    # El pooler de Supabase en modo transaction (puerto 6543) no soporta
    # prepared statements, que es lo que psycopg 3 usa por defecto.
    if ":6543" in DATABASE_URL:
        _opciones["connect_args"] = {"prepare_threshold": None}

engine = create_engine(DATABASE_URL, **_opciones)


# Las foreign keys de SQLite vienen apagadas por defecto. En Postgres siempre
# estan activas, asi que este listener solo aplica al caso local.
if ES_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

Session = sessionmaker(bind=engine)
Base = declarative_base()


def ahora():
    """
    Hora LOCAL, no UTC. El dashboard es de una sola persona en una sola zona
    horaria, y los timestamps se comparan contra los de Garmin, que vienen en
    local (`startTimeLocal`). Con utcnow() un entrenamiento de las 21:00 se
    guardaba como 00:00 del día siguiente y el match no cerraba.
    """
    return datetime.now()

# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class Ejercicio(Base):
    __tablename__ = "ejercicios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(Text, nullable=False, unique=True)
    grupo_muscular = Column(Text, nullable=False)
    notas = Column(Text)
    creado_en = Column(DateTime, default=ahora)

    series = relationship("Serie", back_populates="ejercicio")

    def to_dict(self):
        return {
            "id": self.id,
            "nombre": self.nombre,
            "grupo_muscular": self.grupo_muscular,
            "notas": self.notas or "",
        }


class Sesion(Base):
    __tablename__ = "sesiones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha = Column(Date, nullable=False)
    # Opcional: la rotacion 1-2-3 no siempre se respeta, y un dia mal
    # etiquetado es peor que ninguno. Sin valor, la sesion no entra en
    # el grafico de distribucion por dia.
    dia_rutina = Column(Integer, nullable=True)
    # Como Manuel nombra la sesion hoy: PUSH, PULL, PIERNA. Texto libre en
    # MAYUSCULAS. Reemplaza en la practica a dia_rutina, que queda para las
    # sesiones viejas que ya lo tenian cargado.
    nombre = Column(Text, nullable=True)
    notas = Column(Text)

    # Ciclo de vida: la sesion se abre con "inicio: Push 02/08/26" por
    # Telegram y se cierra con "fin sesion". Las sesiones creadas desde el
    # tab ENTRENO nacen ya cerradas.
    estado = Column(Text, nullable=False, default="cerrada")  # abierta | cerrada
    iniciada_en = Column(DateTime, nullable=True)
    cerrada_en = Column(DateTime, nullable=True)
    # Cuando el bot pregunto "¿cerramos la sesion?" — para no repreguntar.
    pregunta_cierre_en = Column(DateTime, nullable=True)

    # Actividad de fuerza de Garmin que se solapa con esta sesion. Aporta
    # duracion, FC y calorias; el reloj no registra series (totalReps: 0).
    garmin_activity_id = Column(Text, nullable=True)

    creado_en = Column(DateTime, default=ahora)

    series = relationship("Serie", back_populates="sesion", cascade="all, delete-orphan")

    def to_dict(self, include_series=False):
        d = {
            "id": self.id,
            "fecha": self.fecha.isoformat(),
            "dia_rutina": self.dia_rutina,
            "nombre": self.nombre or "",
            "notas": self.notas or "",
            "estado": self.estado or "cerrada",
            "iniciada_en": self.iniciada_en.isoformat() if self.iniciada_en else "",
            "cerrada_en": self.cerrada_en.isoformat() if self.cerrada_en else "",
            "garmin_activity_id": self.garmin_activity_id or "",
            "creado_en": self.creado_en.isoformat() if self.creado_en else "",
            "total_series": len(self.series),
        }
        if include_series:
            d["series"] = [s.to_dict() for s in self.series]
        return d


class Serie(Base):
    __tablename__ = "series"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sesion_id = Column(Integer, ForeignKey("sesiones.id"), nullable=False)
    ejercicio_id = Column(Integer, ForeignKey("ejercicios.id"), nullable=False)
    numero_serie = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    peso_kg = Column(Float, nullable=False, default=0)
    notas = Column(Text)
    creado_en = Column(DateTime, default=ahora)

    sesion = relationship("Sesion", back_populates="series")
    ejercicio = relationship("Ejercicio", back_populates="series")

    def to_dict(self):
        return {
            "id": self.id,
            "sesion_id": self.sesion_id,
            "ejercicio_id": self.ejercicio_id,
            "ejercicio": self.ejercicio.nombre if self.ejercicio else "",
            "grupo_muscular": self.ejercicio.grupo_muscular if self.ejercicio else "",
            "numero_serie": self.numero_serie,
            "reps": self.reps,
            "peso_kg": self.peso_kg,
            "notas": self.notas or "",
        }


class PreguntaPendiente(Base):
    """
    Una pregunta que el bot hizo por Telegram y todavía espera respuesta.

    Nació generalizando `Sesion.pregunta_cierre_en`, que solo servía para el
    cierre de sesión. Al agregar el alta de ejercicios hicieron falta dos cosas
    que un timestamp en la sesión no da: saber DE QUÉ es la pregunta (para que
    el parser interprete la respuesta) y guardar el contexto para ejecutarla
    (qué alias, de qué mensaje de la bandeja).

    Solo se atiende UNA a la vez: la más vieja que siga abierta. Si un mensaje
    trae dos ejercicios sin match se encolan las dos y la segunda se pregunta
    recién cuando se resuelve la primera.
    """
    __tablename__ = "preguntas_pendientes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # cierre_sesion | alta_ejercicio
    tipo = Column(Text, nullable=False)
    # El texto que se mandó al chat, para reconstruir el contexto del parser
    pregunta = Column(Text)
    # JSON con lo necesario para ejecutar la respuesta (alias, mensaje_id...)
    contexto_json = Column(Text)
    # abierta | respondida | cancelada
    estado = Column(Text, nullable=False, default="abierta")
    creada_en = Column(DateTime, default=ahora)
    respondida_en = Column(DateTime, nullable=True)

    def contexto(self) -> dict:
        import json
        try:
            return json.loads(self.contexto_json) if self.contexto_json else {}
        except json.JSONDecodeError:
            return {}

    def to_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "pregunta": self.pregunta or "",
            "contexto": self.contexto(),
            "estado": self.estado,
            "creada_en": self.creada_en.isoformat() if self.creada_en else "",
        }


class MensajeParseado(Base):
    """
    Bandeja de entrada del bot de Telegram.
    Guarda el texto original tal cual llegó y lo que el modelo entendió.
    Todavía NO escribe en sesiones/series: eso pasa cuando confirmás.
    """
    __tablename__ = "mensajes_parseados"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_message_id = Column(Integer)
    texto_original = Column(Text, nullable=False)
    parse_json = Column(Text)
    # parseado | error | confirmado | parcial | descartado | aplicado
    # "parcial":  se confirmaron los ejercicios que matchearon y quedo
    #             al menos uno sin match, pendiente de resolver.
    # "aplicado": mensaje de control (inicio/fin/respuesta) que ya se ejecuto.
    #             No tiene series que confirmar, queda como registro.
    estado = Column(Text, nullable=False, default="parseado")
    error = Column(Text)
    # Que queria decir el mensaje: series | inicio | fin | respuesta | otro
    tipo = Column(Text, nullable=False, default="series")
    # Sesion abierta en el momento en que llego. Null si no habia ninguna.
    sesion_id = Column(Integer, ForeignKey("sesiones.id"), nullable=True)
    recibido_en = Column(DateTime, default=ahora)

    sesion = relationship("Sesion")

    def to_dict(self):
        import json
        try:
            parse = json.loads(self.parse_json) if self.parse_json else None
        except json.JSONDecodeError:
            parse = None
        return {
            "id": self.id,
            "texto_original": self.texto_original,
            "parse": parse,
            "estado": self.estado,
            "error": self.error or "",
            "tipo": self.tipo or "series",
            "sesion_id": self.sesion_id,
            "sesion_nombre": (self.sesion.nombre or "") if self.sesion else "",
            "sesion_fecha": self.sesion.fecha.isoformat() if self.sesion else "",
            "recibido_en": self.recibido_en.isoformat() if self.recibido_en else "",
        }


# ─────────────────────────────────────────────
# Init DB
# ─────────────────────────────────────────────

# Columnas agregadas despues de que la base ya estaba en uso. create_all()
# solo crea tablas nuevas: no toca las existentes, asi que hay que agregarlas
# a mano. Todas son nullable o traen DEFAULT, que es la unica forma de que
# SQLite acepte un ADD COLUMN sin recrear la tabla.
# Los tipos son sintaxis SQLite: esto solo corre contra SQLite (ver abajo).
_COLUMNAS_NUEVAS = {
    "sesiones": [
        ("nombre", "TEXT"),
        ("estado", "TEXT NOT NULL DEFAULT 'cerrada'"),
        ("iniciada_en", "DATETIME"),
        ("cerrada_en", "DATETIME"),
        ("pregunta_cierre_en", "DATETIME"),
        ("garmin_activity_id", "TEXT"),
    ],
    "mensajes_parseados": [
        ("tipo", "TEXT NOT NULL DEFAULT 'series'"),
        ("sesion_id", "INTEGER REFERENCES sesiones(id)"),
    ],
}


def _migrar_columnas():
    """Agrega las columnas que falten. Idempotente: se corre en cada import."""
    # Los tipos de arriba son de SQLite (DATETIME no existe en Postgres). No
    # hace falta portarlos: una base Postgres nace de create_all(), que ya
    # crea las tablas completas, asi que nunca hay columnas que agregar.
    if not ES_SQLITE:
        return

    inspector = inspect(engine)
    tablas = set(inspector.get_table_names())

    with engine.begin() as conn:
        for tabla, columnas in _COLUMNAS_NUEVAS.items():
            if tabla not in tablas:
                continue  # create_all() ya la creo completa
            existentes = {c["name"] for c in inspector.get_columns(tabla)}
            for nombre, tipo in columnas:
                if nombre in existentes:
                    continue
                conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}"))
                print(f"[DB] Columna agregada: {tabla}.{nombre}", flush=True)


# Si la base no contesto al arrancar. El resto del codigo lo puede consultar
# para explicar por que no hay datos, en vez de tirar el mismo stacktrace
# en cada endpoint.
DB_LISTA = False
DB_ERROR = ""


def init_db():
    """
    Crea tablas si no existen y agrega columnas nuevas a las que ya estaban.

    NO lanza si la base no contesta. Con SQLite esto era impensado — el
    archivo esta ahi o no esta — pero contra Postgres remoto una caida de
    red al importar dejaba sin arrancar al server Y al bot. El dashboard
    tiene que levantar igual y mostrar el problema, no morirse en el import.
    """
    global DB_LISTA, DB_ERROR
    try:
        Base.metadata.create_all(engine)
        DB_LISTA = True
        DB_ERROR = ""
    except Exception as e:
        DB_LISTA = False
        DB_ERROR = f"{type(e).__name__}: {e}"
        print(f"[DB] No pude conectar con la base: {DB_ERROR}", flush=True)
        return

    try:
        _migrar_columnas()
    except Exception as e:
        # Una migracion fallida no puede dejar el dashboard sin arrancar
        print(f"[DB] No pude migrar columnas: {e}", flush=True)


def get_session():
    """Retorna una nueva sesión de SQLAlchemy."""
    return Session()


# ─────────────────────────────────────────────
# Helper: leer gym data en formato compatible con el dashboard existente
# ─────────────────────────────────────────────

def read_gym_as_rows() -> list[dict]:
    """
    Lee todas las series de la DB y retorna en el formato flat
    que espera el dashboard (igual que read_sheet('gimnasio')):
    [{fecha, dia, ejercicio, grupo_muscular, serie, reps, peso_kg, notas}, ...]
    """
    session = get_session()
    try:
        series = (
            session.query(Serie)
            .join(Sesion)
            .join(Ejercicio)
            .order_by(Sesion.fecha, Serie.ejercicio_id, Serie.numero_serie)
            .all()
        )
        rows = []
        for s in series:
            rows.append({
                "fecha": s.sesion.fecha.isoformat(),
                "dia": str(s.sesion.dia_rutina) if s.sesion.dia_rutina is not None else "",
                "ejercicio": s.ejercicio.nombre,
                "grupo_muscular": s.ejercicio.grupo_muscular,
                "serie": str(s.numero_serie),
                "reps": str(s.reps),
                "peso_kg": str(s.peso_kg),
                "notas": s.notas or "",
            })
        return rows
    finally:
        session.close()


# ─────────────────────────────────────────────
# Ciclo de vida de la sesión
# ─────────────────────────────────────────────
#
# El protocolo por Telegram es explícito, para no adivinar ni la fecha ni la
# agrupación:
#
#   "inicio: Push 02/08/26"   -> abre la sesión
#   "BP 75 x 3 x 6,4,3"       -> cae en la bandeja, atada a esa sesión
#   "fin sesión"              -> la cierra
#
# Solo puede haber UNA sesión abierta a la vez. Si llega un "inicio" con otra
# abierta, se cierra la anterior: arrastrarla al día siguiente es justo el
# error que dejó sesiones abandonadas en el tab ENTRENO.

# A las N horas sin cerrar, el bot pregunta si la damos por terminada.
HORAS_PARA_PREGUNTAR_CIERRE = 5


def sesion_abierta(session):
    """La sesión abierta, o None. Si por algún motivo hay más de una, la más reciente."""
    return (
        session.query(Sesion)
        .filter(Sesion.estado == "abierta")
        .order_by(Sesion.iniciada_en.desc(), Sesion.id.desc())
        .first()
    )


def abrir_sesion(session, fecha, nombre=None, notas=""):
    """
    Abre una sesión nueva. Si había otra abierta la cierra primero.
    Retorna (sesion_nueva, sesion_cerrada_o_None).
    """
    previa = sesion_abierta(session)
    if previa is not None:
        cerrar_sesion(session, previa)

    nueva = Sesion(
        fecha=fecha,
        nombre=(nombre or "").strip().upper() or None,
        notas=notas,
        estado="abierta",
        iniciada_en=ahora(),
    )
    session.add(nueva)
    session.commit()
    return nueva, previa


def cerrar_sesion(session, sesion, garmin_activity_id=None):
    """Marca la sesión como cerrada. Idempotente."""
    if sesion is None:
        return None
    sesion.estado = "cerrada"
    if sesion.cerrada_en is None:
        sesion.cerrada_en = ahora()
    if garmin_activity_id:
        sesion.garmin_activity_id = str(garmin_activity_id)
    session.commit()
    return sesion


def sesiones_para_preguntar_cierre(session, horas=HORAS_PARA_PREGUNTAR_CIERRE):
    """
    Sesiones abiertas hace más de N horas a las que todavía no les preguntamos.
    El bot las usa para escribir una sola vez y no volver a insistir.
    """
    limite = ahora() - timedelta(hours=horas)
    return (
        session.query(Sesion)
        .filter(
            Sesion.estado == "abierta",
            Sesion.pregunta_cierre_en.is_(None),
            Sesion.iniciada_en.isnot(None),
            Sesion.iniciada_en <= limite,
        )
        .all()
    )


def resumen_sesion(session, sesion):
    """
    Qué hay en una sesión, juntando lo ya confirmado (series en la base) con
    lo que sigue esperando en la bandeja. Se usa para el eco del "fin sesión",
    cuando todavía no confirmaste nada.
    """
    import json as _json

    confirmadas = [
        {"ejercicio": s.ejercicio.nombre if s.ejercicio else "?",
         "reps": s.reps, "peso_kg": s.peso_kg}
        for s in sesion.series
    ]

    pendientes = []
    mensajes = (
        session.query(MensajeParseado)
        .filter(
            MensajeParseado.sesion_id == sesion.id,
            MensajeParseado.tipo == "series",
            MensajeParseado.estado == "parseado",
        )
        .all()
    )
    for m in mensajes:
        try:
            parse = _json.loads(m.parse_json) if m.parse_json else None
        except _json.JSONDecodeError:
            continue
        for ej in (parse or {}).get("ejercicios", []):
            nombre = ej.get("nombre_catalogo") or f"?? {ej.get('alias_detectado', '')}"
            for s in ej.get("series", []):
                pendientes.append({
                    "ejercicio": nombre,
                    "reps": s.get("reps", 0),
                    "peso_kg": s.get("peso_kg", 0),
                })

    todas = confirmadas + pendientes
    volumen = sum((s["reps"] or 0) * (s["peso_kg"] or 0) for s in todas)
    ejercicios = []
    for s in todas:
        if s["ejercicio"] not in ejercicios:
            ejercicios.append(s["ejercicio"])

    return {
        "sesion_id": sesion.id,
        "nombre": sesion.nombre or "",
        "fecha": sesion.fecha.isoformat(),
        "ejercicios": ejercicios,
        "total_series": len(todas),
        "series_confirmadas": len(confirmadas),
        "series_pendientes": len(pendientes),
        "mensajes_pendientes": len(mensajes),
        "volumen_kg": round(volumen),
    }


# ─────────────────────────────────────────────
# Preguntas pendientes del bot
# ─────────────────────────────────────────────
#
# El bot pregunta y espera. Dos casos hoy:
#
#   cierre_sesion   "la sesión sigue abierta, ¿la cerramos?"  -> sí / no
#   alta_ejercicio  "'hip thrust' no está, ¿qué grupo es?"    -> el grupo
#
# Una pregunta vieja no se contesta nunca: si mandás "sí" doce horas después,
# lo más probable es que sea sobre otra cosa. Pasado ese plazo se cancela sola.
HORAS_VALIDEZ_PREGUNTA = 12


def crear_pregunta(session, tipo, pregunta, contexto=None):
    """Registra una pregunta hecha por el bot y la deja esperando respuesta."""
    import json as _json

    p = PreguntaPendiente(
        tipo=tipo,
        pregunta=pregunta,
        contexto_json=_json.dumps(contexto or {}),
        estado="abierta",
    )
    session.add(p)
    session.commit()
    return p


def pregunta_pendiente_actual(session):
    """
    La pregunta que el bot está esperando que contestes, o None.

    De paso cancela las vencidas: es el único lugar por el que pasan todas,
    y así no hace falta otro watchdog.
    """
    limite = ahora() - timedelta(hours=HORAS_VALIDEZ_PREGUNTA)
    vencidas = (
        session.query(PreguntaPendiente)
        .filter(
            PreguntaPendiente.estado == "abierta",
            PreguntaPendiente.creada_en.isnot(None),
            PreguntaPendiente.creada_en < limite,
        )
        .all()
    )
    if vencidas:
        for p in vencidas:
            p.estado = "cancelada"
        session.commit()

    return (
        session.query(PreguntaPendiente)
        .filter(PreguntaPendiente.estado == "abierta")
        .order_by(PreguntaPendiente.creada_en, PreguntaPendiente.id)
        .first()
    )


def cerrar_pregunta(session, pregunta, estado="respondida"):
    """Marca la pregunta como contestada (o cancelada). Idempotente."""
    if pregunta is None:
        return None
    pregunta.estado = estado
    pregunta.respondida_en = ahora()
    session.commit()
    return pregunta


def cancelar_preguntas(session, tipo) -> int:
    """
    Cierra las preguntas de un tipo que quedaron sin contestar. Se usa cuando
    el evento las volvió irrelevantes: si cerraste la sesión a mano, preguntar
    si la cerramos ya no tiene sentido.
    """
    abiertas = (
        session.query(PreguntaPendiente)
        .filter(
            PreguntaPendiente.tipo == tipo,
            PreguntaPendiente.estado == "abierta",
        )
        .all()
    )
    for p in abiertas:
        p.estado = "cancelada"
        p.respondida_en = ahora()
    if abiertas:
        session.commit()
    return len(abiertas)


def hay_pregunta_alta_abierta(session, alias) -> bool:
    """
    ¿Ya preguntamos por este alias? Evita encolar la misma pregunta dos veces
    si mandás el mismo ejercicio sin match en dos mensajes seguidos.
    """
    alias_norm = (alias or "").strip().upper()
    if not alias_norm:
        return False
    abiertas = (
        session.query(PreguntaPendiente)
        .filter(
            PreguntaPendiente.tipo == "alta_ejercicio",
            PreguntaPendiente.estado == "abierta",
        )
        .all()
    )
    return any(
        (p.contexto().get("alias") or "").strip().upper() == alias_norm
        for p in abiertas
    )


def _completar_mensajes_con_ejercicio(session, alias, ejercicio) -> int:
    """
    Rellena el `ejercicio_id` que faltaba en los mensajes de la bandeja que
    mencionaban este alias. Sin esto el alta serviría a medias: el ejercicio
    existiría en el catálogo pero las series que lo estrenaron quedarían afuera.

    Un mensaje que ya estaba "parcial" vuelve a "parseado" para que se pueda
    confirmar de nuevo; las series ya escritas no se duplican porque quedaron
    marcadas con `confirmado` (ver confirmar_mensaje).
    """
    import json as _json

    alias_norm = (alias or "").strip().upper()
    if not alias_norm:
        return 0

    mensajes = (
        session.query(MensajeParseado)
        .filter(
            MensajeParseado.tipo == "series",
            MensajeParseado.estado.in_(("parseado", "parcial")),
        )
        .all()
    )

    tocados = 0
    for m in mensajes:
        try:
            parse = _json.loads(m.parse_json) if m.parse_json else None
        except _json.JSONDecodeError:
            continue
        if not parse or not parse.get("ejercicios"):
            continue

        cambio = False
        for ej in parse["ejercicios"]:
            if ej.get("ejercicio_id"):
                continue
            if (ej.get("alias_detectado") or "").strip().upper() != alias_norm:
                continue
            ej["ejercicio_id"] = ejercicio.id
            ej["nombre_catalogo"] = ejercicio.nombre
            cambio = True

        if cambio:
            m.parse_json = _json.dumps(parse)
            if m.estado == "parcial":
                m.estado = "parseado"
            tocados += 1

    if tocados:
        session.commit()
    return tocados


def crear_ejercicio_desde_alias(session, alias, grupo_muscular):
    """
    Da de alta un ejercicio con el nombre que se escribió en el mensaje y el
    grupo que dijo Manuel, y completa los mensajes de la bandeja que lo
    estaban esperando.

    El grupo lo escribe él a propósito: que lo proponga el modelo abarata la
    interacción pero mete grupos inventados en un catálogo que después ordena
    todos los gráficos.

    Retorna dict con "ok". No lanza: el llamador chequea.
    """
    nombre = (alias or "").strip().upper()
    grupo = (grupo_muscular or "").strip().upper()
    if not nombre:
        return {"ok": False, "error": "Falta el nombre del ejercicio"}
    if not grupo:
        return {"ok": False, "error": "Falta el grupo muscular"}

    existente = (
        session.query(Ejercicio)
        .filter(Ejercicio.nombre == nombre)
        .first()
    )
    if existente is not None:
        # Puede pasar si lo cargaste desde ENTRENO mientras la pregunta estaba
        # abierta. No es un error: se reusa y se completan los mensajes igual.
        completados = _completar_mensajes_con_ejercicio(session, alias, existente)
        return {
            "ok": True,
            "ya_existia": True,
            "ejercicio": existente.to_dict(),
            "mensajes_completados": completados,
        }

    ejercicio = Ejercicio(nombre=nombre, grupo_muscular=grupo)
    session.add(ejercicio)
    session.commit()

    completados = _completar_mensajes_con_ejercicio(session, alias, ejercicio)
    return {
        "ok": True,
        "ya_existia": False,
        "ejercicio": ejercicio.to_dict(),
        "mensajes_completados": completados,
    }


def grupos_musculares(session) -> list[str]:
    """Los grupos que ya existen en el catálogo, para sugerirlos al preguntar."""
    filas = (
        session.query(Ejercicio.grupo_muscular)
        .distinct()
        .order_by(Ejercicio.grupo_muscular)
        .all()
    )
    return [f[0] for f in filas if f[0]]


# ─────────────────────────────────────────────
# Bandeja: confirmar / descartar
# ─────────────────────────────────────────────

def _sesion_destino(session, mensaje):
    """
    A qué sesión van las series de un mensaje al confirmarlo.
    Si el mensaje llegó con una sesión abierta, esa. Si no (te olvidaste el
    "inicio"), una sesión por fecha: se reusa la del día si existe.
    """
    if mensaje.sesion_id:
        s = session.query(Sesion).get(mensaje.sesion_id)
        if s is not None:
            return s

    fecha = (mensaje.recibido_en or ahora()).date()
    existente = (
        session.query(Sesion)
        .filter(Sesion.fecha == fecha)
        .order_by(Sesion.id.desc())
        .first()
    )
    if existente is not None:
        return existente

    nueva = Sesion(fecha=fecha, estado="cerrada", cerrada_en=ahora())
    session.add(nueva)
    session.flush()
    return nueva


def confirmar_mensaje(session, mensaje_id):
    """
    Escribe las series de un mensaje de la bandeja en sesiones/series.

    Los ejercicios sin match se saltean y el mensaje queda en "parcial":
    no se crea el ejercicio automáticamente, que es lo que en su momento
    partió PB INCL MANC en dos. Se dan de alta preguntando por Telegram, y
    ahí el mensaje vuelve a "parseado" para confirmar lo que faltaba.

    Por eso cada ejercicio ya escrito queda marcado con `confirmado` en el
    parse: confirmar dos veces el mismo mensaje escribe solo lo nuevo.

    Retorna dict con el resultado. No lanza: el llamador chequea "ok".
    """
    import json as _json

    mensaje = session.query(MensajeParseado).get(mensaje_id)
    if mensaje is None:
        return {"ok": False, "error": "Mensaje no encontrado"}
    if mensaje.estado in ("confirmado", "descartado"):
        return {"ok": False, "error": f"El mensaje ya está {mensaje.estado}"}
    if mensaje.tipo != "series":
        return {"ok": False, "error": f"Un mensaje de tipo '{mensaje.tipo}' no tiene series"}

    try:
        parse = _json.loads(mensaje.parse_json) if mensaje.parse_json else None
    except _json.JSONDecodeError:
        parse = None
    if not parse or not parse.get("ejercicios"):
        return {"ok": False, "error": "El mensaje no tiene ejercicios parseados"}

    sesion = _sesion_destino(session, mensaje)

    creadas = 0
    sin_match = []
    ya_estaban = 0
    for ej in parse["ejercicios"]:
        if ej.get("confirmado"):
            ya_estaban += 1
            continue

        ejercicio_id = ej.get("ejercicio_id")
        if not ejercicio_id:
            sin_match.append(ej.get("alias_detectado") or "?")
            continue
        if session.query(Ejercicio).get(ejercicio_id) is None:
            sin_match.append(ej.get("alias_detectado") or "?")
            continue

        # numero_serie sigue la numeración que ya tenga la sesión
        ya_hay = (
            session.query(Serie)
            .filter(Serie.sesion_id == sesion.id, Serie.ejercicio_id == ejercicio_id)
            .count()
        )
        for i, s in enumerate(ej.get("series", []), start=1):
            session.add(Serie(
                sesion_id=sesion.id,
                ejercicio_id=int(ejercicio_id),
                numero_serie=ya_hay + i,
                reps=int(s.get("reps") or 0),
                peso_kg=float(s.get("peso_kg") or 0),
            ))
            creadas += 1
        ej["confirmado"] = True

    if creadas == 0 and sin_match:
        session.rollback()
        return {
            "ok": False,
            "error": (
                "Lo que falta sigue sin matchear el catálogo"
                if ya_estaban else
                "Ningún ejercicio matcheó el catálogo"
            ),
            "sin_match": sin_match,
        }
    if creadas == 0 and ya_estaban:
        session.rollback()
        return {"ok": False, "error": "Ya se confirmaron todas las series de este mensaje"}

    mensaje.estado = "parcial" if sin_match else "confirmado"
    mensaje.sesion_id = sesion.id
    mensaje.parse_json = _json.dumps(parse)
    session.commit()

    return {
        "ok": True,
        "series_creadas": creadas,
        "sin_match": sin_match,
        "estado": mensaje.estado,
        "sesion": sesion.to_dict(),
    }


def descartar_mensaje(session, mensaje_id):
    """Marca el mensaje como descartado. No escribe nada en sesiones/series."""
    mensaje = session.query(MensajeParseado).get(mensaje_id)
    if mensaje is None:
        return {"ok": False, "error": "Mensaje no encontrado"}
    if mensaje.estado == "confirmado":
        return {"ok": False, "error": "El mensaje ya está confirmado"}
    mensaje.estado = "descartado"
    session.commit()
    return {"ok": True, "estado": mensaje.estado}


# Auto-init on import
init_db()
