"""Import de fiches vCard (.vcf) vers ``res.partner``.

Parseur maison, sans dépendance externe. ``vobject`` est pourtant dans le
conteneur (Odoo s'en sert pour *exporter* une vCard), mais il lève sur une
fiche malformée et emporte alors tout le fichier ; ici une fiche illisible est
comptée et sautée, les autres passent.

Ce qui est couvert l'est parce que les vrais exports le produisent :

- les préfixes de groupe d'Apple (``item1.TEL``), sans quoi un export d'iPhone
  n'apporte que le cellulaire ;
- ``ENCODING=QUOTED-PRINTABLE`` d'Android et d'Outlook 2.1, y compris sa
  coupure de ligne par un ``=`` final, qui ne s'indente pas ;
- l'échappement RFC 6350 (``\\,`` ``\\;`` ``\\n``) de Google Contacts ;
- le paramètre ``TYPE``, pour séparer le cellulaire du bureau et préférer le
  courriel de travail au courriel personnel ;
- la photo en base64.
"""
import base64
import binascii
import logging
import quopri
import re

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..tools import matching

_logger = logging.getLogger(__name__)

# « item1.TEL » : Apple groupe une propriété et son étiquette sous un préfixe.
_GROUP_RE = re.compile(r"^[A-Za-z0-9-]+\.")

# Types de téléphone qui ne correspondent à aucun champ de res.partner.
_TEL_SKIP = frozenset({"FAX", "PAGER", "ISDN", "BBS", "MODEM", "VIDEO"})
_TEL_MOBILE = frozenset({"CELL", "MOBILE", "IPHONE"})

# Réseaux sociaux : une URL de profil n'est pas le site web de la personne.
_SOCIAL_HOSTS = ("facebook.", "twitter.", "x.com/", "instagram.", "tiktok.")

# En-têtes des formats d'image qu'Odoo accepte sans broncher.
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")
_PHOTO_MAX = 8 * 1024 * 1024


# ── Lecture du fichier ────────────────────────────────────────────────


def _decode_file(data):
    """Texte de la vCard, quel que soit l'encodage du fichier."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"),
                     (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace")
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_qp_fold(line):
    """Une ligne quoted-printable coupée se termine par « = »."""
    if not line.endswith("="):
        return False
    return "QUOTED-PRINTABLE" in line.split(":", 1)[0].upper()


def _unfold(raw):
    """Recolle les lignes repliées.

    Deux pliages coexistent : celui du standard, où la suite commence par une
    espace ou une tabulation, et celui de vCard 2.1, où une ligne
    quoted-printable finie par « = » se poursuit sans indentation.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in raw.split("\n"):
        if lines and line[:1] in (" ", "\t"):
            lines[-1] += line[1:]
        elif lines and _is_qp_fold(lines[-1]):
            lines[-1] = lines[-1][:-1] + line
        else:
            lines.append(line)
    return lines


def _split_params(head):
    """Découpe « ITEM1.TEL;TYPE="WORK,VOICE" » sans casser sur le ; entre guillemets."""
    parts, buf, quoted = [], [], False
    for ch in head:
        if ch == '"':
            quoted = not quoted
            continue
        if ch == ";" and not quoted:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _parse_line(line):
    """``(propriété, paramètres, valeur brute)``, ou ``None`` si la ligne n'en est pas une."""
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    parts = _split_params(head)
    prop = _GROUP_RE.sub("", parts[0].strip()).upper()
    if not prop:
        return None
    params = {"TYPE": set()}
    for raw_param in parts[1:]:
        if "=" in raw_param:
            key, val = raw_param.split("=", 1)
            key, val = key.strip().upper(), val.strip()
            if key == "TYPE":
                params["TYPE"].update(
                    v.strip().upper() for v in val.split(",") if v.strip())
            elif key in ("ENCODING", "VALUE"):
                params[key] = val.upper()
            else:
                params[key] = val
        else:
            # vCard 2.1 écrit ses paramètres sans clé : « ;WORK;CELL ».
            bare = raw_param.strip().upper()
            if not bare:
                continue
            if bare in ("QUOTED-PRINTABLE", "BASE64", "B", "7BIT", "8BIT"):
                params["ENCODING"] = bare
            else:
                params["TYPE"].add(bare)
    return prop, params, value


def _decode_value(value, params):
    if params.get("ENCODING") != "QUOTED-PRINTABLE":
        return value
    charset = params.get("CHARSET") or "utf-8"
    try:
        return quopri.decodestring(value.encode("utf-8")).decode(
            charset, errors="replace")
    except (LookupError, UnicodeError, ValueError):
        return value


def _unescape(value):
    """Défait l'échappement RFC 6350 : ``\\n`` ``\\,`` ``\\;`` ``\\\\``.

    Une séquence non prévue par la norme (``\\é``, que Google produit) perd sa
    barre oblique plutôt que de rester telle quelle dans la fiche.
    """
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in ("n", "N") else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_structured(value):
    """Découpe une valeur composée (N, ADR, ORG) sur ses ``;`` non échappés."""
    parts, buf, esc = [], [], False
    for ch in value:
        if esc:
            buf.append("\\")
            buf.append(ch)
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == ";":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if esc:
        buf.append("\\")
    parts.append("".join(buf))
    return [_unescape(p).strip() for p in parts]


# ── Assemblage d'une fiche ────────────────────────────────────────────


def _absorb_adr(card, types, value):
    bits = _split_structured(value)

    def part(idx):
        return bits[idx] if len(bits) > idx else ""

    adr = {
        "street": part(2), "street2": part(1), "city": part(3),
        "state": part(4), "zip": part(5), "country": part(6),
    }
    if not any(adr.values()):
        return
    # L'adresse de travail l'emporte sur celle de la maison ; à égalité, la
    # première lue reste.
    score = (2 if "WORK" in types else 0) + (1 if "PREF" in types else 0)
    if score > card.get("adr_score", -1):
        card["adr"] = adr
        card["adr_score"] = score


def _absorb(card, prop, params, raw_value):
    types = params.get("TYPE", set())
    if prop == "PHOTO":
        if params.get("ENCODING") in ("B", "BASE64") and params.get("VALUE") != "URI":
            card["photo"] = re.sub(r"\s+", "", raw_value)
        return
    value = _decode_value(raw_value, params)
    if not value.strip():
        return
    if prop == "FN":
        card["fn"] = _unescape(value).strip()
    elif prop == "N" and "n" not in card:
        bits = _split_structured(value)
        family = bits[0] if bits else ""
        given = bits[1] if len(bits) > 1 else ""
        card["n"] = " ".join(p for p in (given, family) if p).strip()
    elif prop == "EMAIL":
        card["emails"].append((types, _unescape(value).strip()))
    elif prop == "TEL":
        card["tels"].append((types, _unescape(value).strip()))
    elif prop == "ORG":
        bits = _split_structured(value)
        if bits and bits[0]:
            card["org"] = bits[0]
    elif prop == "TITLE":
        card["title"] = _unescape(value).strip()
    elif prop == "ROLE":
        card.setdefault("role", _unescape(value).strip())
    elif prop in ("URL", "X-SOCIALPROFILE"):
        card["urls"].append((types, _unescape(value).strip()))
    elif prop == "NOTE":
        card["note"] = _unescape(value).strip()
    elif prop == "ADR":
        _absorb_adr(card, types, value)


def _email_score(types, value):
    score = 0
    if "WORK" in types:
        score += 4
    if "PREF" in types:
        score += 2
    if "HOME" in types:
        score -= 2
    domain = matching.extract_domain(value)
    if domain and domain not in matching.GENERIC_DOMAINS:
        score += 1
    return score


def _reduce_card(card):
    """Ramène les propriétés lues à un seul jeu de valeurs par contact."""
    out = {}
    name = card.get("fn") or card.get("n")
    if name:
        out["name"] = name

    emails = card["emails"]
    if emails:
        # ``max`` garde le premier des ex æquo, donc l'ordre du fichier tranche.
        best = max(emails, key=lambda item: _email_score(item[0], item[1]))
        out["email"] = best[1]
        others = [v for _t, v in emails if v != best[1]]
        if others:
            out["other_emails"] = others

    tels = [(t, v) for t, v in card["tels"] if not (t & _TEL_SKIP)]
    mobiles = [v for t, v in tels if t & _TEL_MOBILE]
    fixed = [(t, v) for t, v in tels if not (t & _TEL_MOBILE)]
    if mobiles:
        out["mobile"] = mobiles[0]
    if fixed:
        best = max(fixed, key=lambda item: (2 if "WORK" in item[0] else 0)
                   + (1 if "PREF" in item[0] else 0))
        out["phone"] = best[1]

    for types, url in card["urls"]:
        low = url.lower()
        if "linkedin.com" in low:
            out.setdefault("linkedin", url)
        elif "website" not in out and not any(h in low for h in _SOCIAL_HOSTS):
            out["website"] = url

    adr = card.get("adr") or {}
    for key in ("street", "street2", "city", "zip"):
        if adr.get(key):
            out[key] = adr[key]
    if adr.get("country"):
        out["country"] = adr["country"]
    if adr.get("state"):
        out["state"] = adr["state"]

    for key in ("org", "title", "role", "note", "photo"):
        if card.get(key):
            out[key] = card[key]
    return out


def _parse_vcards(text):
    """Liste de dictionnaires, un par fiche lisible du fichier."""
    cards, cur = [], None

    def close(card):
        reduced = _reduce_card(card)
        if reduced:
            cards.append(reduced)

    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VCARD":
            cur = {"emails": [], "tels": [], "urls": []}
            continue
        if upper == "END:VCARD":
            if cur is not None:
                close(cur)
            cur = None
            continue
        if cur is None:
            continue
        try:
            parsed = _parse_line(line)
        except Exception:  # noqa: BLE001, une ligne tordue n'emporte pas le fichier
            _logger.warning("vCard : ligne illisible, ignorée", exc_info=True)
            continue
        if parsed:
            _absorb(cur, *parsed)
    if cur is not None:
        # Fichier tronqué : la dernière fiche n'a jamais été fermée. Elle est
        # gardée quand même, plutôt que de perdre un contact en silence.
        close(cur)
    return cards


def _photo_b64(card):
    """La photo, si elle décode en une image qu'Odoo saura traiter."""
    raw = card.get("photo")
    if not raw:
        return False
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(blob) > _PHOTO_MAX or not blob.startswith(_IMAGE_MAGIC):
        return False
    return raw


class BfContactVcardWizard(models.TransientModel):
    _name = "bf.contact.vcard.wizard"
    _description = "Importer une vCard"

    vcf_file = fields.Binary(string="Fichier vCard (.vcf)", required=True)
    vcf_filename = fields.Char()
    result_summary = fields.Text(readonly=True)

    # ── Résolution des références ─────────────────────────────────────

    def _resolve_state(self, country_id, name):
        """La province n'est cherchée que dans un pays connu, sinon « QC »
        trouverait la première province homonyme du monde."""
        if not name or not country_id:
            return False
        state = self.env["res.country.state"].search([
            ("country_id", "=", country_id),
            "|", ("name", "=ilike", name.strip()), ("code", "=ilike", name.strip()),
        ], limit=1)
        return state.id if state else False

    def _find_company(self, name):
        """La société portée par ORG, si elle est déjà au carnet.

        L'égalité de nom passe en premier : une vCard écrit la raison sociale
        au caractère près, et l'appariement flou de ``matching`` cherche ses
        deux premiers mots significatifs *collés*, ce qu'un « Les » au milieu
        suffit à faire manquer. Il ne sert donc que de second recours.
        """
        if not name:
            return False
        company = self.env["res.partner"].search([
            ("is_company", "=", True), ("name", "=ilike", name.strip()),
        ], limit=1)
        if company:
            return company
        partner = matching.find_partner_match(self.env, company=name)
        return partner if partner and partner.is_company else False

    def _card_vals(self, card):
        Partner = self.env["res.partner"]
        country_id = Partner._enrich_country_id(card.get("country"))
        vals = {
            "function": card.get("title") or card.get("role"),
            "email": card.get("email"),
            "phone": card.get("phone"),
            "mobile": card.get("mobile"),
            "website": card.get("website"),
            "street": card.get("street"),
            "street2": card.get("street2"),
            "city": card.get("city"),
            "zip": card.get("zip"),
            "country_id": country_id,
            "state_id": self._resolve_state(country_id, card.get("state")),
        }
        # L'enrichissement LinkedIn n'existe que dans la ligne Blue Fox : la
        # variante Symbifox publiée n'a pas ce champ, et écrire un champ absent
        # lève. Le même fichier sert donc les deux lignées.
        if card.get("linkedin") and "x_linkedin_url" in Partner._fields:
            vals["x_linkedin_url"] = card["linkedin"]
        if card.get("note"):
            vals["comment"] = self._note_html(card["note"].split("\n"))
        photo = _photo_b64(card)
        if photo:
            vals["image_1920"] = photo
        return {k: v for k, v in vals.items() if v}

    @staticmethod
    def _note_html(lines):
        """Un paragraphe HTML à partir de texte brut, échappé."""
        return Markup("<p>%s</p>") % Markup("<br/>").join(lines)

    def _import_extras(self, card, parent=None):
        """Ce que l'import a déduit ou laissé de côté, à dire au chatter."""
        lines = []
        if parent:
            lines.append(_("Rattaché à %s, société déjà au carnet.")
                         % parent.display_name)
        if card.get("other_emails"):
            lines.append(_("Autres courriels lus dans la fiche, non repris : %s")
                         % ", ".join(card["other_emails"]))
        if card.get("photo") and not _photo_b64(card):
            lines.append(_("La photo de la fiche a été écartée : format non reconnu."))
        return lines

    # ── Import ────────────────────────────────────────────────────────

    def action_import(self):
        self.ensure_one()
        try:
            data = base64.b64decode(self.vcf_file)
        except (binascii.Error, ValueError) as exc:
            raise UserError(_("Fichier illisible : %s") % exc)

        cards = _parse_vcards(_decode_file(data))
        if not cards:
            raise UserError(_("Aucune fiche vCard trouvée dans le fichier."))

        Partner = self.env["res.partner"]
        created, updated, skipped = [], [], 0
        for card in cards:
            name, email = card.get("name"), card.get("email")
            if not name and not email:
                skipped += 1
                continue
            vals = self._card_vals(card)
            # L'appariement porte sur la PERSONNE : une société homonyme de
            # l'ORG ne doit pas récolter le courriel et le titre de son employé.
            match = matching.find_partner_match(self.env, name=name, email=email)
            if match:
                match._apply_contact_vals(vals, source=_("import vCard"))
                extras = self._import_extras(card)
                if extras:
                    match.message_post(body=self._note_html(extras))
                updated.append(match.display_name)
                continue

            create_vals = dict(vals)
            create_vals["name"] = name or email
            parent = self._find_company(card.get("org"))
            if parent:
                create_vals["parent_id"] = parent.id
            elif card.get("org"):
                create_vals["company_name"] = card["org"]
            partner = Partner.create(create_vals)
            partner.message_post(body=self._note_html(
                [_("Contact créé depuis un import vCard.")]
                + self._import_extras(card, parent=parent)))
            created.append(partner.display_name)

        summary = _("%(created)d créé(s), %(updated)d mis à jour.") % {
            "created": len(created), "updated": len(updated),
        }
        if skipped:
            summary += _(" %(skipped)d fiche(s) sans nom ni courriel ignorée(s).") % {
                "skipped": skipped,
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import vCard terminé"),
                "message": summary,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
