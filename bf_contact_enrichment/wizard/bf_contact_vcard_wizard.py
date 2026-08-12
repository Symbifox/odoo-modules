import base64

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..tools import matching


def _unfold(raw):
    """RFC 6350 line unfolding: a line starting with space/tab continues the
    previous one."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in raw.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_vcards(text):
    """Minimal vCard parser (no external dependency).

    Handles FN, N, EMAIL, TEL (TYPE cell/mobile vs other), ORG, TITLE, URL and
    ADR. Returns a list of plain dicts, one per VCARD block.
    """
    cards = []
    cur = None
    for line in _unfold(text):
        stripped = line.strip()
        upper = stripped.upper()
        if upper == "BEGIN:VCARD":
            cur = {}
            continue
        if upper == "END:VCARD":
            if cur:
                cards.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        name_part, value = line.split(":", 1)
        params = name_part.split(";")
        prop = params[0].upper()
        ptypes = [p.upper() for p in params[1:]]
        value = value.strip()
        if not value:
            continue
        if prop == "FN":
            cur["fn"] = value
        elif prop == "N" and "n" not in cur:
            # family;given;additional;prefix;suffix
            bits = value.split(";")
            family = bits[0].strip() if bits else ""
            given = bits[1].strip() if len(bits) > 1 else ""
            cur["n"] = " ".join(p for p in [given, family] if p).strip()
        elif prop == "EMAIL":
            cur.setdefault("email", value)
        elif prop == "TEL":
            joined = " ".join(ptypes)
            if "CELL" in joined or "MOBILE" in joined:
                cur.setdefault("mobile", value)
            else:
                cur.setdefault("phone", value)
        elif prop == "ORG":
            cur["org"] = value.split(";")[0].strip()
        elif prop == "TITLE":
            cur["title"] = value
        elif prop == "URL":
            cur.setdefault("url", value)
        elif prop == "ADR":
            # pobox;ext;street;city;region;postal;country
            bits = [b.strip() for b in value.split(";")]

            def part(idx):
                return bits[idx] if len(bits) > idx else ""

            street = " ".join(p for p in [part(1), part(2)] if p).strip()
            if street:
                cur.setdefault("street", street)
            if part(3):
                cur.setdefault("city", part(3))
            if part(5):
                cur.setdefault("zip", part(5))
            if part(6):
                cur.setdefault("country", part(6))
    return cards


class BfContactVcardWizard(models.TransientModel):
    _name = "bf.contact.vcard.wizard"
    _description = "Importer une vCard"

    vcf_file = fields.Binary(string="Fichier vCard (.vcf)", required=True)
    vcf_filename = fields.Char()
    result_summary = fields.Text(readonly=True)

    def action_import(self):
        self.ensure_one()
        try:
            raw = base64.b64decode(self.vcf_file).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise UserError(_("Fichier illisible : %s") % exc)

        cards = _parse_vcards(raw)
        if not cards:
            raise UserError(_("Aucune fiche vCard trouvée dans le fichier."))

        Partner = self.env["res.partner"]
        created, updated = [], []
        for card in cards:
            name = card.get("fn") or card.get("n")
            email = card.get("email")
            if not name and not email:
                continue
            vals = {
                "function": card.get("title"),
                "email": email,
                "phone": card.get("phone"),
                "mobile": card.get("mobile"),
                "website": card.get("url"),
                "street": card.get("street"),
                "city": card.get("city"),
                "zip": card.get("zip"),
                "country_id": Partner._enrich_country_id(card.get("country")),
            }
            if card.get("org"):
                vals["company_name"] = card["org"]
            vals = {k: v for k, v in vals.items() if v}

            match = matching.find_partner_match(
                self.env, name=name, email=email, company=card.get("org"))
            if match:
                match._apply_contact_vals(vals, source=_("import vCard"))
                updated.append(match.display_name)
            else:
                create_vals = dict(vals)
                create_vals["name"] = name or email
                partner = Partner.create(create_vals)
                partner.message_post(body=_("Contact créé depuis un import vCard."))
                created.append(partner.display_name)

        summary = _("%(created)d créé(s), %(updated)d mis à jour.") % {
            "created": len(created), "updated": len(updated),
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
