"""Audience d'un transfert en mode ouvert : un visiteur auto-déclaré.

En mode « destinataires nommés » (le mode historique), la liste des adresses
est connue à l'envoi et le code à usage unique ne fait que PROUVER qu'on est
l'une d'elles. En mode « audience ouverte », le lien ne nomme personne : le
visiteur déclare son identité, reçoit un code, et c'est cette confirmation qui
l'inscrit ici.

Une ligne par (transfert, identité). Elle porte trois choses que le journal
d'accès ne peut pas porter :

* **l'identité** retenue pour ce visiteur — le filigrane du PDF téléchargé et
  (avec le module pont ``bf_securetransfer_sign``) la NDA signée s'y adossent ;
* **les compteurs d'abus** — nombre de codes envoyés, dernier envoi, nombre de
  SMS. Ils vivent en base et non dans la mémoire du processus : la prod tourne
  à ``workers = 6``, donc un plafond gardé en mémoire vaut six fois ce qu'il
  annonce (les limiteurs glissants du contrôleur restent la première ligne,
  mais ils ne peuvent pas être la seule) ;
* **le budget de téléchargement par visiteur** — ``max_downloads`` est global
  au transfert : sur une salle de données, dix visiteurs l'épuiseraient pour
  le onzième.

## ⚠ Deux identités, une règle : le code va TOUJOURS à l'identité déclarée

Un visiteur se déclare par courriel **ou** par mobile, et le code part sur ce
même canal. C'est la seule forme qui prouve quelque chose : laisser saisir une
adresse courriel *et* un numéro de son choix, puis livrer le code au numéro,
reviendrait à donner l'accès au nom de n'importe qui — l'adresse ne serait plus
prouvée du tout. Le numéro n'est donc jamais un « canal de rechange » pour une
identité courriel : c'est une identité à part entière, ou rien.

Conséquence assumée : une liste blanche de domaines ne peut pas s'appliquer à
un numéro. Un transfert qui restreint par domaine n'accepte donc que des
identités courriel (voir ``_audience_admissible`` côté transfert).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from . import sms as sms_helper

_logger = logging.getLogger(__name__)


class SecureTransferAudience(models.Model):
    _name = "secure.transfer.audience"
    _description = "Secure transfer: open audience visitor"
    _order = "id asc"
    _rec_name = "display_identity"

    transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfer",
        required=True,
        index=True,
        ondelete="cascade",
    )
    identity_kind = fields.Selection(
        selection=[("email", "Email"), ("sms", "Mobile")],
        string="Identity type",
        default="email",
        required=True,
    )
    # Les deux colonnes sont nullables et chacune porte sa contrainte
    # d'unicité : PostgreSQL considère deux NULL comme distincts, donc
    # « unique(transfer_id, email) » n'empêche pas plusieurs visiteurs
    # identifiés par leur seul numéro, et réciproquement.
    email = fields.Char(string="Email", index=True)
    phone = fields.Char(string="Mobile (10 digits)", index=True)
    display_identity = fields.Char(
        string="Identity", compute="_compute_display_identity", store=True,
    )
    state = fields.Selection(
        selection=[
            # « invited » est le seul état qui ne vient PAS d'un geste du
            # visiteur : la ligne naît de l'envoi, pour que « qui n'a rien
            # fait » se lise au lieu de se déduire d'une absence.
            ("invited", "Not yet seen"),
            # 🔴 Sans code destinataire exigé, le visiteur n'a aucune
            # identité en session : un téléchargement ne peut s'accrocher à
            # personne. Mais quand le lien ne sert QU'UNE adresse — un envoi
            # fendu, ou un envoi à un seul destinataire — il lui appartient
            # sans ambiguïté. Cet état le dit, sans mentir : « confirmé »
            # signifie « a prouvé son identité par un code », et le certificat
            # d'accès repose sur cette distinction.
            ("seen", "Came without a code"),
            ("pending", "Code sent"),
            ("confirmed", "Confirmed"),
            ("blocked", "Blocked"),
        ],
        string="Status",
        default="pending",
        required=True,
        index=True,
    )
    first_seen = fields.Datetime(
        string="First request", readonly=True, default=fields.Datetime.now,
    )
    # ⚠ Une ligne « invited » est créée avec first_seen à faux (le défaut
    # ci-dessus ne tient que pour les lignes nées d'une visite) : la colonne
    # dit quand la personne s'est présentée, et vide est la bonne réponse
    # quand elle ne l'a jamais fait. C'est invited_at qui date l'envoi.
    invited_at = fields.Datetime(
        string="Listed on", readonly=True,
        help="When the send registered this recipient. Set for the "
             "recipients a transfer names; empty for a visitor who "
             "declared themselves on an open audience link.",
    )
    resend_count = fields.Integer(
        string="Links re-sent", default=0, readonly=True,
    )
    last_resend_at = fields.Datetime(string="Last re-send", readonly=True)
    reminder_count = fields.Integer(
        string="Automatic reminders", default=0, readonly=True,
    )
    confirmed_at = fields.Datetime(string="Confirmed on", readonly=True)
    last_otp_at = fields.Datetime(string="Last code sent", readonly=True)
    otp_send_count = fields.Integer(string="Codes sent", default=0, readonly=True)
    # Essais ratés sur le code EN COURS, en base. Le défi vit dans la
    # session et le seul compteur était par (IP, transfert) en mémoire : un
    # même cookie promené sur vingt IP s'offrait vingt fois huit essais.
    otp_verify_fails = fields.Integer(
        string="Failed attempts on the current code", default=0, readonly=True)
    sms_send_count = fields.Integer(string="SMS sent", default=0, readonly=True)
    download_count = fields.Integer(
        string="Downloads", default=0, readonly=True,
    )
    # Fenêtre glissante d'une heure pour les destinataires nommés : leur
    # budget n'est pas un plafond à vie (ils reviennent pendant toute la vie du
    # transfert), mais il doit exister — sinon un lien fuité, avec des IP qui
    # tournent, sert à inonder la boîte ou le mobile d'un destinataire.
    otp_window_start = fields.Datetime(string="Code window start", readonly=True)
    otp_window_count = fields.Integer(string="Codes in the window", default=0, readonly=True)
    ip = fields.Char(string="IP (first request)", readonly=True)
    user_agent = fields.Char(string="User agent", readonly=True)

    _sql_constraints = [
        ("transfer_email_uniq", "unique(transfer_id, email)",
         "This email is already registered in this transfer's audience."),
        ("transfer_phone_uniq", "unique(transfer_id, phone)",
         "This number is already registered in this transfer's audience."),
    ]

    # Plafonds par identité. Ils s'ajoutent aux limiteurs par (IP, transfert)
    # du contrôleur : ceux-là protègent contre le martèlement d'une IP,
    # ceux-ci contre l'usage du lien comme relais de courriel ou de SMS vers
    # des tiers.
    MAX_OTP_PER_IDENTITY = 5
    # Plus bas que le plafond courriel : un SMS coûte réellement de l'argent.
    MAX_SMS_PER_IDENTITY = 3
    # Un renvoi trop rapproché n'aide personne et double le coût d'un SMS.
    OTP_COOLDOWN_SECONDS = 60
    # Destinataires nommés : codes par heure et par destinataire.
    MAX_OTP_PER_HOUR_NAMED = 5
    # Essais par code envoyé. Le compteur repart à chaque nouveau code, et les
    # envois sont eux-mêmes plafonnés par identité : le total reste borné.
    MAX_OTP_VERIFY_FAILS = 5
    OTP_WINDOW_SECONDS = 3600

    @api.depends("identity_kind", "email", "phone")
    def _compute_display_identity(self):
        """Ce que l'opérateur — et le filigrane — voient.

        ⚠ Un numéro n'est jamais rendu en entier : il finit estampé sur un PDF
        qui circule, et le transfert n'a aucune raison de publier le mobile de
        quelqu'un. Les quatre derniers chiffres suffisent à reconnaître une
        personne dont on a déjà le numéro, et ne le donnent pas à qui ne l'a
        pas."""
        for rec in self:
            if rec.identity_kind == "sms":
                digits = (rec.phone or "")[-4:]
                # Stored and printed in the log and certificate: the
                # transfer's language, not the visitor's who triggered it.
                tenv = rec.transfer_id._log_env() if rec.transfer_id else rec.env
                rec.display_identity = ("•••-••••-%s" % digits) if digits \
                    else tenv._("(mobile)")
            else:
                rec.display_identity = rec.email or ""

    @api.model
    def _identity_values(self, kind, value):
        """Normaliser une identité saisie : ``(kind, email, phone)`` ou None.

        Point de passage unique — la normalisation décide de l'unicité, donc
        deux appelants qui normalisent différemment créeraient deux lignes pour
        la même personne (et deux budgets de codes)."""
        if kind == "sms":
            phone = sms_helper.normalize_na(value or "")
            return ("sms", False, phone) if phone else None
        email = email_normalize(value or "")
        return ("email", email, False) if email else None

    def _identity_value(self):
        """La valeur brute de l'identité (adresse ou numéro) — ce à quoi le
        code doit être livré."""
        self.ensure_one()
        return (self.phone if self.identity_kind == "sms" else self.email) or ""

    def _seconds_since_last_otp(self):
        """Secondes écoulées depuis le dernier code, ou None s'il n'y en a
        jamais eu."""
        self.ensure_one()
        if not self.last_otp_at:
            return None
        return (fields.Datetime.now() - self.last_otp_at).total_seconds()

    def _may_receive_otp(self):
        """(autorisé, motif) — les plafonds par identité, en base.

        Le motif n'est jamais rendu au visiteur tel quel : la page publique
        affiche un refus uniforme (« trop de tentatives »). Le distinguer ici
        sert le journal et le débogage, pas la page."""
        self.ensure_one()
        if self.state == "blocked":
            return False, "blocked"
        if self.transfer_id.audience_mode != "open":
            elapsed = self._seconds_since_last_otp()
            if elapsed is not None and elapsed < self.OTP_COOLDOWN_SECONDS:
                return False, "cooldown"
            if self._otp_window_open() \
                    and self.otp_window_count >= self.MAX_OTP_PER_HOUR_NAMED:
                return False, "otp_cap"
            return True, ""
        if self.otp_send_count >= self.MAX_OTP_PER_IDENTITY:
            return False, "otp_cap"
        if self.identity_kind == "sms" \
                and self.sms_send_count >= self.MAX_SMS_PER_IDENTITY:
            return False, "sms_cap"
        elapsed = self._seconds_since_last_otp()
        if elapsed is not None and elapsed < self.OTP_COOLDOWN_SECONDS:
            return False, "cooldown"
        return True, ""

    def _otp_window_open(self):
        """La fenêtre horaire en cours n'est pas échue."""
        self.ensure_one()
        return bool(self.otp_window_start) and (
            fields.Datetime.now() - self.otp_window_start
        ).total_seconds() < self.OTP_WINDOW_SECONDS

    def _record_otp_sent(self):
        """Compter un code effectivement parti. Appelé APRÈS la livraison : un
        envoi que le fournisseur a refusé ne doit pas consommer le budget du
        visiteur (sinon un opérateur SMS en panne verrouille l'accès)."""
        self.ensure_one()
        now = fields.Datetime.now()
        window = self._otp_window_open()
        vals = {
            "otp_window_start": self.otp_window_start if window else now,
            "otp_window_count": (self.otp_window_count + 1) if window else 1,
            "otp_send_count": self.otp_send_count + 1,
            "sms_send_count": self.sms_send_count
            + (1 if self.identity_kind == "sms" else 0),
            "last_otp_at": now,
            "otp_verify_fails": 0,
        }
        # ⚠ C'est ICI que « pas encore venu » devient « code envoyé ». Une
        # ligne inscrite à l'envoi existe avant toute visite ; sans cette
        # bascule, une personne qui a demandé son code resterait comptée parmi
        # les absents — et le rappel automatique l'aurait relancée alors
        # qu'elle a le courriel en main. Et c'est bien ici, après la livraison
        # du code : un envoi que le fournisseur a refusé ne fait pas d'une
        # personne jamais venue une personne « en attente ».
        if self.state in ("invited", "seen"):
            vals["state"] = "pending"
        if not self.first_seen:
            vals["first_seen"] = now
        self.sudo().write(vals)

    def _lock_otp_attempts(self):
        """Verrou de ligne : deux essais simultanés avec le même cookie ne
        doivent pas lire le même compteur."""
        self.ensure_one()
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM secure_transfer_audience WHERE id = %s FOR UPDATE",
            (self.id,))
        self.invalidate_recordset(["otp_verify_fails"])

    def _otp_attempts_exhausted(self):
        self.ensure_one()
        return self.otp_verify_fails >= self.MAX_OTP_VERIFY_FAILS

    def _record_otp_verify_fail(self):
        self.ensure_one()
        self.sudo().write({"otp_verify_fails": self.otp_verify_fails + 1})

    def _confirm(self):
        """Le code a été validé : le visiteur entre dans l'audience."""
        self.ensure_one()
        if self.state in ("confirmed", "blocked"):
            # « blocked » ne se lève que par le bouton de l'opérateur.
            return self
        self.sudo().write({
            "state": "confirmed",
            "confirmed_at": fields.Datetime.now(),
        })
        return self

    def _register_download(self):
        """Compter un téléchargement pour ce visiteur (budget par personne)."""
        self.ensure_one()
        self.sudo().download_count += 1

    def _register_download_without_code(self):
        """Compter un téléchargement attribuable SANS code, et sortir cette
        personne des absents.

        🔴 Appelé quand le lien ne sert qu'une adresse et qu'aucun code
        n'est exigé : le visiteur n'a rien prouvé, mais le lien n'est parti
        qu'à lui. Sans cela, quelqu'un qui a tout téléchargé restait compté
        « jamais venu » pour toujours, et le rappel automatique le relançait —
        l'inverse exact de ce que l'aide du champ de marque promet.

        L'état ne devient PAS « confirmé » : aucune identité n'a été prouvée,
        et le certificat d'accès lit cette colonne comme une preuve.
        """
        self.ensure_one()
        vals = {"download_count": self.download_count + 1}
        if not self.first_seen:
            vals["first_seen"] = fields.Datetime.now()
        if self.state == "invited":
            vals["state"] = "seen"
        self.sudo().write(vals)

    def _download_budget_left(self):
        """Téléchargements restants pour ce visiteur : None = illimité."""
        self.ensure_one()
        budget = self.transfer_id.audience_max_downloads
        if not budget:
            return None
        return max(0, budget - self.download_count)

    # ------------------------------------------------------------- relance
    def _send_link_again(self, reason):
        """Re-send the share link to THIS identity, and to nobody else.

        ``reason`` is ``manual`` (the operator's button) or ``reminder`` (the
        cron). It picks the journal verb and the actor, and nothing else: the
        mail itself is the SAME branded link mail the first send used. A second
        notice worded differently would land in the recipient's inbox as a new,
        unrecognisable transfer, and the one thing a reminder must do is look
        like the thing it reminds of.
        """
        self.ensure_one()
        transfer = self.transfer_id
        if self.identity_kind != "email" or not self.email:
            raise UserError(_(
                "Only an email identity can be sent a link again: this "
                "visitor declared a mobile number."))
        if transfer.state != "active":
            raise UserError(_(
                "The link can only be sent again while the transfer is "
                "active."))
        if self.state == "blocked":
            raise UserError(_(
                "This visitor is blocked: unblock them first."))
        transfer.with_context(
            st_resend_actor=self.env.user.login if reason == "manual" else None,
            st_resend_kind=reason,
        )._send_link_emails(recipients_only=True, only=[self.email])
        vals = {
            "resend_count": self.resend_count + 1,
            "last_resend_at": fields.Datetime.now(),
        }
        if reason == "reminder":
            vals["reminder_count"] = self.reminder_count + 1
        self.sudo().write(vals)
        return True

    def action_resend_link(self):
        """Bouton par ligne : relancer CETTE personne.

        ⚠ Une méthode sans tiret bas de tête est une surface RPC : le
        ``groups=`` du bouton ne garde que l'interface. Le groupe USER de
        securetransfer est en lecture seule sur le transfert, donc sans ce
        contrôle un compte en lecture seule ferait partir le lien de partage
        par XML-RPC. Même garde que ``action_resend_emails``.
        """
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."))
        for rec in self:
            rec._send_link_again("manual")
        return True

    def action_block(self):
        """Bouton opérateur : couper l'accès d'un visiteur sans toucher au lien
        ni aux autres. La ligne reste — le journal doit garder trace de son
        passage."""
        for rec in self:
            if rec.state == "blocked":
                continue
            rec.state = "blocked"
            rec.transfer_id._log(
                "audience_blocked", actor=self.env.user.login,
                note=rec.transfer_id._log_env()._("Visitor blocked: %s")
                % rec.display_identity,
            )
        return True

    def action_unblock(self):
        for rec in self:
            if rec.state != "blocked":
                continue
            # On revient à l'état RÉEL, pas à « en attente » par défaut :
            # depuis que les destinataires nommés sont inscrits dès l'envoi,
            # une ligne peut n'avoir jamais rien demandé. La rendre « code
            # envoyé » aurait fait disparaître un absent de la liste des
            # absents — et le rappel automatique l'aurait sauté.
            if rec.confirmed_at:
                rec.state = "confirmed"
            elif rec.last_otp_at:
                rec.state = "pending"
            elif rec.download_count:
                rec.state = "seen"
            else:
                rec.state = "invited"
            rec.transfer_id._log(
                "audience_unblocked", actor=self.env.user.login,
                note=rec.transfer_id._log_env()._("Visitor unblocked: %s")
                % rec.display_identity,
            )
        return True

    def unlink(self):
        """L'audience est une pièce du journal : elle dit QUI a été admis. Elle
        ne disparaît qu'avec le transfert, au ramassage final."""
        if not self.env.context.get("st_gc"):
            raise UserError(_(
                "Audience visitors cannot be deleted: they are part of "
                "the access evidence. Use \"Block\"."
            ))
        return super().unlink()
