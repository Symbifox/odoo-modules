import base64
import hashlib

from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Art. 8 du Règlement sur les incidents de confidentialité (chapitre A-2.1,
# r. 3.1) : conservation minimale de 5 ans à compter de la date, ou de la
# période, où l'organisation a pris connaissance de l'incident.
REGISTER_RETENTION_YEARS = 5


class PrivacyIncident(models.Model):
    """Incident de confidentialité et son inscription au registre (Loi 25).

    Deux textes commandent ce modèle :

    * la Loi sur la protection des renseignements personnels dans le secteur
      privé (chapitre P-39.1), art. 3.5 à 3.8 — définition de l'incident,
      mesures raisonnables, avis à la Commission d'accès à l'information et aux
      personnes concernées lorsqu'il y a risque qu'un préjudice sérieux soit
      causé, facteurs d'évaluation de ce risque, et obligation de tenir un
      registre;
    * le Règlement sur les incidents de confidentialité (chapitre A-2.1,
      r. 3.1) — dont l'article 7 énumère les huit éléments que le registre doit
      contenir, et l'article 8 la durée de conservation.

    Chaque champ marqué « Registre — art. 7, N° » correspond à un élément
    prescrit. Le reste sert la conduite de l'incident ou la production des avis
    (art. 3 et 5 du règlement) et n'est pas exigé au registre.

    Les notes internes et le fil de discussion ne sont jamais exposés au
    portail. C'est une séparation structurelle plutôt qu'une case à cocher :
    un texte de travail ne peut pas se retrouver devant le client par oubli.
    """

    _name = "privacy.incident"
    _description = "Incident de confidentialité"
    _inherit = ["mail.thread", "mail.activity.mixin", "privacy.framework.mixin"]
    _order = "awareness_date desc, id desc"

    name = fields.Char(
        string="Numéro",
        readonly=True,
        copy=False,
        index=True,
        default="Nouveau",
    )
    title = fields.Char(
        string="Objet",
        required=True,
        tracking=True,
        help="Ce qui s'est passé, en une ligne.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Organisation concernée",
        tracking=True,
        index=True,
        help="Organisation dont le registre porte cet incident. C'est aussi "
        "l'ancrage du portail : un incident sans organisation n'est visible "
        "d'aucun client.",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Déclaré"),
            ("assessment", "Évaluation du risque"),
            ("action", "Mesures et avis"),
            ("closed", "Clos"),
        ],
        string="État",
        required=True,
        default="draft",
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Provenance de la déclaration
    # ------------------------------------------------------------------
    declared_by_portal = fields.Boolean(
        string="Déclaré depuis le portail",
        readonly=True,
        help="Vrai si l'incident a été déclaré par le client lui-même.",
    )
    declared_by_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Déclaré par",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Provenance : l'avis d'un mandataire (art. 18.3 P-39.1)
    # Le mandataire avise d'une violation ; c'est ici, chez le responsable, qu'elle
    # devient (ou non) un incident. Les faits transmis restent séparés de l'évaluation.
    # ------------------------------------------------------------------
    declared_by_processor = fields.Boolean(
        string="Signalé par un mandataire",
        compute="_compute_declared_by_processor",
        store=True,
    )
    processor_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Mandataire",
        tracking=True,
        ondelete="restrict",
        help="Le fournisseur qui détient les renseignements pour le compte de l'organisation "
        "et qui a donné l'avis.",
    )
    processor_notice_ref = fields.Char(string="Référence de l'avis", tracking=True)
    processor_notice_version = fields.Integer(string="Version de l'avis", tracking=True)
    processor_notice_received_at = fields.Datetime(
        string="Avis reçu le",
        tracking=True,
        help="Le moment où l'avis est parvenu. C'est en principe la date de prise de "
        "connaissance du registre (art. 7, 4°), qui fait courir les cinq ans de conservation.",
    )
    processor_notice_sha256 = fields.Char(
        string="Empreinte de l'avis",
        tracking=True,
        help="L'empreinte SHA-256 du PDF reçu, telle que le mandataire l'a communiquée.",
    )
    processor_notice_last_received_at = fields.Datetime(
        string="Dernière version reçue le",
        help="Le moment où la dernière mise à jour de l'avis est parvenue. La date de la "
        "première réception, elle, ne bouge plus.",
    )
    processor_notice_locked = fields.Boolean(
        string="Provenance verrouillée",
        readonly=True,
        copy=False,
        help="Vrai pour une fiche née d'un avis reçu par la fédération : sa provenance ne bouge "
        "plus qu'à la réception d'une mise à jour du mandataire.",
    )
    processor_notice_pdf = fields.Binary(string="Avis reçu (PDF)", attachment=True)
    processor_notice_pdf_name = fields.Char(string="Nom du fichier de l'avis")
    processor_notice_intact = fields.Boolean(
        string="PDF conforme à son empreinte",
        compute="_compute_processor_notice_intact",
        help="Le PDF conservé correspond-il toujours à l'empreinte consignée ?",
    )
    processor_facts = fields.Text(
        string="Faits transmis par le mandataire",
        help="Ce que le mandataire constate et qui sert l'évaluation : chiffrement, sortie "
        "des données, mesures prises. Votre évaluation, elle, se consigne dans l'onglet "
        "« Évaluation du risque ».",
    )

    # ------------------------------------------------------------------
    # Nature de l'incident — art. 3.6 P-39.1
    # ------------------------------------------------------------------
    incident_type = fields.Selection(
        selection=[
            ("unauthorized_access", "Accès non autorisé par la loi"),
            ("unauthorized_use", "Utilisation non autorisée par la loi"),
            (
                "unauthorized_disclosure",
                "Communication non autorisée par la loi",
            ),
            ("loss", "Perte ou autre atteinte à la protection"),
        ],
        string="Nature de l'incident",
        tracking=True,
        help="Les quatre cas de l'article 3.6 de la Loi sur la protection des "
        "renseignements personnels dans le secteur privé.",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 2° : brève description des circonstances
    # ------------------------------------------------------------------
    circumstances = fields.Text(
        string="Circonstances",
        tracking=True,
        help="Brève description des circonstances de l'incident. "
        "Élément prescrit au registre (art. 7, 2°).",
    )
    cause = fields.Text(
        string="Cause",
        help="La cause de l'incident, si elle est connue. Exigée dans l'avis à "
        "la Commission (art. 3, 4°), pas au registre.",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 3° et 4° : chronologie
    # ------------------------------------------------------------------
    occurrence_date = fields.Date(
        string="Survenance — début",
        tracking=True,
        help="Date de survenance de l'incident, ou début de la période.",
    )
    occurrence_date_end = fields.Date(
        string="Survenance — fin",
        tracking=True,
        help="À remplir seulement si l'incident s'est étalé sur une période.",
    )
    occurrence_is_approximate = fields.Boolean(
        string="Période de survenance approximative",
        help="Le règlement accepte une approximation lorsque la période n'est "
        "pas connue (art. 7, 3°).",
    )
    occurrence_date_note = fields.Text(
        string="Précisions sur la survenance",
        help="Sur quoi repose l'approximation, ou tout élément de chronologie "
        "utile.",
    )
    awareness_date = fields.Date(
        string="Prise de connaissance — début",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
        help="Date à laquelle l'organisation a pris connaissance de l'incident. "
        "C'est le point de départ des 5 ans de conservation au registre.",
    )
    awareness_date_end = fields.Date(
        string="Prise de connaissance — fin",
        help="À remplir seulement si la prise de connaissance s'est faite sur "
        "une période (art. 7, 4°).",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 1° : renseignements personnels visés
    # ------------------------------------------------------------------
    pi_description = fields.Text(
        string="Renseignements personnels visés",
        tracking=True,
        help="Description des renseignements personnels touchés par l'incident. "
        "Élément prescrit au registre (art. 7, 1°).",
    )
    pi_description_unknown = fields.Boolean(
        string="Renseignements visés inconnus",
        help="À cocher si les renseignements visés ne sont pas connus. Le "
        "règlement exige alors la raison justifiant l'impossibilité de les "
        "décrire.",
    )
    pi_unknown_reason = fields.Text(
        string="Raison justifiant l'impossibilité",
        help="Pourquoi les renseignements visés ne peuvent être décrits "
        "(art. 7, 1°).",
    )
    pi_category = fields.Selection(
        selection=[
            ("identification", "Identification (nom, NAS, courriel)"),
            ("medical", "Médical / santé"),
            ("financial", "Financier"),
            ("biometric", "Biométrique"),
            ("geolocation", "Géolocalisation"),
            ("criminal", "Antécédents judiciaires"),
            ("opinion_political", "Opinions politiques / syndicales"),
            ("ethnic_racial", "Origine ethnique / raciale"),
            ("minor", "Renseignements sur un mineur"),
            ("other", "Autre"),
        ],
        string="Catégorie dominante",
        index=True,
        help="Sert au tri et aux statistiques. Le registre s'appuie sur la "
        "description en toutes lettres, pas sur cette catégorie.",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 5° : personnes concernées
    # ------------------------------------------------------------------
    subject_count = fields.Integer(
        string="Personnes concernées",
        tracking=True,
        help="Nombre de personnes concernées par l'incident (art. 7, 5°).",
    )
    subject_count_is_estimate = fields.Boolean(
        string="Nombre approximatif",
        help="À cocher si le nombre n'est pas connu et qu'il s'agit d'une "
        "approximation.",
    )
    subject_count_quebec = fields.Integer(
        string="dont résidents du Québec",
        help="Exigé dans l'avis à la Commission (art. 3, 7°), pas au registre.",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 6° : évaluation du risque de préjudice sérieux
    # Les facteurs repris ici sont ceux de l'art. 3.7 P-39.1 et de
    # l'art. 7, 6° du règlement, qui ajoute les utilisations malveillantes.
    # ------------------------------------------------------------------
    sensitivity_level = fields.Selection(
        selection=[
            ("public", "Public"),
            ("internal", "Interne"),
            ("confidential", "Confidentiel"),
            ("highly_confidential", "Hautement confidentiel"),
        ],
        string="Sensibilité des renseignements",
        default="confidential",
        tracking=True,
    )
    sensitivity_analysis = fields.Text(
        string="Analyse — sensibilité",
        help="Premier facteur : la sensibilité des renseignements concernés.",
    )
    malicious_use_analysis = fields.Text(
        string="Analyse — utilisations malveillantes possibles",
        help="Deuxième facteur, propre au règlement : ce qu'un tiers "
        "mal intentionné pourrait faire de ces renseignements.",
    )
    consequences_analysis = fields.Text(
        string="Analyse — conséquences appréhendées",
        help="Troisième facteur : les conséquences appréhendées de "
        "l'utilisation des renseignements.",
    )
    misuse_probability = fields.Selection(
        selection=[
            ("low", "Faible"),
            ("medium", "Moyenne"),
            ("high", "Élevée"),
        ],
        string="Probabilité d'utilisation préjudiciable",
        tracking=True,
    )
    misuse_analysis = fields.Text(
        string="Analyse — probabilité d'utilisation",
        help="Quatrième facteur : la probabilité que les renseignements soient "
        "utilisés à des fins préjudiciables.",
    )
    rprp_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsable de la protection des RP",
        tracking=True,
        help="L'article 3.7 impose de consulter le responsable de la protection "
        "des renseignements personnels au moment d'évaluer le risque.",
    )
    rprp_consultation_date = fields.Date(
        string="Date de consultation du responsable",
        tracking=True,
    )
    serious_harm_risk = fields.Selection(
        selection=[
            ("undetermined", "À déterminer"),
            ("no", "Non — aucun risque de préjudice sérieux"),
            ("yes", "Oui — risque de préjudice sérieux"),
        ],
        string="Risque de préjudice sérieux",
        required=True,
        default="undetermined",
        tracking=True,
        help="Conclusion de l'évaluation. C'est elle qui déclenche, ou non, "
        "l'obligation d'aviser. Le registre consigne les incidents dans les "
        "deux cas.",
    )
    risk_rationale = fields.Text(
        string="Motifs de la conclusion",
        tracking=True,
        help="Description des éléments qui amènent à conclure qu'il existe, ou "
        "non, un risque qu'un préjudice sérieux soit causé. Élément prescrit "
        "au registre (art. 7, 6°).",
    )

    # ------------------------------------------------------------------
    # Registre — art. 7, 7° : avis
    # ------------------------------------------------------------------
    cai_notified = fields.Boolean(
        string="Commission avisée",
        tracking=True,
    )
    cai_notification_date = fields.Date(
        string="Date de l'avis à la Commission",
        tracking=True,
        help="Élément prescrit au registre lorsqu'il y a risque de préjudice "
        "sérieux (art. 7, 7°).",
    )
    cai_last_update_date = fields.Date(
        string="Dernier complément transmis à la Commission",
        help="L'article 4 du règlement oblige à transmettre avec diligence tout "
        "renseignement de l'avis dont on prend connaissance après coup. Un avis "
        "à la Commission n'est donc pas un envoi unique.",
    )
    subjects_notified = fields.Boolean(
        string="Personnes concernées avisées",
        tracking=True,
    )
    subjects_notification_date = fields.Date(
        string="Date de l'avis aux personnes concernées",
        tracking=True,
        help="Élément prescrit au registre lorsqu'il y a risque de préjudice "
        "sérieux (art. 7, 7°).",
    )
    suggested_measures_to_subjects = fields.Text(
        string="Mesures suggérées aux personnes concernées",
        help="Ce que l'organisation suggère aux personnes concernées de faire "
        "pour diminuer ou atténuer le préjudice. Élément obligatoire de l'avis "
        "à la personne concernée (art. 5, 5°).",
    )
    contact_person_id = fields.Many2one(
        comodel_name="res.users",
        string="Personne-ressource",
        default=lambda self: self.env.user,
        help="Personne à contacter, nommée dans l'avis à la Commission "
        "(art. 3, 2°) et dans l'avis aux personnes concernées (art. 5, 6°).",
    )
    contact_details = fields.Text(
        string="Coordonnées à publier",
        help="Coordonnées permettant aux personnes concernées de se renseigner "
        "davantage.",
    )
    public_notice_given = fields.Boolean(
        string="Avis public donné",
        tracking=True,
    )
    public_notice_date = fields.Date(string="Date de l'avis public")
    public_notice_ground = fields.Selection(
        selection=[
            ("increased_harm", "Avis individuel susceptible de causer un préjudice accru"),
            ("excessive_hardship", "Difficulté excessive pour l'organisation"),
            ("no_contact_info", "Coordonnées de la personne concernée inconnues"),
            ("rapid_action", "Pour agir rapidement (avis individuel toujours dû)"),
        ],
        string="Motif de l'avis public",
        help="Les trois premiers cas rendent l'avis public obligatoire "
        "(art. 6, 2e al.). Le quatrième est facultatif (3e al.) et ne dispense "
        "pas de l'avis individuel.",
    )
    public_notice_reason = fields.Text(
        string="Précisions sur l'avis public",
        help="La raison pour laquelle un avis public a été donné. Élément "
        "prescrit au registre (art. 7, 7°).",
    )
    other_notified_note = fields.Text(
        string="Autres personnes ou organismes avisés",
        help="Toute personne ou tout organisme susceptible de diminuer le "
        "risque (art. 3.5, 2e al.). Seuls les renseignements nécessaires "
        "peuvent leur être communiqués, et le responsable de la protection des "
        "renseignements personnels doit enregistrer la communication.",
    )
    foreign_authority_notified = fields.Boolean(
        string="Autorité hors Québec avisée",
        help="Exigé dans l'avis à la Commission le cas échéant (art. 3, 11°).",
    )
    foreign_authority_note = fields.Text(string="Autorité hors Québec — précisions")
    notification_deferred_investigation = fields.Boolean(
        string="Avis reporté — enquête en cours",
        tracking=True,
        help="L'article 3.5, 3e alinéa, permet de ne pas aviser une personne "
        "concernée tant que cela entraverait une enquête menée par un organisme "
        "chargé de prévenir, détecter ou réprimer le crime.",
    )
    notification_deferral_note = fields.Text(string="Report d'avis — précisions")

    # ------------------------------------------------------------------
    # Registre — art. 7, 8° : mesures prises
    # ------------------------------------------------------------------
    measure_ids = fields.One2many(
        comodel_name="privacy.incident.measure",
        inverse_name="incident_id",
        string="Mesures",
    )
    measure_count = fields.Integer(
        compute="_compute_measure_count",
        string="Nombre de mesures",
    )

    # ------------------------------------------------------------------
    # Interne — jamais exposé au portail
    # ------------------------------------------------------------------
    internal_notes = fields.Text(
        string="Notes internes",
        help="Notes de travail. Ce champ n'est jamais affiché au portail.",
        groups="base.group_user",  # ni lu par RPC depuis le portail
    )

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    register_retention_until = fields.Date(
        string="Conservation au registre jusqu'au",
        compute="_compute_register_retention_until",
        store=True,
        help="Durée minimale de 5 ans, calculée depuis la prise de "
        "connaissance de l'incident (art. 8).",
    )
    days_since_awareness = fields.Integer(
        string="Jours depuis la prise de connaissance",
        compute="_compute_days_since_awareness",
        help="Indicateur de suivi seulement. La loi exige d'aviser « avec "
        "diligence » et ne fixe aucun délai en jours.",
    )
    notification_required = fields.Boolean(
        string="Avis requis",
        compute="_compute_notification_required",
        help="Vrai dès que l'évaluation conclut à un risque de préjudice sérieux.",
    )
    # Stockés : ils servent de filtres de recherche, et un champ calculé non
    # stocké n'est pas interrogeable.
    register_gaps = fields.Char(
        string="Éléments manquants au registre",
        compute="_compute_register_gaps",
        store=True,
        help="Ce qui empêche encore l'inscription d'être complète au sens de "
        "l'article 7.",
    )
    register_complete = fields.Boolean(
        string="Inscription complète",
        compute="_compute_register_gaps",
        store=True,
    )

    @api.depends("awareness_date")
    def _compute_register_retention_until(self):
        for incident in self:
            if incident.awareness_date:
                incident.register_retention_until = (
                    incident.awareness_date
                    + relativedelta(years=REGISTER_RETENTION_YEARS)
                )
            else:
                incident.register_retention_until = False

    def _compute_days_since_awareness(self):
        today = fields.Date.context_today(self)
        for incident in self:
            if incident.awareness_date:
                incident.days_since_awareness = (today - incident.awareness_date).days
            else:
                incident.days_since_awareness = 0

    @api.depends("serious_harm_risk")
    def _compute_notification_required(self):
        for incident in self:
            incident.notification_required = incident.serious_harm_risk == "yes"

    @api.depends("measure_ids")
    def _compute_measure_count(self):
        for incident in self:
            incident.measure_count = len(incident.measure_ids)

    @api.depends(
        "pi_description",
        "pi_description_unknown",
        "pi_unknown_reason",
        "circumstances",
        "occurrence_date",
        "occurrence_is_approximate",
        "occurrence_date_note",
        "awareness_date",
        "subject_count",
        "subject_count_is_estimate",
        "risk_rationale",
        "serious_harm_risk",
        "cai_notification_date",
        "subjects_notification_date",
        "public_notice_given",
        "public_notice_date",
        "public_notice_reason",
        "measure_ids",
    )
    def _compute_register_gaps(self):
        """Les éléments de l'article 7 qui manquent encore à l'inscription.

        Volontairement un signal et non un blocage : la conduite d'un incident
        n'est pas linéaire, et refuser un enregistrement incomplet ferait
        surtout perdre l'information au moment où elle arrive. Le registre doit
        être « tenu à jour » (art. 8), ce qui suppose qu'il se remplit avec le
        temps.
        """
        for incident in self:
            gaps = []
            # 1° renseignements visés, ou la raison de l'impossibilité
            if not incident.pi_description and not (
                incident.pi_description_unknown and incident.pi_unknown_reason
            ):
                gaps.append("renseignements visés (1°)")
            # 2° circonstances
            if not incident.circumstances:
                gaps.append("circonstances (2°)")
            # 3° date ou période de survenance, approximation acceptée
            if not incident.occurrence_date and not incident.occurrence_date_note:
                gaps.append("survenance (3°)")
            # 4° prise de connaissance
            if not incident.awareness_date:
                gaps.append("prise de connaissance (4°)")
            # 5° nombre de personnes concernées, approximation acceptée
            if not incident.subject_count:
                gaps.append("personnes concernées (5°)")
            # 6° éléments menant à conclure, dans un sens comme dans l'autre
            if incident.serious_harm_risk == "undetermined":
                gaps.append("conclusion sur le préjudice sérieux (6°)")
            if not incident.risk_rationale:
                gaps.append("motifs de la conclusion (6°)")
            # 7° dates des avis, seulement s'il y a risque de préjudice sérieux
            if incident.serious_harm_risk == "yes":
                if not incident.cai_notification_date:
                    gaps.append("avis à la Commission (7°)")
                notified = incident.subjects_notification_date or (
                    incident.public_notice_given and incident.public_notice_date
                )
                if not notified and not incident.notification_deferred_investigation:
                    gaps.append("avis aux personnes concernées (7°)")
                if incident.public_notice_given and not incident.public_notice_reason:
                    gaps.append("raison de l'avis public (7°)")
            # 8° mesures prises
            if not incident.measure_ids:
                gaps.append("mesures prises (8°)")
            incident.register_gaps = ", ".join(gaps)
            incident.register_complete = not gaps

    @api.depends("name", "title")
    def _compute_display_name(self):
        for incident in self:
            if incident.name and incident.name != "Nouveau":
                incident.display_name = f"{incident.name} — {incident.title or ''}"
            else:
                incident.display_name = incident.title or "Nouveau"

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        """Ne notifier que les destinataires nommés du message et les abonnés de la fiche : un module
        de transfert (`mail_partner_forwarding`) ajoutait le « transfert » de chaque destinataire,
        notes internes comprises."""
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        message_su = message.sudo()
        allowed = set(message_su.partner_ids.ids) | set(self.sudo().message_partner_ids.ids)
        # Copie et copie cachée de `mail_composer_cc_bcc`, sur le message ou passées par le compositeur.
        for fname in ("recipient_cc_ids", "recipient_bcc_ids"):
            if fname in message_su._fields:
                allowed |= set(message_su[fname].ids)
        if self.env.context.get("is_from_composer"):
            for key in ("partner_cc_ids", "partner_bcc_ids"):
                allowed |= set(getattr(self.env.context.get(key), "ids", None) or [])
        return [r for r in recipients if r.get("id") in allowed]

    @api.depends("processor_notice_pdf", "processor_notice_sha256")
    def _compute_processor_notice_intact(self):
        for incident in self:
            # Le client web lit les formulaires en `bin_size` : le champ rendrait « 44.07 Kb »,
            # pas les octets. L'empreinte se calcule toujours sur les octets.
            pdf = incident.with_context(bin_size=False).processor_notice_pdf
            incident.processor_notice_intact = bool(
                pdf and incident.processor_notice_sha256
                and self._processor_notice_digest(pdf) == incident.processor_notice_sha256)

    @api.depends("processor_partner_id", "processor_notice_ref")
    def _compute_declared_by_processor(self):
        for incident in self:
            incident.declared_by_processor = bool(incident.processor_partner_id or incident.processor_notice_ref)

    @api.model
    def _processor_notice_digest(self, pdf_b64):
        """L'empreinte se calcule sur les octets reçus, jamais sur ce qu'on en annonce."""
        return hashlib.sha256(base64.b64decode(pdf_b64)).hexdigest() if pdf_b64 else False

    @api.model
    def _processor_notice_pdf_vals(self, data):
        pdf = data.get("pdf")
        if not pdf:
            # Sans PDF (resté chez le mandataire), on ne garde pas l'ancien : la fiche montrerait
            # le PDF d'une version avec l'empreinte d'une autre.
            return {"processor_notice_pdf": False, "processor_notice_pdf_name": False,
                    "processor_notice_sha256": data.get("sha256") or False}
        pdf_b64 = base64.b64encode(pdf).decode() if isinstance(pdf, bytes) else pdf
        digest = self._processor_notice_digest(pdf_b64)
        if data.get("sha256") and data["sha256"] != digest:
            raise UserError(_("L'empreinte annoncée ne correspond pas au PDF reçu."))
        return {"processor_notice_pdf": pdf_b64, "processor_notice_sha256": digest,
                "processor_notice_pdf_name": data.get("pdf_name") or "avis.pdf"}

    @api.model
    def _create_from_processor_notice(self, processor, data):
        """Une fiche « Déclaré » à partir de l'avis d'un mandataire.

        Les faits remplissent les éléments du registre ; l'évaluation du risque reste
        vide, parce qu'elle revient au responsable et à son RPRP (art. 3.7).
        """
        received = data.get("received_at") or fields.Datetime.now()
        vals = {
            "title": data.get("title") or _("Avis de %(who)s : %(ref)s", who=processor.name,
                                            ref=data.get("ref") or ""),
            # Le registre de la société elle-même : aucune organisation, donc aucun portail. Avec
            # la société comme organisation, la règle du portail montrait la fiche à tout usager
            # portail qui lui est rattaché.
            "partner_id": False,
            "processor_partner_id": processor.id,
            "processor_notice_ref": data.get("ref"),
            "processor_notice_version": data.get("version") or 1,
            "processor_notice_received_at": received,
            "processor_facts": data.get("facts"),
            "awareness_date": fields.Datetime.to_datetime(received).date(),
            "incident_type": data.get("nature") or False,
            "circumstances": data.get("circumstances"),
            "cause": data.get("cause"),
            "occurrence_date": data.get("occurred_from") or False,
            "occurrence_date_end": data.get("occurred_to") or False,
            "occurrence_is_approximate": bool(data.get("occurred_approximate")),
            "pi_description": data.get("pi_description"),
            "pi_description_unknown": bool(data.get("pi_unknown_reason")) and not data.get("pi_description"),
            "pi_unknown_reason": data.get("pi_unknown_reason"),
            "subject_count": data.get("subject_count") or 0,
            "subject_count_is_estimate": bool(data.get("subject_count_estimate")),
            "subject_count_quebec": data.get("subject_count_quebec") or 0,
            "processor_notice_locked": True,
        }
        vals.update(self._processor_notice_pdf_vals(data))
        return self.create(vals)

    def _apply_processor_update(self, data):
        """Une mise à jour de l'avis : la provenance suit, vos champs ne bougent pas.

        Une version qui n'est pas plus récente que la courante est ignorée : chaque version
        voyage seule, et rien ne garantit qu'elles arrivent dans l'ordre. Rend False alors.
        """
        self.ensure_one()
        version = data.get("version") or 0
        if version <= (self.processor_notice_version or 0):
            self.message_post(
                body=Markup(_("<p>Version <b>%(v)s</b> de l'avis reçue après la version %(cur)s : "
                              "elle est conservée avec l'avis reçu, la fiche reste sur la plus "
                              "récente.</p>")) % {"v": version, "cur": self.processor_notice_version},
                message_type="comment", subtype_xmlid="mail.mt_note",
                author_id=self.env.ref("base.partner_root").id)
            return False
        vals = {
            "processor_notice_version": version,
            "processor_notice_last_received_at": data.get("received_at") or fields.Datetime.now(),
        }
        vals.update(self._processor_notice_pdf_vals(data))
        self.write(vals)
        if data.get("facts"):
            self.processor_facts = data["facts"]
        self.message_post(
            body=Markup(_("<p>Le mandataire a envoyé la version <b>%(v)s</b> de son avis. Les faits "
                          "transmis sont à jour ; votre évaluation et les éléments du registre n'ont pas "
                          "été modifiés : relisez-les.</p>")) % {"v": data.get("version")},
            message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=self.env.ref("base.partner_root").id)
        return True

    # ------------------------------------------------------------------
    # Surcharges
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if not self.env.su:
                vals["processor_notice_locked"] = False
            if vals.get("processor_notice_pdf"):
                vals["processor_notice_sha256"] = self._processor_notice_digest(vals["processor_notice_pdf"])
            if not vals.get("name") or vals["name"] == "Nouveau":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "privacy.incident",
                        sequence_date=vals.get("awareness_date"),
                    )
                    or "Nouveau"
                )
        return super().create(vals_list)

    _PROCESSOR_FIELDS = (
        "processor_partner_id", "processor_notice_ref", "processor_notice_version",
        "processor_notice_received_at", "processor_notice_last_received_at",
        "processor_notice_sha256", "processor_notice_pdf", "processor_notice_pdf_name",
        "processor_facts",
    )

    def write(self, vals):
        if not self.env.su:
            if "processor_notice_locked" in vals:
                raise UserError(_("Le verrou de provenance se pose à la réception d'un avis, pas à la main."))
            # Une fiche née de la fédération : sa provenance ne bouge qu'à la réception d'une mise
            # à jour, qui tourne en superutilisateur.
            if any(f in vals for f in self._PROCESSOR_FIELDS) and self.filtered("processor_notice_locked"):
                raise UserError(_("La provenance d'un avis reçu par la fédération ne se modifie pas."))
            # Une fiche saisie à la main reste corrigeable, mais un PDF ne remplace pas en silence
            # celui dont l'empreinte est consignée : une autre version se déclare comme telle.
            if vals.get("processor_notice_pdf"):
                digest = self._processor_notice_digest(vals["processor_notice_pdf"])
                announced = vals.get("processor_notice_sha256")
                if announced and announced != digest:
                    raise UserError(_("L'empreinte indiquée ne correspond pas au PDF fourni."))
                new_version = vals.get("processor_notice_version") or 0
                if any(i.processor_notice_sha256 and i.processor_notice_sha256 != digest
                       and new_version <= (i.processor_notice_version or 0) for i in self):
                    raise UserError(_("Ce PDF n'est pas celui dont l'empreinte est consignée. S'il s'agit "
                                      "d'une mise à jour de l'avis, indiquez aussi sa version."))
            if "processor_notice_pdf" in vals and not vals["processor_notice_pdf"] \
                    and self.filtered("processor_notice_pdf"):
                raise UserError(_("Un PDF consigné ne s'efface pas : une autre version se consigne par-dessus."))
            if "processor_notice_version" in vals and any(
                    (vals["processor_notice_version"] or 0) < (i.processor_notice_version or 0) for i in self):
                raise UserError(_("La version d'un avis consigné ne redescend pas."))
            if "processor_notice_sha256" in vals and not vals["processor_notice_sha256"] \
                    and self.filtered("processor_notice_sha256"):
                raise UserError(_("Une empreinte consignée ne s'efface pas."))
            if not vals.get("processor_notice_pdf") and vals.get("processor_notice_sha256"):
                for incident in self.with_context(bin_size=False).filtered("processor_notice_pdf"):
                    if self._processor_notice_digest(incident.processor_notice_pdf) != vals["processor_notice_sha256"]:
                        raise UserError(_("L'empreinte indiquée ne correspond pas au PDF consigné."))
        if vals.get("processor_notice_pdf"):
            vals = dict(vals, processor_notice_sha256=self._processor_notice_digest(vals["processor_notice_pdf"]))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def action_start_assessment(self):
        self.write({"state": "assessment"})

    def action_start_actions(self):
        self.write({"state": "action"})

    def action_close(self):
        self.write({"state": "closed"})

    def action_reopen(self):
        self.write({"state": "assessment"})

    def action_view_measures(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Mesures",
            "res_model": "privacy.incident.measure",
            "view_mode": "list,form",
            "domain": [("incident_id", "=", self.id)],
            "context": {"default_incident_id": self.id},
        }
