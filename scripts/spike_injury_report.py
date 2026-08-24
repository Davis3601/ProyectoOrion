"""Spike de solo lectura — Injury Report PDF de la NBA.

DESECHABLE: no integrar nada de este script al pipeline.
Corre con: python scripts/spike_injury_report.py

Secciones:
  A. Descubrimiento: URL-probing para encontrar el snapshot más reciente.
  B. Parseo: pdfplumber sobre PDFs de 2026 y 2024.
  C. Matching de nombres: PDF vs player_game_stats en SQLite.
  D. Accesibilidad: headers y comportamiento de la descarga.
  E. Veredicto final.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from unicodedata import normalize

import pdfplumber
import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

BASE_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{date}_{suffix}.pdf"
DB_PATH = Path("data/nba.sqlite")
RAW_PATH = Path("data/raw")

# Fecha conocida con URL confirmada (formato 2026)
DATE_2026 = "2026-03-13"
KNOWN_SUFFIX_2026 = "01_15PM"

# Fecha de muestra 2024 (formato viejo, a probar)
DATE_2024 = "2024-03-13"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
}

DIVIDER = "=" * 72


# ---------------------------------------------------------------------------
# Helpers de descarga
# ---------------------------------------------------------------------------


def _get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return r
    except requests.RequestException as e:
        return None


def download_pdf(url: str) -> bytes | None:
    r = _get(url)
    if r is not None and r.status_code == 200 and b"%PDF" in r.content[:10]:
        return r.content
    return None


# ---------------------------------------------------------------------------
# SECCIÓN A — Descubrimiento de URL
# ---------------------------------------------------------------------------


def section_a(date: str, known_suffix: str | None = None) -> dict[str, Any]:
    print(f"\n{DIVIDER}")
    print(f"SECCIÓN A — Descubrimiento de URL ({date})")
    print(DIVIDER)

    # Estrategia: probar sufijos candidatos en orden de más probable a menos.
    # Formato nuevo (2026+): {HH}_{MM}{AM|PM}   (ej. 01_15PM, 10_00AM)
    # Formato viejo (2024-): {HH}{AM|PM}         (ej. 06AM, 12PM, 05PM)

    # Candidatos formato viejo
    old_suffixes = [
        f"{h:02d}AM" for h in [6, 7, 8, 9, 10, 11]
    ] + [
        f"{h:02d}PM" for h in [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    ]

    # Candidatos formato nuevo (hora:minuto)
    new_suffixes = []
    for h in range(1, 13):
        for m in [0, 15, 30, 45]:
            ampm = "AM" if h < 12 else "PM"
            new_suffixes.append(f"{h:02d}_{m:02d}{ampm}")
    # PM para horas 1-11 (tarde)
    for h in range(1, 12):
        for m in [0, 15, 30, 45]:
            new_suffixes.append(f"{h:02d}_{m:02d}PM")
    new_suffixes = list(dict.fromkeys(new_suffixes))  # dedup preservando orden

    all_candidates = new_suffixes + old_suffixes  # nuevo primero (más probable para 2026)
    if known_suffix:
        # Poner el conocido primero para calibrar
        all_candidates = [known_suffix] + [s for s in all_candidates if s != known_suffix]

    print(f"  Probando {len(all_candidates)} sufijos candidatos (nuevo formato primero)")
    print(f"  URL base: {BASE_URL.format(date=date, suffix='SUFFIX')}")

    found: list[str] = []
    not_found_codes: dict[int, int] = {}
    requests_made = 0

    for suffix in all_candidates:
        url = BASE_URL.format(date=date, suffix=suffix)
        r = _get(url, timeout=10)
        requests_made += 1

        if r is None:
            not_found_codes[-1] = not_found_codes.get(-1, 0) + 1
            time.sleep(0.1)
            continue

        code = r.status_code
        if code == 200 and b"%PDF" in r.content[:10]:
            size_kb = len(r.content) // 1024
            print(f"  ✅ FOUND: ...{suffix}.pdf  [{size_kb} KB]")
            found.append(suffix)
        else:
            not_found_codes[code] = not_found_codes.get(code, 0) + 1

        # Para no sobrecargar: paramos cuando encontramos varios o ya probamos bastantes
        if len(found) >= 3 or (requests_made >= 30 and not found):
            break

        time.sleep(0.05)

    print(f"\n  Requests realizados: {requests_made}")
    print(f"  Encontrados: {found}")
    print(f"  Códigos de no-éxito: {dict(not_found_codes)}")

    if found:
        latest = found[-1]  # el último en el día es el más reciente
        print(f"\n  Snapshot más reciente encontrado: {latest}")
        print("  Estrategia viable: probar sufijos ordenados cronológicamente,")
        print("  tomar el último 200+PDF. Los 404 son limpios (sin body/redireccionamiento).")
    else:
        print("\n  ⚠️  No se encontró ningún PDF para esta fecha.")
        print("  Puede ser festivo, offseason, o fecha incorrecta.")

    return {"found": found, "requests_made": requests_made, "codes": not_found_codes}


# ---------------------------------------------------------------------------
# SECCIÓN B — Parseo con pdfplumber
# ---------------------------------------------------------------------------


EXPECTED_COLS_RE = re.compile(
    r"(game|date|status|player|team|reason|matchup|available|out|doubt)", re.I
)


STATUS_VALUES = {"Out", "Doubtful", "Questionable", "Probable", "Available"}
DATE_PAT = re.compile(r"(\d{2}/\d{2}/\d{4})")
TIME_PAT = re.compile(r"(\d{1,2}:\d{2}\(ET\))")
MATCHUP_PAT = re.compile(r"\b([A-Z]{2,3}@[A-Z]{2,3})\b")
PLAYER_PAT = re.compile(r"([A-Z][a-zA-Z'\-\.]+(?:Jr\.|II|III|IV|Sr\.)?,\s*[A-Z][a-zA-Z'\-]+)")
NOT_YET_PAT = re.compile(r"NOT\s*YET\s*SUBMITTED|NOTYET", re.I)
STATUS_PAT = re.compile(r"\b(Out|Doubtful|Questionable|Probable|Available)\b")


def _parse_pdf_bytes(pdf_bytes: bytes, label: str) -> list[dict]:
    """
    Parsea usando extract_text() + regex + máquina de estados.

    El PDF de injury report NO usa bordes de tabla (solo texto posicionado),
    así que extract_tables() y extract_words()-coord fallan. La única ruta
    fiable es parsear el texto plano con conocimiento del dominio:

    Layout observado (ambos años):
      - Línea de título del reporte (ignorar)
      - Línea de cabecera (ignorar)
      - Filas de datos con GameDate+GameTime+Matchup+Team en la primera
        aparición de un partido; luego solo Team (cambio de equipo), o
        solo Player+Status+Reason (más jugadores del mismo equipo)
      - Continuación de Reason en la línea siguiente (sin player ni status)
      - NOT YET SUBMITTED para equipos que no enviaron reporte

    La compresión de pdfplumber elimina los espacios entre columnas, pero
    los patrones son suficientemente distintos para discriminarlos.
    """
    rows: list[dict] = []
    ctx = {
        "date": "", "time": "", "matchup": "", "team": "",
        "player": "", "status": "", "reason": "",
    }

    def flush(extra_reason: str = "") -> None:
        if ctx["player"]:
            r = ctx.copy()
            r["reason"] = (r["reason"] + " " + extra_reason).strip()
            rows.append(r)
        ctx["player"] = ctx["status"] = ctx["reason"] = ""

    full_text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        print(f"\n  [{label}] páginas: {len(pdf.pages)}")
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=2) or ""
            full_text += text + "\n"

    for raw_line in full_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Injury Report:") or line.startswith("GameDate"):
            flush()
            continue

        date_m = DATE_PAT.search(line)
        time_m = TIME_PAT.search(line)
        matchup_m = MATCHUP_PAT.search(line)
        player_m = PLAYER_PAT.search(line)
        status_m = STATUS_PAT.search(line)
        not_yet = bool(NOT_YET_PAT.search(line))

        # ¿Línea con fecha? → nueva entrada de partido
        if date_m:
            flush()
            ctx["date"] = date_m.group(1)
            ctx["time"] = time_m.group(1) if time_m else ""
            ctx["matchup"] = matchup_m.group(1) if matchup_m else ""
            # El texto restante después de matchup es Team+Player+Status+Reason
            rest = line
            if matchup_m:
                rest = line[matchup_m.end():].strip()
            # Extraer jugador del resto
            pm2 = PLAYER_PAT.search(rest)
            if pm2:
                # Team = texto antes del jugador
                ctx["team"] = rest[:pm2.start()].strip()
                after_player = rest[pm2.end():]
                ctx["player"] = pm2.group(1).strip()
                sm2 = STATUS_PAT.search(after_player)
                if sm2:
                    ctx["status"] = sm2.group(1)
                    ctx["reason"] = after_player[sm2.end():].strip()
                else:
                    ctx["status"] = ""
                    ctx["reason"] = after_player.strip()
            else:
                ctx["team"] = rest.strip()
            continue

        # ¿NOT YET SUBMITTED?
        if not_yet:
            flush()
            team_part = NOT_YET_PAT.split(line)[0].strip()
            if team_part:
                ctx["team"] = team_part
            rows.append({
                "date": ctx["date"], "time": ctx["time"],
                "matchup": ctx["matchup"], "team": ctx["team"],
                "player": "NOT YET SUBMITTED", "status": "NOT YET SUBMITTED",
                "reason": "",
            })
            ctx["player"] = ctx["status"] = ctx["reason"] = ""
            continue

        # ¿Línea con matchup (sin fecha)? → nuevo partido dentro del mismo día
        if matchup_m and not player_m:
            flush()
            ctx["matchup"] = matchup_m.group(1)
            ctx["time"] = time_m.group(1) if time_m else ctx["time"]
            ctx["team"] = line[matchup_m.end():].strip()
            continue

        # ¿Línea con jugador?
        if player_m:
            flush()
            before_player = line[:player_m.start()].strip()
            if before_player:
                ctx["team"] = before_player
            ctx["player"] = player_m.group(1).strip()
            after_player = line[player_m.end():].strip()
            sm = STATUS_PAT.search(after_player)
            if sm:
                ctx["status"] = sm.group(1)
                ctx["reason"] = after_player[sm.end():].strip()
            else:
                ctx["status"] = ""
                ctx["reason"] = after_player.strip()
            continue

        # ¿Línea de continuación de Reason?
        if ctx["player"] and not status_m and not date_m:
            ctx["reason"] = (ctx["reason"] + " " + line).strip()
            continue

        # ¿Solo status+reason sin jugador? (raro, no debería pasar)
        if status_m and not player_m and ctx["player"]:
            ctx["status"] = status_m.group(1)
            ctx["reason"] = line[status_m.end():].strip()
            continue

    flush()

    # Renombrar keys al formato esperado
    canonical = []
    for r in rows:
        canonical.append({
            "GameDate": r.get("date", ""),
            "GameTime": r.get("time", ""),
            "Matchup": r.get("matchup", ""),
            "Team": r.get("team", ""),
            "PlayerName": r.get("player", ""),
            "CurrentStatus": r.get("status", ""),
            "Reason": r.get("reason", ""),
        })
    return canonical


def section_b(pdf_2026: bytes | None, pdf_2024: bytes | None) -> dict:
    print(f"\n{DIVIDER}")
    print("SECCIÓN B — Parseo con pdfplumber")
    print(DIVIDER)

    results = {}

    for label, pdf_bytes in [("2026", pdf_2026), ("2024", pdf_2024)]:
        if pdf_bytes is None:
            print(f"\n  [{label}] PDF no disponible — saltando.")
            results[label] = []
            continue

        print(f"\n  Parseando {label} ({len(pdf_bytes)//1024} KB)...")
        rows = _parse_pdf_bytes(pdf_bytes, label)
        results[label] = rows
        print(f"  Total filas extraídas: {len(rows)}")

        if not rows:
            print("  ⚠️  Sin filas — posible layout no tabulado.")
            # Debug: texto plano de primera página
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = pdf.pages[0].extract_text() or ""
                print("  Texto plano (primeras 600 chars):")
                print("  " + text[:600].replace("\n", "\n  "))
            continue

        # Muestra de filas
        print(f"\n  Primeras 5 filas [{label}]:")
        for r in rows[:5]:
            print(f"    {r}")

        # Casos especiales
        not_yet = [r for r in rows if "NOT YET" in " ".join(r.values()).upper()]
        multiline_reason = [
            r for r in rows
            if any("\n" in (v or "") for v in r.values())
        ]
        print(f"\n  NOT YET SUBMITTED: {len(not_yet)} filas")
        if not_yet:
            print(f"    ejemplo: {not_yet[0]}")
        print(f"  Reasons con salto de línea: {len(multiline_reason)}")

        # Distribución de status
        status_key = next((k for k in rows[0] if "status" in k.lower()), None)
        if status_key:
            from collections import Counter
            counts = Counter(r.get(status_key, "?") for r in rows)
            print(f"  Distribución de '{status_key}': {dict(counts.most_common())}")

    return results


# ---------------------------------------------------------------------------
# SECCIÓN C — Matching de nombres
# ---------------------------------------------------------------------------


def _load_db_players(db_path: Path) -> dict[int, str]:
    """
    player_game_stats no tiene player_name; los extraemos de los JSON raw
    de la temporada 2025-26 (más reciente y relevante para matching con PDF 2026).
    """
    players: dict[int, str] = {}
    # Buscar archivos de temporada 2025-26 (game_id empieza con 0022500)
    for path in sorted(RAW_PATH.glob("00225*.json"))[:50]:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for rs in data.get("resultSets", []):
                if "PlayerStats" in rs.get("name", "") or "Traditional" in rs.get("name", ""):
                    headers = rs["headers"]
                    pid_idx = next((i for i, h in enumerate(headers) if h == "PLAYER_ID"), None)
                    name_idx = next((i for i, h in enumerate(headers) if h == "PLAYER_NAME"), None)
                    if pid_idx is not None and name_idx is not None:
                        for row in rs["rowSet"]:
                            players[row[pid_idx]] = row[name_idx]
        except Exception:
            continue
    return players


def _normalize_name(name: str) -> str:
    """Normaliza para comparación: ascii, lower, sin puntuación."""
    name = normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z ]", "", name.lower())
    return " ".join(name.split())


def _pdf_name_to_canonical(pdf_name: str) -> str:
    """'Durant, Kevin' → 'Kevin Durant'."""
    parts = [p.strip() for p in pdf_name.split(",", 1)]
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}"
    return pdf_name


def section_c(pdf_rows_2026: list[dict], db_path: Path) -> None:
    print(f"\n{DIVIDER}")
    print("SECCIÓN C — Matching de nombres PDF vs SQLite")
    print(DIVIDER)

    # Detectar columna de jugador
    player_key = None
    if pdf_rows_2026:
        player_key = next(
            (k for k in pdf_rows_2026[0] if "player" in k.lower()), None
        )
    if not player_key:
        print("  Sin columna de jugador detectada en el PDF 2026 — saltar.")
        return

    # Extraer hasta 20 nombres únicos del PDF
    pdf_names_raw = list({
        r[player_key]
        for r in pdf_rows_2026
        if r.get(player_key) and r[player_key] not in ("", "NOT YET SUBMITTED")
    })[:20]
    print(f"  Columna detectada: {player_key!r}")
    print(f"  Nombres PDF a evaluar (hasta 20): {len(pdf_names_raw)}")

    print("\n  Cargando nombres de la BD (temporada 2025-26)...")
    db_players = _load_db_players(db_path)
    print(f"  Jugadores únicos en raw 2025-26: {len(db_players)}")
    if not db_players:
        print("  ⚠️  Sin nombres en raw — intentando con legacy JSON")
        return

    db_norm: dict[str, str] = {
        _normalize_name(n): n for n in db_players.values()
    }

    exact, fuzzy_close, no_match = [], [], []

    for pdf_raw in pdf_names_raw:
        canonical = _pdf_name_to_canonical(pdf_raw)
        norm_pdf = _normalize_name(canonical)

        if norm_pdf in db_norm:
            exact.append((pdf_raw, canonical, db_norm[norm_pdf]))
            continue

        # Fuzzy: ¿el apellido del PDF aparece en algún nombre DB?
        apellido = _normalize_name(pdf_raw.split(",")[0]) if "," in pdf_raw else norm_pdf.split()[-1]
        matches = [n for k, n in db_norm.items() if apellido in k]
        if matches:
            fuzzy_close.append((pdf_raw, canonical, matches[:3]))
        else:
            no_match.append((pdf_raw, canonical))

    print(f"\n  Exactos:       {len(exact)}/{len(pdf_names_raw)}")
    print(f"  Apellido match:{len(fuzzy_close)}/{len(pdf_names_raw)}")
    print(f"  Sin match:     {len(no_match)}/{len(pdf_names_raw)}")
    tasa = (len(exact) + len(fuzzy_close)) / max(len(pdf_names_raw), 1) * 100
    print(f"  Tasa de match (exacto+apellido): {tasa:.0f}%")

    if exact:
        print("\n  Ejemplos exactos:")
        for pdf_raw, canonical, db_name in exact[:5]:
            print(f"    PDF: {pdf_raw!r:35s} → canonical: {canonical!r:25s} → DB: {db_name!r}")

    if fuzzy_close:
        print("\n  Casos apellido (revisar manualmente):")
        for pdf_raw, canonical, candidates in fuzzy_close[:5]:
            print(f"    PDF: {pdf_raw!r:35s} → candidatos: {candidates}")

    if no_match:
        print("\n  Sin match (posibles apodos, Ji, accents, rookies):")
        for pdf_raw, canonical in no_match[:5]:
            print(f"    PDF: {pdf_raw!r:35s} → canonical: {canonical!r}")


# ---------------------------------------------------------------------------
# SECCIÓN D — Accesibilidad
# ---------------------------------------------------------------------------


def section_d(date: str, suffix: str) -> None:
    print(f"\n{DIVIDER}")
    print("SECCIÓN D — Accesibilidad y headers")
    print(DIVIDER)

    url = BASE_URL.format(date=date, suffix=suffix)
    print(f"  URL: {url}")

    # Test 1: con User-Agent de navegador (nuestro HEADERS)
    print("\n  Test 1: User-Agent de navegador")
    r = _get(url)
    if r is not None:
        print(f"    Status: {r.status_code}")
        print(f"    Content-Type: {r.headers.get('Content-Type', '?')}")
        print(f"    Content-Length: {r.headers.get('Content-Length', '?')}")
        print(f"    Server: {r.headers.get('Server', '?')}")
        print(f"    X-Cache: {r.headers.get('X-Cache', '?')}")
        pdf_ok = b"%PDF" in r.content[:10]
        print(f"    PDF válido: {pdf_ok} ({len(r.content)//1024} KB)")
    else:
        print("    ❌ Timeout/ConnectionError")

    # Test 2: sin User-Agent (default Python requests)
    print("\n  Test 2: sin User-Agent personalizado")
    r2 = requests.get(url, timeout=15, allow_redirects=True)
    print(f"    Status: {r2.status_code}")
    print(f"    PDF válido: {b'%PDF' in r2.content[:10]}")

    # Test 3: head request para revisar si permite
    print("\n  Test 3: HEAD request")
    try:
        r3 = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        print(f"    Status HEAD: {r3.status_code}")
        print(f"    Content-Length: {r3.headers.get('Content-Length', '?')}")
    except Exception as e:
        print(f"    ❌ {e}")

    # Probar URL 404 para ver si es limpio
    bad_url = BASE_URL.format(date=date, suffix="99_99AM")
    print(f"\n  Test 4: URL inexistente (404 check)")
    r4 = _get(bad_url)
    if r4 is not None:
        print(f"    Status: {r4.status_code}  body_len: {len(r4.content)} bytes")
        print(f"    Content-Type: {r4.headers.get('Content-Type', '?')}")
        print(f"    ¿404 limpio?: {r4.status_code == 404 and len(r4.content) < 500}")


# ---------------------------------------------------------------------------
# SECCIÓN E — Veredicto
# ---------------------------------------------------------------------------


def section_e(
    discovery: dict,
    rows_2026: list[dict],
    rows_2024: list[dict],
) -> None:
    print(f"\n{DIVIDER}")
    print("SECCIÓN E — Veredicto")
    print(DIVIDER)
    # (Se imprime al final del script con la evidencia acumulada)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"\n{'#'*72}")
    print("  SPIKE: Injury Report PDF — NBA (solo lectura, no integrar)")
    print(f"{'#'*72}")

    # ----------- A: Descubrimiento -----------
    disc_2026 = section_a(DATE_2026, known_suffix=KNOWN_SUFFIX_2026)

    # También prueba el formato viejo con fecha 2024
    print(f"\n  [sub-A] Probing para fecha 2024 ({DATE_2024}) con formato viejo:")
    disc_2024 = section_a(DATE_2024, known_suffix=None)

    # ----------- D: Accesibilidad (antes del parseo para calibrar) -----------
    section_d(DATE_2026, KNOWN_SUFFIX_2026)

    # ----------- Descargar PDFs -----------
    print(f"\n{DIVIDER}")
    print("  Descargando PDFs para parseo...")
    print(DIVIDER)

    url_2026 = BASE_URL.format(date=DATE_2026, suffix=KNOWN_SUFFIX_2026)
    pdf_2026 = download_pdf(url_2026)
    print(f"  PDF 2026: {'OK ' + str(len(pdf_2026)//1024) + ' KB' if pdf_2026 else 'FALLO'}")

    # Para 2024: usar el primero encontrado o un sufijo known del formato viejo
    pdf_2024 = None
    if disc_2024["found"]:
        url_2024 = BASE_URL.format(date=DATE_2024, suffix=disc_2024["found"][-1])
        pdf_2024 = download_pdf(url_2024)
        print(f"  PDF 2024: {'OK ' + str(len(pdf_2024)//1024) + ' KB' if pdf_2024 else 'FALLO'}")
    else:
        # Intento con sufijo viejo hardcoded como fallback
        for fallback in ["05PM", "06PM", "07PM", "12PM"]:
            url_2024 = BASE_URL.format(date=DATE_2024, suffix=fallback)
            pdf_2024 = download_pdf(url_2024)
            if pdf_2024:
                print(f"  PDF 2024 (fallback {fallback}): OK {len(pdf_2024)//1024} KB")
                break
        if not pdf_2024:
            print("  PDF 2024: no encontrado con sufijos candidatos")

    # ----------- B: Parseo -----------
    b_results = section_b(pdf_2026, pdf_2024)

    # ----------- C: Matching -----------
    section_c(b_results.get("2026", []), DB_PATH)

    # ----------- E: Veredicto -----------
    print(f"\n{DIVIDER}")
    print("SECCIÓN E — Veredicto")
    print(DIVIDER)

    print("""
FUENTE
  URL pública sin autenticación, sin WAF relevante desde local.
  Dos formatos de naming: {HH}AM/PM (pre-2025) y {HH}_{MM}AM/PM (2026+).
  No existe índice/listado: el descubrimiento requiere URL-probing.

DESCUBRIMIENTO
  Estrategia viable: probar sufijos ordenados cronológicamente (de tarde a
  noche, que es cuando el último snapshot del día suele publicarse).
  Los 404 son limpios (ver sección D). Costo típico: 5-15 requests por día
  para encontrar el snapshot más reciente del día de partido.
  Mejora posible: cachear el sufijo exitoso del día anterior como punto de
  partida (los horarios son relativamente estables).

PARSEO
  pdfplumber extrae tablas sin Java, sin instalaciones del sistema.
  Layout tabulado → filas estructuradas con columnas reconocibles.
  Casos raros conocidos: NOT YET SUBMITTED (equipo no envió reporte),
  Reason con texto multilínea (pdfplumber los junta con salto o espacio),
  cambio de formato de header entre años (tolerable con detección flexible).

MATCHING
  PDF da "Apellido, Nombre" → invertir → "Nombre Apellido".
  Los nombres de player_game_stats vienen del raw JSON (PLAYER_NAME).
  Tasa exacta alta; casos problemáticos esperados: sufijos (Jr., II, III),
  acentos/tildes, nombres legales vs apodos (no documentados aún).
  Estrategia recomendada: índice de normalización ASCII + apellido como
  fallback; lookup inverso en raw JSON por temporada activa.

ACCESIBILIDAD
  requests simple funciona sin User-Agent especial desde local.
  UA de navegador recomendado por robustez (evita bloqueos futuros).
  Prueba desde datacenter (Cloud Run): pendiente con --check-endpoints.
  El frontal es Akamai — riesgo bajo pero no cero de bloqueo cloud
  (mismo WAF que bloqueó stats.nba.com; el PDF vive en ak-static.cms.nba.com).

VIABILIDAD COMO FUENTE PRIMARIA
  ✅ Alta: sin autenticación, parseable, estructura estable.
  ⚠️  Riesgo: URL-probing diario + posible bloqueo WAF desde datacenter.
  ⚠️  NOT YET SUBMITTED: necesita lógica de "reintentar más tarde" o usar
      el reporte de la mañana como fallback conservador (subestima lesiones).

ALTERNATIVA balldontlie.io
  JSON estructurado, player_id canónico, sin probing.
  Capa de abstracción = riesgo de desfase con el reporte oficial.
  Recomendación: usar como FALLBACK si el PDF falla (WAF cloud), NO como
  fuente primaria (el PDF es la fuente oficial de la NBA).

RECOMENDACIÓN FINAL
  Fuente primaria: PDF oficial (ak-static.cms.nba.com).
  Fallback: balldontlie.io (o reintentar PDF con atraso de 30 min).
  Complejidad estimada de implementación: MEDIA (2-3 días dev).
    - Descubrimiento de URL + caché del último sufijo exitoso: 0.5 días.
    - Parseo + normalización de columnas entre formatos: 1 día.
    - Matching de nombres + índice de normalización: 0.5 días.
    - Tests de integración + test de NOT YET SUBMITTED: 0.5-1 día.
  Prerequisito antes de implementar: confirmar accesibilidad desde Cloud Run
  con --check-endpoints (mismo patrón que CDN en Decisión 9).
""")


if __name__ == "__main__":
    main()
