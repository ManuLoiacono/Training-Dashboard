"""
MANU///LOGS — migracion de SQLite a Postgres (Supabase)

Copia ejercicios, sesiones, series, mensajes y preguntas de la base local
a la remota, conservando los IDs. Es la Fase 2 del plan: la base se va a
Supabase para que el bot pueda correr en un host always-on.

Uso:
    python migrate_postgres.py --dry-run      # cuenta filas, no escribe
    python migrate_postgres.py                # migra
    python migrate_postgres.py --forzar       # migra aunque el destino tenga datos

El origen es siempre `manu_logs.db`. El destino sale de DATABASE_URL en el
.env, asi que la connection string nunca pasa por la linea de comandos ni
queda en el historial de la shell.

No borra nada del origen: si algo sale mal, la base local sigue intacta y
alcanza con volver DATABASE_URL a sqlite.
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SQLITE_PATH = os.path.join(BASE_DIR, "manu_logs.db")


def _fatal(msg: str):
    print(f"\n[X] {msg}", flush=True)
    sys.exit(1)


# El orden importa: las foreign keys tienen que existir antes de apuntarlas.
# mensajes_parseados referencia sesiones, y series referencia las dos.
ORDEN = ["ejercicios", "sesiones", "series", "mensajes_parseados",
         "preguntas_pendientes"]


def main():
    ap = argparse.ArgumentParser(description="Migra la base local a Postgres")
    ap.add_argument("--dry-run", action="store_true",
                    help="solo cuenta filas, no escribe nada")
    ap.add_argument("--forzar", action="store_true",
                    help="migra aunque el destino ya tenga filas (las mezcla)")
    args = ap.parse_args()

    if not os.path.exists(SQLITE_PATH):
        _fatal(f"No encuentro la base local en {SQLITE_PATH}")

    destino_url = os.environ.get("DATABASE_URL", "")
    if not destino_url:
        _fatal("Falta DATABASE_URL en el .env (la connection string de Supabase)")

    # models normaliza la URL (postgresql:// -> postgresql+psycopg://) y arma
    # el engine con pool_pre_ping. Importarlo tambien crea las tablas.
    import models

    if not models.ES_POSTGRES:
        _fatal(
            "DATABASE_URL no apunta a Postgres, apunta a:\n"
            f"    {models.DATABASE_URL}\n"
            "Pone la connection string de Supabase en el .env antes de migrar."
        )

    print("=" * 60)
    print("  MIGRACION SQLite -> Postgres")
    print("=" * 60)
    # Nunca imprimir la URL entera: tiene la password adentro
    host = models.DATABASE_URL.split("@")[-1].split("/")[0] if "@" in models.DATABASE_URL else "?"
    print(f"  Origen:  {SQLITE_PATH}")
    print(f"  Destino: {host}")
    print()

    origen_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    OrigenSession = sessionmaker(bind=origen_engine)
    origen = OrigenSession()
    destino = models.get_session()

    # Mapa nombre_de_tabla -> clase del modelo
    modelos = {
        "ejercicios": models.Ejercicio,
        "sesiones": models.Sesion,
        "series": models.Serie,
        "mensajes_parseados": models.MensajeParseado,
        "preguntas_pendientes": models.PreguntaPendiente,
    }

    tablas_origen = set(inspect(origen_engine).get_table_names())

    try:
        # ── 1. Contar de los dos lados ────────────────────────────────
        print("  TABLA                  ORIGEN   DESTINO")
        print("  " + "-" * 40)
        total_origen = 0
        ocupadas = []
        for tabla in ORDEN:
            modelo = modelos[tabla]
            n_orig = (origen.query(func.count(modelo.id)).scalar()
                      if tabla in tablas_origen else 0)
            n_dest = destino.query(func.count(modelo.id)).scalar()
            total_origen += n_orig
            if n_dest:
                ocupadas.append(tabla)
            print(f"  {tabla:<22} {n_orig:>6}   {n_dest:>7}")
        print()

        if args.dry_run:
            print(f"  [dry-run] {total_origen} filas listas para copiar. "
                  f"No se escribio nada.")
            return

        if ocupadas and not args.forzar:
            _fatal(
                "El destino ya tiene datos en: " + ", ".join(ocupadas) + "\n"
                "Migrar de nuevo duplicaria todo. Si es lo que queres, usa "
                "--forzar;\nsi fue una prueba, vacia esas tablas primero."
            )

        # ── 2. Copiar, respetando el orden de las foreign keys ────────
        copiadas = {}
        for tabla in ORDEN:
            if tabla not in tablas_origen:
                copiadas[tabla] = 0
                continue
            modelo = modelos[tabla]
            columnas = [c.name for c in modelo.__table__.columns]

            filas = origen.query(modelo).order_by(modelo.id).all()
            for fila in filas:
                # Copia campo por campo para conservar el id original: los
                # FK entre tablas apuntan a esos numeros.
                destino.add(modelo(**{c: getattr(fila, c) for c in columnas}))
            destino.flush()
            copiadas[tabla] = len(filas)
            print(f"  {tabla:<22} {len(filas):>6} filas copiadas")

        destino.commit()

        # ── 3. Reacomodar las secuencias ──────────────────────────────
        # Insertar con id explicito NO mueve el contador de Postgres. Sin
        # esto, el primer INSERT nuevo intenta usar el id 1 y explota con
        # "duplicate key value violates unique constraint".
        print()
        for tabla in ORDEN:
            modelo = modelos[tabla]
            maximo = destino.query(func.max(modelo.id)).scalar() or 0
            destino.execute(text(
                "SELECT setval("
                "  pg_get_serial_sequence(:tabla, 'id'), :valor, true)"
            ), {"tabla": tabla, "valor": maximo})
            print(f"  secuencia {tabla:<22} -> proximo id {maximo + 1}")
        destino.commit()

        # ── 4. Verificar ──────────────────────────────────────────────
        print()
        print("  VERIFICACION")
        print("  " + "-" * 40)
        ok = True
        for tabla in ORDEN:
            modelo = modelos[tabla]
            n_orig = (origen.query(func.count(modelo.id)).scalar()
                      if tabla in tablas_origen else 0)
            n_dest = destino.query(func.count(modelo.id)).scalar()
            marca = "OK" if n_dest >= n_orig else "FALTAN"
            if n_dest < n_orig:
                ok = False
            print(f"  {tabla:<22} {n_orig:>6} -> {n_dest:>6}  [{marca}]")

        # El volumen total es el numero que mas duele si se rompe: ya paso
        # una vez que la migracion desde Sheets duplico cada serie.
        vol_orig = origen.query(
            func.sum(models.Serie.reps * models.Serie.peso_kg)).scalar() or 0
        vol_dest = destino.query(
            func.sum(models.Serie.reps * models.Serie.peso_kg)).scalar() or 0
        print(f"\n  Volumen total: {vol_orig:,.0f} kg -> {vol_dest:,.0f} kg")
        if round(vol_orig) != round(vol_dest):
            ok = False
            print("  [X] El volumen no coincide")

        print()
        if ok:
            print("  Migracion OK. La base local queda intacta como backup.")
        else:
            print("  [X] Algo no cuadra. Revisa antes de usar la base remota.")
            sys.exit(1)

    except Exception as e:
        destino.rollback()
        _fatal(f"{type(e).__name__}: {e}")
    finally:
        origen.close()
        destino.close()


if __name__ == "__main__":
    main()
