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
    _description = "Transfert sécurisé — Préréglage de salle de données"
    _order = "sequence, name"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    # Facultative, contrairement à la marque : un préréglage sans société sert
    # tout le monde, ce que la règle multi-société sait déjà lire
    # (company_id = False OR company_id in company_ids).
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        help="Vide = disponible pour toutes les sociétés.",
    )
    note = fields.Text(
        string="À quoi sert ce préréglage",
        help="Affiché à l'expéditeur au moment du choix. Décrivez le type de "
             "dossier visé plutôt que les valeurs — elles sont juste à côté.",
    )

    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Marque",
        domain="[('active', '=', True), ('fixed_recipient', '=', False)]",
        help="Vide = garder la marque déjà choisie dans l'assistant. Une "
             "marque posée ici doit offrir le mode retenu ci-dessous.",
    )
    audience_mode = fields.Selection(
        selection=[
            ("declared", "Destinataires nommés"),
            ("open", "Audience ouverte (le visiteur se déclare)"),
        ],
        string="Mode de transmission", default="open", required=True,
    )
    retention_days = fields.Integer(
        string="Disponible (jours)", default=30,
        help="Durée de vie du lien. Le plafond de la marque s'applique "
             "toujours par-dessus.",
    )
    audience_domains = fields.Text(
        string="Domaines admis",
        help="Vide = la liste de la marque. Une entrée par ligne : "
             "@client.com, ou une adresse complète.",
    )
    audience_max = fields.Integer(
        string="Visiteurs max.", default=0,
        help="0 = valeur de la marque.",
    )
    audience_max_downloads = fields.Integer(
        string="Téléchargements par visiteur", default=0,
        help="0 = illimité. Compté séparément pour chaque personne.",
    )
    audience_allow_sms = fields.Boolean(
        string="Offrir le code par SMS",
        help="Laisse un visiteur s'identifier par son mobile. Incompatible "
             "avec une liste de domaines : un numéro n'a pas de domaine.",
    )
    notify_on_join = fields.Boolean(
        string="Aviser à chaque nouveau visiteur", default=True,
    )

    _sql_constraints = [
        ("retention_days_positive", "CHECK (retention_days > 0)",
         "La durée de disponibilité doit être d'au moins une journée."),
        ("audience_max_positive", "CHECK (audience_max >= 0)",
         "Le nombre maximal de visiteurs ne peut pas être négatif."),
        ("audience_max_downloads_positive", "CHECK (audience_max_downloads >= 0)",
         "Le budget de téléchargements ne peut pas être négatif."),
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
                    "La marque « %s » n'offre pas l'audience ouverte. "
                    "Activez-la sur la marque — Configuration › Marques › "
                    "« Audience ouverte offerte » — ou laissez la marque vide "
                    "dans ce préréglage.",
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
                    "« %s » : le code par SMS et une liste de domaines ne "
                    "peuvent pas coexister. Un numéro de mobile n'a pas de "
                    "domaine, donc la liste le refuserait. Retirez la liste, "
                    "ou n'offrez pas le SMS.",
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
