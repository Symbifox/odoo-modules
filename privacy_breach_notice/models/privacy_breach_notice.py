"""L'avis de violation du mandataire au responsable (art. 18.3 P-39.1).

Vocabulaire tenu exprès : le mandataire avise une *violation* ou une *tentative*
de violation (18.3) ; le responsable, lui, qualifie un *incident* (3.6) dans son
propre registre. L'avis porte les faits dont le responsable a besoin pour remplir
ses obligations, jamais la conclusion sur le risque de préjudice sérieux : c'est
au responsable de la tirer, avec son RPRP (3.7).

La preuve tient en trois choses, et chacune a son champ :

* le moment de l'envoi et l'adresse visée (C-1.1, art. 31, al. 1 et 2) ;
* l'empreinte du PDF envoyé, figée à l'envoi et jamais recalculée (art. 6) ;
* l'accusé : le moment à la seconde chez nous, l'empreinte que la personne a vue,
  son nom, son titre et d'où elle répond (art. 31, al. 3).
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from markupsafe import Markup

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize
from odoo.tools.misc import clean_context

# Les champs qui font le contenu d'un avis. Ils se figent à l'envoi : un avis envoyé
# ne se corrige pas, on en envoie une mise à jour.
CONTENT_FIELDS = frozenset({
    "responsible_id", "officer_partner_id", "officer_email", "contract_ref",
    "notice_type", "stage", "nature", "circumstances", "cause",
    "occurred_from", "occurred_to", "occurred_approximate", "discovered_at",
    "pi_description", "pi_unknown_reason", "sensitivity_note",
    "subject_count", "subject_count_estimate", "subject_count_quebec",
    "encryption", "exfiltration", "assessment_facts",
    "measures_taken", "subject_measures",
    "foreign_authority_note", "law_enforcement", "law_enforcement_note", "third_parties_note",
    "report_period_from", "report_period_to", "report_line_ids",
    "contact_user_id", "company_id",
})

# Ce que l'envoi et l'accusé écrivent, en superutilisateur, et personne d'autre.
# 🔴 Aucune clé de contexte n'en dispense : n'importe quel client RPC peut en poser une.
SYSTEM_FIELDS = frozenset({
    "name", "version", "root_id", "previous_id",
    "state", "signer_id", "signer_title", "sent_at", "sent_to", "pdf_attachment_id",
    "content_sha256", "mail_id", "ack_token",
    "ack_at", "ack_name", "ack_title", "ack_sha256", "ack_ip", "ack_user_agent", "ack_channel",
    "ack_otp_hash", "ack_otp_expiry", "ack_otp_attempts", "ack_otp_last_sent", "ack_otp_count",
    "ack_otp_prev_hash", "ack_otp_prev_expiry",
})
OTP_FIELDS_RESET = {
    "ack_otp_hash": False, "ack_otp_expiry": False, "ack_otp_attempts": 0, "ack_otp_last_sent": False,
    "ack_otp_count": 0, "ack_otp_prev_hash": False, "ack_otp_prev_expiry": False,
}

# Le code à usage unique de l'accusé. Le lien du courriel est un porteur : il revient dans les
# réponses qui le citent, dans les `.eml` classés, dans les liens réécrits par les filtres. Le
# lien seul ne suffit donc pas : il faut aussi un code envoyé à l'adresse désignée AU MOMENT de
# l'accusé, et qu'un lien recopié n'apporte pas.
OTP_TTL = 30 * 60          # secondes de validité d'un code (la file d'envoi n'est pas immédiate)
OTP_RESEND = 60            # secondes avant de pouvoir en redemander un
OTP_MAX_ATTEMPTS = 5       # essais par code
OTP_MAX_CODES = 20         # codes par avis


# Ce que le gabarit de courriel rend à la place du jeton. Rendre le gabarit (aperçu,
# compositeur, `send_mail` vers soi-même) est permis à tout lecteur de l'avis : le vrai jeton
# n'est jamais rendu, il n'est substitué que dans le corps du courriel envoyé, réservé au système.
# À l'envoi, le marqueur est ALÉATOIRE : un marqueur fixe, glissé dans le nom d'un contact que
# le corps affiche, aurait reçu le vrai jeton dans une adresse choisie par un tiers.
ACK_PLACEHOLDER = "__lien-d-accuse__"


def _same(a, b):
    """Comparer deux secrets sans lever sur une chaîne non ASCII (sinon 500 contre 404 : un oracle)."""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class PrivacyBreachNotice(models.Model):
    _name = "privacy.breach.notice"
    _description = "Avis de violation au responsable"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"
    _rec_names_search = ["name", "responsible_id"]

    name = fields.Char(string="Numéro", readonly=True, copy=False, index=True, default="/")
    version = fields.Integer(string="Version", readonly=True, default=1, copy=False)
    root_id = fields.Many2one("privacy.breach.notice", string="Avis initial", readonly=True,
                              copy=False, index=True, ondelete="restrict")
    previous_id = fields.Many2one("privacy.breach.notice", string="Version précédente",
                                  readonly=True, copy=False, ondelete="restrict")
    is_latest = fields.Boolean(string="Version courante", compute="_compute_is_latest")
    company_id = fields.Many2one("res.company", string="Société", required=True, index=True,
                                 default=lambda self: self.env.company)
    state = fields.Selection([
        ("draft", "Brouillon"),
        ("sent", "Envoyé"),
        ("acknowledged", "Accusé reçu"),
    ], string="État", required=True, default="draft", readonly=True, copy=False, tracking=True)

    # --- À qui ------------------------------------------------------------------------
    responsible_id = fields.Many2one(
        "res.partner", string="Responsable", required=True, tracking=True, ondelete="restrict",
        domain="[('is_company', '=', True)]",
        help="L'organisation dont les renseignements personnels sont touchés, et pour qui "
             "nous exerçons le mandat.")
    officer_partner_id = fields.Many2one(
        "res.partner", string="Responsable de la protection des RP", tracking=True, ondelete="restrict",
        help="Repris de la désignation faite sur la fiche de l'organisation.")
    officer_email = fields.Char(
        string="Adresse de réception désignée", tracking=True,
        help="L'adresse que la fiche du responsable désigne, relue à l'envoi : l'avis part là et "
             "nulle part ailleurs. Un document y est présumé reçu dès qu'il y devient accessible "
             "(C-1.1, art. 31). Pour la changer, changez la désignation sur la fiche.")
    contract_ref = fields.Char(string="Référence du contrat")

    # --- Quoi -------------------------------------------------------------------------
    notice_type = fields.Selection([
        ("breach", "Violation"),
        ("attempt", "Tentative ciblée"),
        ("report", "Relevé périodique des tentatives"),
    ], string="Type", required=True, default="breach", tracking=True,
        help="L'article 18.3 vise « toute violation ou tentative de violation ». Une tentative "
             "dirigée vers les renseignements de ce client s'avise à l'unité ; les tentatives "
             "bloquées sur son infrastructure partent en relevé périodique.")
    stage = fields.Selection([
        ("initial", "Avis initial"),
        ("update", "Mise à jour"),
        ("final", "Avis final"),
    ], string="Étape", required=True, default="initial", tracking=True)
    nature = fields.Selection([
        ("unauthorized_access", "Accès non autorisé"),
        ("unauthorized_use", "Utilisation non autorisée"),
        ("unauthorized_disclosure", "Communication non autorisée"),
        ("loss", "Perte ou autre atteinte à la protection"),
    ], string="Nature", tracking=True,
        help="Les quatre cas de l'article 3.6, tels que nous les constatons. Le responsable "
             "décide seul s'il s'agit d'un incident de confidentialité.")
    circumstances = fields.Text(string="Circonstances")
    cause = fields.Text(string="Cause, si elle est connue")

    # --- Quand ------------------------------------------------------------------------
    occurred_from = fields.Date(string="Survenance, du")
    occurred_to = fields.Date(string="Survenance, au")
    occurred_approximate = fields.Boolean(string="Dates approximatives")
    discovered_at = fields.Datetime(string="Constaté par nous le")

    # --- Quels renseignements, combien de personnes -------------------------------------
    pi_description = fields.Text(
        string="Renseignements visés",
        help="Les catégories de renseignements, jamais les renseignements eux-mêmes.")
    pi_unknown_reason = fields.Text(string="Pourquoi on ne peut pas encore les décrire")
    sensitivity_note = fields.Text(string="Sensibilité")
    subject_count = fields.Integer(string="Personnes concernées")
    subject_count_estimate = fields.Boolean(string="Nombre approximatif")
    subject_count_quebec = fields.Integer(string="dont résidant au Québec")

    # --- Les faits utiles à l'évaluation (3.7), sans la conclusion -----------------------
    encryption = fields.Selection([
        ("yes", "Chiffrés"),
        ("partial", "Chiffrés en partie"),
        ("no", "Non chiffrés"),
        ("unknown", "Inconnu"),
    ], string="Chiffrement")
    exfiltration = fields.Selection([
        ("confirmed", "Sortie confirmée"),
        ("suspected", "Sortie soupçonnée"),
        ("none_found", "Aucune trace de sortie"),
        ("unknown", "Inconnu"),
    ], string="Sortie des données")
    assessment_facts = fields.Text(
        string="Autres faits utiles à l'évaluation",
        help="Récupération ou destruction des données, auteur connu, signes d'une "
             "utilisation malveillante. Des faits, pas une conclusion.")

    # --- Ce qui est fait ----------------------------------------------------------------
    measures_taken = fields.Text(string="Mesures prises ou prévues, datées")
    subject_measures = fields.Text(string="Mesures que les personnes pourraient prendre")
    foreign_authority_note = fields.Text(string="Autorités hors Québec avisées")
    law_enforcement = fields.Boolean(string="Corps policier en cause")
    law_enforcement_note = fields.Text(
        string="Précisions sur l'enquête",
        help="Aviser les personnes peut être reporté tant que cela entraverait une enquête "
             "(art. 3.5, al. 3) : le responsable doit le savoir.")
    third_parties_note = fields.Text(
        string="Tiers qui peuvent diminuer le risque",
        help="Par exemple une institution financière (art. 3.5, al. 2).")

    # --- Le relevé périodique -----------------------------------------------------------
    report_period_from = fields.Date(string="Période, du")
    report_period_to = fields.Date(string="Période, au")
    report_line_ids = fields.One2many("privacy.breach.notice.report.line", "notice_id",
                                      string="Tentatives bloquées", copy=True)

    # --- Qui répond, qui signe ----------------------------------------------------------
    contact_user_id = fields.Many2one("res.users", string="Personne à joindre",
                                      default=lambda self: self.env.user)
    signer_id = fields.Many2one("res.users", string="Signé par", readonly=True, copy=False)
    signer_title = fields.Char(string="Titre du signataire", readonly=True, copy=False)

    # --- L'envoi ------------------------------------------------------------------------
    sent_at = fields.Datetime(string="Envoyé le", readonly=True, copy=False, tracking=True)
    sent_to = fields.Char(string="Envoyé à", readonly=True, copy=False)
    pdf_attachment_id = fields.Many2one("ir.attachment", string="Avis envoyé (PDF)",
                                        readonly=True, copy=False, index=True)
    content_sha256 = fields.Char(string="Empreinte SHA-256", readonly=True, copy=False,
                                 help="L'empreinte du PDF envoyé, figée à l'envoi.")
    mail_id = fields.Many2one("mail.mail", string="Courriel", readonly=True, copy=False)
    mail_state = fields.Selection(related="mail_id.state", string="État du courriel")
    ack_token = fields.Char(readonly=True, copy=False, groups="base.group_system")

    # --- L'accusé -----------------------------------------------------------------------
    ack_at = fields.Datetime(string="Accusé reçu le", readonly=True, copy=False, tracking=True)
    ack_name = fields.Char(string="Accusé par", readonly=True, copy=False)
    ack_title = fields.Char(string="Titre", readonly=True, copy=False)
    ack_sha256 = fields.Char(string="Empreinte accusée", readonly=True, copy=False)
    ack_ip = fields.Char(string="Adresse IP", readonly=True, copy=False)
    ack_user_agent = fields.Char(string="Navigateur", readonly=True, copy=False)
    ack_channel = fields.Selection([("link", "Lien de l'avis")], string="Canal de l'accusé",
                                   readonly=True, copy=False)
    ack_otp_hash = fields.Char(readonly=True, copy=False, groups="base.group_system")
    ack_otp_expiry = fields.Datetime(readonly=True, copy=False, groups="base.group_system")
    ack_otp_attempts = fields.Integer(readonly=True, copy=False, groups="base.group_system")
    ack_otp_last_sent = fields.Datetime(readonly=True, copy=False, groups="base.group_system")
    ack_otp_count = fields.Integer(readonly=True, copy=False, groups="base.group_system")
    ack_otp_prev_hash = fields.Char(readonly=True, copy=False, groups="base.group_system")
    ack_otp_prev_expiry = fields.Datetime(readonly=True, copy=False, groups="base.group_system")

    # Pas de contrainte SQL : deux mandataires numérotent de la même façon (AV-2026-0001),
    # et un avis reçu de l'un ne doit pas heurter un avis émis par l'autre. L'unicité se
    # juge dans une portée que les modules qui reçoivent des avis élargissent.
    @api.constrains("name", "version", "company_id")
    def _check_version_unique(self):
        for notice in self:
            if not notice.name or notice.name == "/":
                continue
            if self.sudo().search_count([
                    ("id", "!=", notice.id), ("name", "=", notice.name), ("version", "=", notice.version),
                    ("company_id", "=", notice.company_id.id)] + notice._version_scope_domain()):
                raise ValidationError(_("L'avis %s a déjà cette version.", notice.display_name))

    def _version_scope_domain(self):
        """La portée où un numéro d'avis est unique. Ici, toute la société."""
        return []

    # --- Calculs et affichage ---------------------------------------------------------
    @api.depends("name", "version")
    def _compute_display_name(self):
        for notice in self:
            notice.display_name = (f"{notice.name} v{notice.version}"
                                   if notice.name and notice.name != "/" else _("Nouvel avis"))

    def _compute_is_latest(self):
        later = self.search([("previous_id", "in", self.ids)])
        replaced = set(later.mapped("previous_id").ids)
        for notice in self:
            notice.is_latest = notice.id not in replaced

    @api.onchange("responsible_id")
    def _onchange_responsible_id(self):
        # L'onchange ne contrôle aucun accès : sans le rôle, on ne montre pas la désignation.
        if self.responsible_id and self.env.user.has_group("privacy_consent.group_privacy_user"):
            officer, email = self.responsible_id._privacy_officer_address()
            self.officer_partner_id = officer
            self.officer_email = email

    @api.constrains("officer_email")
    def _check_officer_email(self):
        for notice in self:
            if notice.officer_email and not email_normalize(notice.officer_email):
                raise ValidationError(_("« %s » n'est pas une adresse courriel valide.",
                                        notice.officer_email))

    @api.constrains("responsible_id", "company_id", "root_id")
    def _check_same_responsible_as_root(self):
        """Une mise à jour porte les faits du même client : changer de responsable en brouillon
        enverrait « AV-…-v2 », avec les faits du client A, chez le client B."""
        for notice in self.filtered("root_id"):
            if (notice.responsible_id != notice.root_id.responsible_id
                    or notice.company_id != notice.root_id.company_id):
                raise ValidationError(_("Une mise à jour garde le responsable et la société de l'avis initial."))

    @api.constrains("occurred_from", "occurred_to", "report_period_from", "report_period_to")
    def _check_periods(self):
        for notice in self:
            if notice.occurred_from and notice.occurred_to and notice.occurred_to < notice.occurred_from:
                raise ValidationError(_("La fin de la survenance précède son début."))
            if (notice.report_period_from and notice.report_period_to
                    and notice.report_period_to < notice.report_period_from):
                raise ValidationError(_("La fin de la période du relevé précède son début."))

    # --- ORM --------------------------------------------------------------------------
    # Les deux ensembles s'étendent par méthode : un module qui ajoute un champ de contenu
    # ou de preuve (le pont d'hébergement, la fédération) l'y ajoute.
    def _breach_content_fields(self):
        return CONTENT_FIELDS

    def _breach_system_fields(self):
        return SYSTEM_FIELDS

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]  # ne pas muter le dictionnaire de l'appelant
        system = self._breach_system_fields()
        safe = {"name": "/", "version": 1, "state": "draft"}
        for vals in vals_list:
            if not self.env.su and system.intersection(vals):
                raise UserError(_("Le numéro, la version, l'envoi et l'accusé se consignent seuls."))
            # 🔴 La garde ne voit que `vals` ; Odoo y ajoute ensuite les défauts du contexte
            # (`default_state`, `default_received_at`…) et d'`ir.default`, que tout usager pose
            # pour lui-même. Une valeur explicite et sûre pour chaque champ réservé absent ferme
            # cette porte, ET en superutilisateur : une copie en sudo (la mise à jour) hérite du
            # contexte et des défauts personnels de l'appelant. Ce qu'un chemin sudo écrit
            # exprès (la réception fédérée) reste, puisque la clé est présente.
            for name in system:
                if name in vals or (self._fields[name].groups and not self.env.su):
                    continue  # `ack_token` hors sudo : régénéré à chaque envoi de toute façon
                vals[name] = safe.get(name, False)
            if vals.get("responsible_id") and "officer_email" not in vals:
                officer, email = self.env["res.partner"].browse(vals["responsible_id"])._privacy_officer_address()
                vals.setdefault("officer_partner_id", officer.id or False)
                vals["officer_email"] = email or False
        notices = super().create(vals_list)
        for notice in notices.filtered(lambda n: n.name == "/"):
            notice.sudo().name = self.env["ir.sequence"].sudo().with_company(notice.company_id).next_by_code(
                "privacy.breach.notice") or "/"
        return notices

    def write(self, vals):
        if vals.get("responsible_id") and "officer_email" not in vals:
            officer, email = self.env["res.partner"].browse(vals["responsible_id"])._privacy_officer_address()
            vals = dict(vals, officer_partner_id=officer.id or False, officer_email=email or False)
        if self._breach_content_fields().intersection(vals):
            sent = self.filtered(lambda n: n.state != "draft")
            if sent:
                raise UserError(_(
                    "L'avis %s est déjà envoyé : son contenu ne change plus. "
                    "Créez une mise à jour.", sent[0].display_name))
        if self._breach_system_fields().intersection(vals) and not self.env.su:
            raise UserError(_("Le numéro, la version, l'envoi et l'accusé se consignent seuls."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda n: n.state != "draft"):
            raise UserError(_("Un avis envoyé se conserve : seul un brouillon se supprime."))
        return super().unlink()

    # --- Les versions -----------------------------------------------------------------
    def action_new_version(self):
        """Une mise à jour : la même numérotation, la version suivante, le contenu repris."""
        self.ensure_one()
        if self.state == "draft":
            raise UserError(_("Un brouillon se corrige directement."))
        self.check_access("write")
        self.browse().check_access("create")
        root = self.root_id or self
        pending = self.search([("root_id", "=", root.id), ("state", "=", "draft")], limit=1)
        if not pending:
            last = self.search([("name", "=", self.name), ("company_id", "=", self.company_id.id)]
                               + self._version_scope_domain(), order="version desc", limit=1)
            # La mise à jour part de la DERNIÈRE version, pas de celle sur laquelle on a cliqué,
            # et relit la désignation du responsable, qui a pu changer depuis.
            officer, email = last.responsible_id._privacy_officer_address()
            pending = last.sudo().with_context(clean_context(self.env.context)).copy({
                "name": last.name, "version": last.version + 1, "stage": "update",
                "root_id": root.id, "previous_id": last.id,
                "officer_partner_id": officer.id or False,
                "officer_email": email or False,
            }).sudo(False)
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": pending.id,
            "view_mode": "form", "target": "current",
        }

    # --- L'envoi ----------------------------------------------------------------------
    def _check_ready_to_send(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("L'avis %s est déjà envoyé.", self.display_name))
        responsible = self.responsible_id
        if not responsible.is_company or responsible.commercial_partner_id != responsible:
            raise UserError(_("Le responsable d'un avis est une organisation, et sa propre maison : "
                              "%s ne l'est pas (ou plus). Vérifiez sa fiche avant d'envoyer.", responsible.display_name))
        missing = []
        if not email_normalize(self.officer_email or ""):
            missing.append(_("l'adresse de réception désignée"))
        if self.notice_type == "report":
            if not (self.report_period_from and self.report_period_to):
                missing.append(_("la période du relevé"))
            if not self.report_line_ids:
                missing.append(_("au moins une ligne de tentatives bloquées"))
        else:
            if not (self.circumstances or "").strip():
                missing.append(_("les circonstances"))
            if not self.discovered_at:
                missing.append(_("le moment où nous l'avons constaté"))
            if self.notice_type == "breach":
                if not self.nature:
                    missing.append(_("la nature"))
                if not ((self.pi_description or "").strip() or (self.pi_unknown_reason or "").strip()):
                    missing.append(_("les renseignements visés, ou pourquoi on ne peut pas encore les décrire"))
        if missing:
            raise UserError(_("Avant d'envoyer %(avis)s, il manque : %(liste)s.",
                              avis=self.display_name, liste=", ".join(missing)))

    def action_send(self):
        """Signer, figer le PDF et son empreinte, l'envoyer à l'adresse désignée."""
        # Les créations qui suivent tournent en sudo et héritent du contexte de l'appelant :
        # un `default_headers` (copie cachée), `default_state` (faux « envoyé ») ou
        # `default_public` (PDF public) y passerait. Contexte nettoyé, et valeurs explicites
        # pour ce qui compte, parce qu'un `ir.default` personnel passerait encore.
        self = self.with_context(clean_context(self.env.context))
        self.check_access("write")  # un lecteur n'envoie pas, quel que soit le chemin
        template = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        for notice in self:
            # L'adresse est celle que la fiche du responsable désigne AU MOMENT de l'envoi, pas une
            # adresse retapée dans l'avis : la confirmation dit « adresse de réception désignée ».
            officer, designated = notice.responsible_id._privacy_officer_address()
            notice.write({"officer_partner_id": officer.id or False, "officer_email": designated or False})
            notice._check_ready_to_send()
            user = self.env.user
            email = email_normalize(notice.officer_email)
            notice.sudo().write({
                "signer_id": user.id,
                "signer_title": user.partner_id.function or False,
                "sent_at": fields.Datetime.now(),
                "sent_to": email,
            })
            pdf, _ctype = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
                "privacy_breach_notice.action_report_breach_notice", notice.ids)
            digest = hashlib.sha256(pdf).hexdigest()
            attachment = self.env["ir.attachment"].sudo().create({
                "name": f"{notice.name}-v{notice.version}.pdf",
                "raw": pdf, "mimetype": "application/pdf", "type": "binary",
                "res_model": notice._name, "res_id": notice.id, "res_field": False,
                "public": False, "access_token": False,
            })
            notice.sudo().write(dict(
                OTP_FIELDS_RESET, pdf_attachment_id=attachment.id, content_sha256=digest,
                ack_token=secrets.token_urlsafe(32)))
            marker = f"__accuse-{secrets.token_hex(16)}__"
            mail_id = template.with_context(breach_ack_marker=marker).send_mail(notice.id, email_values={
                **notice._breach_neutral_recipients(),
                "email_to": email, "email_cc": False, "recipient_ids": [(6, 0, [])],
                "headers": False, "state": "outgoing", "scheduled_date": False,
                "attachment_ids": [(6, 0, attachment.ids)], "auto_delete": False,
                "record_company_id": notice.company_id.id,
            })
            # 🔴 Le gabarit n'a rendu qu'un marqueur ; le vrai lien d'accusé n'entre que dans le corps
            # envoyé (`mail.mail.body_html`, réservé au système). Le message lisible dans le chatter,
            # lui, garde le marqueur : aucun lecteur de l'avis ne peut accuser à la place du client.
            mail = self.env["mail.mail"].sudo().browse(mail_id)
            if (mail.body_html or "").count(marker) != 1:
                # Un gabarit modifié qui ne rend plus le lien : l'avis ne part pas sans moyen
                # d'en accuser réception, plutôt que de partir et rester « envoyé » en silence.
                raise UserError(_("Le gabarit du courriel doit rendre le lien d'accusé de réception une "
                                  "fois, et une seule : l'avis n'est pas envoyé."))
            mail.write({"body_html": mail.body_html.replace(marker, notice.sudo().ack_token)})
            notice.sudo().write({"mail_id": mail_id, "state": "sent"})
            notice.message_post(
                body=Markup(_("<p>Avis envoyé à <b>%(to)s</b>, signé par %(who)s.<br/>"
                              "Empreinte SHA-256 du PDF : <code>%(sha)s</code></p>")) % {
                    "to": email, "who": user.name, "sha": digest},
                message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def _pdf_intact(self):
        """Le PDF conservé est-il toujours celui dont on a figé l'empreinte ?"""
        self.ensure_one()
        notice = self.sudo()
        if not (notice.pdf_attachment_id and notice.content_sha256):
            return False
        digest = hashlib.sha256(notice.pdf_attachment_id.raw or b"").hexdigest()
        return _same(digest, notice.content_sha256)

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        """Ne notifier que les destinataires nommés du message et les abonnés de l'avis. Un module
        de transfert (`mail_partner_forwarding`) ajoutait le « transfert » de chaque destinataire,
        une adresse que le rôle « Création de contacts » pose sur n'importe quelle fiche, et lui
        envoyait jusqu'aux notes internes."""
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

    def _message_receive_bounce(self, email, partner):
        """Un rebond se reçoit par la passerelle, en système ; un faux rebond déposé par RPC ne
        marque pas le courriel officiel comme rejeté."""
        self.env["mail.message"]._breach_check_register(self.ids)
        return super()._message_receive_bounce(email, partner)

    def _breach_neutral_recipients(self):
        """Ni copie ni copie cachée, même par un défaut personnel (`ir.default`) du signataire."""
        fields_ = self.env["mail.mail"]._fields
        vals = {f: [(5, 0, 0)] for f in ("recipient_cc_ids", "recipient_bcc_ids") if f in fields_}
        vals.update({f: False for f in ("email_bcc",) if f in fields_})
        return vals

    def message_update(self, msg_dict, update_vals=None):
        """La passerelle bascule en OdooBot APRÈS cet appel, qui porte encore l'appelant : la
        réception (fetchmail, en système) passe ; `message_process` appelé par RPC, que tout
        utilisateur authentifié peut faire, ne fait pas entrer un faux courriel au fil."""
        self.env["mail.message"]._breach_check_register(self.ids)
        return super().message_update(msg_dict, update_vals=update_vals)

    def _message_update_content(self, message, *args, **kwargs):
        """Le fil est un registre : son auteur même ne réécrit ni ne vide une note (« Avis envoyé…
        empreinte », « Codes réarmés »), y compris pendant le délai de `mail_post_defer`."""
        if not self.env["mail.message"]._breach_register_actor_is_system():
            raise UserError(_("Un message du fil d'un avis de violation ne se modifie pas : ajoutez une "
                              "note qui corrige."))
        return super()._message_update_content(message, *args, **kwargs)

    def message_post(self, **kwargs):
        """Une réponse qui cite le courriel porte le lien d'accusé : il ne doit pas entrer dans le
        fil, que tout lecteur de l'avis lit et que la fédération transmet."""
        if len(self) == 1 and kwargs.get("body"):
            token = self.sudo().ack_token
            body = kwargs["body"]
            if token and token in str(body):
                kwargs["body"] = Markup(str(body).replace(token, "…")) if isinstance(body, Markup) \
                    else str(body).replace(token, "…")
        return super().message_post(**kwargs)

    def _ack_url(self):
        """L'adresse d'accusé, avec un MARQUEUR à la place du jeton : voir ACK_PLACEHOLDER."""
        self.ensure_one()
        marker = self.env.context.get("breach_ack_marker") or ACK_PLACEHOLDER
        return f"{self.get_base_url()}/privacy/breach/{self.id}/{marker}"

    def _check_ack_token(self, token):
        self.ensure_one()
        return _same(self.sudo().ack_token, token)

    # --- Le code à usage unique ---------------------------------------------------------
    def _ack_code_hash(self, code):
        return hashlib.sha256(f"{self.sudo().ack_token or ''}:{code}".encode()).hexdigest()

    def _ack_masked_address(self):
        local, _sep, domain = (self.sudo().sent_to or "").partition("@")
        return f"{local[:1]}***@{domain}" if domain else ""

    def _ack_send_code(self):
        """Envoyer un code à l'adresse où l'avis est parti. Rend un message pour la page."""
        self.ensure_one()
        notice = self.sudo()
        if notice.state != "sent":
            return _("Cet avis n'attend plus d'accusé.")
        now = fields.Datetime.now()
        if notice.ack_otp_last_sent and (now - notice.ack_otp_last_sent).total_seconds() < OTP_RESEND:
            return _("Un code vient de partir : attendez une minute avant d'en demander un autre.")
        if notice.ack_otp_count >= OTP_MAX_CODES:
            return _("Trop de codes demandés pour cet avis : communiquez avec la personne à joindre.")
        code = f"{secrets.randbelow(10 ** 6):06d}"
        # Le code d'avant reste bon jusqu'à son expiration : une personne qui reclique pendant que
        # le premier courriel tarde ne doit pas se voir refuser celui qu'elle finit par recevoir.
        notice.write({
            "ack_otp_prev_hash": notice.ack_otp_hash, "ack_otp_prev_expiry": notice.ack_otp_expiry,
            "ack_otp_hash": notice._ack_code_hash(code), "ack_otp_attempts": 0,
            "ack_otp_expiry": now + timedelta(seconds=OTP_TTL), "ack_otp_last_sent": now,
            "ack_otp_count": notice.ack_otp_count + 1,
        })
        # Le code n'est que dans `mail.mail.body_html`, réservé au système. Le courriel reste
        # rattaché à l'avis et n'est pas supprimé : son suivi (mail_tracking) suit alors les droits
        # de l'avis au lieu de devenir un orphelin lisible par tous, et son message lisible porte
        # un texte neutre, jamais le code.
        mail = self.env["mail.mail"].sudo().with_context(clean_context(self.env.context)).create({
            "subject": _("Code pour accuser réception de l'avis %s", notice.display_name),
            "email_from": notice.company_id.partner_id.email_formatted or notice.company_id.email or False,
            **notice._breach_neutral_recipients(),
            "email_to": notice.sent_to, "email_cc": False, "recipient_ids": [(6, 0, [])],
            "reply_to": notice.contact_user_id.email_formatted or False,
            "body_html": Markup(_("<p>Votre code pour accuser réception de l'avis %(avis)s : "
                                  "<b>%(code)s</b></p><p>Il est valable trente minutes et ne sert qu'une "
                                  "fois.</p>")) % {"avis": notice.display_name, "code": code},
            "model": notice._name, "res_id": notice.id, "auto_delete": False, "state": "outgoing",
            "headers": False, "message_type": "email_outgoing", "record_company_id": notice.company_id.id,
        })
        # Le système réécrit le message lisible (le registre refuse cette réécriture à tout autre).
        message = mail.mail_message_id.with_user(SUPERUSER_ID)
        message.write({"body": Markup(_("<p>Code d'accusé envoyé à %s.</p>")) % notice._ack_masked_address()})
        message.flush_recordset(["body"])
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()  # envoyer maintenant, pas au prochain passage du cron
        return _("Un code vient d'être envoyé à %s.", notice._ack_masked_address())

    def _ack_verify_code(self, code):
        """Vérifier le code. Rend (vrai/faux, message). N'élève pas : le compteur d'essais doit
        rester écrit même quand l'accusé échoue ensuite."""
        self.ensure_one()
        notice = self.sudo()
        code = (code or "").strip()
        if not code:
            return False, _("Demandez un code, puis saisissez-le.")
        now = fields.Datetime.now()
        valides = [h for h, exp in ((notice.ack_otp_hash, notice.ack_otp_expiry),
                                    (notice.ack_otp_prev_hash, notice.ack_otp_prev_expiry))
                   if h and exp and exp >= now]
        if not valides:
            return False, _("Ce code a expiré : demandez-en un nouveau.")
        if notice.ack_otp_attempts >= OTP_MAX_ATTEMPTS:
            return False, _("Trop d'essais pour ce code : demandez-en un nouveau.")
        digest = notice._ack_code_hash(code)
        if not any(_same(digest, h) for h in valides):
            notice.write({"ack_otp_attempts": notice.ack_otp_attempts + 1})
            return False, _("Code incorrect.")
        notice.write({"ack_otp_hash": False, "ack_otp_expiry": False,
                      "ack_otp_prev_hash": False, "ack_otp_prev_expiry": False})
        return True, ""

    def action_reset_ack_codes(self):
        """Réarmer l'accusé par code : quelqu'un qui détient le lien a pu épuiser les codes."""
        self.check_access("write")
        for notice in self.filtered(lambda n: n.state == "sent"):
            notice.sudo().write(OTP_FIELDS_RESET)
            notice.message_post(body=_("Codes d'accusé réarmés."), message_type="comment",
                                subtype_xmlid="mail.mt_note")
        return True

    # --- L'accusé ---------------------------------------------------------------------
    def _record_ack(self, name, title, sha256, channel, ip=None, user_agent=None):
        """Consigner l'accusé. Rend False si l'avis était déjà accusé.

        L'empreinte accusée doit être celle du PDF envoyé : un accusé ne vaut que pour
        le texte que la personne a eu sous les yeux.
        """
        self.ensure_one()
        notice = self.sudo()
        if notice.state == "acknowledged":
            return False
        if notice.state != "sent":
            raise UserError(_("Cet avis n'a pas été envoyé."))
        name = (name or "").strip()[:120]
        if not name:
            raise UserError(_("Le nom de la personne qui accuse réception est requis."))
        if not _same(sha256, notice.content_sha256):
            raise UserError(_("L'empreinte accusée n'est pas celle de l'avis envoyé."))
        if not notice._pdf_intact():
            raise UserError(_("Le PDF conservé ne correspond plus à l'avis envoyé : l'accusé ne peut "
                              "pas porter sur un texte qu'on ne peut plus montrer."))
        now = fields.Datetime.now()
        notice.write({
            "ack_at": now, "ack_name": name, "ack_title": (title or "").strip()[:120] or False,
            "ack_sha256": sha256, "ack_ip": (ip or "")[:64] or False,
            "ack_user_agent": (user_agent or "")[:255] or False,
            "ack_channel": channel, "state": "acknowledged",
        })
        notice.message_post(
            body=Markup(_("<p><b>%(name)s</b>%(title)s a accusé réception de l'avis le %(when)s "
                          "(heure du serveur, UTC).<br/>Empreinte accusée : <code>%(sha)s</code></p>")) % {
                "name": name, "title": f", {notice.ack_title}," if notice.ack_title else "",
                "when": fields.Datetime.to_string(now), "sha": sha256},
            message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=self.env.ref("base.partner_root").id,
            partner_ids=notice.signer_id.partner_id.ids)
        return True


class PrivacyBreachNoticeReportLine(models.Model):
    _name = "privacy.breach.notice.report.line"
    _description = "Ligne du relevé des tentatives"
    _order = "notice_id, sequence, id"

    notice_id = fields.Many2one("privacy.breach.notice", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    category = fields.Char(string="Catégorie", required=True,
                           help="Par exemple : connexions refusées à un compte du client, "
                                "exploitation tentée sur son instance.")
    count = fields.Integer(string="Nombre")
    note = fields.Char(string="Précision")

    def write(self, vals):
        notices = self.notice_id
        if vals.get("notice_id"):
            notices |= self.env["privacy.breach.notice"].browse(vals["notice_id"])
        if not self.env.su and notices.filtered(lambda n: n.state != "draft"):
            raise UserError(_("Le relevé d'un avis envoyé ne change plus."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        notices = self.env["privacy.breach.notice"].browse(
            [v.get("notice_id") for v in vals_list if v.get("notice_id")])
        if not self.env.su and notices.filtered(lambda n: n.state != "draft"):
            raise UserError(_("Le relevé d'un avis envoyé ne change plus."))
        lines = super().create(vals_list)
        # `default_notice_id` du contexte ne passe pas par `vals` : on juge le résultat.
        if not self.env.su and lines.notice_id.filtered(lambda n: n.state != "draft"):
            raise UserError(_("Le relevé d'un avis envoyé ne change plus."))
        return lines

    def unlink(self):
        if not self.env.su and self.notice_id.filtered(lambda n: n.state != "draft"):
            raise UserError(_("Le relevé d'un avis envoyé ne change plus."))
        return super().unlink()
