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
    _description = "Lien d'une page de liens"
    _order = "sequence, id"

    page_id = fields.Many2one(
        "bf.linkpage", string="Page", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    name = fields.Char(string="Libellé", required=True, translate=True)
    subtitle = fields.Char(string="Précision", translate=True)
    icon = fields.Char(
        string="Icône",
        help="Classe Font Awesome, par exemple « fa-calendar ». Facultatif.",
    )

    source_code = fields.Selection(
        selection=lambda self: self.env["bf.linkpage.source"]._selection(),
        string="Source",
        default="manual",
        required=True,
        help="D'où vient l'adresse. « Adresse saisie » utilise le champ URL ; "
             "les autres la résolvent depuis la base à chaque affichage.",
    )
    source_available = fields.Boolean(
        string="Source disponible",
        compute="_compute_source_available",
        help="Faux quand le module qui fournit cette source n'est pas installé.",
    )

    url = fields.Char(
        string="Adresse",
        help="Utilisée quand la source est « Adresse saisie ».",
    )
    resolved_url = fields.Char(
        string="Adresse résolue",
        compute="_compute_resolved_url",
        help="Ce vers quoi le lien pointe réellement. Vide = le lien ne "
             "s'affiche pas sur la page publique.",
    )

    # Référence douce vers l'enregistrement visé par une source. Douce parce
    # que la cible vit dans un module OPTIONNEL : un Many2one obligerait ce
    # module à en dépendre, et l'installation échouerait là où le fournisseur
    # est absent.
    source_res_model = fields.Char(string="Modèle visé", readonly=True)
    source_res_id = fields.Integer(string="Enregistrement visé", readonly=True)

    is_social = fields.Boolean(
        string="Réseau social",
        compute="_compute_is_social",
        store=True,
        help="Un lien de réseau s'affiche en rangée d'icônes sous les liens "
             "principaux, sans libellé.",
    )

    from_template = fields.Boolean(
        string="Posé par un gabarit",
        readonly=True,
        help="Un lien posé par un gabarit est remplacé quand le gabarit est "
             "réappliqué. Un lien ajouté à la main ne l'est jamais.",
    )

    click_count = fields.Integer(string="Clics", default=0, readonly=True, copy=False)

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
                    "Le lien « %s » utilise une adresse saisie mais n'en a pas.",
                    link.name,
                ))
            if not link.url.lower().startswith(ALLOWED_SCHEMES):
                raise ValidationError(_(
                    "L'adresse du lien « %(name)s » doit commencer par %(schemes)s.",
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
