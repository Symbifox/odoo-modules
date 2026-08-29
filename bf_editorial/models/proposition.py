# -*- coding: utf-8 -*-
"""Proposer le prochain article.

La règle vivait dans une note qui prenait deux publications de retard à chaque
passe. Ici elle lit la base : la cadence, le ratio par pilier, les dépendances
et l'état de préparation sortent des enregistrements, jamais d'un souvenir.

La proposition s'explique. Un classement sans motif n'aide personne à trancher.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Pondérations du classement. Ce sont des choix éditoriaux, pas des constantes
# techniques : elles se relisent et se discutent.
W_PILLAR_OWED = 50      # le pilier en retard sur sa cible
W_READY = 30            # prêt à publier, garde de pré-vol verte
W_NEAR_READY = 15       # plancher de mots atteint, reste des broutilles
W_PLANNED_DUE = 20      # date prévue atteinte ou dépassée
W_UNBLOCKS = 10         # publier celle-ci en débloque d'autres
W_STALE_DRAFT = 5       # un brouillon qui dort depuis longtemps
W_MAX_SHORT = 45        # pénalité maximale pour un texte très court


class EditorialProposal(models.TransientModel):
    _name = "bf.editorial.proposal"
    _description = "Proposition du prochain article"

    calendar_id = fields.Many2one(
        "bf.editorial.calendar", string="Calendrier", required=True,
        default=lambda self: self._default_calendar(),
    )
    cadence_note = fields.Text(string="Cadence", readonly=True)
    ratio_note = fields.Text(string="Ratio", readonly=True)
    owed_pillar_id = fields.Many2one(
        "blog.tag.category", string="Pilier dû", readonly=True,
    )
    recommendation = fields.Text(string="Recommandation", readonly=True)
    line_ids = fields.One2many(
        "bf.editorial.proposal.line", "proposal_id", string="Candidats",
        readonly=True,
    )
    blocked_note = fields.Text(string="Écartées", readonly=True)

    @api.model
    def _default_calendar(self):
        """Le calendrier à proposer quand personne n'en a nommé un.

        Celui dont l'utilisateur est responsable d'abord : sur une instance à
        plusieurs flux, c'est le sien qu'il vient voir.
        """
        Calendar = self.env["bf.editorial.calendar"]
        mine = Calendar.search(
            [("user_id", "=", self.env.uid)], order="sequence", limit=1,
        )
        return mine or Calendar.search([], order="sequence", limit=1)

    @api.model
    def action_open_next(self):
        """Proposer sans passer par la fiche d'un calendrier.

        La proposition ne vivait que sur le formulaire du calendrier, un écran
        de paramétrage où personne ne va pour se demander quoi publier.
        """
        calendar = self._default_calendar()
        if not calendar:
            raise UserError(_(
                "Aucun calendrier éditorial n'est défini : il n'y a rien à"
                " proposer. Créez-en un sous Atelier éditorial > Calendriers."
            ))
        proposal = self.create({"calendar_id": calendar.id})
        proposal.action_compute()
        return proposal._open()

    def _open(self):
        """Ouvrir la proposition, calculée, dans sa fenêtre."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Proposition"),
            "res_model": "bf.editorial.proposal",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_recompute(self):
        """Recalculer et rester à l'écran, changement de calendrier compris."""
        self.ensure_one()
        self.action_compute()
        return self._open()

    def action_compute(self):
        """Évaluer le calendrier et classer les candidats."""
        self.ensure_one()
        calendar = self.calendar_id
        self.line_ids.unlink()

        self.cadence_note = self._cadence_note(calendar)
        self.ratio_note = calendar.ratio_summary
        owed = self._owed_pillar(calendar)
        self.owed_pillar_id = owed.id if owed else False

        candidates, rejected = self._candidates(calendar)
        scored = []
        for entry in candidates:
            score, reasons = self._score(entry, calendar, owed)
            scored.append((score, entry, reasons))
        scored.sort(key=lambda triple: -triple[0])

        for rank, (score, entry, reasons) in enumerate(scored[:8], start=1):
            self.env["bf.editorial.proposal.line"].create({
                "proposal_id": self.id,
                "sequence": rank,
                "entry_id": entry.id,
                "score": score,
                "rationale": "\n".join("• " + r for r in reasons),
            })

        self.blocked_note = "\n".join(rejected) if rejected else False
        self.recommendation = self._recommendation(scored, calendar, owed)
        return True

    # --- éléments de décision --------------------------------------------
    def _cadence_note(self, calendar):
        if not calendar.cadence_days:
            return _("Aucune cadence définie pour ce calendrier.")
        if not calendar.last_published_date:
            return _("Aucune publication à ce jour : le créneau est ouvert.")
        return _(
            "Dernière publication il y a %(days)s jour(s), cadence visée"
            " %(cadence)s. Créneau %(state)s.",
            days=calendar.days_since_last,
            cadence=calendar.cadence_days,
            state=_("dû") if calendar.slot_due else _("pas encore dû"),
        )

    def _owed_pillar(self, calendar):
        """Le pilier le plus en retard sur sa cible."""
        published = calendar.entry_ids.filtered(
            lambda e: e.published_date and e.stage_id.is_closing
        ).sorted("published_date", reverse=True)[:10]
        if not published or not calendar.pillar_ids:
            return self.env["blog.tag.category"].browse()

        total = len(published)
        worst, worst_gap = None, 0.0
        for pillar in calendar.pillar_ids:
            if not pillar.target_share:
                continue
            count = len(published.filtered(lambda e: e.pillar_id == pillar))
            share = 100.0 * count / total
            gap = pillar.target_share - share
            if gap > worst_gap:
                worst, worst_gap = pillar, gap
        return worst or self.env["blog.tag.category"].browse()

    def _candidates(self, calendar):
        """Les entrées publiables, et le motif de celles qu'on écarte."""
        pool = calendar.entry_ids.filtered(
            lambda e: (
                e.active
                and not e.published_date
                and not e.stage_id.is_closing
                and not e.stage_id.is_abandoned
            )
        )
        keep, rejected = self.env["bf.editorial.entry"].browse(), []
        for entry in pool:
            if entry.is_blocked:
                rejected.append("%s — %s" % (
                    entry.name, (entry.blocking_summary or "").replace("\n", " ; "),
                ))
                continue
            keep |= entry
        return keep, rejected

    def _score(self, entry, calendar, owed):
        """Noter un candidat, en gardant la trace de chaque point accordé."""
        score, reasons = 0, []

        if owed and entry.pillar_id == owed:
            score += W_PILLAR_OWED
            reasons.append(_("Comble le pilier en retard (%s).", owed.name))

        if entry.preflight_ok:
            score += W_READY
            reasons.append(_("Garde de pré-vol verte."))
        elif (
            calendar.word_floor
            and entry.word_count >= calendar.word_floor
        ):
            score += W_NEAR_READY
            reasons.append(_(
                "Plancher de mots atteint (%s), reste des points de contrôle.",
                entry.word_count,
            ))
        elif calendar.word_floor:
            # Proportionnelle au manque : un texte à 1 667 mots perd peu, une
            # coquille vide perd tout. Sans ça, tous les brouillons courts
            # arrivent ex aequo et le premier rang se joue au hasard.
            deficit = max(0, calendar.word_floor - entry.word_count)
            penalty = min(W_MAX_SHORT,
                          round(W_MAX_SHORT * deficit / calendar.word_floor))
            score -= penalty
            if entry.word_count < 50:
                reasons.append(_(
                    "Quasiment vide : %s mots. C'est un titre réservé, pas un"
                    " brouillon.", entry.word_count,
                ))
            else:
                reasons.append(_(
                    "Sous le plancher : %(a)s mots contre %(b)s.",
                    a=entry.word_count, b=calendar.word_floor,
                ))

        today = fields.Date.context_today(self)
        if entry.planned_date and entry.planned_date <= today:
            score += W_PLANNED_DUE
            reasons.append(_("Date prévue atteinte (%s).", entry.planned_date))

        waiting = entry.blocks_ids.filtered(lambda e: not e.stage_id.is_closing)
        if waiting:
            score += W_UNBLOCKS
            reasons.append(_(
                "Débloque %s autre(s) entrée(s).", len(waiting),
            ))

        if entry.create_date:
            age_days = (fields.Datetime.now() - entry.create_date).days
            if age_days > 60:
                score += W_STALE_DRAFT
                reasons.append(_("Brouillon en attente depuis %s jours.", age_days))

        if entry.version_drift:
            score -= W_READY
            reasons.append(_(
                "⚠️ Le module documenté a bougé (%(a)s → %(b)s) : à refact-checker"
                " avant de sortir.",
                a=entry.source_version, b=entry.current_version,
            ))

        if not reasons:
            reasons.append(_("Aucun signal particulier."))
        return score, reasons

    def _recommendation(self, scored, calendar, owed):
        if not scored:
            return _(
                "Aucun candidat publiable. Toutes les entrées ouvertes sont"
                " bloquées, ou le calendrier est vide. Il faut écrire du neuf."
            )
        score, entry, _reasons = scored[0]
        lines = [_("Recommandation : %s", entry.name)]
        if not calendar.slot_due:
            lines.append(_(
                "⚠️ Le créneau n'est pas encore dû. Rien n'oblige à publier"
                " aujourd'hui."
            ))
        if owed:
            lines.append(_("Le ratio réclame un billet « %s ».", owed.name))
        if not entry.preflight_ok:
            lines.append(_(
                "Elle n'est pas prête telle quelle :\n%s",
                entry.preflight_summary,
            ))
        return "\n".join(lines)


class EditorialProposalLine(models.TransientModel):
    _name = "bf.editorial.proposal.line"
    _description = "Candidat à la publication"
    _order = "sequence, id"

    proposal_id = fields.Many2one(
        "bf.editorial.proposal", string="Proposition", required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(string="Rang")
    entry_id = fields.Many2one("bf.editorial.entry", string="Entrée")
    score = fields.Integer(string="Note")
    rationale = fields.Text(string="Motifs")

    def action_open_entry(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.editorial.entry",
            "res_id": self.entry_id.id,
            "view_mode": "form",
        }
