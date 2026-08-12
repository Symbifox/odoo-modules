from odoo import api, fields, models


class PrivacyFramework(models.Model):
    """Cadre réglementaire de confidentialité (Loi 25, GDPR, etc.).

    Enregistrement de référence portant les faits statutaires d'un régime de
    protection des renseignements personnels. Les enregistrements de vie privée
    (consentements, avis, calendriers de conservation, évaluations, registres)
    résolvent leur cadre applicable et les gabarits/rapports en font le rendu.

    Données de référence GLOBALES par défaut (`company_id = False`), partagées
    entre les sociétés via la règle `company_ids + [False]`. La société porte le
    cadre par défaut (`res.company.default_privacy_framework_id`); chaque
    enregistrement peut le surcharger (coexistence multi-juridiction).
    """

    _name = "privacy.framework"
    _description = "Cadre réglementaire de confidentialité"
    _order = "sequence, name"

    code = fields.Char(
        string="Code",
        required=True,
        index=True,
        help="Code technique unique du cadre (ex. : « loi25 », « gdpr », « nz »)",
    )
    name = fields.Char(
        string="Nom",
        required=True,
        translate=True,
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=False,
        help="Laisser vide pour un cadre partagé (global). Renseigner pour une "
        "variante propre à une société.",
    )

    # === Jurisdiction ===
    country_id = fields.Many2one(
        comodel_name="res.country",
        string="Pays / Juridiction",
    )
    jurisdiction_label = fields.Char(
        string="Libellé de juridiction",
        translate=True,
        help="Ex. : « Québec, Canada », « Union européenne »",
    )

    # === Customer-facing legal strings (bilingual columns to render FR+EN at once) ===
    law_short_label = fields.Char(
        string="Étiquette courte de la loi",
        help="Badge affiché dans les courriels/certificats (ex. : « Loi 25 », « GDPR »)",
    )
    law_citation_fr = fields.Char(string="Citation de la loi (FR)")
    law_citation_en = fields.Char(string="Citation de la loi (EN)")
    consent_intro_fr = fields.Text(
        string="Intro consentement (FR)",
        help="Phrase d'introduction de la demande de consentement (français)",
    )
    consent_intro_en = fields.Text(
        string="Intro consentement (EN)",
        help="Phrase d'introduction de la demande de consentement (anglais)",
    )

    # === Supervisory authority ===
    authority_name = fields.Char(
        string="Autorité de surveillance",
        translate=True,
        help="Ex. : « Commission d'accès à l'information (CAI) », « ICO », « OPC »",
    )
    authority_url = fields.Char(string="Site de l'autorité")
    breach_portal_url = fields.Char(string="Portail de déclaration d'incident")

    # === Privacy officer ===
    officer_title_fr = fields.Char(string="Titre du responsable (FR)")
    officer_title_en = fields.Char(string="Titre du responsable (EN)")
    officer_required = fields.Boolean(
        string="Responsable obligatoire",
        help="La désignation d'un responsable de la protection des RP est exigée",
    )

    # === Minors ===
    age_of_majority = fields.Integer(
        string="Âge du consentement",
        default=14,
        help="Âge en deçà duquel le consentement parental s'applique",
    )

    # === Breach notification ===
    breach_notify_authority = fields.Boolean(
        string="Notifier l'autorité",
        default=True,
    )
    breach_notify_authority_hours = fields.Integer(
        string="Délai à l'autorité (heures)",
        help="0 = « sans délai » (pas de délai chiffré)",
    )
    breach_notify_authority_label = fields.Char(
        string="Libellé du délai à l'autorité",
        translate=True,
    )
    breach_notify_individuals = fields.Boolean(
        string="Notifier les personnes concernées",
    )
    breach_notify_individuals_label = fields.Char(
        string="Libellé de la notification aux personnes",
        translate=True,
    )
    breach_threshold_label = fields.Char(
        string="Seuil de déclaration",
        translate=True,
        help="Ex. : « risque de préjudice sérieux », « risk to rights and freedoms »",
    )

    # === Privacy impact assessment ===
    assessment_name_fr = fields.Char(string="Nom de l'évaluation (FR)")
    assessment_name_en = fields.Char(string="Nom de l'évaluation (EN)")
    assessment_citation = fields.Char(string="Citation de l'évaluation d'impact")

    # === Anonymization ===
    anonymization_citation = fields.Char(
        string="Citation — anonymisation",
        help="Référence statutaire encadrant l'anonymisation des RP",
    )

    # === Retention / destruction ===
    default_retention_years = fields.Integer(
        string="Conservation par défaut (années)",
    )
    destruction_basis_template = fields.Text(
        string="Base légale de destruction",
        help="Texte de base légale apposé aux entrées du registre de destruction",
    )
    erasure_basis_citation = fields.Char(
        string="Citation — droit à l'effacement",
    )
    register_immutability_citation = fields.Char(
        string="Citation — immuabilité du registre",
        help="Référence citée dans le message d'immuabilité du registre de destruction",
    )

    # === Misc ===
    languages = fields.Char(
        string="Langues",
        help="Informationnel, ex. : « fr_CA,en_CA »",
    )
    notes = fields.Text(string="Notes")

    lawful_basis_ids = fields.One2many(
        comodel_name="privacy.lawful.basis",
        inverse_name="framework_id",
        string="Bases légales",
    )
    right_ids = fields.One2many(
        comodel_name="privacy.right",
        inverse_name="framework_id",
        string="Droits des personnes concernées",
    )

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "Le code de cadre doit être unique par société !",
        ),
    ]

    @api.depends("name", "law_short_label")
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.name or record.law_short_label or record.code or ""
