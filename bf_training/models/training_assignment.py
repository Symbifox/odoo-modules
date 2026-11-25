import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)

ETATS = [
    ("pending", "Assigned"),
    ("in_progress", "In progress"),
    ("done", "Done"),
    ("cancelled", "Cancelled"),
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
    _description = "Training assignment"
    # 🔴 Le mixin d'activités s'était perdu au déménagement : l'assignation de
    # `bf_security_awareness` l'avait, et ses relances appellent
    # `activity_schedule`. Il n'ajoute aucune colonne. Son `activity_ids`
    # (activités de suivi) ne se confond pas avec `activity_id` (le catalogue).
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date, id"

    partner_id = fields.Many2one(
        "res.partner", string="Person", required=True, index=True, tracking=True,
        help="The contact, because training can be assigned to somebody "
             "with no employee record.")
    employee_id = fields.Many2one(
        "hr.employee", string="Employee", index=True, tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activity", required=True, index=True, tracking=True)
    requirement_id = fields.Many2one(
        "bf.training.requirement", string="Requirement", ondelete="set null")
    obligation_id = fields.Many2one(
        "bf.training.obligation", string="Obligation", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True)

    assigned_date = fields.Datetime(string="Assigned on", default=fields.Datetime.now)
    assigned_by_id = fields.Many2one(
        "res.users", string="Assigned by", default=lambda self: self.env.user)
    due_date = fields.Date(string="Due date", index=True, tracking=True)
    state = fields.Selection(ETATS, string="State", default="pending", required=True,
                             index=True, tracking=True)
    completion = fields.Integer(
        string="Progress (%)", default=0,
        help="Filled in by the module that carries the content player, "
             "when there is one.")
    record_id = fields.Many2one(
        "bf.training.record", string="Completion", ondelete="set null",
        help="The document that closed the assignment.")
    reminder_count = fields.Integer(string="Reminders", default=0, readonly=True)
    training_url = fields.Char(
        string="Link to the training", compute="_compute_training_url",
        help="Where the reminder sends the person. By default, the "
             "assignment record; a bridge can send them straight to the "
             "content.")
    reminder_button_color = fields.Char(
        string="Reminder button colour",
        compute="_compute_reminder_presentation",
        help="The button colour the chosen layout uses itself: the "
             "company's brand accent under the in-house layout, the "
             "colour of its email buttons under Odoo's.")
    reminder_signature_name = fields.Char(
        string="Reminder signature",
        compute="_compute_reminder_presentation",
        help="The name that signs the reminder. Leave it empty when the "
             "company layout already adds its own signature, so nothing "
             "is signed twice.")
    last_reminder_date = fields.Datetime(string="Last reminder", readonly=True)
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
         "Progress is stated between 0 and 100."),
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
            rec.display_name = " · ".join(x for x in (personne, quoi) if x) or _("Assignment")

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

    def _refus_relance(self, gabarit):
        """La phrase qui refuse le clic « Relancer », ou False.

        🔴 Sans gabarit, le rappel se pose au fil : ça compte, quelque
        chose est bien arrivé à la personne, et il n'y a rien à refuser. Avec
        gabarit et sans adresse courriel, rien ne peut partir.
        """
        self.ensure_one()
        if gabarit and not self.partner_id.email:
            # Vouvoiement : c'est le registre de ces modules, mesuré au
            # catalogue (10 chaînes au « vous », aucune au « tu »).
            return _(
                "%(nom)s has no email address: the reminder cannot go "
                "out. Add their address, then send the reminder again.",
                nom=self.partner_id.name)
        return False

    def _relancer(self, refuser=False):
        """Poste la relance au fil de l'assignation. Rend le nombre de relances.

        🔴 Une relance qui ne part pas ne se compte pas. Le compteur et
        la date s'écrivaient avant le moindre envoi, et `send_mail` partait sans
        `try` ni contrôle de l'adresse. Une personne sans courriel voyait donc
        « Relances : 3 » sans avoir jamais rien reçu — et le gabarit portant
        `auto_delete`, même le `mail.mail` en échec était effacé : il ne restait
        aucune trace. Une seule exception emportait aussi la journée entière de
        la tâche planifiée.

        `refuser` est pour les boutons : au clic, une relance impossible se
        refuse tout de suite plutôt que de se ranger quelque part. La tâche
        planifiée, elle, écarte en amont ce qui ne peut pas partir et le compte
        à son journal, pour ne pas reposer la même note chaque jour.
        """
        gabarit = self.env.ref(
            "bf_training.mail_template_training_reminder", raise_if_not_found=False)
        if refuser:
            # 🔴 Avant le premier effet : levé en chemin, un refus annulerait
            # aussi les relances déjà parties du même lot, sans le dire.
            for rec in self:
                refus = rec._refus_relance(gabarit)
                if refus:
                    raise UserError(refus)
        envoyees = 0
        # 🔴 Sans `email_layout_xmlid`, `send_mail` part SANS mise en page : le
        # courriel reçu faisait 550 caractères de HTML nu, sans logo, sans pied,
        # sans nom de société.
        mise_en_page = self._mise_en_page_relance()
        for rec in self:
            # 🔴 UNE seule garde, et elle sert les deux chemins. Le
            # bouton l'emploie pour refuser (ci-dessus), la tâche planifiée pour
            # écarter sans rien compter. Un pont qui ajoute une raison —
            # `bf_training_slides` et le cours non publié — couvre donc les deux
            # d'un coup, au lieu d'avoir à répéter sa condition ici.
            refus = rec._refus_relance(gabarit)
            if refus:
                _logger.warning(
                    "Registre de formation : assignation %s non relancée, %s",
                    rec.id, refus)
                continue
            if not gabarit:
                rec.message_post(body=_(
                    "Reminder: activity \"%(activite)s\" is due on "
                    "%(echeance)s.",
                    activite=rec.activity_id.name,
                    # Le repli aussi passe par le catalogue : il atterrissait
                    # en français au milieu d'un message anglais.
                    echeance=rec.due_date or _("no due date")))
            else:
                try:
                    # 🔴 Point de sauvegarde. Une
                    # erreur de la BASE abandonne la transaction, et le
                    # `message_post` du gestionnaire lèverait alors à son tour,
                    # depuis l'intérieur du `except` : la tâche planifiée serait
                    # de nouveau emportée en entier. Le cœur d'Odoo pose la même
                    # garde dans `mail_mail.py`.
                    with rec.env.cr.savepoint():
                        gabarit.send_mail(
                            rec.id, force_send=False,
                            email_layout_xmlid=mise_en_page)
                except Exception as exc:  # noqa: BLE001
                    # Une exception ne mange plus le reste du lot, et le fil dit
                    # ce qui n'est pas parti.
                    _logger.warning(
                        "Registre de formation : relance en échec pour "
                        "l'assignation %s : %s", rec.id, exc)
                    rec.message_post(body=_(
                        "Reminder not sent: %(raison)s", raison=exc))
                    continue
            rec.reminder_count += 1
            rec.last_reminder_date = fields.Datetime.now()
            envoyees += 1
        return envoyees

    def action_remind(self):
        return self._relancer(refuser=True)

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
        # 🔴 La différence est ce qui n'a PAS pu partir. `_relancer`
        # écarte lui-même ce qui n'a pas d'adresse (et nomme chaque cas à son
        # journal) : la garde ne vit qu'à UN endroit, et ce total est la seule
        # chose que le cron ait à dire en plus. Pas de note au fil ici — la
        # tâche tourne chaque matin et, le compteur ne montant plus, la même
        # assignation resterait éligible indéfiniment : une note par jour sur la
        # même fiche. Le bouton « Relancer », lui, refuse et nomme la personne.
        _logger.info(
            "Registre de formation : %s relances, %s sautée(s) (rien n'a pu partir).",
            envoyees, len(a_relancer) - envoyees)
        return envoyees
