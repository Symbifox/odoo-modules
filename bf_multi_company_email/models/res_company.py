from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_signature_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Gabarit de signature courriel",
        domain="[('bf_is_signature', '=', True)]",
        ondelete="set null",
        help="Gabarit rendu sur le profil de chaque personne de "
             "cette société pour produire une signature HTML uniforme. Le "
             "gabarit s'écrit sur le modèle res.users (object = l'utilisateur).",
    )

    # ------------------------------------------------------------------
    # Signature policy (18.0.2.0.0)
    # ------------------------------------------------------------------
    bf_signature_enforce = fields.Boolean(
        string="Imposer la signature",
        help="Personne ne peut plus modifier sa signature à la main : le "
             "champ est verrouillé dans les préférences et toute écriture "
             "est remplacée par le rendu du gabarit. Les administrateurs "
             "(Paramètres) restent libres.",
    )
    bf_signature_auto_apply = fields.Boolean(
        string="Propager les changements",
        default=True,
        help="Régénère les signatures dès que le gabarit ou les éléments de "
             "marque changent. Décoché, il faut cliquer « Appliquer à tout "
             "le monde ».",
    )

    # -- brand elements the template reads -----------------------------
    bf_signature_logo_url = fields.Char(
        string="Logo (URL)",
        help="Adresse publique du logo. Une image en pièce jointe ou en "
             "data: est bloquée ou dégradée par la plupart des clients ; "
             "une URL stable survit, et écraser le fichier derrière elle "
             "met à jour toutes les signatures déjà collées ailleurs.",
    )
    bf_signature_logo_width = fields.Integer(
        string="Largeur du logo (px)",
        default=110,
    )
    bf_signature_accent_color = fields.Char(
        string="Couleur d'accent",
        # Neutral out of the box. A module installed by another organisation
        # should not open in the publisher's brand; a company that has already
        # chosen a colour keeps it, because the seeding only overwrites a value
        # still equal to this default (see hooks._untouched).
        default="#3C6E9F",
    )
    bf_signature_text_color = fields.Char(
        string="Couleur du texte",
        default="#222222",
        # Depuis la 18.0.5.1.0, ce champ ne porte plus seulement du texte : il
        # est aussi le FOND des boutons de liens rapides, dont le libellé est
        # blanc en dur dans les gabarits livrés. Une couleur claire ici rend
        # donc ce libellé illisible — le défaut que la 18.0.5.1.0 corrige,
        # exactement à l'envers. Choisir le libellé selon la luminance du fond
        # demande une clé de plus dans `res.users.bf_signature_data()`, donc
        # du Python : c'est une suite à faire, au prochain redémarrage du
        # module, et pas quelque chose qu'une écriture de gabarit peut livrer.
        help="Couleur du texte principal de la signature. ⚠ C'est aussi le "
             "fond des boutons de liens rapides, dont le libellé est blanc : "
             "une couleur claire ici rendrait ces libellés illisibles. "
             "Garder une couleur foncée — WCAG AA demande 4,5:1 de contraste "
             "contre le blanc pour du texte normal.",
    )
    bf_signature_muted_color = fields.Char(
        string="Couleur secondaire",
        default="#666666",
    )
    bf_signature_font = fields.Char(
        string="Police",
        # A system stack by default: a brand typeface is not installed on the
        # recipient's machine, and a public module has no business assuming
        # the publisher's. Companies that want theirs set it once.
        default="-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
        help="Pile de polices CSS. Toujours terminer par une police "
             "universelle : aucune police de marque n'est installée chez "
             "le destinataire.",
    )
    bf_signature_tollfree = fields.Char(
        string="Numéro sans frais",
        help="Affiché à côté du téléphone direct de la personne.",
    )
    bf_signature_tagline = fields.Char(
        string="Signature de marque",
        help="Une ligne sous le bloc, facultative.",
    )
    bf_signature_disclaimer = fields.Html(
        string="Avis de confidentialité",
        translate=True,
        sanitize=True,
        help="Le bloc légal que certaines organisations ajoutent au pied de "
             "chaque courriel : confidentialité, destinataire non voulu, "
             "obligation de détruire.\n\n"
             "⚠️ Il voyage AVEC la signature, donc il apparaît là où la "
             "signature apparaît. Les produits qui l'ajoutent à tout le "
             "courrier sortant, y compris les réponses courtes sans "
             "signature, le font à la passerelle — ce n'est pas le même "
             "endroit ni le même outil.\n\n"
             "Le champ est traduisible : chaque correspondant reçoit la "
             "version de la langue enregistrée sur sa fiche.",
    )
    bf_signature_disclaimer_size = fields.Selection(
        selection=[
            ("tiny", "Très petit (9 px)"),
            ("small", "Petit (10 px)"),
            ("normal", "Normal (12 px)"),
        ],
        string="Taille de l'avis",
        default="tiny",
        help="Un avis légal se lit rarement et prend toute la place. Les "
             "organisations qui en mettent un le composent petit.",
    )
    bf_signature_link_ids = fields.One2many(
        comodel_name="bf.signature.link",
        inverse_name="company_id",
        string="Liens rapides",
        help="Affichés en pied de signature pour toute la société.",
    )
    bf_signature_user_count = fields.Integer(
        string="Signatures pilotées",
        compute="_compute_bf_signature_user_count",
    )

    # Per-company mail routing. Surfaced to the standard mail.* config
    # parameters by the ir.config_parameter override (see ir_config_parameter.py).
    # bf_-prefixed names keep this module fully decoupled from the legacy
    # ous_multi_company_email fields so install/uninstall order is free.
    bf_catchall_domain = fields.Char(
        string="Domaine catchall (société)",
        help="Surcharge mail.catchall.domain lorsque cette société est active.",
    )
    bf_catchall_alias = fields.Char(
        string="Alias catchall (société)",
        help="Surcharge mail.catchall.alias lorsque cette société est active.",
    )
    bf_bounce_alias = fields.Char(
        string="Alias bounce (société)",
        help="Surcharge mail.bounce.alias lorsque cette société est active.",
    )
    bf_default_from = fields.Char(
        string="Expéditeur par défaut (société)",
        help="Surcharge mail.default.from lorsque cette société est active.",
    )

    # ------------------------------------------------------------------
    def _compute_bf_signature_user_count(self):
        Users = self.env["res.users"].sudo()
        for company in self:
            company.bf_signature_user_count = Users.search_count([
                ("share", "=", False),
                ("company_id", "=", company.id),
            ]) if company.id else 0

    def _bf_signature_users(self):
        """Internal users whose signature this company drives."""
        return self.env["res.users"].sudo().search([
            ("share", "=", False),
            ("company_id", "in", self.ids),
        ])

    def action_bf_apply_signatures(self):
        """Re-render every signature this company drives."""
        # Réécrit, en sudo, la signature de toute la société, y compris quand
        # la propagation est coupée le temps de préparer un gabarit.
        if not self.env.user.has_group("base.group_erp_manager"):
            raise AccessError(_(
                "Appliquer la signature à toute la société est réservé aux "
                "administrateurs."))
        users = self._bf_signature_users()
        users._bf_apply_company_signature()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Signatures appliquées"),
                "message": _("%s signature(s) régénérée(s).", len(users)),
                "type": "success",
                "sticky": False,
            },
        }

    # Writing any of these changes what the template renders, so the stored
    # signatures are stale the instant they are saved.
    _BF_SIGNATURE_TRIGGERS = (
        "bf_signature_template_id", "bf_signature_logo_url",
        "bf_signature_logo_width", "bf_signature_accent_color",
        "bf_signature_text_color", "bf_signature_muted_color",
        "bf_signature_font", "bf_signature_tollfree", "bf_signature_tagline",
        "bf_signature_disclaimer", "bf_signature_disclaimer_size",
        "name", "website", "email",
    )

    def write(self, vals):
        res = super().write(vals)
        if any(key in vals for key in self._BF_SIGNATURE_TRIGGERS):
            targets = self.filtered("bf_signature_auto_apply")
            if targets:
                targets._bf_signature_users()._bf_apply_company_signature()
        return res
