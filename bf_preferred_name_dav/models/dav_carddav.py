"""Les pronoms et le genre grammatical dans les cartes servies en CardDAV.

Une carte se sert par trois chemins (GET, requêtes groupées des téléphones,
journal de synchronisation), et chacun doit dire la même chose sous le même
ETag :

- le corps : `_book_body` (GET) ET `_book_bodies` (multiget, query,
  sync-collection, PROPFIND avec les données) passent par `_with_pronouns` ;
- l'ETag : celui d'une personne qui a donné ses pronoms est dérivé, pour que
  la pose du pont change l'ETag des cartes déjà servies sans rien écrire ;
- le journal : changer pronoms ou genre le signale aux téléphones
  (`res.partner.write`, plus bas), et la pose aussi (`post_init_hook`).
"""

from odoo import models

from odoo.addons.bf_dav_server.dav import vcard as V

# Les propriétés que le pont écrit et qu'il ne lit jamais d'un téléphone.
OWNED = frozenset({"PRONOUNS", "GRAMGENDER"})
PRONOUN_FIELDS = ("bf_pronouns", "bf_grammatical_gender")


def property_name(line):
    """Le nom d'une ligne de vCard, sans groupe ni paramètres
    (`item1.PRONOUNS;LANGUAGE=fr:iel` → `PRONOUNS`)."""
    return line.split(":", 1)[0].split(";", 1)[0].rsplit(".", 1)[-1].strip().upper()


def vcard_lines(partner):
    """Les lignes PRONOUNS et GRAMGENDER d'une personne, dépliées."""
    if not partner or partner.is_company:
        return []
    lines = []
    pronouns = " ".join((partner.bf_pronouns or "").split())
    if pronouns:
        lines.append("PRONOUNS:%s" % V.esc(pronouns))
    if partner.bf_grammatical_gender:
        # Les valeurs du champ sont celles de la RFC 9554.
        lines.append("GRAMGENDER:%s" % partner.bf_grammatical_gender)
    return lines


def with_pronouns(body, partner):
    """La carte avec les lignes de la fiche, et sans celles qu'un téléphone
    aurait renvoyées avant la pose du pont (données propres de l'usager)."""
    if not body:
        return body
    lines = [ln for ln in V.unfold(body).replace("\r\n", "\n").split("\n")
             if ln.strip() and property_name(ln) not in OWNED]
    if not lines or lines[-1].strip().upper() != "END:VCARD":
        return body
    lines[-1:-1] = vcard_lines(partner)
    return V.CRLF.join(V.fold(ln) for ln in lines) + V.CRLF


class BfDavServiceCarddav(models.AbstractModel):
    _inherit = "bf.dav.service"

    def _book_body(self, node, member):
        body = super()._book_body(node, member)
        if member["key"][:1] != "p":
            return body
        return with_pronouns(body, self.env["res.partner"].browse(int(member["key"][1:])))

    def _book_bodies(self, node, members):
        todo = [m for m in members if "body" not in m and m["key"][:1] == "p"]
        super()._book_bodies(node, members)
        if not todo:
            return
        ids = [int(m["key"][1:]) for m in todo]
        partners = {p.id: p for p in self.env["res.partner"].browse(ids).with_prefetch(ids)}
        for m in todo:
            partner = partners.get(int(m["key"][1:]))
            if partner and m.get("body"):
                m["body"] = with_pronouns(m["body"], partner)

    def _book_members(self, node, keys=None, hrefs=None):
        members = super()._book_members(node, keys=keys, hrefs=hrefs)
        ids = [int(m["key"][1:]) for m in members if m["key"][:1] == "p"]
        if not ids:
            return members
        given = {r["id"] for r in self.env["res.partner"].sudo().search_read(
            [("id", "in", ids), "|", ("bf_pronouns", "!=", False),
             ("bf_grammatical_gender", "!=", False)], ["id"])}
        for m in members:
            if m["key"][:1] == "p" and int(m["key"][1:]) in given:
                # Un ETag dérivé : la carte qui porte désormais les pronoms en
                # change, sans qu'aucune fiche ne soit réécrite à la pose.
                m["etag"] = self._etag("card-pronouns", m["etag"])
        return members

    def _card_extra(self, card):
        # Ce qu'un téléphone renvoie dans ces propriétés n'est pas gardé avec
        # ses données : la carte les reprend de la fiche.
        return [ln for ln in super()._card_extra(card) if property_name(ln) not in OWNED]
