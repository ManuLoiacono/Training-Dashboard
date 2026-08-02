"""
MANU///LOGS — Parser de mensajes de entrenamiento
Convierte texto libre en series estructuradas usando Claude.

Además de las series, clasifica qué quiso decir el mensaje: abrir una sesión,
cerrarla, cargar series o contestar una pregunta del bot.

Ejemplos:
    "inicio: Push 02/08/26"  ->  tipo=inicio, nombre PUSH, fecha 2026-08-02
    "BP 75 x 3 x 6,4,3"      ->  tipo=series, PB 3 series de 75kg con 6, 4 y 3
    "fin sesión"             ->  tipo=fin

Uso directo para probar:
    python message_parser.py "BP 75 x 3 x 6,4,3"
"""

import os
import sys
from datetime import date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import anthropic
from pydantic import BaseModel

from models import get_session, Ejercicio

# Haiku 4.5: la tarea es extracción contra un catálogo fijo, no razonamiento.
# Sin thinking ni effort — Haiku 4.5 no acepta effort.
MODELO = "claude-haiku-4-5"


# ─────────────────────────────────────────────
# Esquema de salida (structured outputs)
# ─────────────────────────────────────────────

class SerieParseada(BaseModel):
    reps: int
    peso_kg: float


class EjercicioParseado(BaseModel):
    # Lo que escribió el usuario, tal cual
    alias_detectado: str
    # id del catálogo, o None si no matcheó con confianza
    ejercicio_id: Optional[int]
    nombre_catalogo: Optional[str]
    series: list[SerieParseada]


class MensajeParse(BaseModel):
    # Qué quiso decir el mensaje:
    #   series    -> carga de ejercicios
    #   inicio    -> abrir una sesión ("inicio: Push 02/08/26")
    #   fin       -> cerrarla ("fin sesión")
    #   respuesta -> contesta una pregunta que hizo el bot ("sí", "dale", "no")
    #   otro      -> no encaja en ninguno
    tipo: str
    ejercicios: list[EjercicioParseado]
    # Solo en tipo=inicio: cómo se llama la sesión (PUSH, PIERNA, PULL...)
    nombre_sesion: Optional[str]
    # Solo en tipo=inicio: fecha en YYYY-MM-DD, resuelta contra la fecha de hoy
    fecha: Optional[str]
    # Solo en tipo=respuesta: "si" | "no"
    respuesta: Optional[str]
    # "alta" solo si todos los ejercicios matchearon y los números son claros
    confianza: str
    # Qué no entendió, o None si entendió todo
    ambiguedad: Optional[str]


# ─────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────

INSTRUCCIONES = """Sos un parser de mensajes de entrenamiento de gimnasio. Recibís un mensaje \
informal escrito por Manuel en el gimnasio y devolvés qué quiso decir.

CONTEXTO
  Hoy es {hoy}.
{contexto}

PRIMERO: CLASIFICAR EL MENSAJE en `tipo`

  "inicio"     Abre una sesión de entrenamiento. Suele venir como
               "inicio: Push 02/08/26", "inicio sesión: Pierna", "arranco pull".
               Extraé el nombre en `nombre_sesion` (PUSH, PULL, PIERNA, FULLBODY...)
               y la fecha en `fecha`, formato YYYY-MM-DD.
               Las fechas cortas son DÍA/MES/AÑO — argentino, no americano:
               "02/08/26" es el 2 de agosto de 2026, NO el 8 de febrero.
               "hoy" y "ayer" se resuelven contra la fecha de arriba.
               Si no dice fecha, usá hoy. Si no dice nombre, `nombre_sesion: null`.

  "fin"        Cierra la sesión: "fin sesión", "termine", "listo, cerramos", "fin".

  "series"     Carga de ejercicios. Es el caso más común.

  "respuesta"  Contesta una pregunta que hizo el bot: "sí", "dale", "no", "todavía no".
               Poné `respuesta`: "si" o "no". Usá este tipo SOLO si arriba dice
               que hay una pregunta pendiente. Si no hay pregunta pendiente,
               un "sí" suelto es tipo "otro".

  "otro"       No encaja en ninguno (saludos, preguntas, cualquier otra cosa).

  Un mensaje puede abrir la sesión Y traer series ("arranco push: BP 75x3x6,4,3").
  En ese caso el tipo es "inicio" y además devolvés los ejercicios.

REGLAS DE LAS SERIES  (aplican cuando hay ejercicios en el mensaje)

1. Cada ejercicio del mensaje tiene que matchear con uno del catálogo de abajo.
   Devolvé su `ejercicio_id` y su `nombre_catalogo` exactos.

2. NUNCA inventes un ejercicio. Si no matchea con confianza razonable,
   poné `ejercicio_id: null`, `nombre_catalogo: null`, describilo en `ambiguedad`
   y marcá `confianza: "baja"`. Es preferible preguntar a adivinar.

3. Los alias son libres: abreviaturas, español, inglés, con o sin tildes.
   Ejemplos: "BP"/"bench"/"banca"/"press banca" -> PB.
   "sentadilla"/"squat" -> SQ. "peso muerto rumano" -> RDL.
   "press frances" -> PF. "militar"/"press militar" -> PM MANC.

4. Formatos de series (todos válidos, no son exhaustivos):
   - "BP 75 x 3 x 6,4,3" = 3 series de 75kg con 6, 4 y 3 reps
   - "BP 75 x 6,4,3"     = lo mismo (la cantidad se deduce de las reps)
   - "BP 3x8 75"         = 3 series de 8 reps a 75kg
   - "BP 70x8 75x6 80x4" = peso distinto por serie (pirámide)
   - "dominadas 3x8"     = peso corporal -> peso_kg: 0

5. El número de series y la lista de reps tienen que ser coherentes.
   Si dice "3 series" pero lista 4 repeticiones, usá la lista, avisá en
   `ambiguedad` que el conteo no cerraba y marcá `confianza: "baja"`.
   Cualquier cosa que anotes en `ambiguedad` implica confianza baja:
   si algo te hizo dudar lo suficiente como para escribirlo, no es alta.

6. Un mensaje puede traer varios ejercicios. Devolvelos todos, en orden.

7. Peso siempre en kg. Si no hay peso y no es un ejercicio de peso corporal,
   poné 0 y mencionalo en `ambiguedad`.

8. Si el mensaje no trae ejercicios (un "fin sesión" pelado, un saludo),
   devolvé `ejercicios: []`. No inventes series que no están.

CATÁLOGO DE EJERCICIOS DISPONIBLES
{catalogo}
"""


def _catalogo_texto(session) -> str:
    ejercicios = (
        session.query(Ejercicio)
        .order_by(Ejercicio.grupo_muscular, Ejercicio.nombre)
        .all()
    )
    return "\n".join(
        f"  id={e.id:<3} {e.nombre:<22} ({e.grupo_muscular})" for e in ejercicios
    )


# ─────────────────────────────────────────────
# Parseo
# ─────────────────────────────────────────────

def _contexto_texto(sesion_abierta=None, pregunta_pendiente=False) -> str:
    """
    Estado del bot, para que el modelo pueda distinguir un "sí" que contesta
    una pregunta de un "sí" suelto, y sepa si ya hay una sesión andando.
    """
    lineas = []
    if sesion_abierta is not None:
        nombre = sesion_abierta.nombre or "sin nombre"
        lineas.append(
            f"  Hay una sesión ABIERTA: {nombre}, del {sesion_abierta.fecha.isoformat()}."
        )
    else:
        lineas.append("  No hay ninguna sesión abierta en este momento.")

    if pregunta_pendiente:
        lineas.append("  El bot preguntó si cerramos la sesión y está esperando respuesta.")
    else:
        lineas.append("  No hay ninguna pregunta pendiente del bot.")
    return "\n".join(lineas)


def parsear(texto: str, sesion_abierta=None, pregunta_pendiente: bool = False) -> MensajeParse:
    """
    Manda el mensaje a Claude y devuelve el parse validado contra el esquema.
    Lanza excepción si la API falla; el llamador decide qué hacer.
    """
    session = get_session()
    try:
        catalogo = _catalogo_texto(session)
    finally:
        session.close()

    client = anthropic.Anthropic()  # toma ANTHROPIC_API_KEY del entorno

    system = INSTRUCCIONES.format(
        hoy=date.today().isoformat(),
        contexto=_contexto_texto(sesion_abierta, pregunta_pendiente),
        catalogo=catalogo,
    )

    respuesta = client.messages.parse(
        model=MODELO,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": texto}],
        output_format=MensajeParse,
    )
    return respuesta.parsed_output


def formatear_para_telegram(parse: MensajeParse) -> str:
    """Eco de confirmación: lo que el bot entendió, para detectar errores al toque."""
    if not parse.ejercicios:
        if parse.tipo == "otro":
            return "No entendí qué querés hacer con ese mensaje. Probá /help"
        return "No entendí ningún ejercicio en ese mensaje."

    lineas = []
    volumen_total = 0.0

    for ej in parse.ejercicios:
        nombre = ej.nombre_catalogo or f"?? {ej.alias_detectado}"
        marca = "OK" if ej.ejercicio_id else "SIN MATCH"
        pesos = {s.peso_kg for s in ej.series}
        reps = ", ".join(str(s.reps) for s in ej.series)
        vol = sum(s.reps * s.peso_kg for s in ej.series)
        volumen_total += vol

        if len(pesos) == 1:
            peso = pesos.pop()
            cabecera = f"{nombre} — {len(ej.series)} series @ {peso:g}kg"
        else:
            cabecera = f"{nombre} — {len(ej.series)} series"
            reps = ", ".join(f"{s.reps}x{s.peso_kg:g}kg" for s in ej.series)

        lineas.append(f"[{marca}] {cabecera}\n   reps: {reps}   vol: {vol:,.0f} kg")

    txt = "\n".join(lineas)
    if len(parse.ejercicios) > 1:
        txt += f"\n\nVolumen total: {volumen_total:,.0f} kg"
    if parse.confianza == "baja":
        txt += "\n\n[!] Confianza baja, revisalo."
    if parse.ambiguedad:
        txt += f"\n[!] {parse.ambiguedad}"
    return txt


if __name__ == "__main__":
    mensaje = " ".join(sys.argv[1:]) or "BP 75 x 3 x 6,4,3"
    print(f"MENSAJE: {mensaje}\n")
    resultado = parsear(mensaje)
    print(formatear_para_telegram(resultado))
    print("\n--- JSON ---")
    print(resultado.model_dump_json(indent=2))
