from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, formatLang, html2plaintext

from .guard import check_computed_not_written
from .voter import PRESENT

MAJORITIES = [
    ("simple", "Majorité simple"),
    ("two_thirds", "Deux tiers"),
    ("percent", "Pourcentage"),
]
VOTE_MODES = [
    ("show_of_hands", "À main levée"),
    ("secret", "Scrutin secret"),
]
RESULTS = [
    ("pending", "Non votée"),
    ("adopted", "Adoptée"),
    ("rejected", "Rejetée"),
]
TALLY_FIELDS = {"votes_for", "votes_against", "votes_abstain", "votes_spoiled"}
# La règle du vote : elle se fixe avant le premier dépouillement.
RULE_FIELDS = {"majority", "majority_percent"}
# Ce que l'assemblée a mis au vote : il se fige au premier dépouillement, et le
# pont le recopie au registre corporatif comme adopté.
CONTENT_FIELDS = ("name", "text", "mover_id", "seconder_id")
# Ce que la trace au fil de l'assemblée consigne à chaque changement.
LOGGED_FIELDS = CONTENT_FIELDS + (
    "votes_for", "votes_against", "votes_abstain", "votes_spoiled",
    "majority", "majority_percent")


class AssemblyProposal(models.Model):
    """Une proposition soumise au vote de l'assemblée.

    Les résultats se SAISISSENT, en totaux : un vote à main levée se compte
    dans la salle, un scrutin secret se dépouille sur papier. Ce qui reste en
    base, ce sont la question, la règle de majorité et les totaux, jamais le
    vote d'une personne nommée.

    ⚠️ L'abstention n'entre pas dans l'assiette de la majorité : on compte les
    voix exprimées, pour et contre. Les y inclure ferait échouer des
    propositions que l'assemblée a adoptées. Une égalité est rejetée : la
    majorité simple, c'est PLUS de la moitié.
    """

    _name = "bf.membership.assembly.proposal"
    _description = "Proposition soumise à l'assemblée"
    _order = "assembly_id, sequence, id"

    sequence = fields.Integer(string="Ordre", default=10)
    assembly_id = fields.Many2one(
        "bf.membership.assembly", string="Assemblée", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(related="assembly_id.company_id", store=True, index=True)
    assembly_state = fields.Selection(related="assembly_id.state", string="État de l'assemblée")
    name = fields.Char(string="Proposition", required=True)
    text = fields.Html(string="Texte de la proposition")
    mover_id = fields.Many2one(
        "bf.membership.assembly.voter", string="Proposée par", ondelete="restrict",
        domain="[('assembly_id', '=', assembly_id), ('attendance', 'in', ('onsite', 'remote'))]",
    )
    seconder_id = fields.Many2one(
        "bf.membership.assembly.voter", string="Appuyée par", ondelete="restrict",
        domain="[('assembly_id', '=', assembly_id), ('attendance', 'in', ('onsite', 'remote'))]",
    )

    majority = fields.Selection(
        MAJORITIES, string="Majorité requise", required=True, default="simple",
        help="Simple : plus de la moitié des voix exprimées. Deux tiers : au "
             "moins les deux tiers des voix exprimées (une modification des "
             "règlements, souvent). Pourcentage : au moins le pourcentage "
             "fixé. Dans les trois cas, les abstentions ne comptent pas.",
    )
    majority_percent = fields.Float(
        string="Pourcentage requis", default=75.0, digits=(5, 2),
        help="Au moins ce pourcentage des voix exprimées, entre 50 % (exclu) "
             "et 100 %. Sous 50 %, une proposition et son contraire "
             "pourraient être adoptées ensemble.",
    )
    vote_mode = fields.Selection(
        VOTE_MODES, string="Mode de vote", required=True, default="show_of_hands",
        help="C.c.Q. art. 351 : le vote se fait à main levée ou, sur demande, "
             "au scrutin secret. Au scrutin secret, Odoo tient le registre des "
             "bulletins remis et les totaux du dépouillement, jamais un lien "
             "entre un votant et son choix.",
    )
    scrutineer_ids = fields.Many2many(
        "res.partner", "bf_membership_assembly_proposal_scrutineer_rel",
        "proposal_id", "partner_id", string="Scrutatrices et scrutateurs",
    )

    votes_for = fields.Integer(string="Pour")
    votes_against = fields.Integer(string="Contre")
    votes_abstain = fields.Integer(string="Abstentions")
    votes_spoiled = fields.Integer(
        string="Bulletins nuls",
        help="Scrutin secret seulement : les bulletins illisibles ou annulés. "
             "Ils comptent parmi les bulletins dépouillés, pas parmi les voix "
             "exprimées.",
    )
    votes_cast = fields.Integer(string="Voix exprimées", compute="_compute_votes_cast", store=True)
    tally_started = fields.Boolean(
        string="Dépouillement commencé", readonly=True, copy=False,
        help="Posé au premier résultat saisi et jamais retiré. La majorité "
             "requise et le mode de vote ne changent plus ensuite, même si les "
             "totaux sont remis à zéro.",
    )

    ballot_ids = fields.One2many("bf.membership.assembly.ballot", "proposal_id", string="Bulletins remis")
    ballot_count = fields.Integer(string="Nombre de bulletins remis", compute="_compute_ballot_count", store=True)

    result = fields.Selection(RESULTS, string="Résultat", compute="_compute_result", store=True)
    result_detail = fields.Char(string="Motif du résultat", compute="_compute_result_detail")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("votes_for", "votes_against")
    def _compute_votes_cast(self):
        for rec in self:
            rec.votes_cast = rec.votes_for + rec.votes_against

    @api.depends("ballot_ids")
    def _compute_ballot_count(self):
        for rec in self:
            rec.ballot_count = len(rec.ballot_ids)

    def _has_tally(self):
        self.ensure_one()
        return any(self[f] for f in TALLY_FIELDS)

    def _evaluate(self):
        """(résultat, motif). Le motif et le résultat lisent le même calcul.

        Les comparaisons se font en entiers (pour × 3 ≥ exprimées × 2 plutôt
        que pour / exprimées ≥ 0,6667) : les deux tiers à la borne exacte
        doivent passer, et une division en virgule flottante les ferait
        échouer d'un cheveu.
        """
        self.ensure_one()
        if not self._has_tally():
            return "pending", _("Aucun résultat saisi.")
        votes_for, cast = self.votes_for, self.votes_cast
        if self.majority == "simple":
            passed = votes_for * 2 > cast
            rule = _("plus de la moitié des voix exprimées")
        elif self.majority == "two_thirds":
            passed = cast > 0 and votes_for * 3 >= cast * 2
            rule = _("au moins les deux tiers des voix exprimées")
        else:
            passed = cast > 0 and float_compare(
                votes_for * 100.0, cast * self.majority_percent, precision_digits=6) >= 0
            rule = _("au moins %s %% des voix exprimées",
                     formatLang(self.env, self.majority_percent, digits=2))
        detail = _(
            "%(for)s pour, %(against)s contre : il faut %(rule)s. Les "
            "abstentions (%(abstain)s) ne comptent pas.",
            **{"for": votes_for, "against": self.votes_against, "rule": rule,
               "abstain": self.votes_abstain})
        return ("adopted" if passed else "rejected"), detail

    @api.depends(*TALLY_FIELDS, "majority", "majority_percent")
    def _compute_result(self):
        for rec in self:
            rec.result = rec._evaluate()[0]

    @api.depends(*TALLY_FIELDS, "majority", "majority_percent")
    @api.depends_context("lang")
    def _compute_result_detail(self):
        for rec in self:
            rec.result_detail = rec._evaluate()[1]

    def _result_label(self):
        self.ensure_one()
        return dict(self._fields["result"]._description_selection(self.env)).get(self.result, "")

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("majority", "majority_percent")
    def _check_majority(self):
        for rec in self:
            if rec.majority == "percent" and not 50 < rec.majority_percent <= 100:
                raise ValidationError(_(
                    "Le pourcentage requis se situe au-dessus de 50 % et au plus "
                    "à 100 %. Pour la moitié plus une voix, choisissez la "
                    "majorité simple."))

    @api.constrains(*TALLY_FIELDS, "vote_mode")
    def _check_tally(self):
        """On ne compte pas plus de voix qu'il n'y en a dans la salle, ni plus
        de bulletins qu'on en a remis.

        C'est la garde qui attrape la saisie faite de mémoire, et celle qui
        fait d'un scrutin secret quelque chose de vérifiable : le dépouillement
        ne peut pas sortir de l'urne plus de bulletins que le registre n'en a
        vu entrer.
        """
        for rec in self:
            values = [rec[f] for f in TALLY_FIELDS]
            if any(v < 0 for v in values):
                raise ValidationError(_("Un décompte de voix n'est jamais négatif."))
            if rec.vote_mode == "show_of_hands":
                if rec.votes_spoiled:
                    raise ValidationError(_("Un vote à main levée n'a pas de bulletin nul."))
                counted = rec.votes_for + rec.votes_against + rec.votes_abstain
                voices = rec.assembly_id.voice_count
                if counted > voices:
                    raise ValidationError(_(
                        "%(counted)s voix comptées pour %(voices)s votants présents ou "
                        "représentés. Corrigez les présences ou le décompte.",
                        counted=counted, voices=voices))
            else:
                counted = sum(values)
                if counted > rec.ballot_count:
                    raise ValidationError(_(
                        "%(counted)s bulletins dépouillés pour %(issued)s bulletins "
                        "remis : on ne dépouille pas plus de bulletins qu'on en a remis.",
                        counted=counted, issued=rec.ballot_count))

    @api.constrains("mover_id", "seconder_id", "assembly_id")
    def _check_mover_and_seconder(self):
        for rec in self:
            for voter, role in ((rec.mover_id, _("La personne qui propose")),
                                (rec.seconder_id, _("La personne qui appuie"))):
                if not voter:
                    continue
                if voter.assembly_id != rec.assembly_id:
                    raise ValidationError(_(
                        "%s doit figurer à la liste des votants de cette assemblée.", role))
                if voter.attendance not in PRESENT:
                    raise ValidationError(_(
                        "%(role)s doit avoir sa présence notée, sur place ou à "
                        "distance ; ce n'est pas le cas de %(name)s.",
                        role=role, name=voter.display_name))
            if (rec.mover_id and rec.seconder_id
                    and rec.mover_id.representative_id == rec.seconder_id.representative_id):
                raise ValidationError(_(
                    "Une proposition s'appuie par une autre personne que celle qui la propose."))

    # ------------------------------------------------------------------
    # Verrous
    # ------------------------------------------------------------------

    def _check_lock(self, assemblies):
        for assembly in assemblies:
            if assembly.state in ("closed", "cancelled"):
                raise UserError(_(
                    "L'assemblée « %s » est close : ses propositions ne changent plus.",
                    assembly.name))

    @staticmethod
    def _tally_in(vals):
        return any(vals.get(f) for f in TALLY_FIELDS)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            # Le drapeau ne vient ni des valeurs ni d'une valeur par défaut du
            # contexte, que l'ORM ajouterait après ce contrôle.
            if any("tally_started" in vals for vals in vals_list):
                raise UserError(_("Le dépouillement commencé se constate ; il ne se saisit pas."))
            self = self.with_context({
                key: value for key, value in self.env.context.items() if key != "default_tally_started"})
        for vals in vals_list:
            check_computed_not_written(self, vals)
        records = super().create(vals_list)
        # Contrôlé sur la proposition créée, pas sur les valeurs reçues :
        # l'assemblée et les totaux peuvent aussi venir d'une valeur par défaut
        # passée dans le contexte, que l'ORM ajoute après ce contrôle.
        for rec in records:
            self._check_lock(rec.assembly_id)
            if rec._has_tally() and rec.assembly_id.state != "open":
                raise UserError(_("Un résultat se saisit pendant l'assemblée, une fois celle-ci ouverte."))
        counted = records.filtered(lambda p: p._has_tally())
        if counted:
            counted.sudo().write({"tally_started": True})
            for rec in counted:
                rec._log_changes({}, created=True)
        return records

    # ------------------------------------------------------------------
    # La trace au fil de l'assemblée
    # ------------------------------------------------------------------

    def _logged_values(self):
        self.ensure_one()
        return {name: self[name] for name in LOGGED_FIELDS} | {"result": self.result}

    def _format_logged(self, name, value):
        field = self._fields[name]
        if field.type == "many2one":
            return value.display_name if value else _("(personne)")
        if field.type == "html":
            return "« %s »" % (html2plaintext(value or "").strip() or _("vide"))
        if field.type == "char":
            return "« %s »" % (value or "")
        if field.type == "selection":
            return dict(field._description_selection(self.env)).get(value, "")
        if name == "majority_percent":
            return "%s %%" % formatLang(self.env, value, digits=2)
        return str(value)

    def _log_changes(self, before, created=False):
        """Consigne au fil de l'assemblée ce qui a changé sur la proposition.

        🔴 La proposition n'a pas de fil : c'est l'assemblée qui garde la trace
        de chaque saisie et de chaque correction des totaux ou de la règle,
        avec la personne qui l'a faite. Sans elle, des totaux remis à zéro puis
        saisis de nouveau ne laisseraient voir que le dernier décompte.
        """
        self.ensure_one()
        after = self._logged_values()
        changes = []
        for name in LOGGED_FIELDS:
            label = self._fields[name]._description_string(self.env)
            if created:
                # À la création, seuls les totaux saisis d'emblée se consignent.
                if name in TALLY_FIELDS and after[name]:
                    changes.append(_("%(field)s : %(new)s", field=label,
                                     new=self._format_logged(name, after[name])))
            elif before[name] != after[name]:
                changes.append(_("%(field)s : %(old)s → %(new)s", field=label,
                                 old=self._format_logged(name, before[name]),
                                 new=self._format_logged(name, after[name])))
        if not changes:
            return
        result = self._format_logged("result", after["result"])
        if not created and before["result"] != after["result"]:
            result = _("%(old)s → %(new)s", old=self._format_logged("result", before["result"]), new=result)
        self.assembly_id._message_log(body=_(
            "« %(proposal)s » : %(changes)s. Résultat : %(result)s.",
            proposal=self.name, changes=" ; ".join(changes), result=result))

    def _check_same_assembly(self, vals):
        """🔴 Une proposition ne change pas d'assemblée.

        Le verrou se lit sur l'assemblée de la proposition : déplacer une
        proposition votée d'une assemblée ouverte vers une assemblée close la
        ferait « adopter » par une assemblée qui ne l'a jamais vue, prête pour
        le registre corporatif, en ne contrôlant que l'assemblée d'origine.
        """
        if "assembly_id" in vals and any(rec.assembly_id.id != vals["assembly_id"] for rec in self):
            raise UserError(_(
                "Une proposition ne change pas d'assemblée. Soumettez-la de "
                "nouveau à l'autre assemblée."))

    def _check_secret_tally_kept(self, vals):
        """🔴 Au scrutin secret, un dépouillement commencé se corrige ; il ne
        s'efface pas.

        Remis à zéro, il rouvrirait la remise des bulletins : une personne
        arrivée en retard recevrait le sien, le dépouillement serait saisi de
        nouveau, et l'écart entre les deux décomptes dirait comment elle a voté.
        """
        if not any(f in vals for f in TALLY_FIELDS):
            return
        for rec in self.filtered(lambda p: p.vote_mode == "secret" and p._has_tally()):
            if not any(vals.get(f, rec[f]) for f in TALLY_FIELDS):
                raise UserError(_(
                    "Le dépouillement de « %s » est commencé : il se corrige, il ne "
                    "s'efface pas.", rec.name))

    def _check_content_kept(self, vals):
        """🔴 Ce que l'assemblée a mis au vote ne change plus après le premier
        dépouillement : le titre, le texte, le proposeur et l'appuyeur.

        Le pont recopie le texte au registre corporatif comme adopté : changé
        après le vote, il ferait inscrire une résolution que l'assemblée n'a
        pas votée. Avant le vote, ils se modifient (un amendement), et chaque
        changement se consigne au fil de l'assemblée.
        """
        keys = set(CONTENT_FIELDS) & vals.keys()
        if not keys:
            return
        for rec in self.filtered("tally_started"):
            for name in keys:
                current, new = rec[name], vals[name]
                if self._fields[name].type == "many2one":
                    changed = current.id != (new or False)
                else:
                    changed = (current or "") != (new or "")
                if changed:
                    raise UserError(_(
                        "Le vote sur « %s » est commencé : ce qui a été mis au vote (titre, "
                        "texte, proposeur, appuyeur) ne change plus. Portez la question à "
                        "une nouvelle proposition.", rec.name))

    def _check_rule_kept(self, vals):
        """La majorité requise se fixe avant le vote : une fois un résultat
        saisi, la changer ferait passer ou tomber la proposition après coup,
        sur les mêmes totaux.

        🔴 Le verrou lit le drapeau du premier dépouillement, jamais les totaux
        du moment : à main levée, les totaux se remettent à zéro, et un verrou
        posé sur eux se lèverait. 2 pour, 1 contre, adoptée ; totaux remis à
        zéro ; majorité portée à 90 % ; 2 pour, 1 contre de nouveau : rejetée.
        """
        if not RULE_FIELDS & vals.keys():
            return
        for rec in self.filtered("tally_started"):
            changed = rec.majority != vals.get("majority", rec.majority) or float_compare(
                rec.majority_percent, vals.get("majority_percent", rec.majority_percent),
                precision_digits=2)
            if changed:
                raise UserError(_(
                    "Un résultat est saisi pour « %s » : sa majorité requise ne change "
                    "plus. Portez la question à une nouvelle proposition.", rec.name))

    def write(self, vals):
        check_computed_not_written(self, vals)
        if not self.env.su and "tally_started" in vals and any(
                rec.tally_started != bool(vals["tally_started"]) for rec in self):
            raise UserError(_("Le dépouillement commencé se constate ; il ne se saisit pas."))
        self._check_same_assembly(vals)
        self._check_lock(self.assembly_id)
        if self._tally_in(vals) and any(a.state != "open" for a in self.assembly_id):
            raise UserError(_("Un résultat se saisit pendant l'assemblée, une fois celle-ci ouverte."))
        self._check_secret_tally_kept(vals)
        self._check_rule_kept(vals)
        self._check_content_kept(vals)
        if "vote_mode" in vals:
            for rec in self:
                if rec.vote_mode != vals["vote_mode"] and (
                        rec.ballot_ids or rec.tally_started or rec._has_tally()):
                    raise UserError(_(
                        "Le vote sur « %s » est commencé : son mode ne change plus. "
                        "Portez la question à une nouvelle proposition.", rec.name))
        logged = set(LOGGED_FIELDS) & vals.keys()
        before = {rec.id: rec._logged_values() for rec in self} if logged else {}
        res = super().write(vals)
        if TALLY_FIELDS & vals.keys():
            # Posé en superutilisateur, jamais retiré.
            self.filtered(lambda p: p._has_tally() and not p.tally_started).sudo().write(
                {"tally_started": True})
        if logged:
            for rec in self:
                rec._log_changes(before[rec.id])
        return res

    def unlink(self):
        self._check_lock(self.assembly_id)
        voted = self.filtered(lambda p: p.ballot_ids or p.tally_started or p._has_tally())
        if voted:
            raise UserError(_(
                "« %s » a été votée : une proposition votée reste au procès-verbal.",
                voted[0].name))
        return super().unlink()

    # ------------------------------------------------------------------
    # Scrutin secret
    # ------------------------------------------------------------------

    def action_issue_ballots(self):
        """Inscrit au registre un bulletin pour chaque votant qui a une voix.

        Se rejoue sans doubler : une personne arrivée en retard reçoit le sien
        au second clic, et les autres ne reçoivent rien de plus. Une fois le
        dépouillement commencé, plus personne ne reçoit de bulletin (voir le
        registre). Un membre représenté reçoit son bulletin par son
        mandataire, et le registre le dit.
        """
        Ballot = self.env["bf.membership.assembly.ballot"]
        for rec in self:
            if rec.vote_mode != "secret":
                raise UserError(_("« %s » se vote à main levée : il n'y a pas de bulletin.", rec.name))
            if rec.assembly_id.state != "open":
                raise UserError(_("Les bulletins se remettent pendant l'assemblée, une fois celle-ci ouverte."))
            issued = rec.ballot_ids.voter_id
            voters = rec.assembly_id.voter_ids.filtered(lambda v: v.has_voice and v not in issued)
            if not voters and not issued:
                raise UserError(_(
                    "Aucun votant présent ou représenté : il n'y a personne à qui "
                    "remettre un bulletin."))
            Ballot.create([{"proposal_id": rec.id, "voter_id": voter.id} for voter in voters])
        return True
