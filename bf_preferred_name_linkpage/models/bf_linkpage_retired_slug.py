"""Les adresses de page retirées : aucune autre personne ne les reprend.

Une adresse quittée (`/l/ancien-nom`) devenait libre, et une autre page pouvait
la prendre : les liens envoyés et les codes QR imprimés de la première menaient
alors chez quelqu'un d'autre. Les adresses retirées sont donc réservées,
toujours sans redirection. Une adresse se retire quand une page la quitte ou est supprimée.

L'adresse retirée porte souvent l'ancien nom de la personne. Elle n'est donc
gardée qu'en EMPREINTE (HMAC-SHA256) : la réserve sait reconnaître une adresse
qu'on lui présente, elle ne sait pas la dire. La clé est propre au module,
créée une fois : `database.secret`, qu'on fait tourner après une fuite de
session ou qu'une copie de base régénère, aurait vidé la réserve sans bruit.

⚠️ Risque résiduel, assumé : qui essaie un nom comme adresse
de sa page apprend qu'elle n'est pas disponible alors que `/l/<nom>` ne répond
pas, donc que quelqu'un l'a quittée. Le refus est le même que pour une adresse
prise, mais l'écart avec la page publique reste lisible.

Une réservation se lève par un administrateur (Paramètres > Technique), par
exemple pour une faute de frappe corrigée.
"""

import hashlib
import hmac
import secrets

from odoo import api, fields, models

KEY_PARAM = "bf_preferred_name_linkpage.retired_slug_key"


class BfLinkpageRetiredSlug(models.Model):
    _name = "bf.linkpage.retired.slug"
    _description = "Retired link page address"

    slug_hash = fields.Char(string="Address fingerprint", required=True, index=True, readonly=True)
    page_id = fields.Many2one(
        "bf.linkpage", string="Page that left it", ondelete="set null", readonly=True,
        help="The page may take this address back.")
    partner_id = fields.Many2one(
        "res.partner", string="Person", ondelete="set null", readonly=True,
        help="Another page of the same person may take this address back; no "
             "one else can.")

    _sql_constraints = [
        ("slug_hash_uniq", "unique(slug_hash)", "This address is already retired."),
    ]

    @api.model
    def _key(self):
        """La clé de l'empreinte, créée une fois.

        Créée à l'installation et à la montée (`post_init_hook`,
        `end-migrate`) : née au premier appel, deux premiers appels
        simultanés se disputaient le paramètre et l'un échouait. Le repli ici ne sert qu'à une base où le
        crochet n'aurait pas tourné.
        """
        Param = self.env["ir.config_parameter"].sudo()
        key = Param.get_param(KEY_PARAM)
        if not key:
            key = secrets.token_hex(32)
            Param.set_param(KEY_PARAM, key)
        return key

    @api.model
    def _fingerprint(self, slug):
        message = ("bf.linkpage:%s" % (slug or "")).encode()
        return hmac.new(self._key().encode(), message, hashlib.sha256).hexdigest()

    @api.model
    def _retire(self, slug, page):
        """Réserve cette adresse ; la page qui la quitte, ou une autre page
        de la même personne, peut y revenir."""
        if not slug:
            return
        fingerprint = self._fingerprint(slug)
        Retired = self.sudo()
        if not Retired.search_count([("slug_hash", "=", fingerprint)]):
            partner = page.partner_id if page.kind == "owner" else page.browse()
            Retired.create({"slug_hash": fingerprint, "page_id": page.id,
                            "partner_id": partner.id or False})
            # Écrit tout de suite : quand la page part en cascade SQL avec sa
            # fiche, une insertion retardée au prochain vidage visait une page
            # déjà supprimée (clé étrangère).
            Retired.flush_model()

    @api.model
    def _is_retired(self, slug, page=None):
        """Vrai si l'adresse est retirée par une AUTRE personne que celle de `page`."""
        if not slug:
            return False
        retired = self.sudo().search([("slug_hash", "=", self._fingerprint(slug))], limit=1)
        if not retired:
            return False
        if not page:
            return True
        # « La même personne » se prouve par la fiche ET par l'usager : sans
        # le second, un usager rattachait un instant sa page à la fiche d'un
        # autre, ou en créait une en son nom, reprenait l'adresse, puis
        # remettait sa propre fiche.
        if retired.partner_id:
            return not (page.kind == "owner" and page.partner_id == retired.partner_id
                        and (not page.user_id or page.user_id.partner_id == retired.partner_id))
        # Adresse d'une page d'organisation : seule cette page peut y revenir.
        return not (page.id and retired.page_id == page)
