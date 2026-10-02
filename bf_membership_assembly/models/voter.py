from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

from .guard import check_computed_not_written

ATTENDANCE = [
    ("absent", "Absence"),
    ("onsite", "Sur place"),
    ("remote", "À distance"),
    ("proxy", "Par procuration"),
]
PRESENT = ("onsite", "remote")

# La liste elle-même : qui vote, en vertu de quelle adhésion, par quelle
# personne. Gelée à l'ouverture. L'assemblée d'une ligne, elle, ne change
# jamais, et le canal de l'avis ne change plus une fois l'avis donné.
STRUCTURE_FIELDS = {
    "assembly_id", "member_id", "membership_id", "in_grace",
    "representative_id", "notice_channel",
}
# Ce qui se constate à la porte et pendant l'assemblée. Gelé à la clôture.
ATTENDANCE_FIELDS = {"attendance", "proxy_holder_id", "note"}

# Ce que la liste calcule pour chaque ligne, comme pour une ligne bâtie depuis
# le registre : ni une saisie ni une valeur par défaut du contexte ne les
# fixent.
LIST_FIELDS = {"in_grace", "notice_channel"}
# La preuve de l'avis par courriel : posée par l'envoi, jamais saisie.
NOTICE_PROOF_FIELDS = {"notice_sent_at"}

# Ce qui se constate à la porte et se consigne au fil de l'assemblée à chaque
# changement : la présence, le mandataire, la personne qui vote.
TRACED_FIELDS = ("attendance", "proxy_holder_id", "representative_id")

# La clé de contexte de la liste bâtie depuis le registre. 🔴 Elle ne vaut
# qu'en superutilisateur, comme celle des transitions de l'assemblée : un
# client RPC la mettrait dans son contexte pour échapper au fil.
SYNC_KEY = "bf_assembly_voter_sync"


class AssemblyVoter(models.Model):
    """Une ligne de la liste des votants : un membre, une voix.

    🔴 Une organisation membre tient UNE ligne, quel que soit le nombre de ses
    délégués. C'est le membre qui a la voix ; la personne qui l'exerce est son
    délégué votant en fonction (`representative_id`). Compter une ligne par
    délégué donnerait à l'organisation autant de voix qu'elle nomme de
    personnes.

    Une organisation sans délégué votant en fonction garde sa ligne (elle est
    membre et elle a droit à l'avis), mais la ligne le signale : personne ne
    peut s'y dire présent tant qu'un délégué n'est pas nommé.
    """

    _name = "bf.membership.assembly.voter"
    _description = "Votant à une assemblée"
    _order = "assembly_id, member_id, id"
    _rec_names_search = ["member_id.name", "representative_id.name", "member_id.member_number"]

    assembly_id = fields.Many2one(
        "bf.membership.assembly", string="Assemblée", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(related="assembly_id.company_id", store=True, index=True)
    assembly_state = fields.Selection(related="assembly_id.state", string="État de l'assemblée")
    member_id = fields.Many2one(
        "res.partner", string="Membre", required=True, index=True, ondelete="restrict",
    )
    member_number = fields.Char(related="member_id.member_number", string="N° de membre")
    is_organization = fields.Boolean(related="member_id.is_company", string="Organisation")
    street = fields.Char(related="member_id.street", string="Adresse")
    street2 = fields.Char(related="member_id.street2", string="Adresse (suite)")
    city = fields.Char(related="member_id.city", string="Ville")
    zip = fields.Char(related="member_id.zip", string="Code postal")
    membership_id = fields.Many2one(
        "bf.membership", string="Adhésion", ondelete="restrict",
        help="L'adhésion qui couvrait la date de référence et qui donne la voix.",
    )
    in_grace = fields.Boolean(
        string="En grâce",
        help="Inscrit parce que l'assemblée admet les membres en grâce : son "
             "adhésion était échue à la date de référence, dans le délai de grâce.",
    )
    representative_id = fields.Many2one(
        "res.partner", string="Personne qui vote", ondelete="restrict",
        help="Le membre lui-même, ou, pour une organisation, son délégué "
             "votant en fonction.",
    )
    allowed_representative_ids = fields.Many2many(
        "res.partner", string="Personnes admises à voter",
        compute="_compute_allowed_representatives",
    )
    missing_representative = fields.Boolean(
        string="Sans délégué votant", compute="_compute_missing_representative", store=True,
    )
    notice_channel = fields.Selection(
        [("email", "Courriel"), ("post", "Poste")], string="Avis par", readonly=True,
        help="Fixé à la convocation : courriel si le membre a consenti aux avis "
             "par courriel et a une adresse, la poste sinon. Il ne bouge plus "
             "ensuite, parce qu'il dit comment l'avis a été donné.",
    )
    notice_sent_at = fields.Datetime(
        string="Avis envoyé par courriel le", readonly=True, copy=False,
        help="Posée par l'envoi de l'avis, ligne par ligne : c'est la preuve que "
             "l'avis est parti à ce membre, et à quel moment. Elle ne dépend pas "
             "des courriels, qui s'effacent une fois partis, et ne se saisit pas.",
    )
    attendance = fields.Selection(ATTENDANCE, string="Présence", required=True, default="absent")
    proxy_holder_id = fields.Many2one(
        "bf.membership.assembly.voter", string="Mandataire", index=True, ondelete="set null",
        help="Le votant présent qui porte la procuration écrite de ce membre.",
    )
    proxy_received_ids = fields.One2many(
        "bf.membership.assembly.voter", "proxy_holder_id", string="Procurations reçues",
    )
    proxy_received_count = fields.Integer(
        string="Procurations portées", compute="_compute_proxy_received_count",
    )
    has_voice = fields.Boolean(
        string="Voix exercée", compute="_compute_has_voice", store=True,
        help="Présence notée, sur place ou à distance, ou procuration à un "
             "mandataire dont la présence est notée.",
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        ("assembly_member_uniq", "unique(assembly_id, member_id)",
         "Ce membre figure déjà à la liste des votants de cette assemblée."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("member_id", "representative_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.member_id.is_company and rec.representative_id:
                rec.display_name = _(
                    "%(person)s pour %(org)s",
                    person=rec.representative_id.name or "", org=rec.member_id.name or "")
            else:
                rec.display_name = rec.member_id.name or ""

    @api.depends("member_id", "assembly_id.record_date", "assembly_id.date")
    def _compute_allowed_representatives(self):
        """Le membre lui-même, ou les délégués votants de l'organisation en
        fonction à la date de référence ou le jour de l'assemblée : un délégué
        remplacé entre les deux ne prive pas l'organisation de sa voix."""
        Partner = self.env["res.partner"]
        for rec in self:
            member = rec.member_id
            if not member:
                rec.allowed_representative_ids = Partner
                continue
            if not member.is_company:
                rec.allowed_representative_ids = member
                continue
            allowed = Partner
            assembly = rec.assembly_id
            days = [assembly.record_date, assembly._meeting_day() if assembly else False]
            for day in filter(None, days):
                allowed |= member._voting_representatives(day)
            rec.allowed_representative_ids = allowed

    @api.depends("member_id.is_company", "representative_id")
    def _compute_missing_representative(self):
        for rec in self:
            rec.missing_representative = bool(rec.member_id.is_company and not rec.representative_id)

    @api.depends("proxy_received_ids")
    def _compute_proxy_received_count(self):
        for rec in self:
            rec.proxy_received_count = len(rec.proxy_received_ids)

    @api.depends("attendance", "proxy_holder_id.attendance")
    def _compute_has_voice(self):
        for rec in self:
            rec.has_voice = rec.attendance in PRESENT or (
                rec.attendance == "proxy" and rec.proxy_holder_id.attendance in PRESENT)

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    def _in_sync(self):
        """Vrai seulement quand la liste se bâtit depuis le registre : la clé
        ET le superutilisateur."""
        return bool(self.env.su and self.env.context.get(SYNC_KEY))

    @api.constrains("member_id", "membership_id")
    def _check_member_is_eligible(self):
        """🔴 Une ligne dit vrai : son adhésion est celle du membre, et le membre
        vote à la date de référence.

        La liste se bâtit depuis le registre, mais une ligne peut aussi se
        créer directement, par un appel RPC ou un import. Sans ce contrôle,
        une personne qui n'est pas membre entrerait à la liste en citant
        l'adhésion d'une autre. La liste bâtie depuis le registre n'est pas
        recontrôlée : ses lignes viennent du même calcul.
        """
        if self._in_sync():
            return
        for assembly in self.assembly_id:
            eligible = assembly._eligible_voters()
            for rec in self.filtered(lambda v, a=assembly: v.assembly_id == a):
                if not rec.membership_id:
                    raise ValidationError(_(
                        "La ligne de %s doit citer l'adhésion qui lui donne sa voix.",
                        rec.member_id.name))
                if rec.membership_id.partner_id != rec.member_id:
                    raise ValidationError(_(
                        "L'adhésion « %(membership)s » n'est pas celle de %(member)s : "
                        "une voix vient de la propre adhésion du membre.",
                        membership=rec.membership_id.display_name, member=rec.member_id.name))
                if rec.member_id not in eligible:
                    raise ValidationError(_(
                        "%(member)s ne vote pas à la date de référence du %(day)s : "
                        "aucune adhésion réglée, dans une catégorie qui vote, ne couvre ce jour-là.",
                        member=rec.member_id.name, day=format_date(self.env, assembly.record_date)))

    @api.constrains("attendance", "representative_id")
    def _check_presence_has_a_person(self):
        """Une présence, et une procuration, demandent une personne qui vote.

        Une organisation sans délégué votant ne se dit ni présente ni
        représentée : personne n'a qualité pour signer sa procuration.
        """
        for rec in self:
            if rec.attendance in PRESENT + ("proxy",) and not rec.representative_id:
                raise ValidationError(_(
                    "%s n'a pas de délégué votant : nommez-le avant de dire "
                    "l'organisation présente ou représentée.", rec.member_id.name))

    @api.constrains("representative_id", "member_id")
    def _check_representative(self):
        for rec in self:
            if not rec.representative_id:
                continue
            if not rec.member_id.is_company and rec.representative_id != rec.member_id:
                raise ValidationError(_(
                    "Une personne membre vote elle-même ; pour se faire "
                    "représenter, elle donne une procuration."))
            if rec.member_id.is_company and rec.representative_id not in rec.allowed_representative_ids:
                raise ValidationError(_(
                    "%(person)s n'est pas un délégué votant de %(org)s en fonction "
                    "à la date de référence ni le jour de l'assemblée.",
                    person=rec.representative_id.name, org=rec.member_id.name))

    @api.constrains("attendance", "proxy_holder_id", "representative_id")
    def _check_proxy(self):
        """Une procuration va à un votant présent, une seule fois, sans relais.

        🔴 Pas en chaîne. Si B porte la procuration de A, B ne peut pas à son
        tour donner la sienne : A serait représenté par quelqu'un qu'il n'a pas
        choisi, et la voix de A se déplacerait sans écrit. Le contrôle joue
        dans les deux sens : vers un mandataire qui a lui-même donné sa
        procuration, et depuis un votant qui porte déjà celles des autres.

        Les comptes passent par `search_count`, pas par le cache du One2many :
        on compte ce qui est écrit, y compris la ligne qu'on vient d'écrire.
        """
        for rec in self:
            assembly = rec.assembly_id
            held = self.search_count([("proxy_holder_id", "=", rec.id), ("attendance", "=", "proxy")])
            if held and rec.attendance not in PRESENT:
                raise ValidationError(_(
                    "%(name)s porte %(n)s procuration(s) : retirez-les avant "
                    "d'inscrire une absence ou une procuration sur cette ligne. "
                    "Une procuration ne se transmet pas en chaîne.",
                    name=rec.display_name, n=held))
            if rec.attendance != "proxy":
                continue
            if not assembly.proxy_allowed:
                raise ValidationError(_(
                    "Les procurations ne sont pas permises à cette assemblée. "
                    "Si les règlements les permettent, cochez « Procurations "
                    "permises » sur l'assemblée."))
            holder = rec.proxy_holder_id
            if not holder:
                raise ValidationError(_("Une procuration nomme son mandataire."))
            if holder == rec:
                raise ValidationError(_("Une procuration ne se donne pas à soi-même."))
            if holder.assembly_id != assembly:
                raise ValidationError(_("Le mandataire doit figurer à la liste des votants de la même assemblée."))
            if holder.attendance == "proxy":
                raise ValidationError(_(
                    "%s a déjà donné sa propre procuration : une procuration ne "
                    "se transmet pas en chaîne.", holder.display_name))
            if holder.attendance not in PRESENT:
                raise ValidationError(_(
                    "La présence de %s n'est pas notée : le mandataire doit être "
                    "là, sur place ou à distance.", holder.display_name))
        # 🔴 Le plafond se compte par PERSONNE qui porte les procurations, sur
        # toutes ses lignes : pour elle-même, et comme personne qui vote pour
        # une organisation. Il se recompte aussi quand la personne qui vote
        # d'une ligne change : la ligne emporte les procurations qu'elle porte.
        self.assembly_id._check_proxy_caps()

    # ------------------------------------------------------------------
    # Verrous
    # ------------------------------------------------------------------

    @api.model
    def _normalize_vals(self, vals):
        """Une ligne qui n'est pas « par procuration » n'a pas de mandataire."""
        if "attendance" in vals and vals["attendance"] != "proxy":
            vals["proxy_holder_id"] = False
        return vals

    def _check_lock(self, assemblies, keys):
        for assembly in assemblies:
            if keys & STRUCTURE_FIELDS and assembly.state not in ("draft", "convened"):
                raise UserError(_(
                    "La liste des votants de « %s » est gelée depuis l'ouverture "
                    "de l'assemblée.", assembly.name))
            if keys & ATTENDANCE_FIELDS and assembly.state in ("closed", "cancelled"):
                raise UserError(_(
                    "L'assemblée « %s » est close : les présences et les "
                    "procurations ne changent plus.", assembly.name))

    @api.model_create_multi
    def create(self, vals_list):
        Assembly = self.env["bf.membership.assembly"]
        Partner = self.env["res.partner"]
        in_sync = self._in_sync()
        defaults = None
        eligible_by_assembly = {}
        for vals in vals_list:
            check_computed_not_written(self, vals)
            if not in_sync and LIST_FIELDS & vals.keys():
                raise UserError(_(
                    "La grâce et le canal de l'avis se calculent comme pour la liste "
                    "bâtie depuis le registre ; ils ne se saisissent pas."))
            if not in_sync and NOTICE_PROOF_FIELDS & vals.keys():
                raise UserError(_("La date d'envoi de l'avis se pose à l'envoi ; elle ne se saisit pas."))
            if not in_sync:
                # Écrite dans les valeurs, elle l'emporte sur une valeur par
                # défaut du contexte, que l'ORM ajouterait après ce contrôle.
                vals["notice_sent_at"] = False
            self._normalize_vals(vals)
            # Le gel se contrôle avant la création, sur l'assemblée où la ligne
            # aboutira, valeur par défaut du contexte comprise : les contraintes
            # de la ligne ne doivent pas refuser à sa place.
            if defaults is None and not (vals.get("assembly_id") and vals.get("member_id")):
                defaults = self.default_get(["assembly_id", "member_id"])
            assembly = Assembly.browse(vals.get("assembly_id") or (defaults or {}).get("assembly_id"))
            if assembly and assembly.state not in ("draft", "convened"):
                raise UserError(_(
                    "La liste des votants de « %s » est gelée depuis l'ouverture "
                    "de l'assemblée.", assembly.name))
            if not in_sync and assembly:
                # 🔴 Une ligne ajoutée à la main reçoit la grâce et le canal que
                # la liste bâtie lui aurait donnés, écrits dans les valeurs : une
                # valeur par défaut du contexte, que l'ORM ajouterait APRÈS ce
                # contrôle, ne les fixe pas. La liste bâtie, elle, écrit en
                # superutilisateur, et `sudo()` retire déjà ces valeurs par
                # défaut du contexte.
                member = Partner.browse(vals.get("member_id") or (defaults or {}).get("member_id"))
                if assembly not in eligible_by_assembly:
                    eligible_by_assembly[assembly] = assembly._eligible_voters()
                vals["in_grace"] = eligible_by_assembly[assembly].get(member, (None, False))[1]
                vals["notice_channel"] = assembly._notice_channel_of(member)
        records = super().create(vals_list)
        if not in_sync:
            # Tout ajout fait à la main, hors de la liste bâtie depuis le
            # registre, se trace au fil de l'assemblée, au nom de la personne.
            for assembly in records.assembly_id:
                added = records.filtered(lambda v, a=assembly: v.assembly_id == a)
                assembly._message_log(body=_(
                    "Ajout à la main à la liste des votants, hors de la liste bâtie "
                    "depuis le registre : %s.", ", ".join(added.mapped("display_name"))))
        return records

    def _check_same_member(self, vals):
        """Une ligne ne change pas de membre : elle se retire, et la ligne du
        bon membre s'ajoute, ce que le fil trace."""
        if "member_id" in vals and any(rec.member_id.id != vals["member_id"] for rec in self):
            raise UserError(_(
                "Une ligne de votant ne change pas de membre : retirez-la et "
                "ajoutez celle du bon membre."))

    def _check_same_assembly(self, vals):
        """🔴 Une ligne ne change pas d'assemblée.

        Le gel se lit sur l'assemblée de la ligne : déplacer une ligne d'une
        assemblée en brouillon vers une assemblée close ajouterait un votant à
        une liste gelée, en ne contrôlant que l'assemblée d'origine.
        """
        if "assembly_id" in vals and any(rec.assembly_id.id != vals["assembly_id"] for rec in self):
            raise UserError(_(
                "Une ligne de votant ne change pas d'assemblée. Bâtissez la liste "
                "de l'autre assemblée depuis le registre."))

    def write(self, vals):
        check_computed_not_written(self, vals)
        if not self._in_sync() and LIST_FIELDS & vals.keys():
            raise UserError(_(
                "La grâce et le canal de l'avis se calculent comme pour la liste "
                "bâtie depuis le registre ; ils ne se saisissent pas."))
        if not self._in_sync() and NOTICE_PROOF_FIELDS & vals.keys():
            raise UserError(_("La date d'envoi de l'avis se pose à l'envoi ; elle ne se saisit pas."))
        self._normalize_vals(vals)
        self._check_same_assembly(vals)
        self._check_same_member(vals)
        self._check_lock(self.assembly_id, set(vals))
        before = {}
        if set(TRACED_FIELDS) & vals.keys() and not self._in_sync():
            before = {rec.id: {name: rec[name] for name in TRACED_FIELDS} for rec in self}
        res = super().write(vals)
        if before:
            self._log_attendance(before)
        return res

    def _log_attendance(self, before):
        """Consigne au fil de l'assemblée chaque changement de présence, de
        mandataire ou de personne qui vote, au nom de la personne qui l'a fait.

        🔴 C'est ce qui fait le quorum et les voix : une présence notée puis
        retirée, une procuration déplacée d'un mandataire à un autre, un
        délégué remplacé ne doivent pas se faire sans laisser de trace.
        """
        def label(name, value):
            field = self._fields[name]
            if field.type == "selection":
                return dict(field._description_selection(self.env)).get(value, "")
            return value.display_name if value else _("(personne)")

        for assembly in self.assembly_id:
            entries = []
            for rec in self.filtered(lambda v, a=assembly: v.assembly_id == a):
                changes = [
                    _("%(field)s : %(old)s → %(new)s",
                      field=self._fields[name]._description_string(self.env),
                      old=label(name, before[rec.id][name]), new=label(name, rec[name]))
                    for name in TRACED_FIELDS if before[rec.id][name] != rec[name]]
                if changes:
                    entries.append(_("%(member)s (%(changes)s)",
                                     member=rec.member_id.name, changes=", ".join(changes)))
            if entries:
                assembly._message_log(body=_(
                    "Présences et procurations : %s.", " ; ".join(entries)))

    def unlink(self):
        for assembly in self.assembly_id:
            if assembly.state not in ("draft", "convened"):
                raise UserError(_(
                    "La liste des votants de « %s » est gelée depuis l'ouverture "
                    "de l'assemblée.", assembly.name))
        holding = self.filtered(lambda v: v.proxy_received_ids - self)
        if holding:
            raise UserError(_(
                "%s porte des procurations : retirez-les d'abord.",
                ", ".join(holding.mapped("display_name"))))
        # Tout retrait fait à la main se trace au fil de l'assemblée, au nom de
        # la personne, comme l'ajout : une liste convoquée qui perd une ligne
        # sans trace perd un membre convoqué sans que personne ne le sache.
        # Les lignes que la liste bâtie retire sont dites par son propre message.
        removed = [] if self._in_sync() else [
            (assembly, ", ".join(self.filtered(lambda v, a=assembly: v.assembly_id == a).mapped("display_name")))
            for assembly in self.assembly_id]
        res = super().unlink()
        for assembly, names in removed:
            assembly._message_log(body=_(
                "Retrait à la main de la liste des votants, hors de la liste bâtie "
                "depuis le registre : %s.", names))
        return res
