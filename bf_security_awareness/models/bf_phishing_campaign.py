import logging
import random
import threading
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import email_normalize, html2plaintext

from .reader_lang import company_env, operator_env, reader_env

_logger = logging.getLogger(__name__)
_PASSAGERE_PENDANT = timedelta(hours=6)


def _cause(exc):
    """The exception's text, without its final full stop: it is quoted inside
    a sentence that adds its own ("…the campaign.. The…"). An
    exception without text (``AssertionError()``) is named by its class."""
    return str(exc).strip().rstrip(".") or type(exc).__name__


def _sans_espaces(texte):
    return " ".join(texte.split())


def _passagere(exc, date_planifiee=None):
    """Une erreur de concurrence que la passe suivante ne rencontrera sans
    doute plus.

    Vider la date planifiée pour elle arrêterait
    pour de bon un lancement qui n'a fait que croiser une autre transaction.
    Elle est journalisée et réessayée, comme avant ce correctif — mais :

    - elle peut arriver ENVELOPPÉE.
      `_release_due_results` rattrape l'erreur de l'envoi de masse et pose une
      note sur une transaction avortée, qui lève `InFailedSqlTransaction` avec
      la vraie cause dans son `__context__`. Toute la chaîne est donc lue ;
    - une erreur qui revient toujours n'est plus
      passagère. Passé ``_PASSAGERE_PENDANT`` après la date planifiée, elle est
      signalée comme les autres : elle ne peut pas rester muette indéfiniment.
    """
    if date_planifiee and fields.Datetime.now() - date_planifiee > _PASSAGERE_PENDANT:
        return False
    vues = set()
    while exc is not None and id(exc) not in vues:
        if isinstance(exc, PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            return True
        vues.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


# Appended to every lure body so we capture opens in real time on our own
# endpoint, independently of the native mass-mailing tracking pixel. The marker
# attribute lets us detect "already injected" precisely, rather than scanning
# the body for a substring that could legitimately appear in template content.
OPEN_PIXEL_MARKER = "data-bf-sa-pixel"
OPEN_PIXEL_SNIPPET = (
    '<img t-att-src="object.open_pixel_url" %s="1" width="1" height="1" '
    'style="display:none;border:0;" alt=""/>' % OPEN_PIXEL_MARKER
)

# Optional QR block (quishing): encodes the per-recipient landing URL via Odoo's
# public barcode controller, so scanning the code on a phone hits /phish/<token>.
QR_MARKER = "data-bf-sa-qr"
QR_SNIPPET = (
    '<div %s="1" style="text-align:center;margin:18px 0;">'
    '<img t-att-src="object.qr_src" alt="QR" width="180" height="180"/>'
    '</div>' % QR_MARKER
)


class BfPhishingCampaign(models.Model):
    _name = "bf.phishing.campaign"
    _description = "Phishing simulation campaign"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(string="Name", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("sending", "Sending"),
            ("running", "In progress"),
            ("done", "Done"),
            ("cancelled", "Canceled"),
        ],
        string="Status", default="draft", required=True, tracking=True,
    )
    template_id = fields.Many2one(
        "bf.phishing.template", string="Lure template",
        required=True, tracking=True,
    )

    # --- Audience selection --------------------------------------------
    recipient_partner_ids = fields.Many2many(
        "res.partner", string="Targeted people",
        domain="[('email', '!=', False)]",
    )
    mailing_list_ids = fields.Many2many(
        "mailing.list", string="Mailing lists",
        help="Contacts from these lists are added to the targeted people.",
    )
    scheduled_date = fields.Datetime(
        string="Scheduled send date",
        help="When set, the scheduled job launches the campaign "
             "automatically on this date.",
    )
    send_window_days = fields.Float(
        string="Send spreading (days)", default=0.0,
        help="Randomly spreads the sends over this window (in days) so "
             "that one recipient cannot warn the others. 0 = send to "
             "everyone at once.",
    )
    mail_server_id = fields.Many2one(
        "ir.mail_server", string="Outgoing server",
        help="SMTP server used to send the lures. Empty = the system's "
             "default server.",
    )
    auto_assign_training = fields.Boolean(
        string="Assign training on failure", default=True,
    )

    # --- Engine links ---------------------------------------------------
    utm_campaign_id = fields.Many2one(
        "utm.campaign", string="UTM campaign", readonly=True, copy=False)
    mailing_id = fields.Many2one(
        "mailing.mailing", string="Mass mailing", readonly=True, copy=False)
    result_ids = fields.One2many(
        "bf.phishing.result", "campaign_id", string="Results")

    # --- Counters -------------------------------------------------------
    recipient_count = fields.Integer(
        compute="_compute_counters", string="Recipients")
    sent_count = fields.Integer(compute="_compute_counters", string="Emails "
                                                                    "sent")
    opened_count = fields.Integer(compute="_compute_counters", string="Emails "
                                                                      "opened")
    clicked_count = fields.Integer(compute="_compute_counters", string="Clicks")
    submitted_count = fields.Integer(
        compute="_compute_counters", string="Credentials submitted")
    reported_count = fields.Integer(
        compute="_compute_counters", string="Reports")
    fail_rate = fields.Float(
        compute="_compute_counters", string="Failure rate (%)",
        help="Share of recipients who clicked or submitted their credentials.")

    @api.depends("result_ids.state", "result_ids.reported")
    def _compute_counters(self):
        for camp in self:
            results = camp.result_ids
            camp.recipient_count = len(results)
            camp.sent_count = len(results.filtered(
                lambda r: r.state != "pending"))
            camp.opened_count = len(results.filtered(
                lambda r: r.state in ("opened", "clicked", "submitted")))
            clicked = len(results.filtered(
                lambda r: r.state in ("clicked", "submitted")))
            camp.clicked_count = clicked
            camp.submitted_count = len(results.filtered(
                lambda r: r.state == "submitted"))
            camp.reported_count = len(results.filtered("reported"))
            camp.fail_rate = (
                100.0 * clicked / camp.recipient_count
                if camp.recipient_count else 0.0
            )

    # ------------------------------------------------------------------ #
    # Audience resolution
    # ------------------------------------------------------------------ #
    def _resolve_target_partners(self):
        self.ensure_one()
        partners = self.recipient_partner_ids
        # mailing.contact has NO partner_id in stock Odoo, so resolve each list
        # contact to a res.partner by email (find-or-create) — a result row
        # requires a partner. Matching on the NORMALIZED address reuses the
        # existing partner — the internal user's one when several carry the
        # address — instead of piling up duplicates.
        for mlist in self.mailing_list_ids:
            for contact in mlist.contact_ids.filtered(lambda c: c.email):
                partners |= self._partenaire_pour_adresse(
                    contact.email, contact.name)
        return partners.filtered(lambda p: p.email)

    def _partenaire_pour_adresse(self, adresse, nom=None):
        """Le partenaire qui porte cette adresse — l'usager interne d'abord.

        🔴 deux défauts d'attribution, antérieurs au lot, et c'est
        d'ICI que la mauvaise attribution partait.

        1. La comparaison se faisait sur `email` BRUT et sensible à la casse :
           une `mailing.list` portant `Alice.X@Exemple.Test` ne retrouvait pas
           le partenaire dont l'adresse est `alice.x@exemple.test`, et CRÉAIT un
           doublon. On compare donc sur `email_normalized` — et on normalise la
           valeur cherchée de la même façon, sans quoi la comparaison reste
           sensible à la casse et ne correspond à rien (mesuré :
           `email_normalized = "Alice.X@Exemple.Test"` rend un ensemble vide).
        2. Quand plusieurs partenaires portent l'adresse, `limit=1` avec l'ordre
           par défaut (`complete_name ASC`) rendait le contact d'une SOCIÉTÉ
           (« ACME Inc, Zoé Exemple ») plutôt que le partenaire de l'usager
           interne (« Zoé Exemple »). On préfère donc explicitement le
           partenaire d'un usager interne actif.

        Conséquence de l'attribution ratée, mesurée : le résultat de la
        campagne naît sur un AUTRE partenaire que celui de l'employé. L'employé
        qui signale SON leurre avec SON jeton n'est alors plus reconnu par
        `_detect_simulation` — bornée au partenaire du signaleur depuis ce lot :
        il perd son crédit et fait ouvrir un faux incident.

        ⚠️ Et c'est bien ICI qu'il faut corriger. Élargir le filtre du jeton de
        `_detect_simulation` à `email_normalized` rouvrirait le défaut que ce lot
        ferme : `email` est dans `SELF_WRITEABLE_FIELDS`, donc un employé réécrit
        sa propre adresse et la met à celle d'un collègue. Le filtre par id de
        partenaire reste le bon.

        La lecture de `user_ids` n'est volontairement PAS sudoée. Mesuré sous
        l'identité d'un Opérateur (`group_bf_secaware_user`, le rôle le plus
        faible qui prépare une campagne) : la règle « user rule » de `res.users`
        est `['|', ('share','=',False), ('company_ids','in',company_ids)]`, donc
        tout usager interne est visible de tous, et le `filtered` rend le bon
        partenaire. Un sudo de plus dans un lot qui réduit justement la surface
        des chemins sudo serait gratuit.
        """
        Partner = self.env["res.partner"]
        normalisee = email_normalize(adresse)
        candidats = Partner.search(
            [("email_normalized", "=", normalisee)] if normalisee
            # Pas une adresse analysable : on garde la comparaison brute d'avant
            # plutôt que de chercher `email_normalized = False`, qui ramasserait
            # tous les partenaires sans adresse valide.
            else [("email", "=", adresse)]
        )
        if len(candidats) > 1:
            interne = candidats.filtered(
                lambda p: any(u.active and not u.share for u in p.user_ids))
            candidats = interne or candidats
        return candidats[:1] or Partner.create({
            "name": nom or adresse,
            "email": adresse,
        })

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #
    def action_prepare(self):
        """Materialise one result per recipient and build the engine records."""
        for camp in self:
            if camp.state not in ("draft",):
                raise UserError(_("The campaign must be in draft."))
            partners = camp._resolve_target_partners()
            if not partners:
                raise UserError(_(
                    "No recipient with a valid email address."))

            # Create results for partners not yet targeted.
            existing = camp.result_ids.mapped("partner_id")
            Result = self.env["bf.phishing.result"]
            to_create = []
            for partner in partners - existing:
                to_create.append({
                    "campaign_id": camp.id,
                    "partner_id": partner.id,
                    "email": partner.email,
                })
            if to_create:
                Result.create(to_create)

            camp._ensure_utm_campaign()
            camp._ensure_mailing()
            camp.state = "scheduled"
        return True

    def _refus_lancement(self):
        """La phrase qui refuse le lancement, ou False quand il peut partir.

        La page publique de la simulation offre « Suivre
        la formation assignée » vers le cours du leurre. La règle du cœur de
        l'eLearning (`website_slides.rule_slide_channel_visibility_signed_in_user`)
        exige `website_published` pour TOUT compte portail, membre inscrit ou
        pas : lancer une campagne dont le cours n'est pas publié envoie chaque
        personne prise par la simulation sur une page qu'elle ne peut pas
        ouvrir. La PRÉPARATION reste permise : préparer la campagne pendant que
        le cours se prépare est l'état courant, et rien n'en part.

        ⚠️ En sudo, pour ce seul booléen, et `prefetch_fields=False` : sans lui,
        la lecture ramènerait tous les champs stockés du cours dans le cache de
        la transaction, servis ensuite au code non-sudo sans aucune règle
        Le nom du cours n'entre pas dans le message.
        """
        self.ensure_one()
        cours = self.template_id.training_channel_id.sudo().with_context(
            prefetch_fields=False)
        if not cours or cours.website_published:
            return False
        lu = operator_env(self, self.company_id)
        return lu.env._(
            "The training course of this lure is not published: the people "
            "caught by the simulation would get a link to a page they cannot "
            "open. Publish the course, then launch the campaign.")

    def action_launch(self):
        # Tous les refus AVANT le premier effet. Levé en chemin, un
        # refus annulerait aussi ce que les campagnes précédentes du lot ont
        # fait, sans que rien le dise.
        for camp in self:
            if camp.state not in ("scheduled", "sending"):
                raise UserError(_("Prepare the campaign first."))
            refus = camp._refus_lancement()
            if refus:
                raise UserError(refus)
        for camp in self:
            if not camp.mailing_id:
                camp._ensure_mailing()
            camp.state = "sending"
            # Assign each pending result a send time: now (blast) or jittered
            # across the window (drip). The cron then releases due batches.
            now = fields.Datetime.now()
            pending = camp.result_ids.filtered(lambda r: r.state == "pending")
            for res in pending:
                if camp.send_window_days > 0:
                    res.scheduled_send = now + timedelta(
                        days=random.uniform(0.0, camp.send_window_days))
                else:
                    res.scheduled_send = now
            camp.state = "running"
            camp._release_due_results()
        return True

    def _release_due_results(self):
        """Send the lure to every pending result whose scheduled_send has
        arrived. Idempotent: released rows move to 'sent' and won't re-enter."""
        self.ensure_one()
        if self.state != "running" or not self.mailing_id:
            return
        now = fields.Datetime.now()
        due = self.result_ids.filtered(
            lambda r: r.state == "pending"
            and r.scheduled_send and r.scheduled_send <= now)
        if not due:
            return
        # Our own source of truth for "sent": independent of SMTP success so the
        # simulation flow works even on a mail-less staging tenant.
        due.register_sent()
        try:
            self.mailing_id.action_send_mail(res_ids=due.ids)
        except Exception as exc:  # noqa: BLE001 - never block the campaign
            _logger.warning(
                "bf_security_awareness: mass-mail send failed for campaign "
                "%s: %s", self.id, exc)
            # Reached from the scheduled job too (OdooBot): the operator who
            # launched, else the company's language.
            camp = operator_env(self, self.company_id)
            camp.message_post(body=camp.env._(
                "The mass mailing failed (%s). Results are still tracked "
                "through the trap link.") % exc)

    def action_cancel(self):
        for camp in self:
            camp.state = "cancelled"
        return True

    def action_done(self):
        for camp in self:
            camp.state = "done"
        return True

    def action_reset_to_draft(self):
        for camp in self:
            camp.state = "draft"
        return True

    # ------------------------------------------------------------------ #
    # Engine record builders
    # ------------------------------------------------------------------ #
    def _ensure_utm_campaign(self):
        self.ensure_one()
        if self.utm_campaign_id:
            return
        # Stored name, read by every operator: the company's language.
        env = company_env(self, self.company_id).env
        self.utm_campaign_id = self.env["utm.campaign"].create({
            "name": env._("Phishing: %s") % self.name,
        })

    def _build_body_html(self):
        self.ensure_one()
        body = self.template_id.email_body or ""
        # Optional QR (quishing) block, injected once.
        if self.template_id.qr_code and QR_MARKER not in body:
            body = "%s\n%s" % (body, QR_SNIPPET)
        # Append our real-time open pixel exactly once (marker-based check).
        if OPEN_PIXEL_MARKER not in body:
            body = "%s\n%s" % (body, OPEN_PIXEL_SNIPPET)
        return body

    def _default_email_from(self):
        # mailing.mailing.email_from is NOT NULL: always resolve to something.
        return (
            self.template_id.email_from
            or self.env.user.email_formatted
            or self.env.company.email
            or "no-reply@localhost"
        )

    def _ensure_mailing(self):
        self.ensure_one()
        model_id = self.env["ir.model"]._get_id("bf.phishing.result")
        body = self._build_body_html()
        email_from = self._default_email_from()
        vals = {
            "subject": self.template_id.subject or self.name,
            "body_arch": body,
            "body_html": body,
            "mailing_model_id": model_id,
            "mailing_domain": repr([
                ("campaign_id", "=", self.id),
            ]),
            "campaign_id": self.utm_campaign_id.id,
            "reply_to_mode": "new",
            "reply_to": email_from,
            "email_from": email_from,
            "mail_server_id": self.mail_server_id.id or False,
            "attachment_ids": [(6, 0, self.template_id.attachment_ids.ids)],
            "keep_archives": True,
        }
        if self.mailing_id:
            self.mailing_id.write(vals)
        else:
            self.mailing_id = self.env["mailing.mailing"].create(vals)

    def action_view_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Results"),
            "res_model": "bf.phishing.result",
            "view_mode": "list,form,pivot,graph",
            "domain": [("campaign_id", "=", self.id)],
            "context": {"default_campaign_id": self.id},
        }

    def action_view_mailing(self):
        self.ensure_one()
        if not self.mailing_id:
            raise UserError(_("No mass mailing has been created yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "mailing.mailing",
            "res_id": self.mailing_id.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------ #
    # Cron
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_scheduler(self):
        """Launch scheduled campaigns whose time has arrived, and reconcile
        bounces for running ones."""
        now = fields.Datetime.now()
        due = self.search([
            ("state", "=", "scheduled"),
            ("scheduled_date", "!=", False),
            ("scheduled_date", "<=", now),
        ])
        for camp in due:
            echec = camp._isoler(lambda c=camp: c._lancer_si_due(now))
            if echec is None:
                continue
            _logger.error(
                "bf_security_awareness: scheduled launch failed for %s",
                camp.id, exc_info=echec)
            if not _passagere(echec, camp.scheduled_date):
                camp._dire_sans_lever(camp._signaler_lancement_echoue, echec)
        running = self.search([("state", "=", "running")])
        for camp in running:
            # Une campagne par passe. Avant, une seule
            # qui levait arrêtait la distribution de TOUTES les autres et la
            # réconciliation des traces.
            echec = camp._isoler(camp._release_running)
            if echec is None:
                continue
            _logger.error(
                "bf_security_awareness: scheduled processing failed for "
                "campaign %s", camp.id, exc_info=echec)
            # Pas d'exemption ici : la note est dédoublonnée, donc une erreur
            # de concurrence se dit une fois, et ne peut pas rester muette.
            camp._dire_sans_lever(camp._noter_echec_moteur, echec)

    def _lancer_si_due(self, maintenant):
        """Lancer, si la campagne est ENCORE due.

        Le lot `due` est lu une fois, puis
        chaque `_isoler` valide et ouvre un nouvel instantané. Entre-temps,
        quelqu'un a pu cliquer « Lancer », replanifier ou annuler : relue ici,
        la campagne n'est plus « Planifiée », et `action_launch` lèverait
        « Prepare the campaign first. » — une fausse alerte, avec activité,
        sur une campagne qui va très bien.
        """
        self.ensure_one()
        if (self.state != "scheduled" or not self.scheduled_date
                or self.scheduled_date > maintenant):
            return
        self.action_launch()

    def _dire_sans_lever(self, signaler, echec):
        """Signaler un échec, sans que le signalement emporte la passe.

        Si `message_post` ou
        `activity_schedule` levait ici, l'exception sortait de la tâche
        planifiée, qui annulait TOUT — la date vidée comprise, donc la campagne
        réessayée toutes les 30 minutes — et les campagnes en cours n'étaient
        plus traitées : l'isolement perdu, par la porte du signalement. Le
        point de sauvegarde est sûr ici : rien dans un signalement ne valide.
        """
        try:
            with self.env.cr.savepoint():
                signaler(echec)
        except Exception:
            _logger.exception(
                "bf_security_awareness: could not report the failure of "
                "campaign %s", self.id)

    def _release_running(self):
        """Une passe de la tâche planifiée sur une campagne en cours."""
        self.ensure_one()
        self._release_due_results()  # drip: send batches whose time arrived
        self.result_ids._sync_from_traces()

    def _isoler(self, geste):
        """Jouer ``geste`` pour cette seule campagne ; rendre l'exception, ou None.

        PAS de point de sauvegarde en production, et ce n'est pas un
        oubli : hors des essais, l'envoi de masse d'Odoo VALIDE la transaction
        en chemin (`mail.compose.message._action_send_mail_mass_mail`,
        `self._cr.commit()` quand `auto_commit`, et `auto_commit` vaut vrai
        partout sauf sous les essais). Une validation libère les points de
        sauvegarde ouverts : la sortie du `with` lèverait alors « savepoint does
        not exist » — sur le chemin du SUCCÈS, et chaque campagne qui part
        serait signalée en échec. On fait donc comme les tâches planifiées du
        cœur : valider avant, valider après, annuler à l'échec. Ce qu'un envoi
        a déjà validé en chemin reste validé, comme avant ce correctif.

        Sous les essais, rien ne valide jamais (`auto_commit` faux) et une
        validation lèverait : un point de sauvegarde y tient le même rôle.
        """
        self.ensure_one()
        cr = self.env.cr
        if getattr(threading.current_thread(), "testing", False):
            try:
                with cr.savepoint():
                    geste()
            except Exception as exc:  # noqa: BLE001 - dit au fil par l'appelant
                return exc
            return None
        # Valider ce qui précède (le signalement de la campagne d'avant). Un
        # échec ICI n'est pas celui de cette campagne : il ne lui est pas
        # imputé, et la passe continue sur une transaction propre.
        try:
            cr.commit()
        except Exception:
            _logger.exception(
                "bf_security_awareness: commit before campaign %s failed",
                self.id)
            cr.rollback()
        try:
            geste()
            cr.commit()
        except Exception as exc:  # noqa: BLE001 - dit au fil par l'appelant
            cr.rollback()
            return exc
        return None

    def _signaler_lancement_echoue(self, exc):
        """Le lancement planifié a échoué ; le dire.

        Avant, seul le journal du serveur le savait : l'opérateur qui avait
        planifié une simulation à 9 h voyait « Planifiée » sans explication, et
        la tâche planifiée réessayait toutes les 30 minutes. Désormais :
        une note au fil, une activité chez qui a créé la
        campagne, et la date planifiée vidée — la tâche cesse de réessayer, la
        campagne reste prête, et l'opérateur relance quand la cause est réglée.
        """
        self.ensure_one()
        lance = self.state != "scheduled"
        self.scheduled_date = False
        camp = operator_env(self, self.company_id)
        camp.message_post(body=camp._motif_lancement_echoue(exc, lance))
        responsable = self.create_uid
        todo = self.env.ref(
            "mail.mail_activity_data_todo", raise_if_not_found=False)
        if not (todo and responsable.active and not responsable.share):
            return
        lu = reader_env(
            self, responsable.lang, self.company_id.sudo().partner_id.lang)
        resume = lu.env._("Scheduled launch failed")
        # Replanifiée puis encore en échec : une seule activité ouverte.
        if self.activity_ids.filtered(
                lambda a: a.user_id == responsable and a.summary == resume):
            return
        lu.activity_schedule(
            activity_type_id=todo.id,
            user_id=responsable.id,
            summary=resume,
            note=lu._motif_lancement_echoue(exc, lance),
        )

    def _motif_lancement_echoue(self, exc, lance):
        if lance:
            # Ce qu'un envoi a validé en chemin n'est pas défait : la campagne
            # est bien partie, ne pas dire le contraire.
            return self.env._(
                "The scheduled launch raised an error after the campaign had "
                "started: %s. The scheduled date has been cleared.") % _cause(exc)
        return self.env._(
            "The scheduled launch failed: %s. The campaign was not launched, "
            "and its scheduled date has been cleared so that the scheduled job "
            "stops retrying. Fix the cause, then launch the campaign or "
            "schedule it again.") % _cause(exc)

    def _noter_echec_moteur(self, exc):
        """Écrire l'échec au fil, sans le répéter.

        La tâche planifiée passe toutes les 30 minutes, et une campagne qui
        lève une fois lèvera sans doute à la passe suivante. Donc une note,
        PAS répétée tant que la même erreur revient —
        c'est-à-dire tant que la dernière note du fil est déjà celle-là.
        """
        self.ensure_one()
        camp = operator_env(self, self.company_id)
        corps = camp.env._(
            "The scheduled job could not process this campaign: %s. The other "
            "campaigns are processed separately; this one will be retried at "
            "the next run.") % _cause(exc)
        dernier = self.env["mail.message"].sudo().search([
            ("model", "=", self._name),
            ("res_id", "=", self.id),
            ("message_type", "in", ("comment", "notification")),
        ], order="id desc", limit=1)
        # Comparé à espaces normalisés.
        # `html2plaintext` réduit les espaces doubles, et PostgreSQL en écrit
        # (« DETAIL:  Key… ») : sans normaliser des deux côtés, une erreur de
        # la base n'était JAMAIS reconnue, et la note revenait 48 fois par jour.
        if dernier and _sans_espaces(html2plaintext(dernier.body or "")) == \
                _sans_espaces(corps):
            return
        camp.message_post(body=corps)
