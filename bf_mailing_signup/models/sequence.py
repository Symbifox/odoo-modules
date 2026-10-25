# -*- coding: utf-8 -*-
"""La séquence d'accueil : une suite de courriels, un par étape, à chaque personne qui a confirmé.

Odoo Community n'a pas l'automatisation marketing. Ce fichier en tient la partie
dont une infolettre a besoin, et rien de plus :

* `bf.mailing.signup.step` : les étapes d'une liste, par langue. Un rang, un délai
  en jours depuis la confirmation, un sujet et un corps HTML qui porte le marqueur
  `{{desabonnement}}`. Le module n'en livre aucune : chaque locataire pose les
  siennes, et sans étape la tâche planifiée ne fait rien.
* des champs sur `mailing.subscription` : la langue du formulaire, la date de la
  confirmation par lien, le rang du dernier courriel parti et sa date, et le compte
  des échecs d'envoi. Une personne qui n'a pas confirmé par le formulaire (un
  import, une inscription à la main) n'a pas de date, donc ne reçoit rien.
* la tâche planifiée, qui envoie à chacune le courriel suivant quand il est dû.

Ce que la séquence ne fait jamais, parce que le formulaire le promet :

* aucun pixel d'ouverture et aucun lien suivi. Les courriels portent
  `X-BF-Sans-Suivi`, que `ir.mail_server.build_email` lit pour retirer ce que
  `mail_tracking` y colle.
* aucun courriel sans lien de désabonnement : une étape dont le corps n'a pas le
  marqueur ne s'enregistre pas.
* aucun courriel à une personne désabonnée, ni à une adresse sur la liste noire.
  Une inscription qui repasse à « désabonnée », par n'importe quel chemin, perd sa date
  de confirmation : seule une confirmation par le lien arme de nouveau la séquence.
"""

import html
import logging
import smtplib
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..controllers.main import (
    ENTETE_SANS_SUIVI, ICP_BASE_URL, ICP_REPLY_TO_EN, ICP_REPLY_TO_FR, MARQUEUR_DESABONNEMENT,
    _url_desabonnement)
from .mail_mail import CLE_ETATS

_logger = logging.getLogger(__name__)

LANGUES = [("fr", "Français"), ("en", "English")]

# Au plus ce nombre de courriels par passage de la tâche planifiée : un retard
# (serveur arrêté, SMTP en panne) se rattrape en plusieurs passages plutôt qu'en
# une rafale qui ferait classer l'expéditeur en pourriel.
LOT_PAR_PASSAGE = 100

# Un envoi qui échoue est retenté après 1, 2, 4, 8, 16 puis 24 heures (plafond), jusqu'à huit
# échecs de suite, soit environ trois jours, puis abandonné : une adresse que le serveur refuse
# pour toujours ne doit pas être retentée toutes les quinze minutes, ni abîmer la réputation de
# l'expéditeur, mais une panne du serveur sortant de quelques heures ne doit pas non plus faire
# abandonner toutes les personnes dues pendant sa durée (rien ne distingue les deux cas : un refus
# propre à une adresse et une panne globale comptent pareil). Remettre `bf_failures` à 0 sur
# l'inscription la relance.
ESSAIS_MAX = 8
ATTENTE_MAX_HEURES = 24


def _attente_heures(echecs):
    """Heures à attendre après le `echecs`-ième échec de suite (1, 2, 4, 8, 16, 24, 24…)."""
    return min(2 ** (echecs - 1), ATTENTE_MAX_HEURES)


class BfMailingSignupStep(models.Model):
    _name = "bf.mailing.signup.step"
    _description = "Welcome sequence step"
    _order = "list_id, lang, rank"

    list_id = fields.Many2one("mailing.list", string="Mailing list", required=True,
                              ondelete="cascade", index=True)
    lang = fields.Selection(LANGUES, string="Language", required=True)
    rank = fields.Integer(string="Rank", required=True,
                          help="1 for the first email, 2 for the second, and so on.")
    delay_days = fields.Integer(
        string="Days after confirmation", required=True,
        help="The email is due this many days after the sign-up was confirmed. After "
             "a delay (server down), the gap to the previous email is kept: a late "
             "email never brings the next one forward.")
    subject = fields.Char(string="Subject", required=True)
    body_html = fields.Html(
        string="Body", required=True, sanitize=False,
        help="Complete HTML of the email. It must contain %s, replaced at "
             "sending by the recipient's own unsubscribe link." % MARQUEUR_DESABONNEMENT)

    _sql_constraints = [
        ("rang_unique", "UNIQUE(list_id, lang, rank)",
         "A list has one step per rank and per language."),
        ("rang_positif", "CHECK(rank > 0)", "The rank starts at 1."),
        ("delai_positif", "CHECK(delay_days >= 0)", "The delay cannot be negative."),
    ]

    @api.constrains("body_html")
    def _check_desabonnement(self):
        for etape in self:
            if MARQUEUR_DESABONNEMENT not in str(etape.body_html or ""):
                raise ValidationError(_(
                    "The body of a step must contain %s, where the unsubscribe link goes.",
                    MARQUEUR_DESABONNEMENT))

    @api.model
    def _bf_index(self):
        """Les étapes, par (liste, langue, rang)."""
        return {(e.list_id.id, e.lang, e.rank): e for e in self.sudo().search([])}

    @api.model
    def _cron_envoyer_sequence(self, auto_commit=True):
        """Envoyer à chaque inscription active le courriel suivant, quand il est dû.

        `auto_commit` (vrai pour la tâche planifiée) valide après chaque courriel ;
        les essais le coupent, comme Odoo le fait pour `mail.mail.send` : un essai
        ne peut pas valider.
        """
        par_cle = self._bf_index()
        if not par_cle:
            return
        rang_max = max(rang for (_liste, _lang, rang) in par_cle)
        listes = sorted({liste for (liste, _lang, _rang) in par_cle})
        Sub = self.env["mailing.subscription"].sudo()
        candidates = Sub.search([
            ("list_id", "in", listes), ("list_id.active", "=", True),
            ("opt_out", "=", False), ("bf_confirmed_at", "!=", False),
            ("bf_step", "<", rang_max), ("bf_failures", "<", ESSAIS_MAX)],
            order="bf_confirmed_at, id")
        maintenant = fields.Datetime.now()
        dues = candidates.filtered(lambda s: s._bf_etape_due(par_cle, maintenant))
        envoyes = 0
        for sub in dues:
            if envoyes >= LOT_PAR_PASSAGE:
                _logger.info("bf_mailing_signup : lot de %d atteint, le reste au prochain "
                             "passage", LOT_PAR_PASSAGE)
                break
            # Pas de `savepoint` autour quand on valide : un COMMIT dans un savepoint
            # fait échouer la sortie du bloc.
            try:
                if auto_commit:
                    parti = sub._bf_envoyer_suivant(commit=True)
                else:
                    with self.env.cr.savepoint():
                        parti = sub._bf_envoyer_suivant()
                if parti:
                    envoyes += 1
            except Exception:           # noqa: BLE001 — une inscription ne bloque pas les autres
                if auto_commit:
                    self.env.cr.rollback()
                self.env.invalidate_all()
                _logger.exception("bf_mailing_signup : séquence en échec pour l'inscription %s",
                                  sub.id)
        if envoyes:
            _logger.info("bf_mailing_signup : %d courriel(s) de séquence envoyé(s)", envoyes)


class MailingSubscription(models.Model):
    _inherit = "mailing.subscription"

    bf_lang = fields.Selection(LANGUES, string="Welcome sequence language", copy=False)
    bf_confirmed_at = fields.Datetime(
        string="Confirmed by email link", copy=False,
        help="Set by the confirmation link of the public form. Empty for a contact "
             "added any other way: it does not get the welcome sequence. Cleared "
             "whenever the subscription is opted out.")
    bf_step = fields.Integer(string="Last welcome email sent", default=0, copy=False)
    bf_step_sent_at = fields.Datetime(string="Last welcome email sent on", copy=False)
    bf_failures = fields.Integer(
        string="Failed welcome email attempts", default=0, copy=False,
        help="Consecutive failed sends of the next welcome email, retried after a growing "
             "delay. After several, the sequence stops for this subscription until this is "
             "reset to 0.")
    bf_attempt_at = fields.Datetime(string="Last failed welcome email attempt", copy=False)

    # 🔴 Un désabonnement efface la date de confirmation, quel que soit le chemin : le
    # lien de la séquence, la page d'Odoo, la liste noire, un geste d'administrateur.
    # Sans quoi une réinscription SANS double consentement (route du site web, import,
    # case décochée) relançait la séquence là où elle s'était arrêtée.
    @staticmethod
    def _bf_vals_desabonnement(vals):
        return bool(vals.get("opt_out") or vals.get("opt_out_datetime")
                    or vals.get("opt_out_reason_id"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if self._bf_vals_desabonnement(vals):
                vals["bf_confirmed_at"] = False
        return super().create(vals_list)

    def write(self, vals):
        if self._bf_vals_desabonnement(vals):
            vals = dict(vals, bf_confirmed_at=False)
        return super().write(vals)

    def _bf_etape_due(self, par_cle, maintenant):
        """L'étape à envoyer maintenant à cette inscription, ou None."""
        self.ensure_one()
        if (self.opt_out or not self.bf_confirmed_at or not self.bf_lang
                or not self.list_id.active or self.bf_failures >= ESSAIS_MAX):
            return None
        if self.bf_failures and self.bf_attempt_at and maintenant < self.bf_attempt_at + timedelta(
                hours=_attente_heures(self.bf_failures)):
            return None
        liste, lang = self.list_id.id, self.bf_lang
        # Le prochain rang qui EXISTE : une étape retirée au milieu (1, 2, 4) ne
        # bloque pas ceux qui en sont au rang 2.
        suivants = sorted(r for (l, la, r) in par_cle if l == liste and la == lang
                          and r > self.bf_step)
        if not suivants:
            return None
        suivante = par_cle[(liste, lang, suivants[0])]
        echeance = self.bf_confirmed_at + timedelta(days=suivante.delay_days)
        if self.bf_step and self.bf_step_sent_at:
            # L'écart prévu entre les deux étapes, compté depuis le dernier envoi
            # réel : après un retard, le courriel suivant ne part pas dans la foulée.
            precedente = par_cle.get((liste, lang, self.bf_step))
            ecart = suivante.delay_days - precedente.delay_days if precedente else 0
            echeance = max(echeance, self.bf_step_sent_at + timedelta(days=max(ecart, 0)))
        return suivante if maintenant >= echeance else None

    def _bf_envoyer_suivant(self, commit=False):
        """Envoyer à cette inscription le courriel suivant s'il est dû. Rend True s'il est parti.

        Le verrou de ligne (`SKIP LOCKED`) sert la course entre le clic de
        confirmation, qui envoie le premier courriel tout de suite, et la tâche
        planifiée : celle qui arrive seconde passe son tour, puis relit l'état.

        Le rang n'avance que si le courriel est RÉELLEMENT parti : l'état `sent` que
        le cœur pose juste avant de l'effacer, noté par `mail.mail._bf_noter_etats`.
        L'absence du courriel après l'envoi ne prouve rien (le cœur efface aussi un
        courriel sans destinataire calculé). Le courriel part en `auto_delete`, comme
        la confirmation : un module qui garde une copie des envois sortants n'en garde
        alors que l'enveloppe, pas le corps qui porte le lien à jeton.

        `commit=True` (tâche planifiée) valide après chaque envoi, et AVANT la note du
        fil du contact : une note qui échoue ne doit pas renvoyer un courriel déjà parti.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT 1 FROM mailing_subscription WHERE id = %s FOR UPDATE SKIP LOCKED",
            (self.id,))
        if not self.env.cr.fetchone():
            return False
        self.invalidate_recordset()
        par_cle = self.env["bf.mailing.signup.step"]._bf_index()
        maintenant = fields.Datetime.now()
        etape = self._bf_etape_due(par_cle, maintenant)
        if not etape:
            return False
        contact = self.contact_id
        email = contact.email_normalized
        if not email or contact.is_blacklisted:
            return False

        icp = self.env["ir.config_parameter"].sudo()
        lang = self.bf_lang
        url = _url_desabonnement(self.env, self.list_id.id, email, lang)
        if not url:
            # Pas de `base_url` : un courriel sans lien de désabonnement qui marche ne part pas.
            # Ce n'est pas l'échec d'une adresse, donc rien n'est compté contre l'inscription.
            _logger.error("bf_mailing_signup : paramètre %s absent, aucun courriel de séquence ne "
                          "peut partir (le lien de désabonnement n'aurait pas de site)", ICP_BASE_URL)
            return False
        corps = str(etape.body_html).replace(
            MARQUEUR_DESABONNEMENT, html.escape(url, quote=True))
        reply_to = icp.get_param(ICP_REPLY_TO_EN if lang == "en" else ICP_REPLY_TO_FR) or ""
        # `email_from` absent, comme la confirmation : le repli sur l'adresse
        # d'envoi de l'instance est le seul alignement SPF et DKIM.
        mail = self.env["mail.mail"].sudo().create({
            "subject": etape.subject,
            "body_html": corps,
            "email_to": email,
            "reply_to": reply_to or False,
            # Explicitement vide : sans cette clé, `mail.message` calcule l'expéditeur depuis
            # l'usager qui crée le courriel, et la tâche planifiée (`__system__`) prêterait son
            # nom d'affichage à l'adresse d'envoi de l'instance (`"OdooBot" <bonjour@…>`).
            "email_from": False,
            "auto_delete": True,
            "headers": repr({
                ENTETE_SANS_SUIVI: "1",
                "List-Unsubscribe": f"<{url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }),
        })
        mail_id = mail.id
        etats = self.env.cr.cache.setdefault(CLE_ETATS, {})
        etats.pop(mail_id, None)
        try:
            mail.send(raise_exception=False)
        except smtplib.SMTPServerDisconnected:
            # Le cœur relance celle-là même avec `raise_exception=False` (le serveur a
            # coupé : on ne sait pas si le courriel est parti). Un échec comme un autre.
            _logger.warning("bf_mailing_signup : SMTP coupé pendant l'envoi (mail.mail %d)", mail_id)
        parti = etats.pop(mail_id, None) == "sent"
        if mail.exists():
            mail.unlink()               # un échec le garde : il porte l'adresse, et on reprend
        if not parti:
            echecs = self.bf_failures + 1
            self.write({"bf_failures": echecs, "bf_attempt_at": maintenant})
            (_logger.error if echecs >= ESSAIS_MAX else _logger.warning)(
                "bf_mailing_signup : courriel %d de la séquence NON parti (mail.mail %d), "
                "échec %d sur %d%s", etape.rank, mail_id, echecs, ESSAIS_MAX,
                ", abandonné pour cette inscription" if echecs >= ESSAIS_MAX else "")
            if commit:
                self.env.cr.commit()
            return False
        self.write({"bf_step": etape.rank, "bf_step_sent_at": maintenant,
                    "bf_failures": 0, "bf_attempt_at": False})
        if commit:
            self.env.cr.commit()
        _logger.info("bf_mailing_signup : courriel %d de la séquence envoyé (mail.mail %d)",
                     etape.rank, mail_id)
        try:
            with self.env.cr.savepoint():
                contact.message_post(body=_(
                    "Welcome sequence: email %(rang)s sent (%(sujet)s).",
                    rang=etape.rank, sujet=etape.subject))
            if commit:
                self.env.cr.commit()
        except Exception:               # noqa: BLE001 — la note passe après l'envoi, jamais avant
            _logger.exception("bf_mailing_signup : note de séquence non posée (contact %s)",
                              contact.id)
        return True
