"""Les pronoms et le genre grammatical dans les cartes poussées vers Nextcloud."""

from odoo import models

from odoo.addons.contacts_nextcloud_sync.models.nextcloud_contacts_sync_config import _escape_vcard


def _fold(line):
    """Plie une ligne de vCard à 75 octets (RFC 6350, § 3.2), sans couper un
    caractère UTF-8."""
    out, current = [], ""
    for char in line:
        limit = 75 if not out else 74
        if len((current + char).encode("utf-8")) > limit:
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return "\r\n ".join(out)


class NextcloudContactsSyncConfig(models.Model):
    _inherit = "nextcloud.contacts.sync.config"

    def _partner_to_vcard(self, partner, uid=None):
        text = super()._partner_to_vcard(partner, uid=uid)
        if not partner or partner.is_company:
            return text
        lines = []
        pronouns = " ".join((partner.bf_pronouns or "").split())
        if pronouns:
            lines.append("PRONOUNS:%s" % _escape_vcard(pronouns))
        if partner.bf_grammatical_gender:
            lines.append("GRAMGENDER:%s" % partner.bf_grammatical_gender)
        if not lines:
            return text
        end = text.rfind("END:VCARD")
        newline = "\r\n" if "\r\n" in text else "\n"
        return text[:end] + "".join(_fold(ln).replace("\r\n", newline) + newline
                                    for ln in lines) + text[end:]
