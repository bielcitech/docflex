"""Calculs automatiques des formulaires PDF (totaux, moyennes, expressions simples).

Les formulaires d'Acrobat décrivent leurs calculs en JavaScript. Sans moteur JavaScript, on
reconnaît les deux formes que produit Acrobat sans programmation :

- `AFSimple_Calculate("SUM", new Array("Prix", "Port"))` : somme, moyenne, produit, min, max ;
- la « notation simplifiée » (`Prix * Quantité - Remise`), recopiée par Acrobat entre
  `/** BVCALC` et `EVCALC **/`.

Le résultat est mis en forme selon `AFNumber_Format` / `AFPercent_Format` (décimales,
séparateurs, devise). Les scripts écrits à la main restent ignorés.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

OPERATIONS = {"SUM": "Somme", "AVG": "Moyenne", "PRD": "Produit", "MIN": "Minimum", "MAX": "Maximum"}
SEPARATORS = {0: (",", "."), 1: ("", "."), 2: (".", ","), 3: ("", ","), 4: ("'", ".")}  # milliers, décimales


@dataclass(frozen=True)
class Calculation:
    target: str                  # nom du champ calculé
    operation: str = ""          # SUM, AVG… ; vide pour une expression
    sources: tuple[str, ...] = ()
    expression: str = ""         # notation simplifiée
    number_format: str = ""      # script de mise en forme (AFNumber_Format…)


# ------------------------------------------------------------------ lecture du PDF

def _holder(doc: pymupdf.Document, xref: int) -> int:
    """Objet qui porte le nom et les actions du champ : le widget lui-même ou son parent."""
    if doc.xref_get_key(xref, "T")[0] == "null":
        kind, parent = doc.xref_get_key(xref, "Parent")
        if kind == "xref":
            return int(parent.split()[0])
    return xref


def _script(doc: pymupdf.Document, holder: int, event: str) -> str:
    kind, value = doc.xref_get_key(holder, f"AA/{event}/JS")
    if kind == "string":
        return value
    if kind == "xref":
        number = int(value.split()[0])
        if doc.xref_is_stream(number):
            return _decode(doc.xref_stream(number))
        kind, value = doc.xref_get_key(number, "")  # chaîne rangée dans son propre objet
        return value if kind == "string" else ""
    return ""


def _decode(data: bytes) -> str:
    if data.startswith((b"\xfe\xff", b"\xff\xfe")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _strings(text: str) -> list[str]:
    return [s for s in re.findall(r'"((?:[^"\\]|\\.)*)"', text)]


def parse_calculation(target: str, script: str, number_format: str = "") -> Calculation | None:
    simple = re.search(r'AFSimple_Calculate\(\s*"(\w+)"\s*,\s*(.*?)\)\s*;?\s*$', script, re.S)
    if simple and simple.group(1).upper() in OPERATIONS:
        names = _strings(simple.group(2))
        if len(names) == 1 and "," in names[0]:  # variante : « "A, B, C" »
            names = [n.strip() for n in names[0].split(",")]
        return Calculation(target, simple.group(1).upper(), tuple(names), number_format=number_format)
    expression = re.search(r"/\*\*\s*BVCALC\s*(.*?)\s*EVCALC\s*\*\*/", script, re.S)
    if expression:
        return Calculation(target, expression=expression.group(1).strip(), number_format=number_format)
    return None


def calculations(doc: pymupdf.Document) -> list[Calculation]:
    """Calculs reconnus, dans l'ordre de calcul du formulaire (/CO), sinon dans l'ordre des pages."""
    found: dict[int, Calculation] = {}
    for page in doc:
        for widget in page.widgets():
            holder = _holder(doc, widget.xref)
            if holder in found or not widget.field_name:
                continue
            script = _script(doc, holder, "C")
            calc = parse_calculation(widget.field_name, script, _script(doc, holder, "F")) if script else None
            if calc is not None:
                found[holder] = calc
    kind, order = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/CO")
    listed = [int(n) for n in re.findall(r"(\d+)\s+\d+\s+R", order)] if kind == "array" else []
    return [found[x] for x in listed if x in found] + [c for x, c in found.items() if x not in listed]


# ------------------------------------------------------------------ nombres

def to_number(text: str) -> float | None:
    """« 1 234,50 € », « 1,234.50 », « 12 % » → nombre ; None si ce n'est pas un nombre."""
    cleaned = re.sub(r"[^\d,.\-]", "", str(text).replace("−", "-"))
    if not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:  # le dernier séparateur est celui des décimales
        thousands = "," if cleaned.rfind(".") > cleaned.rfind(",") else "."
        cleaned = cleaned.replace(thousands, "").replace(",", ".")
    elif cleaned.count(",") > 1:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _args(script: str, function: str) -> list[str]:
    match = re.search(rf"{function}\((.*?)\)", script, re.S)
    return [a.strip() for a in re.findall(r'"(?:[^"\\]|\\.)*"|[^,]+', match.group(1))] if match else []


def format_number(value: float, number_format: str) -> str:
    """Mise en forme selon AFNumber_Format / AFPercent_Format ; sinon sans zéros inutiles."""
    percent = "AFPercent_Format" in number_format
    args = _args(number_format, "AFPercent_Format" if percent else "AFNumber_Format")
    if not args:
        return f"{value:.10f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    decimals = int(float(args[0])) if args[0].lstrip("-").replace(".", "").isdigit() else 2
    style = int(float(args[1])) if len(args) > 1 and args[1].isdigit() else 0
    thousands, point = SEPARATORS.get(style, SEPARATORS[0])
    number = value * 100 if percent else value
    text = f"{abs(number):,.{decimals}f}".replace(",", "\0").replace(".", point).replace("\0", thousands)
    sign = "-" if round(number, decimals) < 0 else ""
    if percent:
        return f"{sign}{text} %"
    currency = args[4].strip('"') if len(args) > 4 else ""
    prepend = len(args) > 5 and args[5].strip().lower() == "true"
    return f"{sign}{currency}{text}" if prepend else f"{sign}{text}{currency}"


# ------------------------------------------------------------------ expressions

_TOKEN = re.compile(r"\s*(?:(\d+(?:[.,]\d+)?)|([-+*/()])|((?:\\.|[^\s\-+*/()\\])+))")


def evaluate(expression: str, value_of) -> float:
    """Notation simplifiée : nombres, noms de champs (« \\ » protège espaces et symboles),
    + - * / et parenthèses. `value_of(nom)` donne la valeur numérique d'un champ."""
    tokens, pos = [], 0
    while pos < len(expression.rstrip()):
        match = _TOKEN.match(expression, pos)
        if not match or match.end() == pos:
            raise ValueError(f"expression illisible : {expression}")
        number, operator, name = match.groups()
        tokens.append(("n", float(number.replace(",", "."))) if number else
                      ("o", operator) if operator else ("f", re.sub(r"\\(.)", r"\1", name)))
        pos = match.end()
    tokens.append(("end", None))
    index = 0

    def peek():
        return tokens[index]

    def take():
        nonlocal index
        index += 1
        return tokens[index - 1]

    def atom() -> float:
        kind, value = take()
        if kind == "n":
            return value
        if kind == "f":
            return value_of(value)
        if (kind, value) == ("o", "-"):
            return -atom()
        if (kind, value) == ("o", "+"):
            return atom()
        if (kind, value) == ("o", "("):
            result = sum_()
            if take() != ("o", ")"):
                raise ValueError("parenthèse non fermée")
            return result
        raise ValueError(f"expression illisible : {expression}")

    def product() -> float:
        result = atom()
        while peek()[0] == "o" and peek()[1] in "*/":
            operator = take()[1]
            right = atom()
            result = result * right if operator == "*" else (result / right if right else 0.0)
        return result

    def sum_() -> float:
        result = product()
        while peek()[0] == "o" and peek()[1] in "+-":
            operator = take()[1]
            result = result + product() if operator == "+" else result - product()
        return result

    result = sum_()
    if peek()[0] != "end":
        raise ValueError(f"expression illisible : {expression}")
    return result


# ------------------------------------------------------------------ calcul

def _values(doc: pymupdf.Document) -> dict[str, str]:
    values = {}
    for page in doc:
        for widget in page.widgets():
            if widget.field_name and widget.field_name not in values:
                values[widget.field_name] = str(widget.field_value or "")
    return values


def compute(calc: Calculation, values: dict[str, str]) -> float:
    def value_of(name: str) -> float:
        return to_number(values.get(name, "")) or 0.0

    if calc.expression:
        return evaluate(calc.expression, value_of)
    numbers = [value_of(name) for name in calc.sources]
    if not numbers:
        return 0.0
    if calc.operation == "SUM":
        return sum(numbers)
    if calc.operation == "AVG":
        return sum(numbers) / len(numbers)
    if calc.operation == "PRD":
        result = 1.0
        for number in numbers:
            result *= number
        return result
    return min(numbers) if calc.operation == "MIN" else max(numbers)


def recalculate(doc: pymupdf.Document) -> set[int]:
    """Recalcule les champs calculés ; retourne les pages dont un champ a changé.

    Plusieurs passes : un total peut dépendre d'un sous-total calculé plus loin."""
    if not doc.is_form_pdf:
        return set()  # pas de formulaire : rien à parcourir
    calcs = calculations(doc)
    changed: set[int] = set()
    for _ in range(len(calcs) + 1):
        values, moved = _values(doc), False
        for calc in calcs:
            try:
                text = format_number(compute(calc, values), calc.number_format)
            except ValueError:
                continue  # expression que l'on ne sait pas lire : le champ garde sa valeur
            if values.get(calc.target) == text:
                continue
            for page in doc:
                for widget in page.widgets():
                    if widget.field_name == calc.target:
                        widget.field_value = text
                        widget.update()
                        changed.add(page.number)
            values[calc.target], moved = text, True
        if not moved:
            break
    return changed


def set_calculation(doc: pymupdf.Document, page: pymupdf.Page, xref: int, operation: str,
                    sources: list[str], decimals: int = 2) -> None:
    """Fait de ce champ le résultat d'une opération sur d'autres champs (lecture seule).

    Écrit comme Acrobat : action de calcul et de mise en forme, ordre de calcul (/CO). PyMuPDF
    n'enregistre pas ces scripts sur un champ existant : on les écrit directement."""
    holder = _holder(doc, xref)
    names = ", ".join(f'"{name}"' for name in sources)
    calc = f'AFSimple_Calculate("{operation}", new Array ({names}));'
    fmt = f'AFNumber_Format({decimals}, 2, 0, 0, "", false);'
    doc.xref_set_key(holder, "AA", f"<</C<</S/JavaScript/JS {pymupdf.get_pdf_str(calc)}>>"
                                   f"/F<</S/JavaScript/JS {pymupdf.get_pdf_str(fmt)}>>>>")
    kind, flags = doc.xref_get_key(holder, "Ff")
    doc.xref_set_key(holder, "Ff", str((int(flags) if kind == "int" else 0) | pymupdf.PDF_FIELD_IS_READ_ONLY))
    catalog = doc.pdf_catalog()
    kind, order = doc.xref_get_key(catalog, "AcroForm/CO")
    listed = re.findall(r"\d+\s+\d+\s+R", order) if kind == "array" else []
    if f"{holder} 0 R" not in listed:
        doc.xref_set_key(catalog, "AcroForm/CO", "[" + " ".join(listed + [f"{holder} 0 R"]) + "]")
    recalculate(doc)
