"""Préréglages de salle de données — la configuration d'un lien, nommée et rejouée.

Une salle de données se règle toujours de la même façon pour un même type de
dossier : même durée, même plafond de visiteurs, même budget de téléchargement
par personne, même politique d'entente. Refaire ces sept choix à chaque
nouveau dossier, c'est sept occasions de se tromper — et l'audience ouverte
est précisément le mode où une erreur ne se voit pas. Un lien mal réglé
n'affiche rien d'anormal : il refuse des gens, un par un, chacun devant un
formulaire qui parle d'un code.

Le modèle n'invente donc aucun comportement. Il POSE des valeurs que
l'assistant d'envoi sait déjà appliquer, et il refuse d'enregistrer une
combinaison que l'envoi rejetterait de toute façon.

⚠ La marque est facultative, et les préréglages livrés n'en portent AUCUNE :
ce module est la source unique de quatre locataires, et une marque semée ici
n'existerait sur aucun d'eux. Sans marque, le préréglage se pose sur celle que
l'expéditeur a déjà choisie — et l'assistant dit tout haut quand cette
marque-là ne peut pas honorer le préréglage.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SecureTransferTemplate(models.Model):
    _name = "secure.transfer.template"
    _description = "Secure transfer: data room preset"
    _order = "sequence, name"

    name = fields.Char(string="Name", required=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    # Facultative, contrairement à la marque : un préréglage sans société sert
    # tout le monde, ce que la règle multi-société sait déjà lire
    # (company_id = False OR company_id in company_ids).
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        help="Empty = available to all companies.",
    )
    note = fields.Text(
        string="What this preset is for",
        help="Shown to the sender when choosing. Describe the kind of "
             "file it is meant for rather than the values: they are right "
             "next to it.",
    )

    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Brand",
        domain="[('active', '=', True), ('fixed_recipient', '=', False)]",
        help="Empty = keep the brand already chosen in the wizard. A "
             "brand set here must offer the mode chosen below.",
    )
    audience_mode = fields.Selection(
        selection=[
            ("declared", "Named recipients"),
            ("open", "Open audience (the visitor identifies themselves)"),
        ],
        string="Delivery mode", default="open", required=True,
    )
    retention_days = fields.Integer(
        string="Available (days)", default=30,
        help="Lifetime of the link. The brand's cap always applies on top.",
    )
    audience_domains = fields.Text(
        string="Allowed domains",
        help="Empty = the brand's list. One entry per line: @client.com, "
             "or a full address.",
    )
    audience_max = fields.Integer(
        string="Max. visitors", default=0,
        help="0 = brand value.",
    )
    audience_max_downloads = fields.Integer(
        string="Downloads per visitor", default=0,
        help="0 = unlimited. Counted separately for each person.",
    )
    audience_allow_sms = fields.Boolean(
        string="Offer the code by SMS",
        help="Lets a visitor identify themselves by mobile. Incompatible "
             "with a domain list: a number has no domain.",
    )
    notify_on_join = fields.Boolean(
        string="Notify on each new visitor", default=True,
    )

    _sql_constraints = [
        ("retention_days_positive", "CHECK (retention_days > 0)",
         "The availability period must be at least one day."),
        ("audience_max_positive", "CHECK (audience_max >= 0)",
         "The maximum number of visitors cannot be negative."),
        ("audience_max_downloads_positive", "CHECK (audience_max_downloads >= 0)",
         "The download budget cannot be negative."),
    ]

    @api.constrains("audience_mode", "brand_id")
    def _check_brand_offers_mode(self):
        """Même barrière que sur le transfert, un cran plus tôt.

        Un préréglage est fait pour être rejoué sans relire : s'il porte un
        mode que sa marque n'offre pas, il produit un envoi refusé à chaque
        usage. Autant refuser l'enregistrement une fois."""
        for rec in self:
            if rec.audience_mode != "open" or not rec.brand_id:
                continue
            if not rec.brand_id.allow_open_audience:
                raise ValidationError(_(
                    "The \"%s\" brand does not offer the open audience. "
                    "Turn it on for the brand (Configuration › Brands › "
                    "\"Open audience offered\"), or leave the brand empty "
                    "in this preset.",
                    rec.brand_id.display_name,
                ))

    @api.constrains("audience_allow_sms", "audience_domains")
    def _check_sms_excludes_allowlist(self):
        """Une liste de domaines et le canal SMS se contredisent.

        `secure.transfer._audience_admissible` tranche déjà ainsi : dès qu'une
        liste blanche est posée, les identités mobiles sont refusées plutôt
        que laissées passer sans contrôle. Cocher les deux ici donnerait un
        préréglage qui promet le SMS et le refuse à l'exécution — le genre de
        réglage qu'on ne découvre que par le visiteur qui n'entre pas."""
        for rec in self:
            if rec.audience_allow_sms and (rec.audience_domains or "").strip():
                raise ValidationError(_(
                    "\"%s\": the code by SMS and a domain list cannot "
                    "coexist. A mobile number has no domain, so the list "
                    "would reject it. Remove the list, or do not offer "
                    "SMS.",
                    rec.display_name,
                ))

    def _apply_vals(self):
        """Les valeurs que ce préréglage pose sur l'assistant d'envoi.

        Point d'extension, sur le modèle de
        `secure.transfer.send.wizard._transfer_vals` : un module satellite
        (l'entente de confidentialité) ajoute sa clé ICI plutôt que de
        réécrire l'onchange de l'assistant.

        ⚠ `brand_id` n'est présent que si le préréglage en porte une. Une
        clé absente veut dire « ne touche pas », pas « remets à vide » :
        l'appelant écrit ce qu'il reçoit, rien de plus."""
        self.ensure_one()
        vals = {
            "audience_mode": self.audience_mode,
            "retention_days": self.retention_days,
            "audience_domains": (self.audience_domains or "").strip() or False,
            "audience_max": self.audience_max,
            "audience_max_downloads": self.audience_max_downloads,
            "audience_allow_sms": self.audience_allow_sms,
            "notify_on_join": self.notify_on_join,
        }
        if self.brand_id:
            vals["brand_id"] = self.brand_id.id
        return vals
