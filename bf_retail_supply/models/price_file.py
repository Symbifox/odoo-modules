# -*- coding: utf-8 -*-
import base64
import csv
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, time, timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_retail.models.res_company import _bf_retail_dans_la_langue_du_commerce

MANAGER = "bf_retail.group_retail_manager"

# A wholesaler's list is a few thousand rows. Past this, the file is not a price list but
# an accident — a database export, a join gone wrong — and it is refused by name instead
# of reading and creating a quarter of a million rows in one transaction.
PLAFOND = 20000

# The headers a wholesaler's export carries, in French or in English. The WHOLE normalised
# header is looked up first, then its words one by one: "Nom" is a name, not a number that
# happens to start with "no", and "Nouveau prix" is a price. A prefix test read the
# description column as the code column and matched nothing.
ENTETES = {
    "code": ("code", "barcode", "codebarres", "codebarre", "upc", "ean", "ean13", "gtin",
             "sku", "reference", "refer", "itemcode", "codeproduit", "codearticle", "item",
             "article", "no", "number", "numero", "num", "productcode", "codefournisseur",
             "vendorcode", "noarticle", "noproduit"),
    "name": ("name", "nom", "description", "designation", "produit", "product", "libelle",
             "nomproduit", "itemdescription", "productdescription", "descriptionproduit",
             "nomarticle", "itemname", "productname"),
    "price": ("price", "prix", "cost", "cout", "unitprice", "prixunitaire", "net",
              "netprice", "prixnet", "listprice", "prixliste", "casecost", "coutcaisse",
              "unitcost", "coutunitaire", "nouveauprix", "newprice", "tarif", "caseprice",
              "prixcaisse"),
    "uom": ("uom", "unit", "unite", "format", "pack", "packsize", "case", "caisse",
            "emballage", "size", "conditionnement", "unitedemesure", "unitofmeasure",
            "formatdevente"),
}

# When one WORD names two roles at once, the more specific one wins: "Unit price" is a
# price, "Code produit" is a code. It only ever breaks a tie between two roles the same word
# carries — the role of a header is decided by its FIRST known word, not by ranking every
# role every word of it holds.
PRIORITE = ("price", "code", "uom", "name")

# A shelf price is not a purchase price. A wholesaler sends "Code;Description;Prix de
# détail;Prix net", and the retail column read as the price opened the vendor line at 6.49
# instead of 4.25 without a word on the screen. These words take the price role AWAY from a
# column; they say nothing about its other roles, so "Format de vente" is still a unit.
MOTS_DETAIL = ("detail", "retail", "vente", "ventes", "sale", "sales", "selling", "msrp",
               "pdsf", "srp", "tablette", "shelf", "suggere", "suggested")

# How far down the file the header is looked for: it sits under a title, a date, a logo and
# a blank row more often than on the first line.
FENETRE_ENTETE = 20

# The unit labels of the trade, in both languages, as TOKENS. The key on the left is a
# technical family, never a factor: nothing here says how many units a case holds, and
# nothing divides a price by a number read in a file.
JETONS_UNITE = {}
for _famille, _mots in {
    "unit": ("ea", "each", "eaches", "un", "une", "unit", "units", "unite", "unites",
             "unitaire", "u", "pc", "pce", "pcs", "piece", "pieces", "ch", "chaque",
             "chacun", "item", "items"),
    "dozen": ("doz", "dz", "dzn", "dozen", "dozens", "douzaine", "douzaines"),
    # A container: a label that announces one never passes for the unit it holds.
    "pack": ("cs", "case", "cases", "caisse", "caisses", "ct", "ctn", "carton", "cartons",
             "bx", "box", "boxes", "boite", "boites", "pk", "pkg", "pack", "packs",
             "paquet", "paquets", "tray", "trays", "plateau", "plateaux", "flat", "flats",
             "crate", "bundle", "ballot", "sac", "sacs", "bag", "bags", "sleeve", "lot",
             "palette", "pallet", "skid"),
    "kg": ("kg", "kgs", "kgm", "kilo", "kilos", "kilogram", "kilograms", "kilogramme",
           "kilogrammes"),
    "g": ("g", "gr", "gm", "gram", "grams", "gramme", "grammes"),
    "t": ("t", "tonne", "tonnes", "ton", "tons"),
    "lb": ("lb", "lbs", "livre", "livres", "pound", "pounds"),
    "oz": ("oz", "ounce", "ounces", "once", "onces"),
    "l": ("l", "lt", "ltr", "litre", "litres", "liter", "liters"),
    "ml": ("ml", "millilitre", "millilitres", "milliliter", "milliliters"),
    "m": ("m", "metre", "metres", "meter", "meters"),
    "cm": ("cm", "centimetre", "centimetres", "centimeter", "centimeters"),
    "mm": ("mm", "millimetre", "millimetres", "millimeter", "millimeters"),
    "km": ("km", "kilometre", "kilometres", "kilometer", "kilometers"),
    "in": ("in", "inch", "inches", "po", "pouce", "pouces"),
    "ft": ("ft", "foot", "feet", "pi", "pied", "pieds"),
    "yd": ("yd", "yard", "yards", "verge", "verges"),
    "mi": ("mi", "mile", "miles"),
    "m3": ("m3", "cubicmeter", "cubicmetre", "metrecube"),
    "gal": ("gal", "gallon", "gallons"),
    "qt": ("qt", "quart", "quarts", "pinte", "pintes"),
    "floz": ("floz",),
}.items():
    for _mot in _mots:
        JETONS_UNITE[_mot] = _famille

# A container wins over the unit it holds: "CASE OF 12 UNITS" is a case.
ORDRE_UNITE = ("pack", "dozen", "kg", "g", "t", "lb", "oz", "l", "ml", "m3", "gal", "qt",
               "floz", "m", "cm", "mm", "km", "in", "ft", "yd", "mi", "unit")

# The families that MEASURE rather than count. A count in front of one of them describes the
# item ("4 L" is a four-litre jug); a count in front of a countable family, or in front of
# nothing at all, announces a container ("12 UNITS", "12").
MESURES = frozenset(ORDRE_UNITE) - frozenset(("pack", "dozen", "unit"))

# A times sign between two numbers: "12 x 1 L" counts twelve of something measured.
MULTIPLICATEURS = frozenset(("x",))

# What a label announces when it is a measure written with a count: the FORMAT of the item,
# which says nothing about the unit the wholesaler priced. Neither a unit nor a container.
FORMAT = "format"

# The units Odoo seeds, read by their EXTERNAL IDENTIFIER: `uom.name` is translatable, and
# reading it made the same file pass in English and be left out in French.
UOM_TECHNIQUE = {
    "uom.product_uom_unit": "unit",
    "uom.product_uom_dozen": "dozen",
    "uom.product_uom_kgm": "kg",
    "uom.product_uom_gram": "g",
    "uom.product_uom_ton": "t",
    "uom.product_uom_lb": "lb",
    "uom.product_uom_oz": "oz",
    "uom.product_uom_litre": "l",
    "uom.product_uom_cubic_meter": "m3",
    "uom.product_uom_gal": "gal",
    "uom.product_uom_qt": "qt",
    "uom.product_uom_floz": "floz",
    "uom.product_uom_meter": "m",
    "uom.product_uom_cm": "cm",
    "uom.product_uom_millimeter": "mm",
    "uom.product_uom_km": "km",
    "uom.product_uom_inch": "in",
    "uom.product_uom_foot": "ft",
    "uom.product_uom_yard": "yd",
    "uom.product_uom_mile": "mi",
}

# What the file was read FROM: frozen as soon as it is read, because the lines, the
# fingerprint and the matching all answer to these four and to nothing else.
FIGE_APRES_LECTURE = ("partner_id", "file", "date", "write_costs")

# Why a row of the file was left out of the apply. Nothing is ever dropped in silence: a
# wholesaler sends 300 rows with 2 broken ones, and refusing the lot would stop the store.
REFUS = [
    ("price", "Price not readable"),
    ("zero", "Price of zero or less"),
    ("code", "Row without a code"),
    ("unknown", "Code with no item in this store"),
    ("unit", "Unit to check"),
    ("repeat", "The same item again further down the file"),
    ("clash", "The same item at two different prices"),
]


def sans_accent(texte):
    return "".join(
        c for c in unicodedata.normalize("NFD", texte or "")
        if unicodedata.category(c) != "Mn")


def normalise(texte):
    return re.sub(r"[^a-z0-9]", "", sans_accent(texte).lower())


def jetons(texte):
    """The words and the numbers of a label, accents and punctuation gone.

    Tokens, never substrings: `normalise("CASE OF 12 UNITS")` CONTAINS "units", and a
    substring test read a case of twelve as an each — a twelve-fold error on a price.
    """
    return re.findall(r"[a-z]+|[0-9]+", sans_accent(texte).lower())


def famille_unite(etiquette):
    """(what the label announces, the number written down), read on its tokens.

    No factor is ever guessed: the number is only ever compared to another number someone
    else wrote down, never used to divide a price. Four answers:

    * a technical family ("unit", "l", "kg"): the label NAMES a unit of purchase, and "1 KG"
      names the kilogram — a unit is a unit of one;
    * "pack": the label announces a CONTAINER, either by name ("CASE") or by a count that
      multiplies ("12 UNITS", "12 x 1 L", "12"), and a container never passes for what it
      holds;
    * FORMAT: a measure written with a count ("4 L", "750 ML") — the format of the item
      itself. 🔴 It used to be read as a container, so a dairy's file was refused in full
      the day it carried its Format column and applied the day it did not: adding the
      information made the file fail. A lone measure says nothing about the unit a price is
      quoted in, so it raises nothing;
    * False: nobody recognises this label, which is a doubt rather than a pass.
    """
    mots = jetons(etiquette)
    familles = {JETONS_UNITE[mot] for mot in mots if mot in JETONS_UNITE}
    nombres = [int(mot) for mot in mots if mot.isdigit()]
    nombre = max(nombres) if nombres else None
    compte = bool(nombre and nombre > 1)
    # A count MULTIPLIES when a second number or a times sign says so, or when what it
    # counts is countable rather than measurable.
    multiplie = compte and (len(nombres) > 1 or bool(set(mots) & MULTIPLICATEURS)
                            or not familles & MESURES)
    for famille in ORDRE_UNITE:
        if famille in familles:
            if multiplie and famille != "pack":
                return "pack", nombre
            return (FORMAT if compte and famille in MESURES else famille), nombre
    return ("pack" if multiplie else False), nombre


class RetailPriceFile(models.Model):
    """The price file of a wholesaler, read and applied to the vendor lines.

    🔴 A new price does not overwrite the one before it: it opens a NEW vendor line dated
    from the file and closes the previous one. Odoo's vendor lines already carry start and
    end dates, which makes them a series; overwriting would throw away exactly the history
    this module exists to keep. And a new line is the OLD line with a new price: the
    minimum order, the lead time, the sequence, the currency and the description are what
    the supplier agreed to, not something a price list restates every week.

    A quoted price is not a cost: what the store paid is on the bill. The file writes a cost
    row only when asked, for a store whose supplier does not bill per item.

    What the reader cannot read is counted, listed and named, never dropped in silence: a
    partly unreadable file applies what it can and says what it could not.
    """
    _name = "bf.retail.price.file"
    _description = "Wholesaler price file"
    _order = "date desc, id desc"
    _inherit = ["mail.thread"]
    _check_company_auto = True

    name = fields.Char("Reference", required=True, copy=False, readonly=True, default="/")
    company_id = fields.Many2one(
        "res.company", "Company", required=True, readonly=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one(
        "res.partner", "Supplier", required=True, check_company=True,
        domain="[('bf_retail_supplier', '=', True)]")
    date = fields.Date(
        "In force from", required=True, default=fields.Date.context_today,
        help="The day the prices of this file take effect: the new vendor lines start then, "
             "and the lines before them are closed the day before.")
    file = fields.Binary("File", required=True, attachment=True)
    filename = fields.Char("File name")
    digest = fields.Char("Fingerprint", readonly=True, copy=False)
    columns_note = fields.Char(
        "Columns read", readonly=True, copy=False,
        help="Which column of the file was read as the code, the description, the price and "
             "the unit, and what the rows were split on. A wholesaler who sends a retail "
             "price next to a net price sends two columns of prices: this says which one "
             "the store applied.")
    price_header = fields.Char(
        "Price column", readonly=True, copy=False,
        help="The name the file gives the column the prices were read from. When it names a "
             "case, the price is the price of the case and the rows are left out rather "
             "than applied per unit.")
    state = fields.Selection(
        [("draft", "Draft"), ("read", "Read"), ("applied", "Applied")],
        default="draft", required=True, readonly=True, tracking=True)
    write_costs = fields.Boolean(
        "Also write the cost series", default=False,
        help="Off: the file only moves the vendor prices, and the cost stays what the bills "
             "say. On: each price also lands in the cost series, for a store whose supplier "
             "does not bill item by item.")
    line_ids = fields.One2many("bf.retail.price.file.line", "file_id", "Lines", readonly=True)
    line_count = fields.Integer("Lines", compute="_compute_counts")
    matched_count = fields.Integer("Matched", compute="_compute_counts")
    unmatched_count = fields.Integer("Not matched", compute="_compute_counts")
    doubt_count = fields.Integer("Unit to check", compute="_compute_counts")
    rejected_count = fields.Integer("Left out", compute="_compute_counts")

    @api.depends("line_ids.product_id", "line_ids.rejected")
    def _compute_counts(self):
        for record in self:
            lines = record.line_ids
            record.line_count = len(lines)
            record.matched_count = len(lines.filtered(
                lambda line: line.product_id and not line.rejected))
            record.unmatched_count = len(lines.filtered(lambda line: not line.product_id))
            record.doubt_count = len(lines.filtered(
                lambda line: line.product_id and line.rejected == "unit"))
            record.rejected_count = len(lines.filtered(lambda line: line.rejected))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(
                    vals.get("company_id") or self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(company).next_by_code(
                    "bf.retail.price.file") or "/"
        return super().create(vals_list)

    def _bf_check_manager(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("A price file is the retail manager's."))

    def _bf_meme_valeur(self, champ, valeur):
        """True when writing `valeur` in `champ` would leave this file as it is."""
        self.ensure_one()
        definition = self._fields[champ]
        if definition.type == "many2one":
            return (self[champ].id or False) == (valeur or False)
        if definition.type == "date":
            return self[champ] == (fields.Date.to_date(valeur) if valeur else False)
        if definition.type == "binary":
            courant, neuf = self[champ] or b"", valeur or b""
            if isinstance(courant, str):
                courant = courant.encode()
            if isinstance(neuf, str):
                neuf = neuf.encode()
            return courant == neuf
        return (self[champ] or False) == (valeur or False)

    def write(self, vals):
        """🔴 Once the file is read, what it was read FROM no longer moves.

        The supplier and the attachment were writable right up to the apply, and only the
        apply made them readonly. Changing the supplier between "Read" and "Apply" posted
        the prices at the OTHER wholesaler — on vendor lines built from nothing, since none
        of its lines matched — and left the real supplier's price open. Replacing the
        attachment was worse: the fingerprint kept the value of the file that had been read,
        so the twin check passed and the prices of the FILE BEFORE were the ones applied.
        """
        fige = [champ for champ in FIGE_APRES_LECTURE if champ in vals]
        if fige:
            for record in self:
                if record.state == "draft":
                    continue
                changes = [champ for champ in fige
                           if not record._bf_meme_valeur(champ, vals[champ])]
                if changes:
                    raise UserError(_(
                        "%s was read from this supplier, this file and this date. Changing "
                        "one of them now would apply prices nobody read: write a new price "
                        "file instead.", record.name))
        return super().write(vals)

    # ── reading the file ─────────────────────────────────────────────────────────────────
    @api.model
    def _bf_role(self, entete):
        """The role of one column, from its whole name then from its FIRST known word.

        🔴 The first word recognised decides. Collecting the roles of every word and then
        ranking them made "Description de l'article" a code — "article" is one of the code
        words and PRIORITE puts a code before a name — so the real Code column was skipped
        and three hundred rows came out as "Code with no item in this store".

        And a shelf price is not a purchase price: a column whose name says retail, sale or
        suggested never takes the price role, whatever else it holds.
        """
        cle = normalise(entete)
        if not cle:
            return False
        mots = jetons(entete)
        detail = any(mot in MOTS_DETAIL for mot in mots)
        for role in PRIORITE:
            if cle in ENTETES[role]:
                return False if role == "price" and detail else role
        for mot in mots:
            roles = {role for role, valeurs in ENTETES.items() if mot in valeurs}
            if not roles:
                continue
            for role in PRIORITE:
                if role in roles:
                    return False if role == "price" and detail else role
        return False

    def _bf_colonnes(self, entetes):
        """{role: index} from the header row, whatever the column names."""
        trouve = {}
        for index, entete in enumerate(entetes):
            role = self._bf_role(entete)
            if role and role not in trouve:
                trouve[role] = index
        return trouve

    def _bf_entete(self, lignes):
        """(columns, first data row): the header may sit under a title and a date."""
        for index, ligne in enumerate(lignes[:FENETRE_ENTETE]):
            trouve = self._bf_colonnes(ligne)
            if "code" in trouve and "price" in trouve:
                return trouve, index + 1
        return None, 0

    def _bf_note_colonnes(self, colonnes, entetes, separateur=False):
        """Which column was read as what, and how the file was split.

        🔴 The only line on the screen that can tell a storekeeper the price came out of the
        wrong column. A file carrying "Prix de détail" next to "Prix net" read one of the
        two and reported "1 row read, 1 to apply, 0 left out", naming neither.
        """
        libelles = {"code": _("Code"), "name": _("Description"), "price": _("Price"),
                    "uom": _("Unit")}
        morceaux = []
        for role in ("code", "name", "price", "uom"):
            index = colonnes.get(role)
            if index is None:
                continue
            nom = str(entetes[index]).strip() if index < len(entetes) else ""
            morceaux.append("%s: %s" % (libelles[role], nom or "#%s" % (index + 1)))
        if not morceaux:
            return ""
        texte = _("Columns read: %s.", " | ".join(morceaux))
        if separateur:
            noms = {"\t": _("a tab"), ";": "« ; »", ",": "« , »", "|": "« | »"}
            texte = "%s %s" % (texte, _("Separator: %s.", noms.get(separateur, separateur)))
        return texte

    def _bf_texte(self, octets):
        for encodage in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return octets.decode(encodage)
            except UnicodeDecodeError:
                continue
        raise UserError(_("This file is not readable as text."))

    def _bf_lignes_csv(self, octets):
        """(rows, the separator read) of a CSV, whatever its encoding and its shape.

        🔴 The separator is CHOSEN here rather than sniffed. `csv.Sniffer` refuses a sample
        whose rows do not all hold the same number of cells: one missing last cell, or a
        title line above the header, and it raised — then the comma it fell back on turned
        each row into a single cell, and the refusal accused the file of carrying neither a
        code nor a price when both were there. Three rows of "here are the items whose
        price changed" never got through.
        """
        texte = self._bf_texte(octets)
        meilleur = None
        for delimiteur in (";", ",", "\t", "|"):
            try:
                lignes = [list(ligne) for ligne in
                          csv.reader(io.StringIO(texte), delimiter=delimiteur)]
            except csv.Error:
                continue
            lignes = [ligne for ligne in lignes if any(str(c).strip() for c in ligne)]
            if not lignes:
                continue
            colonnes, _depart = self._bf_entete(lignes)
            largeurs = [len(ligne) for ligne in lignes]
            courante = max(set(largeurs), key=largeurs.count)
            note = (1 if colonnes else 0,
                    courante if courante > 1 else 0,
                    largeurs.count(courante) / len(largeurs))
            if meilleur is None or note > meilleur[0]:
                meilleur = (note, lignes, delimiteur)
        return (meilleur[1], meilleur[2]) if meilleur else ([], False)

    @staticmethod
    def _bf_cellule_xlsx(valeur):
        """What an Excel cell holds, as text, without inventing a shape.

        `str` on a cell read by openpyxl turns a barcode into "123456789012.0" and a date
        into something that no longer looks like a date at all.
        """
        if valeur is None:
            return ""
        if isinstance(valeur, bool):
            return str(valeur)
        if isinstance(valeur, int):
            return str(valeur)
        if isinstance(valeur, float):
            return str(int(valeur)) if valeur.is_integer() else repr(valeur)
        if isinstance(valeur, (datetime, date, time)):
            return valeur.isoformat()
        return str(valeur)

    def _bf_lignes_xlsx(self, octets):
        """(rows, the name of the sheet read).

        A workbook holds a "Read me" sheet, last year's list and this week's. Reading the
        first sheet and saying nothing applied the wrong list without a word; the sheet
        that carries a header wins, and its name is written to the chatter.
        """
        try:
            import openpyxl
        except ImportError:
            raise UserError(_("Reading an Excel file takes the openpyxl package."))
        classeur = openpyxl.load_workbook(io.BytesIO(octets), read_only=True, data_only=True)
        try:
            repli = None
            for nom in classeur.sheetnames:
                lignes = []
                for ligne in classeur[nom].iter_rows(values_only=True):
                    lignes.append([self._bf_cellule_xlsx(cellule) for cellule in ligne])
                lignes = [ligne for ligne in lignes if any(c.strip() for c in ligne)]
                if not lignes:
                    continue
                colonnes, _depart = self._bf_entete(lignes)
                if colonnes:
                    return lignes, nom
                if repli is None:
                    repli = (lignes, nom)
            return repli if repli else ([], False)
        finally:
            classeur.close()

    @staticmethod
    def _bf_nombre(texte):
        """A price as a wholesaler writes it: 1 234,56, $1,234.56, 12.50 CAD.

        🔴 ONE number and nothing else that could be read as one. The reader before this
        threw the letters away and glued the digits back together: "2 pour 3,00" became
        23,00 and was applied, "1 20" became 120,00, and "(3,50)" lost its sign. A cell
        nobody can read as one price is not a price — it is named and left out.
        """
        if isinstance(texte, bool) or texte is None:
            return None
        if isinstance(texte, (int, float)):
            return float(texte)
        brut = re.sub(r"[\s ]+", " ", str(texte)).strip()
        if not brut:
            return None
        # The currency is noise around the number, as a sign or as a code; a word is not.
        # "nzd" and "nz$" are in the list because New Zealand is a market this module names:
        # without them the whole of a dairy's list read as "Price not readable".
        brut = re.sub(r"[$€£¥¢]|\b(?:cad|usd|eur|gbp|chf|aud|jpy|mxn|nzd|nz|ca|us)\b", " ",
                      brut, flags=re.IGNORECASE)
        brut = re.sub(r"\s+", " ", brut).strip().lstrip("+")
        if not re.fullmatch(r"-?\d+(?:[ .,]\d{3})*(?:[.,]\d{1,20})?", brut):
            return None
        # 🔴 "1,250" is 1.25 in French and 1 250 in English, and nothing in the cell says
        # which: ONE separator followed by exactly three digits is ambiguous, and a factor of
        # a thousand is not something the house guesses. The row is refused and named. A
        # number format per supplier would settle it, and belongs to a later lot.
        if re.fullmatch(r"-?\d{1,3}[.,]\d{3}", brut):
            return None
        brut = brut.replace(" ", "")
        if "," in brut and "." in brut:
            brut = brut.replace(",", "") if brut.rfind(".") > brut.rfind(",") \
                else brut.replace(".", "").replace(",", ".")
        else:
            brut = brut.replace(",", ".")
        try:
            return float(brut)
        except ValueError:
            return None

    def _bf_jumeau(self, digest):
        """Another file of this supplier, the same bytes, already applied.

        Both the read and the apply ask: reading two copies before applying either of them
        let both through, because only the read used to look.
        """
        return self.sudo().search([
            ("id", "!=", self.id), ("partner_id", "=", self.partner_id.id),
            ("company_id", "=", self.company_id.id), ("digest", "=", digest),
            ("state", "=", "applied")], limit=1)

    def action_read(self):
        """Read the file into lines, matched to the items of the store."""
        self.ensure_one()
        self._bf_check_manager()
        if self.state == "applied":
            raise UserError(_("This file is already applied."))
        if not self.file:
            raise UserError(_("No file."))
        octets = base64.b64decode(self.file)
        digest = hashlib.sha256(octets).hexdigest()
        jumeau = self._bf_jumeau(digest)
        if jumeau:
            raise UserError(_("This exact file was already applied as %s on %s.",
                              jumeau.name, jumeau.date))
        # The reader is chosen on the CONTENT, not on the file name: a wholesaler sends a
        # workbook called .csv, and an old .xls read as text is a page of mojibake.
        feuille = False
        separateur = False
        if octets[:4] == b"PK\x03\x04":
            lignes, feuille = self._bf_lignes_xlsx(octets)
        elif octets[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise UserError(_("This is an old Excel file (.xls). Save it as .xlsx or as a "
                              "CSV, and read it again."))
        else:
            lignes, separateur = self._bf_lignes_csv(octets)
        lignes = [ligne for ligne in lignes if any(str(c).strip() for c in ligne)]
        if not lignes:
            raise UserError(_("This file has no line."))
        if len(lignes) > PLAFOND:
            raise UserError(_(
                "This file holds %(lues)s rows, more than the %(plafond)s a price list is "
                "read up to. Split it, or check that it is a price list.",
                lues=len(lignes), plafond=PLAFOND))
        colonnes, depart = self._bf_entete(lignes)
        if not colonnes:
            # The rows ARE named, with their numbers: the refusal used to quote row 1, which
            # on a file whose header sits under a title and a date is the title.
            apercu = "\n".join(
                _("Row %(numero)s: %(cellules)s", numero=index + 1,
                  cellules=" | ".join(str(c) for c in ligne[:8]))
                for index, ligne in enumerate(lignes[:3]))
            raise UserError(_(
                "No column holding a code and a column holding a price were both found in "
                "the first %(fenetre)s rows. What was read:\n%(apercu)s",
                fenetre=FENETRE_ENTETE, apercu=apercu))
        entetes = lignes[depart - 1] if depart else []
        self.write({
            "columns_note": self._bf_note_colonnes(colonnes, entetes, separateur),
            "price_header": (str(entetes[colonnes["price"]]).strip()
                             if colonnes["price"] < len(entetes) else ""),
        })
        self.line_ids.unlink()
        vals_list = []
        for numero, ligne in enumerate(lignes[depart:], start=depart + 1):
            def cellule(role):
                index = colonnes.get(role)
                return str(ligne[index]).strip() if index is not None and index < len(ligne) \
                    else ""
            code = cellule("code")
            prix = self._bf_nombre(cellule("price"))
            refus = False
            if not code:
                refus = "code"
            elif prix is None:
                refus = "price"
            elif prix <= 0.0:
                refus = "zero"
            vals_list.append({
                "file_id": self.id, "row_no": numero, "code": code, "name": cellule("name"),
                "uom_label": cellule("uom"), "price": prix or 0.0, "rejected": refus,
            })
        if not vals_list:
            raise UserError(_("This file carries a header and not one row under it."))
        lines = self.env["bf.retail.price.file.line"].create(vals_list)
        lines._bf_match()
        self._bf_marquer_repetitions(lines)
        self.write({"digest": digest, "state": "read"})
        # `_bf_rapport` is called ON the relocalised record, not on `self`: it builds its own
        # pieces with `_` and reads the labels of `rejected` through `self.env`, so the
        # language of the store has to be carried by the recordset it receives.
        dit = _bf_retail_dans_la_langue_du_commerce(self, self.company_id)
        # 🔴 `_message_log` and not `message_post`, here and at the application: a report of
        # what the module just did has nobody to notify, and `message_post` REQUIRES an
        # address for its author -- a manager with no email saw the rows read and then
        # everything rolled back by the body of the thread.
        dit._message_log(body=dit._bf_rapport(
            dit.env._("%(lues)s row(s) read, %(prises)s to apply, %(hors)s left out.",
                      lues=len(lines), prises=self.matched_count, hors=self.rejected_count),
            feuille))
        return True

    def _bf_marquer_repetitions(self, lines):
        """The same item twice in one file: never the last one winning in silence.

        The same price twice is not ambiguous and applies once; two different prices are,
        and neither is applied — the item is named instead.
        """
        groupes = {}
        for line in lines:
            if line.product_id and not line.rejected:
                groupes.setdefault(line.product_id.id, []).append(line)
        for membres in groupes.values():
            if len(membres) < 2:
                continue
            if len({round(line.price, 4) for line in membres}) == 1:
                for line in membres[1:]:
                    line.rejected = "repeat"
            else:
                for line in membres:
                    line.rejected = "clash"

    def _bf_rapport(self, entete, feuille=False):
        """What was read and what was left out, motive by motive, row by row.

        Reads the language off its OWN recordset -- both the `_` of this frame and the labels
        of the `rejected` selection go through `self.env`. Its two callers hand it a recordset
        already set to the language of the store, and `entete` is built the same way.
        """
        self.ensure_one()
        morceaux = [entete]
        if self.columns_note:
            morceaux.append(self.columns_note)
        if feuille:
            morceaux.append(_("Sheet read: %s.", feuille))
        libelles = dict(self.line_ids._fields["rejected"]._description_selection(self.env))
        for cle, libelle in libelles.items():
            visees = self.line_ids.filtered(lambda line: line.rejected == cle)
            if not visees:
                continue
            noms = [line.code or "#%s" % line.row_no for line in visees[:20]]
            if len(visees) > 20:
                noms.append(_("and %s more", len(visees) - 20))
            morceaux.append("%s (%s) : %s" % (libelle, len(visees), ", ".join(noms)))
        # 🔴 `Markup` et non un `str` : les deux portes du chatter échappent le corps
        # (`message_post` l. 2281, `_message_log_batch` l. 2894), donc un séparateur écrit en
        # texte sortait LITTÉRALEMENT — le gérant lisait « … laissée(s) de côté.<br/>Colonnes
        # lues : … » au fil. `Markup.join` échappe chaque morceau (le nom d'un fournisseur
        # peut porter un `&`) et garde le séparateur comme balise ; `escape` d'un `Markup`
        # le rend tel quel, puisqu'il porte `__html__`.
        return Markup("<br/>").join(morceaux)

    # ── applying it ──────────────────────────────────────────────────────────────────────
    def action_apply(self):
        """Open a new vendor line per matched price, and close the one before it."""
        self.ensure_one()
        self._bf_check_manager()
        if self.state != "read":
            raise UserError(_("Read the file first."))
        rejoue = self._bf_jumeau(self.digest)
        if rejoue:
            raise UserError(_("This exact file was already applied as %s on %s.",
                              rejoue.name, rejoue.date))
        lines = self.line_ids.filtered(lambda line: line.product_id and not line.rejected)
        if not lines:
            raise UserError(_("Nothing to apply: not one row of this file carries a price "
                              "this store can read on an item it follows."))
        Info = self.env["product.supplierinfo"].sudo()
        Cost = self.env["bf.retail.cost"].sudo()
        partner = self.partner_id.commercial_partner_id
        veille = self.date - timedelta(days=1)
        # Which variants of each model this file carries. A vendor line written on the MODEL
        # is the template of every variant of it, so it is only closed when the file covers
        # all of them.
        couverture = {}
        for line in lines:
            couverture.setdefault(line.product_id.product_tmpl_id.id, set()).add(
                line.product_id.id)
        posees = 0
        # Closed only once every row is posted: closing a model's line in the middle of the
        # loop would hide it from the variants that come after it in the same file, which is
        # the very thing this guards against.
        a_fermer = Info.browse()
        for line in lines:
            product = line.product_id
            # 🔴 `child_of`, the same reading as the match: a wholesaler's vendor lines are
            # often written on a division or a branch of it. Looking them up on the
            # commercial partner alone matched them when the file was read and missed them
            # when it was applied, so the apply built a line from nothing and left the
            # branch's price open beside it.
            anciennes = Info.search([
                ("partner_id", "child_of", partner.id),
                ("company_id", "in", (self.company_id.id, False)),
                "|", ("product_id", "=", product.id),
                "&", ("product_id", "=", False),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                "|", ("date_end", "=", False), ("date_end", ">=", self.date)])
            meme_jour = anciennes.filtered(
                lambda i: i.date_start == self.date and i.product_id and i.company_id)
            # The unit of the price is the unit of the vendor line already there, never a
            # unit guessed from the file: a case priced as a unit is a 24-fold error.
            courantes = anciennes.filtered(
                lambda i: not i.date_start or i.date_start <= self.date)
            modele = meme_jour[:1] or courantes.sorted(
                lambda i: (i.date_start or date.min, i.id))[-1:] or anciennes[:1]
            uom = modele.product_uom or product.uom_po_id or product.uom_id
            # 🔴 The line before is closed on BOTH paths. A vendor line already dated today —
            # one the buyer typed this morning — used to send the apply down the "same day"
            # branch, where nothing closed the line that was open before it: two prices
            # stayed valid on the same day, and the one Odoo picked was a matter of order.
            a_fermer |= (anciennes - meme_jour).filtered(
                lambda i: (not i.date_start or i.date_start < self.date)
                and self._bf_remplacable(i, couverture))
            if meme_jour:
                # A corrected file of the same day writes on its own line instead of stacking
                # a second one that starts the same day.
                meme_jour.write({"price": line.price})
                posees += len(meme_jour)
            else:
                Info.create(self._bf_valeurs_ligne(modele, anciennes, product, line))
                posees += 1
            if self.write_costs:
                par_unite = uom._compute_price(line.price, product.uom_id) \
                    if uom.category_id == product.uom_id.category_id else line.price
                valeurs = {
                    "product_id": product.id,
                    "company_id": self.company_id.id,
                    "date": self.date,
                    "cost": par_unite,
                    "uom_id": product.uom_id.id,
                    "source": "file",
                    "partner_id": partner.id,
                    "note": self.name,
                }
                # The same correction as the vendor line, carried to the cost series: two
                # files of the same day wrote TWO costs on one date, and the series then read
                # a change between them that never happened. A file rewrites the cost IT
                # wrote, and never a cost a bill observed.
                ancien = Cost.search([
                    ("product_id", "=", product.id),
                    ("company_id", "=", self.company_id.id),
                    ("date", "=", self.date), ("source", "=", "file")], limit=1)
                if ancien:
                    ancien.write(valeurs)
                else:
                    Cost.create(valeurs)
        a_fermer.write({"date_end": veille})
        self.write({"state": "applied"})
        dit = _bf_retail_dans_la_langue_du_commerce(self, self.company_id)
        # Logged and not posted, for the reason spelled out at the reading above: the vendor
        # lines WERE opened, and a manager with no email saw them all rolled back.
        dit._message_log(body=dit._bf_rapport(dit.env._(
            "%(posees)s vendor price(s) applied, %(hors)s row(s) left out.",
            posees=posees, hors=self.rejected_count)))
        return True

    def _bf_remplacable(self, info, couverture):
        """Whether this file may close that vendor line.

        🔴 Two lines it may not touch.

        A line written on the MODEL (no variant) is the template of every variant of it. The
        Purchase tab creates exactly that line by default, and closing it on a file carrying
        ONE variant left the sisters with nothing to inherit: the second variant opened at
        min_qty 0 — so the guide proposed 8 where it used to propose 24 — and at sequence 1,
        which moves the item from one guide to another.

        A line with no company is shared by every store of the database. One store's file
        closing it took the price away from the other store without putting one back.
        """
        self.ensure_one()
        if not info.company_id:
            return False
        if info.product_id:
            return True
        variantes = set(info.product_tmpl_id.product_variant_ids.ids)
        return bool(variantes) and variantes <= couverture.get(
            info.product_tmpl_id.id, set())

    def _bf_valeurs_ligne(self, modele, anciennes, product, line):
        """The new vendor line: the one before it, with a new price and new dates.

        🔴 Everything else is what the supplier agreed to, not something a weekly price
        list restates. Building the line from nothing put the minimum order back to 0 —
        and the minimum is the only representation left of a wholesaler's case format —
        reset the sequence, which decides WHICH guide the item lands in, and turned a
        price quoted in another currency into a price in the currency of the company.
        """
        self.ensure_one()
        valeurs = modele.copy_data()[0] if modele else {}
        suivantes = anciennes.filtered(
            lambda i: i.date_start and i.date_start > self.date)
        # A price list of today does not hide the list that starts in two weeks: the new
        # line is closed the day before the next one begins.
        fin = min(suivantes.mapped("date_start")) - timedelta(days=1) if suivantes else False
        valeurs.update({
            "partner_id": self.partner_id.commercial_partner_id.id,
            "product_id": product.id,
            "product_tmpl_id": product.product_tmpl_id.id,
            "company_id": self.company_id.id,
            "price": line.price,
            "date_start": self.date,
            "date_end": fin,
            "product_code": modele.product_code or line.code or False,
            "product_name": modele.product_name or line.name or False,
        })
        return valeurs


class RetailPriceFileLine(models.Model):
    _name = "bf.retail.price.file.line"
    _description = "Wholesaler price file line"
    _order = "row_no, id"

    file_id = fields.Many2one(
        "bf.retail.price.file", "File", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="file_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="file_id.currency_id")
    row_no = fields.Integer(
        "Row", readonly=True, help="Where this row sits in the file.")
    code = fields.Char("Code in the file")
    name = fields.Char("Description in the file")
    uom_label = fields.Char("Unit in the file")
    price = fields.Float("Price", digits=(16, 4), required=True)
    product_id = fields.Many2one("product.product", "Item", readonly=True)
    matched_on = fields.Selection(
        [("barcode", "Barcode"), ("default_code", "Internal reference"),
         ("supplier_code", "The supplier's own code")],
        "Matched on", readonly=True)
    current_price = fields.Float("Vendor price now", digits=(16, 4), readonly=True)
    delta_pct = fields.Float("Moves (%)", digits=(16, 2), readonly=True)
    rejected = fields.Selection(
        REFUS, "Left out", readonly=True,
        help="Why this row is left out of the apply. It is kept and named either way: a "
             "file of 300 rows with 2 broken ones applies the 298 and says which 2 it "
             "could not read.")
    check_unit = fields.Boolean(
        "Unit to check", compute="_compute_check_unit",
        help="The unit written in the file does not look like the unit of the vendor line "
             "in Odoo. The line is left out of the apply: a case priced as a unit is worse "
             "than no price at all.")

    @api.depends("rejected")
    def _compute_check_unit(self):
        for line in self:
            line.check_unit = line.rejected == "unit"

    @api.model
    def _bf_familles_uom(self):
        """{uom id: technical family} for the units Odoo seeds."""
        familles = {}
        for xmlid, famille in UOM_TECHNIQUE.items():
            uom = self.env.ref(xmlid, raise_if_not_found=False)
            if uom:
                familles[uom.id] = famille
        return familles

    def _bf_famille_uom(self, uom, familles):
        """The family of the unit of a vendor line, read on technical forms only.

        Never on `uom.name`: the field is translatable, so the same file passed in English
        and was left out in French. The seeded units answer to their external identifier,
        and anything else to the SOURCE of its name, never to a translation of it.
        """
        if not uom:
            return False, None
        if uom.id in familles:
            return familles[uom.id], None
        return famille_unite(uom.with_context(lang="en_US").name or "")

    def _bf_doute_unite(self, uom, familles):
        """Does the unit written in the file disagree with the unit of the vendor line?

        Tokens, not substrings; technical forms, not a translated name; and no factor is
        ever guessed. A label that announces a container when the vendor line is per unit
        is a doubt, and so is a label nobody recognises: the row is left out and named,
        never applied at a factor someone hoped for.
        """
        self.ensure_one()
        cle_uom, nombre_uom = self._bf_famille_uom(uom, familles)
        # 🔴 The COLUMN is a unit label too. "Prix caisse", "Case price" and "Case cost" are
        # read as prices — they are prices — but the price they hold is the price of the
        # case. A file of one column of case prices on vendor lines per unit gave no doubt at
        # all and posted 31.00 the unit instead of 1.29: the 24-fold error the README says is
        # worse than no price, through the front door.
        entete, _nombre_entete = famille_unite(self.file_id.price_header or "")
        if entete == "pack" and cle_uom != "pack":
            return True
        etiquette = (self.uom_label or "").strip()
        if not etiquette:
            return False
        cle_fichier, nombre_fichier = famille_unite(etiquette)
        if cle_fichier == FORMAT:
            # The format of the ITEM — "4 L" — and not a unit of purchase: the price applies
            # in the unit of the vendor line, which is all the file ever said.
            return False
        if cle_fichier and cle_fichier == cle_uom:
            return bool(nombre_fichier and nombre_uom and nombre_fichier != nombre_uom)
        if not cle_fichier and not cle_uom:
            # Two labels nobody knows agree only when they say the same thing.
            return jetons(etiquette) != jetons(uom.with_context(lang="en_US").name or "")
        return True

    def _bf_match(self):
        """Match each line to an item: barcode, then internal reference, then the code the
        supplier itself uses on its vendor line."""
        Product = self.env["product.product"].sudo()
        Info = self.env["product.supplierinfo"].sudo()
        familles = self._bf_familles_uom()
        for line in self:
            file = line.file_id
            company = file.company_id
            partner = file.partner_id.commercial_partner_id
            code = (line.code or "").strip()
            product = Product.search(
                [("barcode", "in", line._bf_codes_barres(code)),
                 ("company_id", "in", (company.id, False))], limit=1)
            source = "barcode" if product else False
            if not product:
                product = Product.search(
                    [("default_code", "=", code),
                     ("company_id", "in", (company.id, False))], limit=1)
                source = "default_code" if product else False
            info = Info.browse()
            if not product:
                info = Info.search([
                    ("partner_id", "child_of", partner.id),
                    ("company_id", "in", (company.id, False)),
                    ("product_code", "=", code)], limit=1)
                product = info.product_id or info.product_tmpl_id.product_variant_ids[:1]
                source = "supplier_code" if product else False
            if not product or not product.bf_retail_managed:
                line.write({"product_id": False, "matched_on": False,
                            "rejected": line.rejected or "unknown"})
                continue
            if not info:
                # 🔴 Both bounds. Without an upper bound on `date_start`, a vendor line that
                # begins in two weeks was read as the price "now": the screen the decision is
                # taken on showed 5.00 and −78 % where it had to show 4.55 and +10 %.
                info = Info.search([
                    ("partner_id", "child_of", partner.id),
                    ("company_id", "in", (company.id, False)),
                    "|", ("product_id", "=", product.id),
                    "&", ("product_id", "=", False),
                    ("product_tmpl_id", "=", product.product_tmpl_id.id),
                    "|", ("date_end", "=", False), ("date_end", ">=", file.date),
                    "|", ("date_start", "=", False), ("date_start", "<=", file.date)],
                    order="date_start desc, id desc", limit=1)
            ancien = info.price if info else 0.0
            uom = info.product_uom or product.uom_po_id or product.uom_id
            line.write({
                "product_id": product.id,
                "matched_on": source,
                "current_price": ancien,
                "delta_pct": ((line.price - ancien) / ancien * 100.0) if ancien else 0.0,
            })
            if not line.rejected and line._bf_doute_unite(uom, familles):
                line.rejected = "unit"

    @staticmethod
    def _bf_codes_barres(code):
        """The code as written, and the barcode it is once Excel gives back the zero it ate.

        A spreadsheet reads 0123456789012 as a number and writes back 123456789012; a GTIN
        has a fixed length, so padding it back and matching EXACTLY invents nothing.
        """
        if not code:
            return []
        codes = [code]
        if code.isdigit():
            codes += [code.rjust(longueur, "0")
                      for longueur in (8, 12, 13, 14) if len(code) < longueur]
        return codes
