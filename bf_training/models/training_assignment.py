import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)

ETATS = [
    ("pending", "Assignée"),
    ("in_progress", "En cours"),
    ("done", "Faite"),
    ("cancelled", "Annulée"),
]


class BfTrainingAssignment(models.Model):
    """Demander à quelqu'un de suivre une activité, et le relancer.

    L'assignation n'est pas l'obligation : l'obligation est la règle appliquée à
    une personne, l'assignation est le geste qu'on pose pour la remplir. On peut
    assigner sans exigence (une recommandation), et une exigence peut être
    remplie sans qu'on ait jamais assigné quoi que ce soit.

    Ce modèle vient de `bf_security_awareness`, où il ne servait qu'à la
    remédiation après un hameçonnage simulé. Il est repris ici parce que rien
    dans son travail n'était propre à la cybersécurité, et parce qu'il était
    encore à zéro enregistrement partout : le déplacer ne coûtait rien.
    """

    _name = "bf.training.assignment"
    _description = "Assignation de formation"
    _inherit = ["mail.thread"]
    _order = "due_date, id"

    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True, index=True, tracking=True,
        help="Le contact, parce qu'une formation peut être assignée à quelqu'un "
             "qui n'a pas de fiche d'employé.")
    employee_id = fields.Many2one(
        "hr.employee", string="Employé", index=True, tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activité", required=True, index=True, tracking=True)
    requirement_id = fields.Many2one(
        "bf.training.requirement", string="Exigence", ondelete="set null")
    obligation_id = fields.Many2one(
        "bf.training.obligation", string="Obligation", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)

    assigned_date = fields.Datetime(string="Assignée le", default=fields.Datetime.now)
    assigned_by_id = fields.Many2one(
        "res.users", string="Assignée par", default=lambda self: self.env.user)
    due_date = fields.Date(string="Échéance", index=True, tracking=True)
    state = fields.Selection(ETATS, string="État", default="pending", required=True,
                             index=True, tracking=True)
    completion = fields.Integer(
        string="Avancement (%)", default=0,
        help="Rempli par le module qui porte le lecteur, quand il y en a un.")
    record_id = fields.Many2one(
        "bf.training.record", string="Réalisation", ondelete="set null",
        help="La pièce qui a clos l'assignation.")
    reminder_count = fields.Integer(string="Relances", default=0, readonly=True)
    training_url = fields.Char(
        string="Lien vers la formation", compute="_compute_training_url",
        help="Où la relance envoie la personne. Par défaut, la fiche de "
             "l'assignation ; un pont peut l'envoyer directement au contenu.")
    reminder_button_color = fields.Char(
        string="Couleur du bouton de relance",
        compute="_compute_reminder_presentation",
        help="La couleur du bouton que la mise en page retenue emploie elle-même : "
             "l'accent de marque de la société sous la mise en page maison, la "
             "couleur des boutons de ses courriels sous celle d'Odoo.")
    reminder_signature_name = fields.Char(
        string="Signature de la relance",
        compute="_compute_reminder_presentation",
        help="Le nom qui signe la relance. Vide quand la mise en page de la "
             "société pose déjà sa propre signature, pour ne pas signer deux fois.")
    last_reminder_date = fields.Datetime(string="Dernière relance", readonly=True)
    note = fields.Html(string="Note", sanitize_attributes=False)

    # Mises en page de la relance, par ordre de préférence : la première présente
    # sur la base sert. `bluefox_branding` n'est pas une dépendance et ne le
    # deviendra pas ; quand il est installé, la relance se range avec les autres
    # courriels de la société (bandeau, accent, police et pied de marque) au lieu
    # de partir dans l'habillage d'Odoo. Sans lui, la mise en page légère d'Odoo.
    _MISES_EN_PAGE_RELANCE = (
        "bluefox_branding.bf_mail_layout",
        "mail.mail_notification_light",
    )

    _sql_constraints = [
        ("completion_bornee", "check (completion >= 0 and completion <= 100)",
         "Un avancement se dit entre 0 et 100."),
    ]


    def _compute_training_url(self):
        """Le lien de la relance : là où la personne peut agir.

        🔴 La relance disait « la formation X est attendue » sans aucun lien : la
        personne savait qu'elle devait quelque chose et n'avait aucun moyen d'y
        aller. `/mail/view` gère la connexion et les droits d'accès, et renvoie
        vers la fiche. `bf_training_slides` le surcharge pour aller droit au
        cours en ligne quand l'activité en a un.
        """
        for rec in self:
            base = rec.get_base_url()
            rec.training_url = (
                "%s/mail/view?model=%s&res_id=%s" % (base, rec._name, rec.id)
                if rec.id else False)

    def _mise_en_page_relance(self):
        """La première mise en page de `_MISES_EN_PAGE_RELANCE` qui existe."""
        for xmlid in self._MISES_EN_PAGE_RELANCE:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return False

    @api.depends("company_id")
    def _compute_reminder_presentation(self):
        """Le bouton et la signature suivent la mise en page retenue.

        🔴 Première version : la couleur native des boutons de courriel, quelle
        que soit la mise en page. Chez Blue Fox elle vaut #729daf, hors palette,
        pendant que tous les autres courriels maison portent l'accent de marque :
        la relance était le seul courriel gris-bleu de la maison. Chaque mise en
        page a sa source de couleur, et le bouton prend celle de la sienne.

        ⚠️ Aucune couleur n'est assombrie ici. L'accent brut sous du texte blanc
        est un arbitrage du propriétaire de la marque (voir `branding.scss` de
        `bluefox_branding`), pas un défaut à corriger dans un module voisin.
        """
        maison = self._mise_en_page_relance() == "bluefox_branding.bf_mail_layout"
        for rec in self:
            societe = rec.company_id or self.env.company
            if maison and "report_brand_primary" in societe._fields:
                couleur = societe.report_brand_primary
            else:
                couleur = societe.email_secondary_color
            rec.reminder_button_color = couleur or "#875A7B"
            # La mise en page maison ajoute la signature par défaut de la société
            # quand elle en a une : « Merci, Société Exemple » suivi du bloc
            # « Société Exemple » signait deux fois.
            signe_deja = (
                maison and "brand_email_signature_default" in societe._fields
                and not is_html_empty(societe.brand_email_signature_default))
            rec.reminder_signature_name = False if signe_deja else societe.name

    @api.depends("employee_id.name", "partner_id.name", "activity_id.name")
    def _compute_display_name(self):
        """« Personne · Activité », jamais « bf.training.…,12 ».

        🔴 Sans ce calcul, le fil d'Ariane, le chatter et chaque pont qui cite
        une assignation affichent l'identifiant technique : c'est ce que la QA de
        parcours a vu sur tous les écrans qui la mentionnent.

        ⚠️ Chaque moitié a son repli, et ce n'est pas du zèle. `bf_security_awareness`
        hérite de ce modèle et crée ses assignations avec le PARTENAIRE et le cours
        seulement, sans employé ni activité : une première version rendait
        « ? · ? », pire que l'identifiant qu'elle remplaçait.
        """
        for rec in self:
            personne = rec.employee_id.name or rec.partner_id.name
            quoi = rec.activity_id.name
            rec.display_name = " · ".join(x for x in (personne, quoi) if x) or _("Assignation")

    @api.model_create_multi
    def create(self, vals_list):
        for valeurs in vals_list:
            if not valeurs.get("due_date"):
                jours = int(self.env["ir.config_parameter"].sudo().get_param(
                    "bf_training.default_due_days", 30))
                valeurs["due_date"] = fields.Date.context_today(self) + relativedelta(days=jours)
            if valeurs.get("employee_id") and not valeurs.get("partner_id"):
                employe = self.env["hr.employee"].browse(valeurs["employee_id"]).sudo()
                partenaire = employe.work_contact_id or employe.user_id.partner_id
                if partenaire:
                    valeurs["partner_id"] = partenaire.id
        return super().create(vals_list)

    def action_mark_done(self):
        for rec in self:
            rec.state = "done"
            rec.completion = 100

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def _relancer(self):
        """Poste la relance au fil de l'assignation. Rend le nombre de relances."""
        gabarit = self.env.ref(
            "bf_training.mail_template_training_reminder", raise_if_not_found=False)
        envoyees = 0
        # 🔴 Sans `email_layout_xmlid`, `send_mail` part SANS mise en page : le
        # courriel reçu faisait 550 caractères de HTML nu, sans logo, sans pied,
        # sans nom de société.
        mise_en_page = self._mise_en_page_relance()
        for rec in self:
            if gabarit:
                gabarit.send_mail(
                    rec.id, force_send=False, email_layout_xmlid=mise_en_page)
            else:
                rec.message_post(body=_(
                    "Rappel : l'activité « %(activite)s » est attendue pour le "
                    "%(echeance)s.",
                    activite=rec.activity_id.name,
                    echeance=rec.due_date or "sans échéance"))
            rec.reminder_count += 1
            rec.last_reminder_date = fields.Datetime.now()
            envoyees += 1
        return envoyees

    def action_remind(self):
        return self._relancer()

    @api.model
    def _cron_reminders(self):
        """Relance ce qui approche de l'échéance ou l'a passée, une fois par jour."""
        jour = fields.Date.context_today(self)
        preavis = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_training.reminder_days", 7))
        limite = jour + relativedelta(days=preavis)
        candidates = self.search([
            ("state", "in", ("pending", "in_progress")),
            ("due_date", "<=", limite),
        ])
        # Une relance par jour au plus : le registre rappelle, il ne harcèle pas.
        a_relancer = candidates.filtered(
            lambda a: not a.last_reminder_date
            or a.last_reminder_date.date() < jour)
        envoyees = a_relancer._relancer()
        _logger.info("Registre de formation : %s relances.", envoyees)
        return envoyees
