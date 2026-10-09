from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import email_normalize


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Minor / Guardian relationship
    is_minor_child = fields.Boolean(
        string="Personne mineure",
        help="Cocher si ce contact est une personne mineure (moins de 14 ans). "
        "Les consentements seront adressés aux responsables légaux.",
    )
    legal_guardian_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="child_id",
        column2="guardian_id",
        string="Responsables légaux",
        help="Parents ou tuteurs de la personne mineure",
    )
    minor_child_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="guardian_id",
        column2="child_id",
        string="Enfants à charge",
    )

    # Consent records
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="subject_partner_id",
        string="Enregistrements de consentement",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_stats",
        string="Nombre de consentements",
    )

    # Preferences
    preference_id = fields.Many2one(
        comodel_name="privacy.contact.preference",
        string="Préférences de vie privée",
        compute="_compute_preference_id",
        inverse="_inverse_preference_id",
        help="Préférences de vie privée et de communication du contact",
    )

    # Consent badges (computed)
    has_marketing_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement marketing",
        help="Possède un consentement actif accordé pour les finalités marketing",
    )
    has_recording_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement enregistrement",
        help="Possède un consentement actif accordé pour les finalités d'enregistrement",
    )
    has_reference_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement référence",
        help="Possède un consentement actif accordé pour être utilisé comme référence",
    )

    # Quick access to DNC status
    do_not_contact = fields.Boolean(
        compute="_compute_do_not_contact",
        string="Ne pas contacter",
        help="Indicateur principal « ne pas contacter » provenant des préférences",
    )

    # Responsable de la protection des renseignements personnels de l'organisation
    # (art. 3.1 P-39.1). Stocké ici une fois : la société le reprend par son partenaire.
    privacy_officer_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Responsable de la protection des RP",
        tracking=True,
        copy=False,
        ondelete="restrict",
        groups="privacy_consent.group_privacy_user",
        help="La personne qui exerce la fonction de responsable de la protection des "
        "renseignements personnels de cette organisation : par défaut la personne ayant "
        "la plus haute autorité, ou celle à qui elle l'a déléguée par écrit (art. 3.1).",
    )
    privacy_officer_email = fields.Char(
        string="Adresse de réception désignée",
        tracking=True,
        copy=False,
        groups="privacy_consent.group_privacy_user",
        help="L'adresse où cette organisation accepte de recevoir les avis qui touchent "
        "ses renseignements personnels, par exemple l'avis de violation d'un mandataire. "
        "Un document y est présumé reçu dès qu'il y devient accessible (C-1.1, art. 31). "
        "Sans elle, aucun avis ne part : l'adresse n'est jamais devinée.",
    )
    privacy_officer_public_url = fields.Char(
        string="Coordonnées publiées",
        tracking=True,
        copy=False,
        groups="privacy_consent.group_privacy_user",
        help="La page du site de l'organisation où le titre et les coordonnées de son "
        "responsable sont publiés (art. 3.1).",
    )

    _PRIVACY_OFFICER_FIELDS = ("privacy_officer_partner_id", "privacy_officer_email",
                               "privacy_officer_public_url")

    def _privacy_officer_change(self, vals):
        """Les champs de désignation dont `vals` change vraiment la valeur.

        Le formulaire d'Odoo 18 envoie tous ses champs à la création, `False` compris, et une
        fusion de contacts réécrit ce qui ne bouge pas : seule une vraie différence compte.
        """
        changes = []
        for name in self._PRIVACY_OFFICER_FIELDS:
            if name not in vals:
                continue
            new = vals[name] or False
            for partner in self.sudo():
                old = partner[name].id if name == "privacy_officer_partner_id" else partner[name]
                if (old or False) != new:
                    changes.append(name)
                    break
        return changes

    def _privacy_officer_guard(self):
        if not self.env.user.has_group("privacy_consent.group_privacy_manager"):
            raise AccessError(_("Désigner le responsable de la protection des renseignements "
                                "personnels demande le rôle de gestionnaire de la vie privée."))

    def write(self, vals):
        # Lire la désignation suffit au rôle d'utilisateur ; la changer décide où partent des
        # avis légaux et qui peut en accuser réception : c'est au gestionnaire de le faire.
        if not self.env.su and any(f in vals for f in self._PRIVACY_OFFICER_FIELDS) \
                and self._privacy_officer_change(vals):
            self._privacy_officer_guard()
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get(f) for vals in vals_list for f in self._PRIVACY_OFFICER_FIELDS):
            self._privacy_officer_guard()
        partners = super().create(vals_list)
        # `default_privacy_officer_*` du contexte et `ir.default` ne passent pas par `vals` :
        # on juge le résultat.
        if not self.env.su and any(p[f] for p in partners.sudo() for f in self._PRIVACY_OFFICER_FIELDS):
            self._privacy_officer_guard()
        return partners

    @api.constrains("privacy_officer_email")
    def _check_privacy_officer_email(self):
        for partner in self.sudo():
            if partner.privacy_officer_email and not email_normalize(partner.privacy_officer_email):
                raise ValidationError(_(
                    "L'adresse de réception désignée « %s » n'est pas une adresse courriel valide.",
                    partner.privacy_officer_email))

    def _privacy_officer_address(self):
        """Le responsable de cette organisation et l'adresse où l'aviser.

        Rend (contact, adresse). Seule l'adresse désignée, saisie par un gestionnaire de la vie
        privée, compte : ni le courriel général de l'organisation, ni celui de la fiche contact
        du responsable, que tout gestionnaire de contacts peut modifier. Un avis légal ne part
        pas à une adresse devinée.

        Lue sur l'enregistrement LUI-MÊME, jamais sur sa société mère : un gestionnaire de
        contacts qui rattache le client A sous le client B ne doit pas dérouter les avis de A.
        """
        self.ensure_one()
        org = self.sudo()
        return org.privacy_officer_partner_id, email_normalize(org.privacy_officer_email or "") or ""

    @api.depends("consent_ids")
    def _compute_consent_stats(self):
        for partner in self:
            partner.consent_count = len(partner.consent_ids)

    def _compute_preference_id(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.preference_id = preference.id if preference else False

    def _inverse_preference_id(self):
        # Allow setting preference via this field
        pass

    @api.depends("consent_ids", "consent_ids.status", "consent_ids.purpose_id")
    def _compute_consent_badges(self):
        for partner in self:
            active_consents = partner.consent_ids.filtered(
                lambda c: c.status == "granted"
            )
            # Check for marketing consent
            partner.has_marketing_consent = any(
                c.purpose_id.code == "marketing" for c in active_consents
            )
            # Check for recording consent
            partner.has_recording_consent = any(
                c.purpose_id.code == "recording" for c in active_consents
            )
            # Check for reference consent
            partner.has_reference_consent = any(
                c.purpose_id.code == "reference" for c in active_consents
            )

    def _compute_do_not_contact(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.do_not_contact = preference.do_not_contact if preference else False

    def action_view_consents(self):
        """Open consent records for this partner."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("subject_partner_id", "=", self.id)],
            "context": {"default_subject_partner_id": self.id},
        }

    def action_view_preferences(self):
        """Open or create privacy preferences."""
        self.ensure_one()
        Preference = self.env["privacy.contact.preference"]
        preference = Preference.search([
            ("partner_id", "=", self.id),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

        if preference:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "res_id": preference.id,
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "context": {"default_partner_id": self.id},
            }

    def action_request_consent(self):
        """Open wizard to request consent.

        If opened from a minor's form: the subject is the minor child,
        and the emails will be sent to all legal guardians automatically.
        If opened from a guardian's form: include all minor children as
        subjects so consent can be requested for each child.
        """
        self.ensure_one()
        subject_ids = [self.id]
        if not self.is_minor_child and self.minor_child_ids:
            # Guardian: include all minor children as subjects
            subject_ids = self.minor_child_ids.ids
        return {
            "type": "ir.actions.act_window",
            "name": "Demander le consentement",
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_ids": [(6, 0, subject_ids)]},
        }
