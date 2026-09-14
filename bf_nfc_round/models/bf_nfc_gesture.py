"""Le geste « point de tournée » : un passage, et où on en est.

⚠️ Écrit en sudo après la lecture du point avec les droits de qui tape : faire la
ronde n'est pas un privilège de gestion. Le passage porte la personne qui tape,
jamais le compte d'une pastille signée.

⚠️ La tournée ouverte se retrouve par personne ET par heure du tapotement : deux
agents qui font la même ronde en même temps font deux passages, et un point tapé
le lendemain commence une nouvelle tournée plutôt que de compléter l'ancienne.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfNfcGesture(models.Model):
    _inherit = "bf.nfc.gesture"

    kind = fields.Selection(
        selection_add=[("round_checkpoint", "Point de tournée")],
        ondelete={"round_checkpoint": "cascade"},
    )

    def _executer_round_checkpoint(self, tag, tap, params):
        self.ensure_one()
        self._exiger_une_personne(params)
        point = tag._cible()
        if not point or point._name != "bf.nfc.round.checkpoint":
            raise UserError(_("Cette pastille ne désigne pas un point de tournée."))
        point = point.sudo()
        tournee = point.round_id
        moment = self._moment(params)
        moi = self.env.user
        Passage = self.env["bf.nfc.round.run"].sudo()
        ouverte = Passage.search([
            ("round_id", "=", tournee.id), ("user_id", "=", moi.id), ("state", "=", "running"),
            ("date_start", "<=", moment), ("date_start", ">=", moment - tournee._duree()),
        ], order="date_start desc", limit=1)
        if not ouverte:
            ouverte = Passage.create({"round_id": tournee.id, "user_id": moi.id, "date_start": moment})

        deja = ouverte.passage_ids.filtered(lambda p: p.checkpoint_id == point)
        if deja:
            return {"titre": tournee.name, "url": None,
                    "message": _("« %(point)s » était déjà tapé à %(heure)s.",
                                 point=point.name, heure=self._heure(deja[0].tapped_at))}

        precedents = tournee.checkpoint_ids.filtered(
            lambda p: (p.sequence, p.id) < (point.sequence, point.id))
        hors_ordre = tournee.strict_order and bool(precedents - ouverte.passage_ids.checkpoint_id)
        self.env["bf.nfc.round.passage"].sudo().create({
            "run_id": ouverte.id, "checkpoint_id": point.id, "user_id": moi.id,
            "tapped_at": moment, "offline": bool(params.get("differe")), "out_of_order": hors_ordre,
        })
        faits = ouverte.passage_ids.checkpoint_id
        total = len(tournee.checkpoint_ids)
        if len(faits) >= total:
            ouverte.write({"state": "done", "date_end": moment})
            minutes = int((moment - ouverte.date_start).total_seconds() // 60)
            return {"titre": tournee.name, "url": None,
                    "message": _("Tournée complète : %(n)s points en %(min)s min.", n=total, min=minutes)}
        suivant = (tournee.checkpoint_ids - faits)[:1]
        message = _("Point %(n)s sur %(total)s. Prochain : %(suivant)s.",
                    n=len(faits), total=total, suivant=suivant.name)
        if hors_ordre:
            message = _("Hors ordre : un point précédent n'a pas été tapé. ") + message
        return {"titre": tournee.name, "url": None, "message": message}
