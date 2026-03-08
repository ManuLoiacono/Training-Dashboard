"""
MANU///LOGS — Parser de PDFs antropométricos (protocolo ISAK)
Extrae ~25 variables del informe de composición corporal.
Uso: python antro_parser.py pdfs/informe.pdf
"""

import os
import re
import sys
from pathlib import Path

import pdfplumber

BASE_DIR = Path(__file__).parent
PDFS_DIR = BASE_DIR / "pdfs"


def parse_all_pdfs() -> list[dict]:
    """Escanea la carpeta /pdfs/ y parsea cada PDF. Retorna lista de mediciones."""
    if not PDFS_DIR.is_dir():
        return []

    results = []
    for filepath in sorted(PDFS_DIR.glob("*.pdf")):
        try:
            data = parse_antro_pdf(str(filepath))
            if data:
                results.append(data)
        except Exception as e:
            print(f"[ANTRO] Error parseando {filepath.name}: {e}")
            continue

    return sorted(results, key=lambda r: r.get("fecha", ""))


def parse_antro_pdf(filepath: str) -> dict | None:
    """
    Parsea un PDF antropométrico ISAK y retorna un dict con todas las variables.
    Usa extracción de texto (no tablas) porque es más confiable para este formato.
    """
    with pdfplumber.open(filepath) as pdf:
        # Texto "limpio" de las páginas (para perímetros, pliegues, básicos)
        raw_text_parts = []
        # Texto de celdas de tablas (para masas/piel que no salen en el texto raw)
        table_cell_parts = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            raw_text_parts.append(text)
            for table in page.extract_tables():
                for row in table:
                    for cell in row:
                        if cell and cell.strip():
                            table_cell_parts.append(cell)

        raw_text = "\n".join(raw_text_parts)
        # Texto combinado: raw + celdas de tablas (para masas y complementarios)
        combined_text = raw_text + "\n" + "\n".join(table_cell_parts)

        if not raw_text.strip():
            return None

        # Extraer fecha
        fecha = _extract_fecha(filepath, raw_text)

        # Extraer datos básicos (página 1) — usa raw text
        basicos = _parse_basicos(raw_text)

        # Extraer 5 masas corporales (página 2) — usa combined (piel solo en tabla)
        masas = _parse_masas(combined_text)

        # Extraer perímetros (página 1) — usa raw text (tiene Score-Z en la misma línea)
        perimetros = _parse_perimetros(raw_text)

        # Extraer pliegues cutáneos (página 1) — usa raw text
        pliegues = _parse_pliegues(raw_text)

        # Extraer datos complementarios (página 2) — usa combined
        complementarios = _parse_complementarios(combined_text)

        if not basicos.get("peso"):
            return None

        return {
            # Campos básicos (formato API del dashboard)
            "fecha": fecha,
            "peso_kg": basicos.get("peso", "0"),
            "talla_cm": basicos.get("talla", "0"),
            "masa_muscular_kg": masas.get("muscular_kg", "0"),
            "masa_adiposa_kg": masas.get("adiposa_kg", "0"),
            "masa_osea_kg": masas.get("osea_kg", "0"),
            "masa_residual_kg": masas.get("residual_kg", "0"),
            "masa_piel_kg": masas.get("piel_kg", "0"),
            "cintura_cm": perimetros.get("cintura", "0"),
            "brazo_relajado_cm": perimetros.get("brazo_relajado", "0"),
            "brazo_flex_cm": perimetros.get("brazo_flex", "0"),
            "metabolismo_basal": complementarios.get("metabolismo_basal", "0"),
            "gasto_total": complementarios.get("gasto_total", "0"),
            # Campos extendidos para gráficos
            "perimetros_all": {
                "brazo_relajado": perimetros.get("brazo_relajado", "0"),
                "brazo_flex": perimetros.get("brazo_flex", "0"),
                "antebrazo": perimetros.get("antebrazo", "0"),
                "torax": perimetros.get("torax", "0"),
                "cintura": perimetros.get("cintura", "0"),
                "muslo_sup": perimetros.get("muslo_sup", "0"),
                "muslo_med": perimetros.get("muslo_med", "0"),
                "pantorrilla": perimetros.get("pantorrilla", "0"),
                "scorez": perimetros.get("scorez", {}),
            },
            "pliegues_all": {
                "triceps": pliegues.get("triceps", "0"),
                "subescapular": pliegues.get("subescapular", "0"),
                "supraespinal": pliegues.get("supraespinal", "0"),
                "abdominal": pliegues.get("abdominal", "0"),
                "muslo": pliegues.get("muslo", "0"),
                "pantorrilla": pliegues.get("pantorrilla", "0"),
                "scorez": pliegues.get("scorez", {}),
            },
            "masas_percent": {
                "muscular": masas.get("muscular_pct", "0"),
                "adiposa": masas.get("adiposa_pct", "0"),
                "osea": masas.get("osea_pct", "0"),
                "residual": masas.get("residual_pct", "0"),
                "piel": masas.get("piel_pct", "0"),
            },
            "masas_scorez": {
                "muscular": masas.get("muscular_sz", "0"),
                "adiposa": masas.get("adiposa_sz", "0"),
                "osea": masas.get("osea_sz", "0"),
                "residual": masas.get("residual_sz", "0"),
            },
            "factor_actividad": complementarios.get("factor_actividad", "0"),
            "suma_6_pliegues": complementarios.get("suma_6_pliegues", "0"),
        }


# ─── Helpers ────────────────────────────────────────────────


def _comma_to_float(value: str) -> str:
    """Convierte string con coma decimal a string con punto. Maneja errores."""
    if not value or "#" in value or "NUM" in value.upper():
        return "0"
    cleaned = value.strip().replace(",", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    try:
        return str(round(float(cleaned), 2))
    except ValueError:
        return "0"


def _extract_fecha(filepath: str, text: str) -> str:
    """Extrae la fecha del nombre del archivo o del texto del PDF."""
    # Patrón del nombre: "...13-02-26.pdf" -> 2026-02-13
    basename = os.path.basename(filepath)
    match = re.search(r"(\d{1,2})-(\d{2})-(\d{2})\.pdf$", basename, re.IGNORECASE)
    if match:
        day, month, year = match.groups()
        year_full = f"20{year}" if int(year) < 50 else f"19{year}"
        return f"{year_full}-{month.zfill(2)}-{day.zfill(2)}"

    # Fallback: buscar "Fecha de medición: DD/MM/YYYY" en el texto
    date_match = re.search(r"Fecha de medici[oó]n:\s*(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if date_match:
        day, month, year = date_match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    return ""


def _parse_basicos(text: str) -> dict:
    """Extrae peso y talla del texto."""
    result = {}

    # Peso (kg) 77,50
    peso_match = re.search(r"Peso\s*\(kg\)\s*(\d+[,\.]\d+)", text)
    if peso_match:
        result["peso"] = _comma_to_float(peso_match.group(1))

    # Talla (cm) 173,50
    talla_match = re.search(r"Talla\s*\(cm\)\s*(\d+[,\.]\d+)", text)
    if talla_match:
        result["talla"] = _comma_to_float(talla_match.group(1))

    return result


def _parse_masas(text: str) -> dict:
    """Extrae las 5 masas corporales de la página 2."""
    result = {}

    # Patrón: "Masa Adiposa 16,072 22,96% 17,83 -1,30 1,754"
    # Captura: porcentaje, kg, score-z
    masa_patterns = {
        "adiposa": r"Masa\s+Adiposa\s+[\d,\.]+\s+([\d,\.]+)%\s+([\d,\.]+)\s+([\-\d,\.]+)",
        "muscular": r"Masa\s+Muscular\s+[\d,\.]+\s+([\d,\.]+)%\s+([\d,\.]+)\s+([\-\d,\.]+)",
        "residual": r"Masa\s+Residual\s+[\d,\.]+\s+([\d,\.]+)%\s+([\d,\.]+)\s+([\-\d,\.]+)",
        "osea": r"Masa\s+[OÓ]sea\s+[\d,\.]+\s+([\d,\.]+)%\s+([\d,\.]+)\s+([\-\d,\.]+)",
    }

    for name, pattern in masa_patterns.items():
        match = re.search(pattern, text)
        if match:
            pct, kg, sz = match.groups()
            result[f"{name}_pct"] = _comma_to_float(pct)
            result[f"{name}_kg"] = _comma_to_float(kg)
            result[f"{name}_sz"] = _comma_to_float(sz)

    # Masa de la Piel: buscar "4,78% 3,71" en el texto (está agrupada con otras masas)
    piel_match = re.search(r"Masa\s+de\s+la\s+Piel\s+[\d,\.]+\s+([\d,\.]+)%\s+([\d,\.]+)", text)
    if piel_match:
        result["piel_pct"] = _comma_to_float(piel_match.group(1))
        result["piel_kg"] = _comma_to_float(piel_match.group(2))
    else:
        # Buscar en el bloque agrupado de masas (tabla página 2):
        # "50,15% 38,93 ... \n 10,64% 8,26 ... \n 11,47% 8,78 ... \n 4,78% 3,71 ..."
        piel_pct_match = re.search(r"([\d,\.]+)%\s+([\d,\.]+)\s+#", text)
        if piel_pct_match:
            result["piel_pct"] = _comma_to_float(piel_pct_match.group(1))
            result["piel_kg"] = _comma_to_float(piel_pct_match.group(2))
        else:
            # Fallback: buscar "Piel; 3,71" en el texto del gráfico
            piel_fallback = re.search(r"Piel;\s*([\d,\.]+)", text)
            if piel_fallback:
                result["piel_kg"] = _comma_to_float(piel_fallback.group(1))

    return result


def _extract_first_and_last_number(line: str) -> tuple[str, str]:
    """Extrae el primer y último número de una línea de texto.
    Retorna (primer_numero, ultimo_numero) como strings."""
    numbers = re.findall(r"-?\d+[,\.]\d+", line)
    if len(numbers) >= 2:
        return numbers[0], numbers[-1]
    elif len(numbers) == 1:
        return numbers[0], numbers[0]
    return "", ""


def _parse_perimetros(text: str) -> dict:
    """Extrae perímetros y sus Score-Z del texto de la página 1."""
    result = {}
    scorez = {}

    # Cada línea tiene: "Brazo Relajado 34,70 34,04 0,63 0,30 3,07"
    # Primer número = valor medido, último número = Score-Z
    perim_keywords = [
        (r"Brazo\s+Relajado\s+", "brazo_relajado"),
        (r"Brazo\s+Flexionado\s+en\s+Tensi[oó]n\s+", "brazo_flex"),
        (r"Antebrazo\s+", "antebrazo"),
        (r"T[oó]rax\s+Mesoesternal\s+", "torax"),
        (r"Cintura\s+\(m[ií]nima\)\s+", "cintura"),
        (r"Muslo\s+\(superior\)\s+", "muslo_sup"),
    ]

    for line in text.split("\n"):
        for pattern, key in perim_keywords:
            if re.search(pattern, line):
                first, last = _extract_first_and_last_number(line)
                if first:
                    result[key] = _comma_to_float(first)
                    scorez[key] = _comma_to_float(last)
                break

    # Muslo (medial) aparece en perímetros (~57cm) y pliegues (~11mm)
    for line in text.split("\n"):
        if re.search(r"Muslo\s+\(medial\)", line):
            first, last = _extract_first_and_last_number(line)
            val = float(_comma_to_float(first)) if first else 0
            if val > 30:  # perímetro > 30cm
                result["muslo_med"] = _comma_to_float(first)
                scorez["muslo_med"] = _comma_to_float(last)
                break

    # Pantorrilla (máxima) = perímetro (~36cm)
    for line in text.split("\n"):
        if re.search(r"Pantorrilla\s+\(m[aá]xima\)", line):
            first, last = _extract_first_and_last_number(line)
            if first:
                result["pantorrilla"] = _comma_to_float(first)
                scorez["pantorrilla"] = _comma_to_float(last)
            break

    result["scorez"] = scorez
    return result


def _parse_pliegues(text: str) -> dict:
    """Extrae pliegues cutáneos y sus Score-Z del texto de la página 1."""
    result = {}
    scorez = {}

    # Cada línea tiene: "Tríceps 10,00 9,81 1,55 ... -1,25"
    # Primer número = valor medido (mm), último = Score-Z
    pliegue_keywords = [
        (r"Tr[ií]ceps\s+", "triceps"),
        (r"Subescapular\s+", "subescapular"),
        (r"Supraespinal\s+", "supraespinal"),
        (r"Abdominal\s+", "abdominal"),
    ]

    for line in text.split("\n"):
        for pattern, key in pliegue_keywords:
            if re.search(pattern, line):
                first, last = _extract_first_and_last_number(line)
                if first:
                    result[key] = _comma_to_float(first)
                    scorez[key] = _comma_to_float(last)
                break

    # Muslo (medial) pliegue (~11mm, no ~57cm que es perímetro)
    for line in text.split("\n"):
        if re.search(r"Muslo\s+\(medial\)", line):
            first, last = _extract_first_and_last_number(line)
            val = float(_comma_to_float(first)) if first else 0
            if val < 30:  # pliegue < 30mm
                result["muslo"] = _comma_to_float(first)
                scorez["muslo"] = _comma_to_float(last)

    # Pantorrilla pliegue (~9mm, sin "(máxima)" que es el perímetro)
    for line in text.split("\n"):
        if re.search(r"Pantorrilla\s+\d", line) and not re.search(r"m[aá]xima", line):
            first, last = _extract_first_and_last_number(line)
            val = float(_comma_to_float(first)) if first else 0
            if val < 30:  # pliegue < 30mm
                result["pantorrilla"] = _comma_to_float(first)
                scorez["pantorrilla"] = _comma_to_float(last)
                break

    result["scorez"] = scorez
    return result


def _parse_complementarios(text: str) -> dict:
    """Extrae metabolismo basal, gasto total, factor actividad, etc."""
    result = {}

    # Metabolismo basal: 1755,4 Kcal
    meta_match = re.search(r"Metabolismo\s+basal:\s*([\d,\.]+)", text)
    if meta_match:
        result["metabolismo_basal"] = _comma_to_float(meta_match.group(1))

    # Gasto energético total estimado: 2809 Kcal
    gasto_match = re.search(r"Gasto\s+energ[eé]tico\s+total\s+estimado:\s*([\d,\.]+)", text)
    if gasto_match:
        result["gasto_total"] = _comma_to_float(gasto_match.group(1))

    # F.Act: 1,6
    fact_match = re.search(r"F\.Act:\s*([\d,\.]+)", text)
    if fact_match:
        result["factor_actividad"] = _comma_to_float(fact_match.group(1))

    # Sum 6pl 72,50
    sum6_match = re.search(r"Sum\s+6pl\s+([\d,\.]+)", text)
    if sum6_match:
        result["suma_6_pliegues"] = _comma_to_float(sum6_match.group(1))

    return result


# ─── CLI ────────────────────────────────────────────────


def _print_result(data: dict) -> None:
    """Imprime los datos extraídos de forma legible."""
    print(f"\n{'='*50}")
    print(f"  INFORME ANTROPOMÉTRICO — {data['fecha']}")
    print(f"{'='*50}")
    print(f"  Peso: {data['peso_kg']} kg")
    print(f"  Talla: {data['talla_cm']} cm")
    print(f"\n  5 MASAS CORPORALES:")
    print(f"    Muscular: {data['masa_muscular_kg']} kg ({data['masas_percent']['muscular']}%)")
    print(f"    Adiposa:  {data['masa_adiposa_kg']} kg ({data['masas_percent']['adiposa']}%)")
    print(f"    Ósea:     {data['masa_osea_kg']} kg ({data['masas_percent']['osea']}%)")
    print(f"    Residual: {data['masa_residual_kg']} kg ({data['masas_percent']['residual']}%)")
    print(f"    Piel:     {data['masa_piel_kg']} kg ({data['masas_percent']['piel']}%)")
    print(f"\n  PERÍMETROS (cm):")
    p = data['perimetros_all']
    sz = p['scorez']
    for key, label in [
        ("brazo_relajado", "Brazo Relajado"),
        ("brazo_flex", "Brazo Flexionado"),
        ("antebrazo", "Antebrazo"),
        ("torax", "Tórax"),
        ("cintura", "Cintura"),
        ("muslo_sup", "Muslo Superior"),
        ("muslo_med", "Muslo Medial"),
        ("pantorrilla", "Pantorrilla"),
    ]:
        val = p.get(key, "0")
        s = sz.get(key, "-")
        print(f"    {label}: {val} cm (Score-Z: {s})")
    print(f"\n  PLIEGUES CUTÁNEOS (mm):")
    pl = data['pliegues_all']
    plsz = pl['scorez']
    for key, label in [
        ("triceps", "Tríceps"),
        ("subescapular", "Subescapular"),
        ("supraespinal", "Supraespinal"),
        ("abdominal", "Abdominal"),
        ("muslo", "Muslo"),
        ("pantorrilla", "Pantorrilla"),
    ]:
        val = pl.get(key, "0")
        s = plsz.get(key, "-")
        print(f"    {label}: {val} mm (Score-Z: {s})")
    print(f"\n  DATOS COMPLEMENTARIOS:")
    print(f"    Metabolismo basal: {data['metabolismo_basal']} Kcal")
    print(f"    Gasto total est.: {data['gasto_total']} Kcal")
    print(f"    Factor actividad: {data['factor_actividad']}")
    print(f"    Suma 6 pliegues:  {data['suma_6_pliegues']} mm")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        data = parse_antro_pdf(filepath)
        if data:
            _print_result(data)
        else:
            print(f"No se pudieron extraer datos de {filepath}")
            sys.exit(1)
    else:
        results = parse_all_pdfs()
        if not results:
            print("No se encontraron PDFs en la carpeta /pdfs/")
            sys.exit(1)
        for r in results:
            _print_result(r)
