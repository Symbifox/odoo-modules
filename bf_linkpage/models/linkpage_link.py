"""Un lien d'une page.

Un lien est soit une adresse écrite à la main, soit une SOURCE que le module
résout à l'affichage. C'est le second cas qui porte la valeur : il survit au
changement d'adresse en amont, donc au QR déjà imprimé.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Les schémas admis dans une URL de lien. Tout le reste est refusé, `javascript:`
# en tête : une page publique qui rend un href fourni par un usager du back-office
# rendrait sinon du script exécuté chez le visiteur.
ALLOWED_SCHEMES = ("http://", "https://", "mailto:", "tel:", "sms:")


def _safe_url(url):
    """Rendre l'URL si son schéma est admis, sinon False.

    Le filtre porte sur l'adresse RÉSOLUE, pas seulement sur l'adresse saisie.
    La contrainte d'écriture ne voit que le champ `url` ; une source dynamique,
    elle, lit un champ que personne n'a validé — le site web d'un contact, par
    exemple. Sans ce filtre, `//exemple.invalide/x` posé dans une fiche de
    contact transforme `/l/<slug>/go/<id>` en redirecteur ouvert hébergé sur le
    domaine de la maison, ce qui est exactement ce qu'on prête à une adresse de
    confiance dans un courriel d'hameçonnage. Mesuré le 2026-08-30 : la
    redirection partait bel et bien vers `http://evil.invalid/x`.

    Une adresse refusée rend False, donc le lien DISPARAÎT de la page, ce qui
    est déjà la règle du module pour tout ce qui ne se résout pas.
    """
    if not url:
        return False
    candidate = url.strip()
    if not candidate.lower().startswith(ALLOWED_SCHEMES):
        return False
    return candidate


class BfLinkpageLink(models.Model):
    _name = "bf.linkpage.link"
    _description = "Link page link"
    _order = "sequence, id"

    page_id = fields.Many2one(
        "bf.linkpage", string="Page", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    name = fields.Char(string="Label", required=True, translate=True)
    subtitle = fields.Char(string="Details", translate=True)
    icon = fields.Char(
        string="Icon",
        help="Font Awesome class, for example \"fa-calendar\". Optional.",
    )

    source_code = fields.Selection(
        selection=lambda self: self.env["bf.linkpage.source"]._selection(),
        string="Source",
        default="manual",
        required=True,
        help="Where the address comes from. \"Manual address\" uses the "
             "URL field; the others resolve it from the database on every "
             "display.",
    )
    source_available = fields.Boolean(
        string="Source available",
        compute="_compute_source_available",
        help="False when the module providing this source is not installed.",
    )

    url = fields.Char(
        string="Address",
        help="Used when the source is \"Manual address\".",
    )
    resolved_url = fields.Char(
        string="Resolved address",
        compute="_compute_resolved_url",
        help="What the link actually points to. Empty = the link is not "
             "shown on the public page.",
    )

    # Référence douce vers l'enregistrement visé par une source. Douce parce
    # que la cible vit dans un module OPTIONNEL : un Many2one obligerait ce
    # module à en dépendre, et l'installation échouerait là où le fournisseur
    # est absent.
    source_res_model = fields.Char(string="Target model", readonly=True)
    source_res_id = fields.Integer(string="Target record", readonly=True)

    is_social = fields.Boolean(
        string="Social network",
        compute="_compute_is_social",
        store=True,
        help="A social network link is shown in a row of icons under the "
             "main links, without a label.",
    )

    from_template = fields.Boolean(
        string="Set by a template",
        readonly=True,
        help="A link set by a template is replaced when the template is "
             "reapplied. A link added by hand never is.",
    )

    click_count = fields.Integer(string="Clicks", default=0, readonly=True, copy=False)

    # ── calculs ──────────────────────────────────────────────────────────────

    # Stocké et calculé, pas saisi : la nature d'un lien découle de sa source.
    # Laisser quelqu'un cocher « réseau social » sur un lien de téléphone
    # produirait une rangée d'icônes qui n'a aucun sens et que personne ne
    # saurait expliquer six mois plus tard.
    @api.depends("source_code")
    def _compute_is_social(self):
        social = self.env["bf.linkpage.source"]._social_codes()
        for link in self:
            link.is_social = link.source_code in social

    @api.depends("source_code")
    def _compute_source_available(self):
        available = self.env["bf.linkpage.source"]._available_codes()
        for link in self:
            link.source_available = link.source_code in available

    # Toute valeur qu'un résolveur LIT doit figurer ici. `booking_slug` et
    # `meet_url` manquaient à l'ajout des deux sources correspondantes : le
    # champ n'étant pas stocké, la page publique restait juste (transaction
    # neuve à chaque requête), mais le back-office affichait l'ancienne
    # adresse et l'ancien compte de liens affichés jusqu'à la fin de la
    # session. Un écart invisible côté public, donc, et trompeur côté écran.
    @api.depends(
        "source_code", "url", "source_res_model", "source_res_id",
        "page_id.partner_id", "page_id.user_id", "page_id.slug",
        "page_id.booking_slug", "page_id.meet_url",
        # Le conseiller est lu par les trois sources `advisor_*`. Il manquait
        # à cette liste que le back-office affiche l'ancienne adresse jusqu'à
        # la fin de la session, exactement comme `booking_slug` avant lui.
        "page_id.advisor_user_id",
    )
    def _compute_resolved_url(self):
        Source = self.env["bf.linkpage.source"]
        for link in self:
            link.resolved_url = _safe_url(Source._resolve(link.source_code, link))

    # ── référence douce ──────────────────────────────────────────────────────

    def _source_record(self, expected_model):
        """L'enregistrement visé, ou un ensemble vide.

        Rend vide dès que le modèle attendu n'est pas celui enregistré, ou que
        l'enregistrement a disparu : une référence douce n'a pas de contrainte
        d'intégrité, donc c'est ici qu'on vérifie plutôt que de faire confiance.
        """
        self.ensure_one()
        if not self.source_res_model or not self.source_res_id:
            return self.env[expected_model].browse()
        if self.source_res_model != expected_model:
            return self.env[expected_model].browse()
        record = self.env[expected_model].sudo().browse(self.source_res_id)
        return record if record.exists() else self.env[expected_model].browse()

    # ── contraintes ──────────────────────────────────────────────────────────

    @api.constrains("source_code", "url")
    def _check_url(self):
        for link in self:
            if link.source_code != "manual":
                continue
            if not link.url:
                raise ValidationError(_(
                    "The link \"%s\" uses a manual address but has none.",
                    link.name,
                ))
            if not link.url.lower().startswith(ALLOWED_SCHEMES):
                raise ValidationError(_(
                    "The address of the link \"%(name)s\" must start with "
                    "%(schemes)s.",
                    name=link.name,
                    schemes=", ".join(ALLOWED_SCHEMES),
                ))

    def _register_click(self):
        """Compter un clic sans jamais retarder la redirection du visiteur."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.sudo().write({"click_count": self.click_count + 1})
        except Exception:  # noqa: BLE001
            pass
