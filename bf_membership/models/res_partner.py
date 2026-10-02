from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

# Ce qu'un contact dit de l'appartenance de la personne : ni lu, ni cherché sans
# le rôle Membres. 🔴 Odoo refuse de LIRE un champ réservé, mais laisse chercher
# « != False » ou « in » sur un One2many réservé : la liste des membres se
# dresserait par une simple recherche.
MEMBERSHIP_FIELDS = {
    "membership_ids", "delegate_ids", "delegation_ids", "member_status", "member_number",
    "current_membership_id", "member_since", "member_until", "membership_count",
    "directory_consent", "notice_email_consent",
}



CONSENT_FIELDS = {"directory_consent", "notice_email_consent"}

MEMBER_STATUSES = [
    ("member", "Membre en règle"),
    ("grace", "En grâce"),
    ("pending", "En attente (demande ou paiement)"),
    ("former", "Ancien membre"),
    ("none", "Non membre"),
]


class ResPartner(models.Model):
    """Le membre est un contact, personne ou organisation.

    Le statut se lit ici, calculé depuis les adhésions. Il dépend de la date du
    jour (une adhésion échoit à minuit) : la passe quotidienne le recalcule pour
    tous les membres, comme le fait la tâche du module `membership` d'Odoo.
    """

    _inherit = "res.partner"

    # 🔴 Être membre d'une association peut révéler une santé, une croyance,
    # une allégeance : ces champs sont réservés au rôle Membres, pas à tout
    # employé qui ouvre un contact (Loi 25).
    member_number = fields.Char(
        string="N° de membre", copy=False, index=True, readonly=True, groups="bf_membership.group_membership_user",
        help="Attribué à la première adhésion et gardé pour toujours, même "
             "après un départ : un ancien membre qui revient reprend son "
             "numéro.",
    )
    membership_ids = fields.One2many("bf.membership", "partner_id", string="Adhésions", groups="bf_membership.group_membership_user")
    member_status = fields.Selection(
        MEMBER_STATUSES, string="Statut de membre", default="none",
        compute="_compute_member_status", store=True, index=True, groups="bf_membership.group_membership_user",
    )
    current_membership_id = fields.Many2one(
        "bf.membership", string="Adhésion courante",
        compute="_compute_member_status", store=True, groups="bf_membership.group_membership_user",
    )
    member_since = fields.Date(string="Membre depuis", compute="_compute_member_status", store=True,
                               groups="bf_membership.group_membership_user")
    member_until = fields.Date(string="En règle jusqu'au", compute="_compute_member_status", store=True,
                               groups="bf_membership.group_membership_user")
    membership_count = fields.Integer(compute="_compute_membership_count", groups="bf_membership.group_membership_user")

    directory_consent = fields.Boolean(
        string="Paraît au répertoire des membres", copy=False, groups="bf_membership.group_membership_user",
        help="Le membre accepte que son nom paraisse au répertoire. Décoché "
             "d'office : la Loi 25 (art. 9.1) veut les paramètres les plus "
             "protecteurs par défaut.",
    )
    notice_email_consent = fields.Boolean(
        string="Accepte les avis par courriel", copy=False, groups="bf_membership.group_membership_user",
        help="Avis de convocation et documents d'assemblée par courriel. Sans "
             "ce consentement, l'avis part par la poste (la Loi canadienne sur "
             "les OBNL n'inscrit le courriel au registre que si le membre y a "
             "consenti).",
    )

    delegate_ids = fields.One2many(
        "bf.membership.delegate", "organization_id", string="Délégués", groups="bf_membership.group_membership_user",
    )
    delegation_ids = fields.One2many(
        "bf.membership.delegate", "partner_id", string="Représente", groups="bf_membership.group_membership_user",
    )

    _sql_constraints = [
        ("member_number_uniq", "unique(member_number)", "Ce numéro de membre est déjà attribué."),
    ]

    @api.depends(
        "membership_ids.state", "membership_ids.payment_state", "membership_ids.ever_settled",
        "membership_ids.date_start", "membership_ids.date_end",
        "membership_ids.type_id.grace_days",
    )
    def _compute_member_status(self):
        today = fields.Date.context_today(self)
        for partner in self:
            status, current, since = partner._membership_status(partner.membership_ids, today)
            partner.member_status = status
            partner.current_membership_id = current
            partner.member_since = since
            partner.member_until = current.date_end if current else False

    @api.model
    def _membership_status(self, memberships, today):
        """Le statut que donnent ces adhésions ce jour-là : (statut, adhésion
        courante, membre depuis). Le contact le calcule sur toutes ses
        adhésions ; le répertoire d'une société, sur les siennes seulement."""
        settled = memberships.filtered(
            lambda m: m.state in ("active", "expired", "withdrawn")
            and m.payment_state in ("paid", "exempt"))
        current = memberships.filtered(lambda m: m._covers(today)).sorted("date_start")[:1]
        since = min(settled.mapped("date_start")) if settled else False
        if current:
            return "member", current, since
        last = settled.filtered(lambda m: m.date_end).sorted("date_end")[-1:]
        withdrawn_after = last and memberships.filtered(
            lambda m: m.state == "withdrawn" and m.withdrawal_date
            and m.withdrawal_date >= last.date_start)
        in_grace = (last and last.state != "withdrawn" and not withdrawn_after
                    and last.date_end < today
                    <= last.date_end + relativedelta(days=last.type_id.grace_days))
        if in_grace:
            return "grace", current, since
        if memberships.filtered(lambda m: m.state in ("draft", "waiting")):
            return "pending", current, since
        if settled or memberships.sudo().filtered("ever_settled"):
            # Un retrait sans paiement n'a jamais fait de membre : une demande
            # retirée n'entre pas au registre des anciens. Une adhésion payée
            # puis renversée (un chèque sans provision), si.
            return "former", current, since
        return "none", current, since

    def _member_status_in(self, company, day=None):
        """Le statut de ce contact dans UNE société : celui que lit le
        répertoire de cette société, plutôt que le statut toutes sociétés
        confondues du champ `member_status`."""
        self.ensure_one()
        memberships = self.sudo().membership_ids.filtered(lambda m: m.company_id == company)
        return self._membership_status(memberships, day or fields.Date.context_today(self))[0]

    def _compute_membership_count(self):
        groups = self.env["bf.membership"]._read_group(
            [("partner_id", "in", self.ids)], ["partner_id"], ["__count"])
        counts = {p.id: n for p, n in groups}
        for partner in self:
            partner.membership_count = counts.get(partner.id, 0)

    def _ensure_member_number(self):
        """Le prochain numéro LIBRE de la séquence.

        🔴 Une liste importée peut porter des numéros au format de la séquence
        (« 00001 ») : sans ce saut, la mise en règle suivante buterait sur la
        contrainte d'unicité, et un paiement de cotisation ne s'enregistrerait plus.
        """
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        sequence = self.env["ir.sequence"].sudo()
        for partner in self.sudo().filtered(lambda p: not p.member_number):
            for _essai in range(10000):
                number = sequence.next_by_code("bf.membership.member")
                if not Partner.search_count([("member_number", "=", number)]):
                    break
            partner.member_number = number

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            role = self.env.user.has_group("bf_membership.group_membership_user")
            manager = self.env.user.has_group("bf_membership.group_membership_manager")
            # Les `default_*` du contexte complètent la création APRÈS ce contrôle :
            # retirés, sans quoi un appel direct cocherait un consentement.
            reserved = MEMBERSHIP_FIELDS if not role else {"member_number"} if not manager else set()
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if not (k.startswith("default_") and k[len("default_"):] in reserved)})
            if not role and any(vals.get(f) for vals in vals_list for f in MEMBERSHIP_FIELDS):
                raise AccessError(_("Ces champs sont réservés au rôle Membres."))
            if not manager and any(vals.get("member_number") for vals in vals_list):
                raise UserError(_("Le numéro de membre ne se fixe que par une personne responsable des membres."))
        records = super().create(vals_list)
        consents = [(rec, {f: False for f in CONSENT_FIELDS}) for rec, vals in zip(records, vals_list)
                    if CONSENT_FIELDS & {k for k, v in vals.items() if v}]
        for rec, before in consents:
            rec._log_consents({rec.id: before})
        return records

    def write(self, vals):
        if not self.env.su and "is_company" in vals:
            # 🔴 La nature d'un contact retenu (personne ou organisation) ne change
            # pas : une personne cochée « Société » perdrait sa voix, une
            # organisation décochée voterait sans délégué.
            changed = self.filtered(lambda p: p.is_company != bool(vals["is_company"]))
            if changed._membership_held():
                if not self.env.user.has_group("bf_membership.group_membership_user"):
                    self._refuse_membership_change()
                raise UserError(_(
                    "%s porte une adhésion ou une délégation : sa nature (personne ou "
                    "organisation) ne change pas, sans quoi sa voix changerait de porteur. "
                    "Créez plutôt un nouveau contact.", changed._membership_held()[:1].display_name))
        if not self.env.su and MEMBERSHIP_FIELDS & vals.keys():
            # 🔴 Sans le rôle, un refus UNIFORME avant toute lecture : comparer
            # d'abord le numéro au numéro en place dirait qui en a un.
            if not self.env.user.has_group("bf_membership.group_membership_user"):
                raise AccessError(_("Ces champs sont réservés au rôle Membres."))
            # 🔴 Champs calculés stockés : l'ORM ne refuse pas de les écrire. Un
            # statut « membre » posé à la main ouvrirait la carte et le répertoire.
            calcules = {"member_status", "current_membership_id", "member_since", "member_until"} & vals.keys()
            if calcules:
                raise UserError(_("Le statut de membre se calcule ; il ne s'écrit pas (%s).", ", ".join(sorted(calcules))))
            # 🔴 Le rôle d'une société ne s'étend pas aux membres d'une autre
            # association de la base : leurs consentements (le répertoire public
            # de l'autre) et leur numéro ne s'écrivent que depuis chez elle.
            if self._membership_foreign():
                self._refuse_membership_change()
            if "member_number" in vals and not self.env.user.has_group(
                    "bf_membership.group_membership_manager") and any(
                    p.sudo().member_number != (vals["member_number"] or False) for p in self):
                raise UserError(_("Le numéro de membre ne se change que par une personne responsable des membres."))
        consents = (CONSENT_FIELDS | {"member_number"}) & vals.keys()
        before = {p.id: {f: p.sudo()[f] for f in consents} for p in self} if consents else {}
        res = super().write(vals)
        if consents:
            self._log_consents(before)
        return res

    def _log_consents(self, before):
        """La preuve d'un consentement changé (ou d'un numéro de membre corrigé),
        sur l'adhésion la plus récente.

        🔴 Pas au fil du contact : tout employé lit ce fil, et même un message
        dont les valeurs suivies lui sont cachées dit que l'équipe des membres
        est passée par là. Un contact sans adhésion n'a pas de consentement qui
        compte : rien à consigner."""
        Membership = self.env["bf.membership"].sudo()
        where = _(", au portail") if self.env.user._is_portal() else ""
        for partner in self.sudo():
            # 🔴 `env._` et non `_` : dans une fonction imbriquée, `_()` ne trouve
            # pas `self` dans son cadre, ni donc la langue (« no translation
            # language detected »).
            def shown(f, value):
                if f in CONSENT_FIELDS:
                    return self.env._("oui") if value else self.env._("non")
                return value or self.env._("aucun")
            changes = [
                _("%(label)s : %(old)s → %(new)s", label=partner._fields[f].string,
                  old=shown(f, before[partner.id][f]), new=shown(f, partner[f]))
                for f in sorted(before.get(partner.id, {})) if before[partner.id][f] != partner[f]
                # L'attribution d'un premier numéro est la mise en règle, déjà suivie.
                and (f in CONSENT_FIELDS or before[partner.id][f])]
            # Un consentement vaut pour toutes les associations de la base où la
            # personne a une adhésion : la preuve va sur la plus récente de
            # CHACUNE, que chaque équipe la lise chez elle.
            memberships = Membership.search([("partner_id", "=", partner.id)], order="date_start desc, id desc")
            targets = [memberships.filtered(lambda m: m.company_id == company)[:1]
                       for company in memberships.company_id]
            if changes:
                for target in targets:
                    target._message_log(body=_("Changés par %(who)s%(where)s : %(changes)s",
                                               who=self.env.user.name, where=where, changes=" ; ".join(changes)))

    def unlink(self):
        if not self.env.su and not self.env.user.has_group("bf_membership.group_membership_user"):
            # 🔴 Le droit de supprimer d'abord, le même refus natif pour tous :
            # l'archivage qui suit écrit en superutilisateur, et ouvrirait sinon
            # à un usager du portail ce que la suppression lui refuse.
            self.check_access("unlink")
            # 🔴 Sans le rôle, rien ne doit dire qui est membre : ni un refus, ni
            # son message. Un contact qui porte une adhésion ou une délégation est
            # archivé au lieu d'être supprimé, en silence ; l'adhésion le note.
            # 🔴 Un contact lié à un usager actif reste au refus natif d'Odoo : le
            # même texte (« cannot delete… linked to an active user ») pour tous,
            # alors que l'archiver répondrait par un autre (« cannot archive… »).
            held = self._membership_held().filtered(lambda p: not p.with_context(active_test=True).user_ids)
            if held:
                held.write({"active": False})
                held._note_membership_archive(self.env.user)
            rest = self - held
            return super(ResPartner, rest).unlink() if rest else True
        memberships = self.sudo().membership_ids
        if memberships and not self.env.su and not self.env.user.has_group(
                "bf_membership.group_membership_manager"):
            raise UserError(_(
                "Ce contact porte une demande d'adhésion : seule une personne responsable "
                "des membres le supprime."))
        if not all(m._never_member() for m in memberships):
            raise UserError(_(
                "Ce contact est ou a été membre : le registre le garde. "
                "Archivez-le plutôt que de le supprimer."))
        # Une demande jamais payée (pourriel, désistement) part avec son contact.
        memberships.unlink()
        return super().unlink()

    def _membership_held(self):
        """Les contacts de `self` (en superutilisateur) qui portent une donnée
        d'appartenance : sans le rôle, ils ne se suppriment ni ne se fusionnent,
        et rien ne doit dire pourquoi. Les greffons l'étendent (lignes de votant,
        présidence d'une assemblée, contact du formulaire public)."""
        return self.sudo().filtered(lambda p: p.membership_ids or p.delegate_ids or p.delegation_ids)

    def _membership_foreign(self):
        """Les contacts de `self` (en superutilisateur) qui ont une adhésion,
        quelle qu'elle soit, dans une société hors de celles de la personne qui
        agit.

        🔴 Pas « aucune adhésion chez moi » : l'agent d'une société se créait
        une demande pour le membre d'une autre, puis cochait ses consentements,
        qui valent pour le répertoire et les avis de l'autre association."""
        companies = self.env.user.company_ids
        return self.sudo().filtered(
            lambda p: p.membership_ids.filtered(lambda m: m.company_id not in companies))

    @api.model
    def _refuse_membership_change(self):
        """Le refus neutre d'un changement de contact que l'appartenance interdit,
        un seul texte pour le socle et les greffons : il ne dit pas pourquoi."""
        raise UserError(_("Ce changement n'est pas permis avec vos droits. "
                          "Demandez à une personne administratrice."))

    def _note_membership_archive(self, user):
        """La trace d'un archivage qui remplace une suppression, là où le rôle
        Membres la lit. Les greffons l'étendent."""
        body = _("Contact archivé au lieu d'être supprimé, par %s.", user.name)
        partners = self.sudo()
        for records in (partners.membership_ids, partners.delegate_ids | partners.delegation_ids):
            for record in records:
                record._message_log(body=body)

    def _member_list_row(self, company):
        """La catégorie et le statut d'un contact pour la liste des membres d'UNE
        société : ceux de ses adhésions dans cette société seulement."""
        self.ensure_one()
        memberships = self.sudo().membership_ids.filtered(lambda m: m.company_id == company)
        status = self._membership_status(memberships, fields.Date.context_today(self))[0]
        last = memberships.filtered(lambda m: m.state not in ("draft", "refused")).sorted("date_start")[-1:]
        return {"category": last.type_id.name or "", "status": dict(MEMBER_STATUSES)[status]}

    def action_view_memberships(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_membership.action_membership")
        action["domain"] = [("partner_id", "=", self.id)]
        action["context"] = {"default_partner_id": self.id}
        return action

    def _voting_representatives(self, day):
        """Qui vote pour ce membre ce jour-là : lui-même, ou, pour une
        organisation, LE délégué qui porte sa voix (au plus une personne).

        Une organisation membre a une voix, comme une personne : ses autres
        délégués et ses substituts peuvent assister, pas voter.
        """
        self.ensure_one()
        if not self.is_company:
            return self
        delegates = self.delegate_ids.filtered(lambda d: d.voting and d._in_office(day))
        return delegates.sorted(lambda d: d.date_from or fields.Date.to_date("1900-01-01"), reverse=True)[:1].partner_id

