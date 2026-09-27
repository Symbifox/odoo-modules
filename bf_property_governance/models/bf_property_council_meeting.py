"""La réunion du conseil d'administration : art. 1084.1 et 1086.1 C.c.Q.

Le module décrivait les décisions du conseil sans décrire l'organe qui les
prend. C'est pourtant le conseil qui fixe la contribution aux charges communes
après consultation de l'assemblée (art. 1072), qui doit consulter avant toute
contribution spéciale (art. 1072.1), qui fait établir le carnet d'entretien et
le tient à jour (art. 1070.2), qui obtient l'étude du fonds de prévoyance et
fixe les sommes à y verser (Loi 16, art. 153 al. 1), qui détermine l'utilisation
du fonds (art. 1071) et qui désigne la personne devant qui le registre se
consulte (art. 1070.1). Aucun de ces gestes n'avait d'endroit où se consigner.

🔴 **L'art. 1070 al. 1 met les procès-verbaux du conseil AU REGISTRE**, au même
titre que ceux de l'assemblée. Sans cet objet, le registre de tout syndicat
servi par le module était incomplet de moitié.

⚠️ **Art. 1084.1 : pas d'accord préalable à demander.** L'article fait pour les
réunions du conseil ce que l'art. 1088.1 fait pour l'assemblée, et il l'écarte
expressément du régime général des personnes morales : l'art. 344 exige que les
administrateurs soient « tous d'accord » pour siéger par un moyen technologique,
la règle propre à la copropriété ne l'exige pas. Le module ne demande donc aucun
accord, et une case « les administrateurs ont consenti » serait une condition
inventée.

⚠️ **La condition, c'est la communication immédiate ENTRE TOUS.** Une diffusion
à sens unique, où l'on écoute sans pouvoir intervenir, ne remplit pas l'article.
Le module ne peut pas le constater lui-même : la case est une attestation de qui
préside, et elle reste au dossier.

⚠️ **Art. 1086.1 : trente jours, le même délai que l'art. 1102.1** donne à
l'assemblée. Ici il court de la réunion du conseil, et il ne court pas d'une
réunion annulée.

⚠️ **Ce que cet objet ne porte PAS, et pourquoi.**

- **La composition du conseil et l'élection de ses membres.** Rien de sourcé au
  cahier ne fixe le nombre d'administrateurs, la durée des mandats ni le mode
  d'élection : c'est la déclaration de copropriété qui le fait, et elle varie.
  Coder un modèle d'administrateurs supposerait de choisir à sa place.
- **Les participants, un à un.** Ils se nomment au procès-verbal. Les porter en
  champ structuré serait le premier pas vers la liste d'administrateurs que le
  point précédent écarte.
- **La résolution écrite hors réunion.** L'art. 1102.1 la nomme pour ce que le
  conseil transmet à la suite d'une ASSEMBLÉE. Que le conseil puisse lui-même
  décider par résolution écrite sans se réunir demande une source que le cahier
  n'a pas encore : tant qu'elle manque, le module n'en fabrique pas le régime.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Art. 1086.1 C.c.Q. : le conseil transmet le procès-verbal de ses réunions
# dans les 30 jours. Même chiffre que l'art. 1102.1 pour l'assemblée, mais deux
# articles distincts : la constante est nommée ici pour que le jour où l'un des
# deux change, l'autre ne suive pas par accident.
COUNCIL_MINUTES_DAYS = 30


class BfPropertyCouncilMeeting(models.Model):
    _name = "bf.property.council.meeting"
    _description = "Réunion du conseil d'administration"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.property.organisation.authority", "bf.property.syndicat.regime"]
    _order = "date desc, id desc"

    name = fields.Char(string="Objet", required=True, tracking=True)
    organisation_id = fields.Many2one(
        "bf.property.organisation",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="organisation_id.company_id", store=True, string="Société"
    )
    date = fields.Datetime(string="Tenue le", required=True, tracking=True)
    location = fields.Char(string="Lieu")
    agenda = fields.Html(string="Ordre du jour")

    # ── Mode de tenue (art. 1084.1) ──

    participation_mode = fields.Selection(
        [
            ("in_person", "En personne"),
            ("remote", "À distance"),
            ("hybrid", "Hybride"),
        ],
        string="Mode de tenue",
        default="in_person",
        required=True,
        tracking=True,
        help="Art. 1084.1 C.c.Q. : le conseil peut siéger à l'aide de moyens "
             "permettant à tous les participants de communiquer immédiatement "
             "entre eux. Aucun accord préalable des administrateurs n'est "
             "requis, à la différence du régime général de l'art. 344.",
    )
    remote_means = fields.Char(
        string="Moyens technologiques",
        tracking=True,
        help="Ce par quoi les administrateurs se joignent et se parlent : "
             "plateforme, lien, numéro. D'une réunion sans salle, c'est cela "
             "le lieu.",
    )
    remote_immediate_communication = fields.Boolean(
        string="Communication immédiate entre tous",
        tracking=True,
        help="Art. 1084.1 C.c.Q. : les moyens doivent permettre à TOUS les "
             "participants de communiquer immédiatement ENTRE EUX. Une "
             "diffusion à sens unique ne remplit pas la condition. Le module ne "
             "peut pas le constater : la case est une attestation de qui "
             "préside, et elle reste au dossier.",
    )
    # ⚠️ Rendu à la lecture, jamais stocké : une phrase stockée se
    # range dans la langue de qui a déclenché le calcul.
    participation_warning = fields.Char(
        string="Réserve sur le mode de tenue",
        compute="_compute_participation_warning",
        compute_sudo=True,
    )

    # ── Procès-verbal (art. 1086.1) ──

    minutes = fields.Html(string="Procès-verbal")
    minutes_deadline = fields.Date(
        string="Transmission du PV au plus tard le",
        compute="_compute_minutes",
        store=True,
        help="Art. 1086.1 C.c.Q. : le conseil transmet le procès-verbal de ses "
             "réunions dans les 30 jours.",
    )
    minutes_sent_date = fields.Date(string="PV transmis le", tracking=True)
    minutes_state = fields.Selection(
        [
            ("pending", "À transmettre"),
            ("overdue", "En retard"),
            ("sent", "Transmis dans le délai"),
            ("sent_late", "Transmis hors délai"),
            ("na", "Sans objet"),
        ],
        string="Transmission du PV",
        compute="_compute_minutes",
        store=True,
    )

    cancelled = fields.Boolean(string="Annulée", tracking=True)
    state = fields.Selection(
        [
            ("planned", "Prévue"),
            ("held", "Tenue"),
            ("cancelled", "Annulée"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
        tracking=True,
    )

    # ── Calculs ──

    @api.depends("date", "cancelled")
    def _compute_state(self):
        """⚠️ « Tenue » se déduit du passage de la date, d'où le cron.

        Un champ stocké qui dépend d'aujourd'hui se fige au dernier calcul ; le
        laisser non stocké le rendrait incherchable. Le cron le rafraîchit, et
        c'est le même arbitrage que sur l'assemblée.
        """
        now = fields.Datetime.now()
        for meeting in self:
            if meeting.cancelled:
                meeting.state = "cancelled"
            elif meeting.date and meeting.date <= now:
                meeting.state = "held"
            else:
                meeting.state = "planned"

    @api.depends("date", "minutes_sent_date", "cancelled")
    def _compute_minutes(self):
        """⚠️ Le délai ne court pas d'une réunion annulée.

        Une réunion qui n'a pas eu lieu n'a pas de procès-verbal à transmettre,
        et l'afficher « en retard » ferait porter au tableau de bord une dette
        qui n'existe pas.
        """
        today = fields.Date.context_today(self)
        for meeting in self:
            if meeting.cancelled or not meeting.date:
                meeting.minutes_deadline = False
                meeting.minutes_state = "na" if meeting.cancelled else "pending"
                continue
            deadline = meeting.date.date() + timedelta(days=COUNCIL_MINUTES_DAYS)
            meeting.minutes_deadline = deadline
            if meeting.minutes_sent_date:
                meeting.minutes_state = (
                    "sent" if meeting.minutes_sent_date <= deadline else "sent_late"
                )
            else:
                meeting.minutes_state = "overdue" if today > deadline else "pending"

    @api.depends(
        "participation_mode", "remote_means", "remote_immediate_communication"
    )
    @api.depends_context("lang")
    def _compute_participation_warning(self):
        for meeting in self:
            warning = ""
            if meeting.participation_mode in ("remote", "hybrid"):
                if not meeting.remote_means:
                    warning = _(
                        "Les moyens technologiques ne sont pas indiqués : d'une "
                        "réunion sans salle, c'est cela le lieu."
                    )
                elif not meeting.remote_immediate_communication:
                    warning = _(
                        "La communication immédiate entre TOUS les participants "
                        "n'est pas attestée (art. 1084.1 C.c.Q.). Une diffusion "
                        "à sens unique ne remplit pas la condition."
                    )
            meeting.participation_warning = warning

    # ── Contraintes ──

    @api.constrains("minutes_sent_date", "date")
    def _check_minutes_not_before_the_meeting(self):
        for meeting in self:
            if (
                meeting.minutes_sent_date
                and meeting.date
                and meeting.minutes_sent_date < meeting.date.date()
            ):
                raise ValidationError(
                    _(
                        "« %s » : un procès-verbal ne se transmet pas avant la "
                        "réunion qu'il relate."
                    )
                    % meeting.name
                )

    # ── Décisions ──

    def action_send_minutes(self):
        """Consigne la transmission du PV, art. 1086.1."""
        self._ensure_organisation_decides(_("Transmettre un procès-verbal du conseil"))
        today = fields.Date.context_today(self)
        for meeting in self:
            if meeting.cancelled:
                raise UserError(
                    _("« %s » a été annulée : il n'y a pas de procès-verbal.")
                    % meeting.name
                )
            if not meeting.minutes:
                raise UserError(
                    _(
                        "Rédigez le procès-verbal de « %s » avant d'en "
                        "consigner la transmission : consigner l'envoi d'une "
                        "page blanche ne prouve rien."
                    )
                    % meeting.name
                )
            if meeting.minutes_sent_date:
                raise UserError(
                    _("Le procès-verbal de « %(name)s » a déjà été transmis le "
                      "%(date)s.")
                    % {"name": meeting.name, "date": meeting.minutes_sent_date}
                )
            meeting.minutes_sent_date = today
            meeting.message_post(
                body=_(
                    "Procès-verbal transmis le %(date)s. Art. 1086.1 C.c.Q. : "
                    "le conseil transmet le procès-verbal de ses réunions dans "
                    "les 30 jours. %(verdict)s"
                )
                % {
                    "date": today,
                    "verdict": _("Dans le délai.")
                    if meeting.minutes_state == "sent"
                    else _("⚠️ Hors délai."),
                }
            )
        return True

    def action_cancel(self):
        self._ensure_organisation_decides(_("Annuler une réunion du conseil"))
        for meeting in self:
            if meeting.minutes_sent_date:
                raise UserError(
                    _(
                        "Le procès-verbal de « %s » a été transmis : la réunion "
                        "a eu lieu et ne s'annule plus."
                    )
                    % meeting.name
                )
            meeting.cancelled = True
            meeting.message_post(body=_("Réunion annulée."))
        return True

    def action_reopen(self):
        self._ensure_organisation_decides(_("Rouvrir une réunion du conseil"))
        self.write({"cancelled": False})
        return True

    @api.model
    def _cron_refresh_state(self):
        """Le passage de la date, et celui de l'échéance, ne sont pas des écritures.

        ⚠️ Deux champs stockés dépendent d'aujourd'hui : l'état de la réunion et
        celui de la transmission. Sans ce cron, une réunion tenue hier resterait
        « prévue » et un PV en retard resterait « à transmettre » jusqu'à la
        prochaine écriture sur la fiche.
        """
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                "|",
                "&", ("state", "=", "planned"), ("date", "<=", now),
                "&", ("minutes_state", "=", "pending"),
                ("minutes_deadline", "<", today),
            ]
        )
        stale.modified(["date", "minutes_sent_date", "cancelled"])
        return len(stale)
