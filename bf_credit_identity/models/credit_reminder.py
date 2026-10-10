"""Un rappel de crédit et d'identité : privé à la personne qui le possède.

Ce que le module garde, et comment :

* **La vie privée.** Une règle GLOBALE (security/credit_identity_rules.xml)
  réserve chaque rappel à son propriétaire, administrateurs compris ; seul
  ``sudo()`` passe, et un administrateur système peut lever la règle elle-même.
  La règle d'Odoo se lit sur l'état d'AVANT une écriture : ``write`` refuse donc
  ici de changer le propriétaire, sinon une personne pourrait déposer un rappel
  dans l'espace d'une autre.
* **Le nom.** Odoo lit le nom d'un rappel en superutilisateur à deux endroits :
  l'erreur d'accès en mode debug, et le nom d'une activité (``res_name``). Le nom
  affiché se calcule donc selon la personne qui lit (``_compute_display_name``).
* **Aucun abonné, aucun avis.** Un rappel n'a pas d'abonné (``mail.followers``
  refuse d'en créer, voir models/mail_followers.py) et son fil n'avise personne
  (``_notify_get_recipients``) : ni l'avis d'assignation d'une activité, ni une
  mention, ni une note ne partent par courriel. Une activité sur un rappel reste
  à son propriétaire (models/mail_activity.py).
* **Rien ne sort.** Le rappel devient une activité Odoo pour son propriétaire,
  levée par la tâche planifiée avec ``mail_activity_quick_update``. Aucun champ
  n'est suivi (``tracking``) : la tâche planifiée n'écrit aucun message dans le
  fil. Le modèle déclare une portée sensible (``_gen_scope``), lue sans
  dépendance par les modules qui la reconnaissent : ``daily_todo_digest`` à partir
  de 18.0.2.4.0 et Gen à partir de 18.0.1.36.0. La version publiée du résumé
  quotidien (18.0.2.3.0) ne la lit pas encore.
* **Le calcul.** Un rappel fait repart du jour où il est fait (comme une
  maintenance d'hébergement), pas de son échéance : fait en retard, il ne
  s'accumule pas. Un rappel ponctuel (« une fois ») s'archive quand il est fait.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

KINDS = [
    ("report_equifax", "Equifax credit report"),
    ("report_transunion", "TransUnion credit report"),
    ("statements", "Bank and credit card statements"),
    ("measures_review", "Review of the protective measures"),
    ("alert_renewal", "Security alert renewal"),
    ("freeze_back", "Put the security freeze back"),
    ("other", "Other"),
]

BUREAUS = [
    ("equifax", "Equifax"),
    ("transunion", "TransUnion"),
    ("both", "Both bureaus"),
]

UNITS = [
    ("day", "Days"),
    ("week", "Weeks"),
    ("month", "Months"),
    ("year", "Years"),
]

# L'activité est levée ce nombre de jours avant l'échéance.
LEAD_DAYS = 7

# Les pages officielles ouvertes par le bouton d'un rappel : (français, anglais).
# Lues le 2026-10-02.
PAGES = {
    "equifax_report": (
        "https://www.equifax.ca/fr/personnel/produits/dossier-de-cr%C3%A9dit-de-consommateur/",
        "https://www.equifax.ca/personal/products/equifax-consumer-credit-report/",
    ),
    "transunion_report": (
        "https://www.transunion.ca/fr/product/version-consommateur",
        "https://www.transunion.ca/product/consumer-disclosure",
    ),
    "equifax_account": ("https://my.equifax.ca/login", "https://my.equifax.ca/login"),
    "transunion_account": (
        "https://ocs.transunion.ca/secureocs/#/home",
        "https://ocs.transunion.ca/secureocs/#/home",
    ),
    "amf_credit_file": (
        "https://lautorite.qc.ca/grand-public/dossier-de-credit",
        "https://lautorite.qc.ca/en/general-public/personal-finances/credit-report",
    ),
}


def page_for(kind, bureau, lang):
    """L'adresse officielle d'un rappel, dans la langue de la personne."""
    if kind == "report_equifax":
        key = "equifax_report"
    elif kind == "report_transunion":
        key = "transunion_report"
    elif kind in ("alert_renewal", "freeze_back") and bureau in ("equifax", "transunion"):
        key = "%s_account" % bureau
    elif kind in ("measures_review", "alert_renewal", "freeze_back"):
        key = "amf_credit_file"
    else:
        return False
    fr, en = PAGES[key]
    return fr if (lang or "").startswith("fr") else en


class CreditReminder(models.Model):
    _name = "bf.credit.reminder"
    _description = "Credit and identity reminder"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "next_date, id"
    # Portée sensible : un simple attribut, lu sans dépendance par le résumé
    # quotidien (18.0.2.4.0 et suivantes) et par le verrou de Gen (18.0.1.36.0 et
    # suivantes).
    _gen_scope = "bf_credit_identity"
    _gen_scope_label = _lt("Credit & Identity: reminders")

    name = fields.Char(required=True)
    kind = fields.Selection(KINDS, required=True, default="other")
    bureau = fields.Selection(BUREAUS)
    user_id = fields.Many2one(
        "res.users", string="Person", required=True, readonly=True, index=True,
        ondelete="cascade", default=lambda self: self.env.user)
    next_date = fields.Date(
        "Next date", required=True, default=fields.Date.context_today)
    recurring = fields.Boolean(
        default=True, help="Unchecked: the reminder is archived once done.")
    interval_number = fields.Integer("Every", default=12)
    interval_unit = fields.Selection(UNITS, string="Unit", default="month", required=True)
    last_done_date = fields.Date("Last done", readonly=True, copy=False)
    link_url = fields.Char("Official page")
    note = fields.Text()
    active = fields.Boolean(default=True)
    # L'échéance pour laquelle une activité a déjà été levée : la tâche planifiée
    # n'en lève pas deux pour la même, et déplace la sienne si l'échéance bouge.
    raised_for_date = fields.Date(readonly=True, copy=False)
    due_state = fields.Selection(
        [("overdue", "Overdue"), ("soon", "Due soon"), ("planned", "Planned")],
        string="Status", compute="_compute_due_state")

    @api.depends("next_date")
    @api.depends_context("tz")
    def _compute_due_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.next_date:
                rec.due_state = "planned"
            elif rec.next_date < today:
                rec.due_state = "overdue"
            elif rec.next_date <= today + timedelta(days=LEAD_DAYS):
                rec.due_state = "soon"
            else:
                rec.due_state = "planned"

    @api.depends("name", "user_id")
    @api.depends_context("uid")
    def _compute_display_name(self):
        """« Private reminder » pour qui n'en est pas propriétaire.

        On teste l'uid, jamais ``env.su`` : l'erreur d'accès en mode debug et le
        nom d'une activité lisent le nom en superutilisateur, mais sous l'uid de la
        personne qui lit. Le superutilisateur (tâche planifiée) lit le vrai nom.
        """
        uid = self.env.uid
        for rec, lu in zip(self, self.sudo()):
            proprio = uid in (lu.user_id.id, SUPERUSER_ID)
            rec.display_name = lu.name if proprio else self.env._("Private reminder")

    @api.constrains("recurring", "interval_number")
    def _check_interval(self):
        for rec in self:
            if rec.recurring and rec.interval_number <= 0:
                raise ValidationError(self.env._(
                    "A recurring reminder needs an interval of at least 1."))

    @api.onchange("kind", "bureau")
    def _onchange_kind(self):
        labels = dict(self._fields["kind"]._description_selection(self.env))
        if self.kind and (not self.name or self.name in labels.values()):
            self.name = labels[self.kind]
        self.link_url = page_for(self.kind, self.bureau, self.env.lang) or self.link_url
        if self.kind in ("alert_renewal", "freeze_back"):
            self.recurring = False

    # ------------------------------------------------------------------ ORM
    # Création : le propriétaire est la personne qui crée (valeur par défaut). Un
    # rappel créé pour quelqu'un d'autre est refusé par la règle globale, qui se
    # lit sur l'enregistrement créé.

    @api.model_create_multi
    def create(self, vals_list):
        # Aucun abonné : à la création, Odoo abonne la créatrice sans passer par
        # message_subscribe (mail_thread.create, _insert_followers).
        return super(CreditReminder, self.with_context(mail_create_nosubscribe=True)).create(vals_list)

    def write(self, vals):
        if "user_id" in vals and not self.env.su:
            if any(rec.user_id.id != vals["user_id"] for rec in self):
                raise UserError(self.env._(
                    "A reminder stays with the person who created it."))
        res = super().write(vals)
        if "next_date" in vals:
            # Une échéance déplacée à la main déplace l'activité déjà levée : sans
            # ça, une échéance repoussée hors de la fenêtre garderait l'ancienne date.
            act_type = self._activity_type()
            for rec in self.sudo():
                ouvertes = rec.activity_ids.filtered(lambda a: a.activity_type_id == act_type)
                if ouvertes and rec.next_date:
                    ouvertes.with_context(mail_activity_quick_update=True).write(
                        {"date_deadline": rec.next_date})
                    rec.raised_for_date = rec.next_date
        return res

    # ------------------------------------------------------------------ fil
    # Un rappel n'a AUCUN abonné. Un abonné reçoit par courriel ce qui s'écrit
    # dans le fil, et la liste des abonnés dit à qui appartient chaque rappel.

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        """La porte publique : le droit de lecture d'abord, pour que la réponse ne
        dise pas à qui est un rappel ; puis rien. Les activités l'appellent aussi."""
        if not self.env.su:
            self.check_access("read")
        return True

    def _message_subscribe(self, partner_ids=None, subtype_ids=None, customer_ids=None):
        """La porte interne (auteur d'un message, passerelle, mention) : rien."""
        return True

    def _message_auto_subscribe_followers(self, updated_values, default_subtype_ids):
        return []

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        """Le fil d'un rappel n'avise personne : ni l'avis d'assignation d'une
        activité (``action_notify`` passe par ``message_notify`` du rappel), ni une
        note, ni une mention."""
        return []

    def _check_no_one_else_named(self, partner_ids):
        """Personne d'autre que le propriétaire n'est nommé dans le fil : un
        partenaire nommé lit le message (``mail.message``), avis ou non."""
        if not partner_ids or self.env.su:
            return
        for rec in self.sudo():
            if set(partner_ids) - {rec.user_id.partner_id.id}:
                raise UserError(self.env._(
                    "This reminder is private: you cannot mention or notify anyone on it."))

    def message_post(self, **kwargs):
        self._check_no_one_else_named(kwargs.get("partner_ids"))
        return super().message_post(**kwargs)

    def message_notify(self, **kwargs):
        if not self.env.su:
            self.check_access("write")
        self._check_no_one_else_named(kwargs.get("partner_ids"))
        return super().message_notify(**kwargs)

    # -------------------------------------------------------------- actions
    def _interval(self):
        self.ensure_one()
        return relativedelta(**{"%ss" % self.interval_unit: self.interval_number})

    def _activity_type(self):
        return self.env.ref(
            "bf_credit_identity.mail_activity_type_credit", raise_if_not_found=False)

    def action_mark_done(self):
        """Fait aujourd'hui : ferme l'activité, puis calcule la suivante ou archive."""
        act_type = self._activity_type()
        for rec in self:
            ouvertes = rec.activity_ids.filtered(lambda a: a.activity_type_id == act_type)
            if ouvertes:
                # Le drapeau : l'activité faite ne fait pas avancer le rappel une
                # seconde fois (voir mail_activity._action_done).
                ouvertes.with_context(bf_credit_fait=True).action_feedback()
        self._fait_aujourd_hui()
        return True

    def _fait_aujourd_hui(self):
        """Repart du jour où c'est fait ; un rappel ponctuel s'archive. Appelé par le
        bouton du rappel ET quand on fait son activité (systray, fil, liste)."""
        today = fields.Date.context_today(self)
        for rec in self:
            vals = {"last_done_date": today}
            if rec.recurring:
                vals["next_date"] = today + rec._interval()
            else:
                vals["active"] = False
            rec.write(vals)

    def action_open_link(self):
        self.ensure_one()
        if not self.link_url:
            raise UserError(self.env._("This reminder has no official page."))
        url = self.link_url
        # Une page officielle connue s'ouvre dans la langue de la personne qui
        # clique, pas dans celle du jour où le rappel a été créé. Une adresse
        # saisie à la main reste telle quelle.
        for fr, en in PAGES.values():
            if url in (fr, en):
                url = fr if (self.env.lang or "").startswith("fr") else en
                break
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    # ---------------------------------------------------------------- cron
    @api.model
    def _cron_raise_activities(self):
        """Lève l'activité des rappels dont l'échéance tombe dans LEAD_DAYS jours."""
        today = fields.Date.context_today(self)
        horizon = today + timedelta(days=LEAD_DAYS)
        dus = self.sudo().search([("next_date", "<=", horizon)])
        # Relever aussi un rappel dû qui n'a plus d'activité ouverte (archivé puis
        # désarchivé, activité supprimée) : sans ça, il resterait en retard à jamais.
        act_type = self._activity_type()
        avec_activite = set()
        if act_type and dus:
            avec_activite = set(self.env["mail.activity"].sudo().search([
                ("res_model", "=", self._name), ("res_id", "in", dus.ids),
                ("activity_type_id", "=", act_type.id)]).mapped("res_id"))
        dus.filtered(
            lambda r: r.raised_for_date != r.next_date or r.id not in avec_activite
        )._raise_activity()

    def _raise_activity(self):
        """Une activité par rappel, à son propriétaire, à l'échéance, SANS avis.

        🔴 ``mail_activity_quick_update`` : sans lui, Odoo envoie l'avis
        d'assignation (« ... vous a été assigné ») par courriel à toute personne
        dont les notifications passent par courriel, dès que l'activité est créée
        par quelqu'un d'autre (la tâche planifiée). Les essais le mesurent.

        Aucun résumé n'est stocké : la liste des activités montre le nom du type,
        traduit pour la personne, et le nom du rappel (``res_name``).
        """
        act_type = self._activity_type()
        if not act_type:
            return
        Activity = self.env["mail.activity"].sudo().with_context(
            mail_activity_quick_update=True)
        model_id = self.env["ir.model"]._get_id(self._name)
        for rec in self.sudo():
            existantes = rec.activity_ids.filtered(lambda a: a.activity_type_id == act_type)
            if existantes:
                existantes[1:].unlink()
                existantes[:1].with_context(mail_activity_quick_update=True).write({
                    "date_deadline": rec.next_date,
                    "user_id": rec.user_id.id,
                })
            else:
                Activity.create({
                    "res_model_id": model_id,
                    "res_id": rec.id,
                    "activity_type_id": act_type.id,
                    "user_id": rec.user_id.id,
                    "date_deadline": rec.next_date,
                })
            rec.raised_for_date = rec.next_date
