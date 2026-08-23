"""Résolutions et scrutins.

Les seuils viennent du Code civil du Québec, texte à jour au 1er avril 2026 :

- art. 1096 : majorité des voix des copropriétaires présents ou représentés
- art. 1097 : trois quarts des voix des copropriétaires présents ou représentés
- art. 1098 : trois quarts des copropriétaires représentant 90 % des voix de
  *tous* les copropriétaires

⚠️ L'art. 1097 n'est PLUS une double majorité, et c'est l'erreur la plus
facile à commettre ici parce que la doctrine en ligne décrit encore souvent
l'ancien régime. La Loi 16 (2019, c. 28, a. 53), en vigueur le 10 janvier
2020, a remplacé « à la majorité » par « par » et « de tous les
copropriétaires » par « des copropriétaires, présents ou représentés ». La
condition en nombre a disparu : il ne reste que les trois quarts des voix de
l'assistance. Une version antérieure de ce module exigeait encore les deux, et
rejetait donc des résolutions que la loi adopte — le cas typique étant le
copropriétaire majoritaire seul en faveur. L'art. 1103 fait précisément d'une
« erreur dans le calcul des voix » un motif d'annulation, ouvert 90 jours.

Deux pièges qui, eux, tiennent toujours :

1. Les dénominateurs diffèrent. Les art. 1096 et 1097 se mesurent sur
   l'assistance, l'art. 1098 sur l'ensemble du syndicat, présents ou non — et
   l'art. 1098, non touché par la Loi 16, garde sa condition en nombre.
2. À une assemblée de reprise, une décision de l'art. 1097 exige en plus que
   les membres présents représentent au moins la majorité des voix de tous
   les copropriétaires (art. 1089 al. 2).

Le calcul est une aide à la présidence, pas un avis juridique : le résultat
reste révisable à la main, et toute révision doit être motivée.

Le mode de scrutin, lui, ne vient pas du chapitre de la copropriété, qui est
muet là-dessus. Il vient de l'art. 351 al. 2 C.c.Q. : « Le vote des membres se
fait à main levée ou, sur demande, au scrutin secret. » Cette règle du régime
général des personnes morales atteint le syndicat par l'art. 334, puisque
l'art. 1039 en fait une personne morale et que rien, aux art. 1096 à 1098, ne
règle la manière de voter — ces articles-là ne fixent que les seuils.

⚠️ La chaîne 1039 → 334 → 351 est une lecture, et elle est à confirmer en
validation juridique externe (P2.3), au même titre que celle des délais du
promoteur. Elle a une conséquence pratique qu'il vaut mieux avoir vue : c'est
l'art. 351, et non l'art. 1089.1, qui ouvre le droit d'exiger le scrutin
secret. L'art. 1089.1 ne fait que dire à quelles conditions un participant à
distance peut voter « lorsqu'un tel vote est demandé » — il présuppose le droit
de le demander au lieu de le créer. Un module qui ne coderait le vote secret
que pour les assemblées à distance se tromperait donc de sens.
"""
import secrets

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from .bf_property_secret_ballot import hash_receipt, new_receipt_code

VOTE_DIGITS = 4

# Les cas énumérés par chaque article, tels qu'ils se lisent au texte à jour au
# 1er avril 2026. L'art. 1097 en compte cinq : le cinquième, la modification de
# la description des parties privatives visée à l'art. 1070, a été ajouté par
# 2020, c. 5, a. 198. Une énumération qui s'arrête à quatre envoie l'utilisateur
# vers la majorité ordinaire pour une décision qui exige les trois quarts.
MAJORITY_CASES = """Art. 1096 — toutes les décisions du syndicat qui ne relèvent \
pas d'un autre article, y compris la modification du règlement de l'immeuble et \
la correction d'une erreur matérielle dans la déclaration.

Art. 1097 — 1° acquisition ou aliénation immobilière par le syndicat ; \
2° travaux de transformation, d'agrandissement ou d'amélioration des parties \
communes, la répartition de leur coût et l'hypothèque mobilière qui les \
finance ; 3° construction de bâtiments pour créer de nouvelles fractions ; \
4° modification de l'acte constitutif de copropriété ou de l'état descriptif \
des fractions ; 5° modification de la description des parties privatives visée \
à l'art. 1070.

Art. 1098 — 1° changement de destination de l'immeuble ; 2° aliénation des \
parties communes dont la conservation est nécessaire au maintien de cette \
destination ; 3° modification de la déclaration pour permettre la détention \
d'une fraction en jouissance périodique et successive."""

MAJORITY_LABELS = {
    "art_1096": "Art. 1096 C.c.Q. — majorité des voix des présents ou représentés",
    "art_1097": "Art. 1097 C.c.Q. — trois quarts des voix des copropriétaires "
                "présents ou représentés",
    "art_1098": "Art. 1098 C.c.Q. — trois quarts des copropriétaires représentant "
                "90 % des voix de tous les copropriétaires",
}


class BfPropertyResolution(models.Model):
    _name = "bf.property.resolution"
    _description = "Résolution d'assemblée"
    _inherit = ["mail.thread"]
    _order = "assembly_id, sequence, id"

    name = fields.Char(string="Résolution", required=True, tracking=True)
    sequence = fields.Integer(string="Ordre", default=10)
    assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Assemblée",
        required=True,
        ondelete="cascade",
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="assembly_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="assembly_id.company_id", store=True, string="Société"
    )
    description = fields.Html(string="Texte de la résolution")
    majority_type = fields.Selection(
        [
            ("art_1096", "Ordinaire (art. 1096)"),
            ("art_1097", "Renforcée (art. 1097)"),
            ("art_1098", "Qualifiée (art. 1098)"),
        ],
        string="Majorité requise",
        default="art_1096",
        required=True,
        tracking=True,
        help=MAJORITY_CASES,
    )
    majority_label = fields.Char(
        string="Règle appliquée", compute="_compute_majority_label"
    )

    ballot_mode = fields.Selection(
        [
            ("open", "À main levée"),
            ("secret", "Scrutin secret"),
        ],
        string="Mode de scrutin",
        default="open",
        required=True,
        tracking=True,
        help="Art. 351 al. 2 C.c.Q. : le vote se fait à main levée ou, sur "
             "demande, au scrutin secret. Au scrutin secret, le registre dit "
             "qui a reçu un bulletin et l'urne porte les choix : aucun chemin "
             "en base ne mène de l'un à l'autre.",
    )
    secret_requested_by_id = fields.Many2one(
        "res.partner",
        string="Scrutin secret demandé par",
        tracking=True,
        help="Art. 351 al. 2 C.c.Q. : le scrutin secret se tient « sur "
             "demande ». Qui l'a demandé se consigne, parce que c'est la "
             "demande qui justifie d'écarter le vote à main levée.",
    )
    secret_request_note = fields.Char(string="Note sur la demande")

    vote_ids = fields.One2many(
        "bf.property.vote", "resolution_id", string="Scrutin"
    )
    secret_ballot_ids = fields.One2many(
        "bf.property.secret.ballot", "resolution_id", string="Urne"
    )
    ballot_issued_count = fields.Integer(
        string="Bulletins remis", compute="_compute_ballot_box", store=True
    )
    ballot_cast_count = fields.Integer(
        string="Bulletins déposés", compute="_compute_ballot_box", store=True
    )
    secret_exposure_count = fields.Integer(
        string="Bulletins que leur poids isole",
        compute="_compute_ballot_box",
        store=True,
        help="Nombre de bulletins seuls de leur poids dans l'urne. Rapprochés "
             "du registre, ceux-là désignent leur auteur : un scrutin pondéré "
             "n'est secret qu'entre bulletins de même poids. Le module compte "
             "et le dit ; il ne promet pas un secret que l'arithmétique ne "
             "peut pas tenir.",
    )
    ballot_box_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("open", "Scrutin non ouvert"),
            ("balanced", "Urne conforme au registre"),
            ("unbalanced", "Urne non conforme au registre"),
        ],
        string="État de l'urne",
        compute="_compute_ballot_box",
        store=True,
    )
    ballot_box_detail = fields.Char(
        string="Contrôle de l'urne", compute="_compute_ballot_box", store=True
    )
    votes_not_cast = fields.Float(
        string="Voix non déposées",
        compute="_compute_tally",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Voix de bulletins remis mais jamais déposés dans l'urne. Elles "
             "ne sont ni pour, ni contre, ni abstention : elles n'ont pas été "
             "exprimées. Les dénominateurs des art. 1096 et 1097 se mesurent "
             "de toute façon sur les voix présentes, pas sur les bulletins.",
    )

    votes_for = fields.Float(compute="_compute_tally", store=True, digits=(16, VOTE_DIGITS),
                             string="Voix pour")
    votes_against = fields.Float(compute="_compute_tally", store=True, digits=(16, VOTE_DIGITS),
                                 string="Voix contre")
    votes_abstain = fields.Float(compute="_compute_tally", store=True, digits=(16, VOTE_DIGITS),
                                 string="Voix abstention")
    owners_for = fields.Integer(compute="_compute_tally", store=True, string="Copropriétaires pour")
    owners_against = fields.Integer(compute="_compute_tally", store=True,
                                    string="Copropriétaires contre")
    owners_abstain = fields.Integer(compute="_compute_tally", store=True,
                                    string="Copropriétaires abstention")

    result = fields.Selection(
        [
            ("pending", "Non voté"),
            ("adopted", "Adoptée"),
            ("rejected", "Rejetée"),
        ],
        string="Résultat",
        compute="_compute_result",
        store=True,
        tracking=True,
    )
    result_detail = fields.Char(
        string="Motif du résultat", compute="_compute_result", store=True
    )
    result_override = fields.Selection(
        [
            ("adopted", "Adoptée"),
            ("rejected", "Rejetée"),
        ],
        string="Résultat corrigé à la main",
        tracking=True,
        help="Le calcul est une aide, pas un avis juridique. Une correction "
             "manuelle exige une justification.",
    )
    override_reason = fields.Char(string="Justification de la correction", tracking=True)

    def _selection_label(self, field_name):
        """Libellé traduit d'un champ de sélection, pour le procès-verbal."""
        value = self[field_name]
        labels = dict(self._fields[field_name]._description_selection(self.env))
        return labels.get(value, value or "")

    @api.depends("majority_type")
    def _compute_majority_label(self):
        for rec in self:
            rec.majority_label = MAJORITY_LABELS.get(rec.majority_type, "")

    @api.depends(
        "ballot_mode",
        "vote_ids.choice",
        "vote_ids.votes",
        "secret_ballot_ids.choice",
        "secret_ballot_ids.votes",
        "secret_ballot_ids.voter_key",
    )
    def _compute_tally(self):
        for rec in self:
            secret = rec.ballot_mode == "secret"
            box = rec.secret_ballot_ids
            for choice, vfield, ofield in (
                ("for", "votes_for", "owners_for"),
                ("against", "votes_against", "owners_against"),
                ("abstain", "votes_abstain", "owners_abstain"),
            ):
                if secret:
                    ballots = box.filtered(lambda b, c=choice: b.choice == c)
                    rec[vfield] = sum(ballots.mapped("votes"))
                    # La clé de votant existe pour cette ligne-ci : sans elle,
                    # l'urne rendrait un nombre de BULLETINS là où l'art. 1098
                    # veut un nombre de COPROPRIÉTAIRES, et le détenteur de
                    # trois fractions compterait pour trois.
                    rec[ofield] = len(set(ballots.mapped("voter_key")))
                    continue
                lines = rec.vote_ids.filtered(lambda v, c=choice: v.choice == c)
                rec[vfield] = sum(lines.mapped("votes"))
                # Des personnes, pas des bulletins : un copropriétaire qui
                # détient plusieurs fractions vote une fois par fraction mais
                # ne compte que pour un dans les majorités en nombre.
                rec[ofield] = len(lines.mapped("partner_id"))
            rec.votes_not_cast = (
                sum(box.filtered(lambda b: not b.choice).mapped("votes"))
                if secret
                else 0.0
            )

    @api.depends(
        "ballot_mode",
        "vote_ids.votes",
        "vote_ids.partner_id",
        "secret_ballot_ids.choice",
        "secret_ballot_ids.votes",
        "secret_ballot_ids.voter_key",
    )
    def _compute_ballot_box(self):
        """Contrôle l'urne sans l'ouvrir : elle doit être une permutation du registre.

        Le contrôle compare deux profils : pour chaque personne du registre, le
        tuple trié des voix de ses bulletins ; pour chaque clé de votant de
        l'urne, le même tuple. Deux listes triées, comparées telles quelles.
        Elles concordent si et seulement si l'urne contient exactement les
        bulletins remis, au bon poids et au bon regroupement par personne — et
        la comparaison ne dit toujours pas quelle clé est quelle personne.

        C'est cela, « vérifiés subséquemment » au sens de l'art. 1089.1 : on
        recompte l'urne entière sans jamais rouvrir un bulletin.
        """
        for rec in self:
            box = rec.secret_ballot_ids
            rec.ballot_issued_count = len(box)
            rec.ballot_cast_count = len(box.filtered("choice"))
            weights = [round(b.votes, VOTE_DIGITS) for b in box]
            rec.secret_exposure_count = len(
                [w for w in weights if weights.count(w) == 1]
            )
            if rec.ballot_mode != "secret":
                rec.ballot_box_state = "na"
                rec.ballot_box_detail = False
                continue
            if not box:
                rec.ballot_box_state = "open"
                rec.ballot_box_detail = _("Le scrutin n'a pas été ouvert.")
                continue
            register = {}
            for line in rec.vote_ids:
                register.setdefault(line.partner_id.id, []).append(
                    round(line.votes, VOTE_DIGITS)
                )
            urn = {}
            for ballot in box:
                urn.setdefault(ballot.voter_key, []).append(
                    round(ballot.votes, VOTE_DIGITS)
                )
            register_profile = sorted(tuple(sorted(v)) for v in register.values())
            urn_profile = sorted(tuple(sorted(v)) for v in urn.values())
            balanced = register_profile == urn_profile
            rec.ballot_box_state = "balanced" if balanced else "unbalanced"
            rec.ballot_box_detail = _(
                "%(issued)d bulletin(s) remis à %(voters)d votant(s) pour "
                "%(weight).4f voix ; %(cast)d déposé(s). Registre : "
                "%(rlines)d ligne(s) pour %(rvoters)d personne(s) et "
                "%(rweight).4f voix. %(verdict)s"
            ) % {
                "issued": len(box),
                "voters": len(urn),
                "weight": sum(weights),
                "cast": rec.ballot_cast_count,
                "rlines": len(rec.vote_ids),
                "rvoters": len(register),
                "rweight": sum(sum(v) for v in register.values()),
                "verdict": _("L'urne correspond au registre.")
                if balanced
                else _(
                    "L'urne ne correspond PAS au registre. Art. 1103 C.c.Q. : "
                    "une erreur dans le calcul des voix ouvre l'annulation de "
                    "la décision, pendant 90 jours."
                ),
            }

    @api.depends(
        "votes_for", "votes_against", "votes_abstain",
        "owners_for", "owners_against", "owners_abstain",
        "majority_type", "result_override",
        "ballot_mode", "ballot_cast_count",
        "assembly_id.votes_present", "assembly_id.owners_present",
        "assembly_id.total_votes", "assembly_id.total_owners",
        "assembly_id.is_reconvened",
    )
    def _compute_result(self):
        for rec in self:
            if rec.result_override:
                rec.result = rec.result_override
                rec.result_detail = _("Résultat corrigé à la main.")
                continue
            if not rec.vote_ids:
                rec.result = "pending"
                rec.result_detail = _("Aucun scrutin consigné.")
                continue
            if rec.ballot_mode == "secret" and not rec.ballot_cast_count:
                # Le registre existe dès l'ouverture du scrutin ; l'urne, elle,
                # peut rester vide. Sans ce cas, une résolution dont personne
                # n'a encore voté se lirait « rejetée », faute de voix pour.
                rec.result = "pending"
                rec.result_detail = _(
                    "Scrutin secret ouvert, aucun bulletin déposé."
                )
                continue
            passed, detail = rec._evaluate_majority()
            rec.result = "adopted" if passed else "rejected"
            rec.result_detail = detail

    def _evaluate_majority(self):
        """Applique la règle de majorité. Rend (adoptée ?, explication)."""
        self.ensure_one()
        assembly = self.assembly_id
        votes_present = assembly.votes_present
        owners_present = assembly.owners_present

        if self.majority_type == "art_1096":
            required = votes_present / 2.0
            passed = float_compare(
                self.votes_for, required, precision_digits=VOTE_DIGITS
            ) > 0
            return passed, _(
                "Art. 1096 : %(f).4f voix pour sur %(p).4f présentes ou "
                "représentées ; il en faut plus de %(r).4f. Les abstentions "
                "restent au dénominateur."
            ) % {"f": self.votes_for, "p": votes_present, "r": required}

        if self.majority_type == "art_1097":
            # Art. 1089 al. 2 : à une assemblée de reprise, ces décisions
            # exigent en plus que les présents représentent au moins la
            # majorité des voix de tous les copropriétaires.
            if assembly.is_reconvened:
                half_of_all = assembly.total_votes / 2.0
                if float_compare(
                    votes_present, half_of_all, precision_digits=VOTE_DIGITS
                ) < 0:
                    return False, _(
                        "Art. 1089 al. 2 : à une assemblée de reprise, une "
                        "décision de l'art. 1097 exige que les présents "
                        "représentent au moins la majorité des voix de tous les "
                        "copropriétaires. Présents : %(p).4f sur %(t).4f."
                    ) % {"p": votes_present, "t": assembly.total_votes}
            # Voix seulement. Aucune condition en nombre : la Loi 16 l'a
            # retirée le 10 janvier 2020 (2019, c. 28, a. 53). Voir l'en-tête.
            votes_required = votes_present * 0.75
            votes_ok = float_compare(
                self.votes_for, votes_required, precision_digits=VOTE_DIGITS
            ) >= 0
            return votes_ok, _(
                "Art. 1097 : %(f).4f voix pour sur %(r).4f requises, soit les "
                "trois quarts de %(p).4f voix présentes ou représentées "
                "(%(vstate)s). Depuis le 10 janvier 2020, cet article ne pose "
                "plus de condition sur le nombre de copropriétaires : "
                "%(of)d sur %(op)d ici, sans effet sur le résultat."
            ) % {
                "f": self.votes_for, "r": votes_required, "p": votes_present,
                "vstate": _("atteint") if votes_ok else _("non atteint"),
                "of": self.owners_for, "op": owners_present,
            }

        # art_1098 : les dénominateurs portent sur TOUT le syndicat.
        total_owners = assembly.total_owners
        total_votes = assembly.total_votes
        owners_required = total_owners * 0.75
        votes_required = total_votes * 0.90
        owners_ok = self.owners_for >= owners_required
        votes_ok = float_compare(
            self.votes_for, votes_required, precision_digits=VOTE_DIGITS
        ) >= 0
        return owners_ok and votes_ok, _(
            "Art. 1098 : %(of)d copropriétaire(s) pour sur %(ot)d au registre "
            "(il en faut %(orq).2f) et %(f).4f voix pour sur %(vt).4f au total "
            "(il en faut %(vrq).4f). Ces seuils se mesurent sur tous les "
            "copropriétaires, présents ou non."
        ) % {
            "of": self.owners_for, "ot": total_owners, "orq": owners_required,
            "f": self.votes_for, "vt": total_votes, "vrq": votes_required,
        }

    @api.constrains("result_override", "override_reason")
    def _check_override_reason(self):
        for rec in self:
            if rec.result_override and not rec.override_reason:
                raise ValidationError(
                    _(
                        "Une correction manuelle du résultat doit être motivée. "
                        "Renseignez la justification."
                    )
                )

    def write(self, vals):
        """Le mode de scrutin se fige dès que le scrutin est ouvert.

        Basculer de « à main levée » à « secret » une fois les bulletins remis
        changerait la source du décompte sous une résolution déjà votée : les
        voix consignées d'un côté cesseraient d'être lues, celles de l'autre
        n'existeraient pas. Le résultat basculerait sans que rien ne le dise.
        """
        if "ballot_mode" in vals:
            for resolution in self:
                if resolution.ballot_mode == vals["ballot_mode"]:
                    continue
                if resolution.vote_ids or resolution.secret_ballot_ids:
                    raise UserError(
                        _(
                            "Le scrutin de « %s » est déjà ouvert : son mode ne "
                            "change plus. Reprenez le scrutin à zéro, ou portez "
                            "la question à une nouvelle résolution."
                        )
                        % resolution.name
                    )
        return super().write(vals)

    def action_load_ballot(self):
        """Ouvre le scrutin : une ligne par membre présent ou représenté."""
        for resolution in self:
            if resolution.assembly_id.state == "closed":
                raise UserError(_("L'assemblée est clôturée."))
            if resolution.ballot_mode == "secret":
                # Les récépissés ne s'affichent qu'une fois. Ouvrir deux
                # scrutins d'un même geste n'en montrerait qu'un, et les codes
                # de l'autre seraient perdus sans que rien ne le signale : les
                # bulletins resteraient dans l'urne, indéposables.
                if len(self) > 1:
                    raise UserError(
                        _(
                            "Un scrutin secret s'ouvre résolution par "
                            "résolution : les récépissés ne s'affichent qu'une "
                            "fois et ne se retrouvent pas."
                        )
                    )
                return resolution._open_secret_ballot()
            resolution._open_show_of_hands()
        return True

    def _open_show_of_hands(self):
        self.ensure_one()
        existing = set(self.vote_ids.mapped("attendance_id").ids)
        rows = [
            {"resolution_id": self.id, "attendance_id": attendance.id}
            for attendance in self.assembly_id._ballot_attendance()
            if attendance.id not in existing
        ]
        if rows:
            self.env["bf.property.vote"].create(rows)
        return True

    def _open_secret_ballot(self):
        """Remet les bulletins, d'un seul tenant, et rend la liste à distribuer.

        ⚠️ D'un seul tenant, et c'est une contrainte de conception, pas une
        commodité. Rouvrir le scrutin pour un retardataire supposerait de
        savoir quelle clé de votant lui appartient déjà — donc de conserver le
        lien personne / bulletin que tout ce modèle existe pour ne pas garder.
        Un scrutin se remet à zéro tant que rien n'a été déposé ; passé le
        premier dépôt, il se termine, comme un scrutin papier.
        """
        self.ensure_one()
        Ballot = self.env["bf.property.secret.ballot"]
        if self.secret_ballot_ids or self.vote_ids:
            raise UserError(
                _(
                    "Le scrutin secret est déjà ouvert. Il ne se rouvre pas "
                    "pour ajouter un votant : reprenez-le à zéro tant qu'aucun "
                    "bulletin n'a été déposé."
                )
            )
        attending = self.assembly_id._ballot_attendance()
        if not attending:
            raise UserError(
                _("Aucun membre présent ou représenté qui dispose d'une voix : "
                  "il n'y a personne à qui remettre un bulletin.")
            )
        self.env["bf.property.vote"].create(
            [
                {
                    "resolution_id": self.id,
                    "attendance_id": attendance.id,
                    # Le registre ne porte pas de choix : c'est ce qui le sépare
                    # de l'urne.
                    "choice": False,
                    "ballot_issued": True,
                }
                for attendance in attending
            ]
        )
        keys = {
            partner.id: secrets.token_hex(8)
            for partner in attending.mapped("partner_id")
        }
        payload, distribution = [], []
        for attendance in attending:
            code = new_receipt_code()
            payload.append(
                {
                    "resolution_id": self.id,
                    "receipt_hash": hash_receipt(code),
                    "voter_key": keys[attendance.partner_id.id],
                    "votes": attendance.votes,
                }
            )
            distribution.append(
                _("%(unit)s — %(owner)s — %(votes).4f voix : %(code)s")
                % {
                    "unit": attendance.unit_id.display_name,
                    "owner": attendance.partner_id.name,
                    "votes": attendance.votes,
                    "code": code,
                }
            )
        # L'ordre des lignes en base ne doit rien dire de l'ordre de la feuille
        # de présence : sans ce brassage, le premier bulletin de l'urne serait
        # celui du premier nom de la liste.
        secrets.SystemRandom().shuffle(payload)
        Ballot.create(payload)
        wizard = self.env["bf.property.ballot.issue"].create(
            {"resolution_id": self.id, "distribution": "\n".join(distribution)}
        )
        self.message_post(
            body=_(
                "Scrutin secret ouvert : %(n)d bulletin(s) remis. Les "
                "récépissés ne sont affichés qu'une fois et ne sont conservés "
                "nulle part."
            )
            % {"n": len(payload)}
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Récépissés à distribuer"),
            "res_model": "bf.property.ballot.issue",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_reset_ballot(self):
        """Reprend le scrutin à zéro, tant qu'aucun bulletin n'a été déposé."""
        for resolution in self:
            if resolution.assembly_id.state == "closed":
                raise UserError(_("L'assemblée est clôturée."))
            if resolution.secret_ballot_ids.filtered("choice"):
                raise UserError(
                    _(
                        "Des bulletins ont déjà été déposés. Un scrutin entamé "
                        "ne se reprend pas : portez la question à une nouvelle "
                        "résolution."
                    )
                )
            resolution.secret_ballot_ids.unlink()
            resolution.vote_ids.unlink()
            resolution.message_post(body=_("Scrutin repris à zéro avant tout dépôt."))
        return True

    def action_open_deposit(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Déposer un bulletin"),
            "res_model": "bf.property.ballot.deposit",
            "view_mode": "form",
            "target": "new",
            "context": {"default_resolution_id": self.id},
        }

    def action_open_receipt_check(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Vérifier un récépissé"),
            "res_model": "bf.property.ballot.receipt",
            "view_mode": "form",
            "target": "new",
            "context": {"default_resolution_id": self.id},
        }

    def action_verify_ballot_box(self):
        """Consigne le recomptage de l'urne au dossier de la résolution.

        Le contrôle lui-même est calculé en continu ; ce que cette action ajoute
        est une trace datée, versée au fil de la résolution. Un recomptage qui
        ne laisse rien derrière lui ne prouve rien à qui le demandera plus tard.
        """
        for resolution in self:
            if resolution.ballot_mode != "secret":
                raise UserError(
                    _("Cette résolution se vote à main levée : il n'y a pas d'urne.")
                )
            tally = _(
                "Pour : %(f).4f voix / %(of)d votant(s). Contre : %(a).4f voix "
                "/ %(oa)d. Abstention : %(b).4f voix / %(ob)d. Non déposées : "
                "%(n).4f voix."
            ) % {
                "f": resolution.votes_for, "of": resolution.owners_for,
                "a": resolution.votes_against, "oa": resolution.owners_against,
                "b": resolution.votes_abstain, "ob": resolution.owners_abstain,
                "n": resolution.votes_not_cast,
            }
            secrecy = _(
                "Secret du scrutin : %(n)d bulletin(s) seul(s) de leur poids "
                "dans l'urne, donc rattachables à leur auteur par le registre."
            ) % {"n": resolution.secret_exposure_count}
            resolution.message_post(
                body=Markup("<p>%s</p><p>%s</p><p>%s</p>")
                % (resolution.ballot_box_detail or "", tally, secrecy)
            )
        return True


class BfPropertyVote(models.Model):
    _name = "bf.property.vote"
    _description = "Voix exprimée"
    _order = "resolution_id, id"

    resolution_id = fields.Many2one(
        "bf.property.resolution",
        string="Résolution",
        required=True,
        ondelete="cascade",
        index=True,
    )
    attendance_id = fields.Many2one(
        "bf.property.assembly.attendance",
        string="Membre",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="resolution_id.company_id", store=True, string="Société"
    )
    partner_id = fields.Many2one(
        related="attendance_id.partner_id", store=True, string="Copropriétaire"
    )
    unit_id = fields.Many2one(
        related="attendance_id.unit_id", store=True, string="Fraction"
    )
    votes = fields.Float(
        related="attendance_id.votes", store=True, digits=(16, VOTE_DIGITS),
        string="Voix"
    )
    choice = fields.Selection(
        [
            ("for", "Pour"),
            ("against", "Contre"),
            ("abstain", "Abstention"),
        ],
        string="Vote",
        default="abstain",
        help="Au scrutin secret, cette colonne reste vide et le demeure : le "
             "registre dit qui a reçu un bulletin, jamais ce qu'il en a fait.",
    )
    ballot_issued = fields.Boolean(
        string="Bulletin remis",
        help="Art. 1089.1 C.c.Q. : au scrutin secret, la seule chose que le "
             "registre retient d'un votant est qu'un bulletin lui a été remis.",
    )

    _sql_constraints = [
        (
            "unique_ballot",
            "UNIQUE(resolution_id, attendance_id)",
            "Ce membre a déjà voté sur cette résolution.",
        ),
    ]

    @api.constrains("choice", "resolution_id")
    def _check_choice_matches_mode(self):
        """À main levée, une ligne de scrutin sans vote n'a pas de sens.

        Le champ a cessé d'être obligatoire pour que le registre d'un scrutin
        secret puisse rester vide. La contrainte rend l'obligation là où elle
        s'appliquait, sans la rendre là où elle briserait le secret.
        """
        for rec in self:
            if rec.resolution_id.ballot_mode == "open" and not rec.choice:
                raise ValidationError(
                    _(
                        "Un scrutin à main levée consigne le vote de chacun : "
                        "renseignez le vote de %s."
                    )
                    % (rec.partner_id.display_name or "")
                )
            if rec.resolution_id.ballot_mode == "secret" and rec.choice:
                raise ValidationError(
                    _(
                        "Ce scrutin est secret : le vote se dépose dans l'urne "
                        "par récépissé, jamais sur la ligne de registre qui "
                        "nomme le votant."
                    )
                )

    @api.constrains("resolution_id", "attendance_id")
    def _check_same_assembly(self):
        for rec in self:
            if rec.attendance_id.assembly_id != rec.resolution_id.assembly_id:
                raise ValidationError(
                    _(
                        "Ce membre appartient à une autre assemblée que la "
                        "résolution soumise au vote."
                    )
                )
