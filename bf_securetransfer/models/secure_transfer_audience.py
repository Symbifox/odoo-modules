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
    _description = "Transfert sécurisé — Visiteur d'une audience ouverte"
    _order = "id asc"
    _rec_name = "display_identity"

    transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfert",
        required=True,
        index=True,
        ondelete="cascade",
    )
    identity_kind = fields.Selection(
        selection=[("email", "Courriel"), ("sms", "Mobile")],
        string="Type d'identité",
        default="email",
        required=True,
    )
    # Les deux colonnes sont nullables et chacune porte sa contrainte
    # d'unicité : PostgreSQL considère deux NULL comme distincts, donc
    # « unique(transfer_id, email) » n'empêche pas plusieurs visiteurs
    # identifiés par leur seul numéro, et réciproquement.
    email = fields.Char(string="Courriel", index=True)
    phone = fields.Char(string="Mobile (10 chiffres)", index=True)
    display_identity = fields.Char(
        string="Identité", compute="_compute_display_identity", store=True,
    )
    state = fields.Selection(
        selection=[
            ("pending", "Code envoyé"),
            ("confirmed", "Confirmé"),
            ("blocked", "Bloqué"),
        ],
        string="État",
        default="pending",
        required=True,
        index=True,
    )
    first_seen = fields.Datetime(
        string="Première demande", readonly=True, default=fields.Datetime.now,
    )
    confirmed_at = fields.Datetime(string="Confirmé le", readonly=True)
    last_otp_at = fields.Datetime(string="Dernier code envoyé", readonly=True)
    otp_send_count = fields.Integer(string="Codes envoyés", default=0, readonly=True)
    sms_send_count = fields.Integer(string="SMS envoyés", default=0, readonly=True)
    download_count = fields.Integer(
        string="Téléchargements", default=0, readonly=True,
    )
    ip = fields.Char(string="IP (première demande)", readonly=True)
    user_agent = fields.Char(string="Agent utilisateur", readonly=True)

    _sql_constraints = [
        ("transfer_email_uniq", "unique(transfer_id, email)",
         "Ce courriel est déjà inscrit à l'audience de ce transfert."),
        ("transfer_phone_uniq", "unique(transfer_id, phone)",
         "Ce numéro est déjà inscrit à l'audience de ce transfert."),
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
                rec.display_identity = ("•••-••••-%s" % digits) if digits else _("(mobile)")
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
        if self.otp_send_count >= self.MAX_OTP_PER_IDENTITY:
            return False, "otp_cap"
        if self.identity_kind == "sms" \
                and self.sms_send_count >= self.MAX_SMS_PER_IDENTITY:
            return False, "sms_cap"
        elapsed = self._seconds_since_last_otp()
        if elapsed is not None and elapsed < self.OTP_COOLDOWN_SECONDS:
            return False, "cooldown"
        return True, ""

    def _record_otp_sent(self):
        """Compter un code effectivement parti. Appelé APRÈS la livraison : un
        envoi que le fournisseur a refusé ne doit pas consommer le budget du
        visiteur (sinon un opérateur SMS en panne verrouille l'accès)."""
        self.ensure_one()
        self.sudo().write({
            "otp_send_count": self.otp_send_count + 1,
            "sms_send_count": self.sms_send_count
            + (1 if self.identity_kind == "sms" else 0),
            "last_otp_at": fields.Datetime.now(),
        })

    def _confirm(self):
        """Le code a été validé : le visiteur entre dans l'audience."""
        self.ensure_one()
        if self.state == "confirmed":
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

    def _download_budget_left(self):
        """Téléchargements restants pour ce visiteur : None = illimité."""
        self.ensure_one()
        budget = self.transfer_id.audience_max_downloads
        if not budget:
            return None
        return max(0, budget - self.download_count)

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
                note=_("Visiteur bloqué : %s") % rec.display_identity,
            )
        return True

    def action_unblock(self):
        for rec in self:
            if rec.state != "blocked":
                continue
            # On revient à « confirmé » quand il l'avait été, sinon en attente.
            rec.state = "confirmed" if rec.confirmed_at else "pending"
            rec.transfer_id._log(
                "audience_unblocked", actor=self.env.user.login,
                note=_("Visiteur débloqué : %s") % rec.display_identity,
            )
        return True

    def unlink(self):
        """L'audience est une pièce du journal : elle dit QUI a été admis. Elle
        ne disparaît qu'avec le transfert, au ramassage final."""
        if not self.env.context.get("st_gc"):
            raise UserError(_(
                "Les visiteurs d'une audience ne se suppriment pas : ils font "
                "partie de la preuve d'accès. Utilisez « Bloquer »."
            ))
        return super().unlink()
