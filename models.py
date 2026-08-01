"""
MANU///LOGS — SQLAlchemy models
Tablas: ejercicios, sesiones, series.
Auto-init al importar si el .db no existe.
"""

import os
from datetime import datetime

from sqlalchemy import (
    Column, Integer, Text, Float, Date, DateTime,
    ForeignKey, create_engine, event,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///manu_logs.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Para SQLite, resolver path relativo al directorio del proyecto
if DATABASE_URL.startswith("sqlite:///") and not DATABASE_URL.startswith("sqlite:////"):
    db_path = DATABASE_URL.replace("sqlite:///", "")
    DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, db_path)}"

engine = create_engine(DATABASE_URL, echo=False)

# Habilitar foreign keys en SQLite
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

Session = sessionmaker(bind=engine)
Base = declarative_base()

# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class Ejercicio(Base):
    __tablename__ = "ejercicios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(Text, nullable=False, unique=True)
    grupo_muscular = Column(Text, nullable=False)
    notas = Column(Text)
    creado_en = Column(DateTime, default=datetime.utcnow)

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
    notas = Column(Text)
    creado_en = Column(DateTime, default=datetime.utcnow)

    series = relationship("Serie", back_populates="sesion", cascade="all, delete-orphan")

    def to_dict(self, include_series=False):
        d = {
            "id": self.id,
            "fecha": self.fecha.isoformat(),
            "dia_rutina": self.dia_rutina,
            "notas": self.notas or "",
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
    creado_en = Column(DateTime, default=datetime.utcnow)

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
    # parseado | error | confirmado | descartado
    estado = Column(Text, nullable=False, default="parseado")
    error = Column(Text)
    recibido_en = Column(DateTime, default=datetime.utcnow)

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
            "recibido_en": self.recibido_en.isoformat() if self.recibido_en else "",
        }


# ─────────────────────────────────────────────
# Init DB
# ─────────────────────────────────────────────

def init_db():
    """Crea tablas si no existen."""
    Base.metadata.create_all(engine)


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


# Auto-init on import
init_db()
