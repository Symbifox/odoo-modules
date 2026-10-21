"""Mise en table des sources externes : JSON, CSV, classeur Excel, lignes SQL.

Chaque lecteur rend le même dictionnaire que Grist depuis le début :
{"header": [...], "types": [...], "rows": [[...]], "truncated": bool}. Les types disent
au tableur comment afficher la colonne : "Date" (serial entier), "DateTime" (serial),
"Numeric", "Bool", "Text".
"""
import csv
import datetime
import decimal
import io
import json
import math
import re
import zipfile

# Serial de tableur : jours depuis le 1899-12-30.
_EPOCH = datetime.datetime(1899, 12, 30)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_NUMBER_COMMA = re.compile(r"-?\d+(,\d+)?")

MAX_UNZIPPED = 200 * 1024 * 1024  # classeur décompressé, au plus (bombe zip)
MAX_ZIP_ENTRIES = 10000
MAX_CELL_TEXT = 1000
MAX_COLUMNS = 500  # colonnes lues d'une feuille au plus
MAX_SCANNED_ROWS = 200000  # lignes parcourues au plus, vides comprises
MAX_FLATTEN_DEPTH = 3


class SourceFormatError(Exception):
    """La source ne se lit pas comme une table. Le message est sûr à montrer."""


def to_serial(moment):
    """date ou datetime → serial de tableur. Un datetime avec fuseau est ramené en UTC."""
    if isinstance(moment, datetime.datetime):
        if moment.tzinfo:
            moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return (moment - _EPOCH).total_seconds() / 86400
    return (moment - _EPOCH.date()).days


def _text(value):
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_CELL_TEXT else text[:MAX_CELL_TEXT] + "…"


def _number(value):
    value = float(value) if isinstance(value, decimal.Decimal) else value
    # NaN et l'infini (Decimal('NaN') de PostgreSQL, NaN admis par json) casseraient le JSON
    # rendu au navigateur : une case vide.
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _parse_iso(text):
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def typed(header, raw_rows, limit, number_comma=False):
    """Colonnes typées d'après leurs valeurs : une colonne n'est Date, DateTime, Numeric ou
    Bool que si TOUTES ses valeurs non vides le sont ; sinon du texte, tel quel."""
    truncated = len(raw_rows) > limit
    header = list(header)[:MAX_COLUMNS]  # CSV ou JSON très larges : bornés comme un classeur
    raw_rows = [(list(r) + [None] * (len(header) - len(r)))[:len(header)] for r in raw_rows[:limit]]
    types = []
    columns = []
    for col in range(len(header)):
        values = [r[col] for r in raw_rows]
        present = [v for v in values if v is not None and v != ""]
        kind, convert = "Text", lambda v: _text(v)
        if present and all(isinstance(v, bool) for v in present):
            kind, convert = "Bool", lambda v: v
        elif present and all(isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool)
                             for v in present):
            kind, convert = "Numeric", _number
        elif present and all(isinstance(v, datetime.datetime) for v in present):
            if all(v.time() == datetime.time() and not v.tzinfo for v in present):
                kind, convert = "Date", lambda v: round(to_serial(v))
            else:
                kind, convert = "DateTime", to_serial
        elif present and all(isinstance(v, datetime.date) and not isinstance(v, datetime.datetime)
                             for v in present):
            kind, convert = "Date", to_serial
        elif present and all(isinstance(v, str) for v in present):
            if all(_DATE.fullmatch(v) for v in present) and all(_parse_iso(v) for v in present):
                kind, convert = "Date", lambda v: round(to_serial(_parse_iso(v)))
            elif all(_DATETIME.fullmatch(v) and _parse_iso(v) for v in present):
                kind, convert = "DateTime", lambda v: to_serial(_parse_iso(v))
            elif all(_NUMBER.fullmatch(v) for v in present):
                kind, convert = "Numeric", lambda v: float(v) if "." in v else int(v)
            elif number_comma and all(_NUMBER_COMMA.fullmatch(v) for v in present):
                kind, convert = "Numeric", lambda v: float(v.replace(",", ".")) if "," in v else int(v)
        types.append(kind)
        columns.append([None if v is None or v == "" else convert(v) for v in values])
    rows = [[columns[c][i] for c in range(len(header))] for i in range(len(raw_rows))]
    return {"header": [_text(h) for h in header], "types": types, "rows": rows, "truncated": truncated}


# ----------------------------------------------------------------------
# JSON
# ----------------------------------------------------------------------

def _flatten(record, prefix="", depth=0, out=None):
    out = {} if out is None else out
    for key, value in record.items():
        name = "%s.%s" % (prefix, key) if prefix else str(key)
        if isinstance(value, dict) and depth < MAX_FLATTEN_DEPTH:
            _flatten(value, name, depth + 1, out)
        elif isinstance(value, list):
            if all(not isinstance(v, (dict, list)) for v in value):
                out[name] = ", ".join("" if v is None else str(v) for v in value)
            else:
                out[name] = json.dumps(value, ensure_ascii=False, default=str)
        elif isinstance(value, dict):
            out[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[name] = value
    return out


def from_json(body, records_path, limit):
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise SourceFormatError("not JSON") from None
    for step in [s for s in (records_path or "").split(".") if s]:
        if isinstance(data, dict) and step in data:
            data = data[step]
        elif isinstance(data, list) and step.isdigit() and int(step) < len(data):
            data = data[int(step)]
        else:
            raise SourceFormatError("path")
    if isinstance(data, dict):
        data = [data]  # un seul enregistrement : une table d'une ligne
    if not isinstance(data, list):
        raise SourceFormatError("path")
    if data and all(isinstance(r, list) for r in data):
        # Liste de listes (Google Sheets, beaucoup de portails) : la première est l'en-tête.
        header = ["" if h is None else str(h) for h in data[0]]
        return typed(header, data[1:limit + 2], limit)
    if data and all(not isinstance(r, (dict, list)) for r in data):
        return typed(["value"], [[r] for r in data[:limit + 1]], limit)
    records = [_flatten(r) for r in data[:limit + 1] if isinstance(r, dict)]
    header = []
    seen = set()
    for record in records:
        for key in record:
            if key not in seen:
                seen.add(key)
                header.append(key)
    return typed(header, [[r.get(h) for h in header] for r in records], limit)


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------

def _decode(body):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SourceFormatError("encoding")


def from_csv(body, limit):
    text = _decode(body)
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = []
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            rows.append(row)
            if len(rows) > limit + 1:
                break
    except csv.Error:
        raise SourceFormatError("csv") from None
    if not rows:
        return {"header": [], "types": [], "rows": [], "truncated": False}
    header, data = rows[0], rows[1:]
    data = [[cell.strip() for cell in r][:len(header)] for r in data]
    # « 12,5 » est un nombre seulement dans un CSV à point-virgule (convention française).
    return typed([h.strip() for h in header], data, limit, number_comma=delimiter == ";")


# ----------------------------------------------------------------------
# Classeur Excel (.xlsx, .xlsm)
# ----------------------------------------------------------------------

def _check_zip(body):
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise SourceFormatError("xlsx") from None
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_UNZIPPED:
        raise SourceFormatError("too big")


def check_unzipped(body):
    """Taille décompressée MESURÉE (pas celle que déclare l'archive, qui se falsifie).

    Jouée au dépôt du fichier : le fichier ne change ensuite qu'avec un nouveau dépôt.
    """
    _check_zip(body)
    archive = zipfile.ZipFile(io.BytesIO(body))
    total = 0
    for entry in archive.infolist():
        with archive.open(entry) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UNZIPPED:
                    raise SourceFormatError("too big")


def _workbook(body):
    import openpyxl  # importé à la demande : seul ce type en a besoin

    # Mesurée à chaque lecture, pas seulement au dépôt : la pièce jointe peut être réécrite
    # sans passer par la connexion. Le résultat est gardé une heure en cache.
    check_unzipped(body)
    try:
        return openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception:  # openpyxl lève toutes sortes d'erreurs sur un fichier abîmé
        raise SourceFormatError("xlsx") from None


def xlsx_sheets(body):
    workbook = _workbook(body)
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def from_xlsx(body, sheet, limit):
    workbook = _workbook(body)
    try:
        if sheet not in workbook.sheetnames:
            raise SourceFormatError("sheet")
        worksheet = workbook[sheet]
        # Les dimensions que déclare le classeur sont souvent fausses : trop petites, openpyxl
        # s'arrête à la dernière ligne déclarée et perd la suite ; trop grandes, elles feraient
        # des lignes de 16 384 cellules sans max_col. On lit ce qui est écrit.
        worksheet.reset_dimensions()
        rows = []
        for scanned, row in enumerate(worksheet.iter_rows(values_only=True, max_col=MAX_COLUMNS)):
            if scanned >= MAX_SCANNED_ROWS:
                break
            if not any(v is not None and v != "" for v in row):
                continue
            rows.append(list(row))
            if len(rows) > limit + 1:
                break
    finally:
        workbook.close()
    if not rows:
        return {"header": [], "types": [], "rows": [], "truncated": False}
    header = rows[0]
    width = max((i + 1 for i, h in enumerate(header) if h is not None and h != ""), default=0)
    header = ["" if h is None else str(h) for h in header[:width]]
    data = []
    for row in rows[1:]:
        cells = row[:width]
        # Une erreur d'Excel (#DIV/0!…) arrive en texte : elle reste du texte.
        data.append([None if v is None else v for v in cells])
    return typed(header, data, limit)


# ----------------------------------------------------------------------
# Lignes SQL
# ----------------------------------------------------------------------

def from_sql(header, rows, limit):
    cleaned = []
    for row in rows:
        cells = []
        for value in row:
            if isinstance(value, (bytes, bytearray, memoryview)):
                value = "(binary)"
            elif isinstance(value, datetime.time):
                value = value.isoformat()
            elif isinstance(value, datetime.timedelta):
                value = value.total_seconds() / 86400  # une durée en jours, comme un tableur
            elif isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False, default=str)
            elif value is not None and not isinstance(
                    value, (str, int, float, bool, decimal.Decimal, datetime.date)):
                value = str(value)
            cells.append(value)
        cleaned.append(cells)
    return typed(list(header), cleaned, limit)


# ----------------------------------------------------------------------
# Une requête, une seule
# ----------------------------------------------------------------------

def single_statement(sql):
    """La requête sans point-virgule final ; SourceFormatError si elle en contient d'autres.

    Lit les chaînes ('…', "…", $balise$…$balise$, E'…') et les commentaires (--, /* */)
    pour ne pas prendre un « ; » qui y figure pour une fin de requête.
    """
    text = (sql or "").strip()
    i, n = 0, len(text)
    end = None
    while i < n:
        c = text[i]
        if end is not None and not (c.isspace() or text.startswith("--", i) or text.startswith("/*", i)):
            raise SourceFormatError("sql")  # quoi que ce soit après le « ; » : une autre requête
        if c in ("'", '"'):
            backslash = c == "'" and i > 0 and text[i - 1] in "eE" and (
                i < 2 or not (text[i - 2].isalnum() or text[i - 2] in "_$"))
            i += 1
            while i < n:
                if backslash and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == c:
                    if i + 1 < n and text[i + 1] == c:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif c == "$":
            m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", text[i:])
            if m and not (i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")):
                tag = m.group(0)
                close = text.find(tag, i + len(tag))
                if close < 0:
                    raise SourceFormatError("sql")
                i = close + len(tag)
            else:
                i += 1
        elif text.startswith("--", i):
            newline = text.find("\n", i)
            i = n if newline < 0 else newline + 1
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise SourceFormatError("sql")
        elif c == ";":
            end = i
            i += 1
        else:
            i += 1
    statement = text if end is None else text[:end]
    if not statement.strip():
        raise SourceFormatError("sql")
    return statement
