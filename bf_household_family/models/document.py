"""Les papiers de la famille : passeports, cartes, permis, polices, et leur échéance.

Classe PRIVÉE, en clair au serveur,
partage exprès au foyer (comme le budget). Aucun champ NAS. Rappel d'un passeport
au moins six mois avant l'échéance : le passeport canadien d'un enfant de moins de
16 ans dure cinq ans au plus et ne se renouvelle pas (nouvelle demande), et
plusieurs pays exigent trois à six mois de validité à l'entrée.

Qui voit un papier : son titulaire, ou les parents de l'enfant qui en est titulaire
(lecture et écriture), et les personnes avec qui il est partagé (lecture seule).
Aucune exception pour l'administration : la règle est la même pour tous, comme
celles de Healthy Fox.

🔴 Ce que la règle ne garde pas, et que ce fichier garde :

1. Pas de fil, pas d'activité, pas d'abonné. Les six portes d'un fil Odoo (voir
   bf_household_inventory) n'existent donc pas ici. Le rappel part en To-do
   privée, visible des seuls titulaires (règle du cœur sur les tâches sans projet,
   gestionnaires de projet compris).
2. Le nom. Odoo lit ``display_name`` en sudo là où la règle ne regarde pas (le
   message d'erreur d'accès en mode debug). Pour qui n'y a pas accès, un papier
   s'appelle « Papier privé ».
3. Le titulaire. La règle ne se lit que sur l'état d'AVANT une écriture : on ne
   confie un papier qu'à soi-même ou à son enfant, et on ne le partage qu'avec
   une personne du foyer.
4. Les pièces jointes. Une photo déposée sur une fiche neuve naît sans fiche : elle
   est rattachée à l'enregistrement, et on ne rattache que les siennes.
"""
import re
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .common import is_household_member, neutral_env

DOC_TYPES = [
    ("passport", "Passport"),
    ("health_card", "Health insurance card"),
    ("driver_licence", "Driver's licence"),
    ("birth_certificate", "Birth certificate"),
    ("citizenship", "Citizenship certificate"),
    ("pr_card", "Permanent resident card"),
    ("visa", "Visa or permit"),
    ("insurance", "Insurance policy"),
    ("vaccination", "Vaccination record"),
    ("other", "Other"),
]

#: Le délai du rappel, en jours avant l'échéance. Passeport et carte de résident
#: permanent : six mois (183 jours), le minimum arbitré.
REMINDER_DAYS = {
    "passport": 183,
    "pr_card": 183,
    "visa": 90,
    "health_card": 60,
    "driver_licence": 60,
    "insurance": 30,
}
REMINDER_DEFAULT = 30


#: Neuf chiffres, groupés ou non par trois, n'importe où dans un texte.
SIN_IN_TEXT = re.compile(r"(?<!\d)\d{3}[\s.\-–/_]*\d{3}[\s.\-–/_]*\d{3}(?!\d)")


def text_holds_sin(texte):
    """Un NAS dans un texte : seul ou glissé dans une phrase (notes, émetteur, numéro)."""
    return looks_like_sin(texte) or any(
        looks_like_sin(m.group(0)) for m in SIN_IN_TEXT.finditer(texte or ""))


def looks_like_sin(number):
    """Neuf chiffres qui passent la clé de Luhn : la forme d'un numéro d'assurance
    sociale. Service Canada conseille de le garder sous clé et de ne le donner que
    quand la loi l'exige : Symbifox ne le garde pas."""
    chiffres = re.sub(r"[\s.\-–/_]", "", number or "")
    if not re.fullmatch(r"\d{9}", chiffres):
        return False
    total = 0
    for i, c in enumerate(chiffres):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class HouseholdDocument(models.Model):
    _name = "bf.household.document"
    _description = "Family paper"
    _order = "expiry_date asc nulls last, id"

    name = fields.Char(string="Paper", compute="_compute_name", store=True)
    doc_type = fields.Selection(DOC_TYPES, string="Type", required=True, default="passport")
    holder_user_id = fields.Many2one(
        "res.users", string="Holder", index=True, ondelete="cascade",
        default=lambda self: self.env.user, domain="[('share', '=', False)]")
    child_id = fields.Many2one(
        "bf.household.child", string="Child", index=True, ondelete="cascade",
        help="A child's papers are held by their parents.")
    holder_name = fields.Char(string="Holder name", compute="_compute_holder_name")

    number = fields.Char(string="Number")
    issuing_authority = fields.Char(string="Issued by", help="Country, province or organisation.")
    issue_date = fields.Date(string="Issued on")
    expiry_date = fields.Date(string="Expires on")
    reminder_days = fields.Integer(
        string="Remind me (days before)", compute="_compute_reminder_days", store=True, readonly=False,
        help="A passport: at least six months, many countries require that much validity at entry.")
    reminder_date = fields.Date(string="Reminder on", compute="_compute_reminder_date", store=True)
    reminder_task_id = fields.Many2one("project.task", string="Reminder", copy=False, readonly=True)
    status = fields.Selection(
        [("none", "No expiry"), ("valid", "Valid"), ("soon", "Renew soon"), ("expired", "Expired")],
        string="Status", compute="_compute_status")

    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_household_document_attachment_rel", "document_id", "attachment_id",
        string="Photos and scans")
    notes = fields.Text(string="Notes")
    share_user_ids = fields.Many2many(
        "res.users", "bf_household_document_share_rel", "document_id", "user_id",
        string="Shared with", domain="[('share', '=', False)]",
        help="Read-only access for these members of the household.")

    is_owner = fields.Boolean(string="I hold this paper", compute="_compute_is_owner")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("doc_type", "holder_user_id", "child_id", "child_id.name", "holder_user_id.name")
    def _compute_name(self):
        types = dict(self._fields["doc_type"]._description_selection(self.env))
        for rec in self:
            lu = rec.sudo()
            qui = lu.child_id.name or lu.holder_user_id.name or ""
            rec.name = f"{types.get(rec.doc_type, '')} · {qui}" if qui else types.get(rec.doc_type, "")

    def _compute_holder_name(self):
        for rec in self:
            lu = rec.sudo()
            rec.holder_name = lu.child_id.name or lu.holder_user_id.name

    @api.depends("doc_type")
    def _compute_reminder_days(self):
        for rec in self:
            rec.reminder_days = REMINDER_DAYS.get(rec.doc_type, REMINDER_DEFAULT)

    @api.depends("expiry_date", "reminder_days")
    def _compute_reminder_date(self):
        for rec in self:
            rec.reminder_date = rec.expiry_date and rec.expiry_date - timedelta(days=max(rec.reminder_days, 0))

    def _compute_status(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.expiry_date:
                rec.status = "none"
            elif rec.expiry_date < today:
                rec.status = "expired"
            elif rec.reminder_date and rec.reminder_date <= today:
                rec.status = "soon"
            else:
                rec.status = "valid"

    @api.depends_context("uid")
    @api.depends("holder_user_id", "child_id")
    def _compute_is_owner(self):
        for rec in self:
            rec.is_owner = self.env.uid in rec.sudo()._bf_owners().ids

    @api.depends("name")
    @api.depends_context("uid")
    def _compute_display_name(self):
        """Garde 2 : « Papier privé » pour qui n'y a pas accès. ``sudo()`` garde
        ``env.uid`` : le calcul reconnaît qui lit, même quand Odoo lit en sudo."""
        uid = self.env.uid
        for rec, lu in zip(self, self.sudo()):
            lecteurs = lu._bf_owners() | lu.share_user_ids
            visible = uid == SUPERUSER_ID or uid in lecteurs.ids
            rec.display_name = lu.name if visible else self.env._("Private paper")

    def _bf_owners(self):
        """Le titulaire, ou les parents de l'enfant titulaire."""
        self.ensure_one()
        lu = self.sudo()
        if lu.child_id:
            return lu.child_id.primary_parent_id | lu.child_id.second_parent_id
        return lu.holder_user_id

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("holder_user_id", "child_id")
    def _check_holder(self):
        for rec in self.sudo():
            if bool(rec.holder_user_id) == bool(rec.child_id):
                raise ValidationError(_("A paper belongs either to a member of the household or to a child."))
            if rec.holder_user_id and not is_household_member(rec.holder_user_id):
                raise ValidationError(_("A paper belongs to an active member of the household."))

    @api.constrains("number", "notes", "issuing_authority")
    def _check_no_sin(self):
        # Le numéro, et les textes libres : « Aucun NAS » vaut pour toute la fiche
        # (relecture adverse du 2026-10-09).
        for rec in self:
            if any(text_holds_sin(v) for v in (rec.number, rec.notes, rec.issuing_authority)):
                raise ValidationError(_(
                    "This looks like a social insurance number. Symbifox does not keep it: "
                    "Service Canada advises keeping it locked away and giving it only when the law "
                    "requires it."))

    @api.constrains("share_user_ids", "holder_user_id", "child_id")
    def _check_share(self):
        for rec in self.sudo():
            for user in rec.share_user_ids:
                if not is_household_member(user):
                    raise ValidationError(_("A paper is shared only with members of the household."))
                if user in rec._bf_owners():
                    raise ValidationError(_("%s already holds this paper.", user.name))

    @api.constrains("issue_date", "expiry_date")
    def _check_dates(self):
        for rec in self:
            if rec.issue_date and rec.expiry_date and rec.expiry_date < rec.issue_date:
                raise ValidationError(_("A paper cannot expire before it is issued."))

    @api.constrains("attachment_ids")
    def _check_attachments(self):
        for rec in self.sudo():
            for piece in rec.attachment_ids:
                if piece.res_model != self._name or piece.res_id not in (0, rec.id):
                    raise ValidationError(_("Only photos and scans added to this paper can be attached to it."))

    def copy(self, default=None):
        """Dupliquer un papier, c'est en publier le contenu : seuls ses titulaires le
        font, jamais une personne qui le lit par un partage (relecture adverse)."""
        if not self.env.su:
            for rec in self:
                if self.env.uid not in rec._bf_owners().ids:
                    raise AccessError(_("You hold papers only for yourself or for your own child."))
        return super().copy(default)

    def _bf_check_holder_is_mine(self):
        """Garde 3 : on ne confie un papier qu'à soi-même, ou à son enfant."""
        if self.env.su:
            return
        for rec in self.sudo():
            if rec.child_id:
                if self.env.uid not in (rec.child_id.primary_parent_id | rec.child_id.second_parent_id).ids:
                    raise AccessError(_("You hold papers only for yourself or for your own child."))
            elif rec.holder_user_id.id != self.env.uid:
                raise AccessError(_("You hold papers only for yourself or for your own child."))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Le formulaire envoie aussi le titulaire, champ invisible, à sa valeur par
            # défaut (soi) : un enfant choisi l'emporte toujours (vu au navigateur).
            if vals.get("child_id"):
                vals["holder_user_id"] = False
        recs = super().create(vals_list)
        recs._bf_check_holder_is_mine()
        recs._bf_link_attachments()
        return recs

    def write(self, vals):
        if vals.get("child_id"):
            vals["holder_user_id"] = False
        res = super().write(vals)
        if {"holder_user_id", "child_id"} & set(vals):
            self._bf_check_holder_is_mine()
        if "attachment_ids" in vals:
            self._bf_link_attachments()
        if {"expiry_date", "reminder_days"} & set(vals):
            # Une échéance qui change rouvre le rappel.
            self.sudo().filtered("reminder_task_id").write({"reminder_task_id": False})
        return res

    def _bf_link_attachments(self):
        """Garde 4 : rattacher à la fiche les photos déposées pendant sa saisie
        (nées sans fiche), et seulement celles de la personne qui écrit."""
        for rec in self:
            orphelines = rec.sudo().attachment_ids.filtered(
                lambda a: a.res_model == self._name and not a.res_id
                and (self.env.su or a.create_uid.id == self.env.uid))
            if orphelines:
                orphelines.write({"res_id": rec.id})

    # ------------------------------------------------------------------
    # Rappels
    # ------------------------------------------------------------------
    @api.model
    def _cron_send_reminders(self):
        """Une To-do privée par papier à renouveler, assignée à ses titulaires.

        Une tâche sans projet n'est visible que de ses personnes assignées (règle
        du cœur ``ir_rule_private_task``), gestionnaires de projet compris. Son nom
        dit le type et la personne, jamais le numéro."""
        today = fields.Date.context_today(self)
        env = neutral_env(self.env)
        dus = env[self._name].search([
            ("reminder_date", "<=", today), ("expiry_date", "!=", False),
            ("expiry_date", ">=", today - relativedelta(months=1)), ("reminder_task_id", "=", False),
        ])
        types = dict(self._fields["doc_type"]._description_selection(self.env))
        for doc in dus:
            titulaires = doc._bf_owners().filtered(lambda u: u.active)
            if not titulaires:
                continue
            tache = env["project.task"].with_context(mail_create_nolog=True).create({
                "name": _("Renew: %(paper)s (%(person)s)", paper=types.get(doc.doc_type), person=doc.holder_name),
                "user_ids": [(6, 0, titulaires.ids)],
                "date_deadline": doc.expiry_date,
                "project_id": False,
            })
            doc.reminder_task_id = tache.id
        return len(dus)
