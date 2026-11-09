# -*- coding: utf-8 -*-
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Les seuls champs qu'une main peut poser après coup : les coordonnées de la
# personne tirée. Tout le reste est le tirage lui-même.
CLAIM_FIELDS = frozenset(("claim_name", "claim_contact", "claim_date"))


class BfWheelSpin(models.Model):
    _name = "bf.wheel.spin"
    _description = "Tirage d'une roue de fortune"
    _order = "date desc, id desc"

    wheel_id = fields.Many2one(
        "bf.wheel", string="Roue", required=True, ondelete="cascade",
        index=True, readonly=True)
    company_id = fields.Many2one(related="wheel_id.company_id", store=True, index=True)
    date = fields.Datetime(
        string="Tiré le", required=True, readonly=True,
        default=fields.Datetime.now, copy=False)
    spun_by_id = fields.Many2one(
        "res.users", string="Tiré par", readonly=True,
        default=lambda self: self.env.user, copy=False)
    winner_label = fields.Char(string="Gagnant", required=True, readonly=True)
    segment_id = fields.Many2one(
        "bf.wheel.segment", string="Segment", readonly=True, ondelete="set null")
    winner_index = fields.Integer(string="Rang gagnant", readonly=True)
    candidate_count = fields.Integer(string="Candidats", readonly=True)
    weight_total = fields.Integer(string="Poids total", readonly=True)
    source = fields.Char(string="Garniture", readonly=True)
    candidates = fields.Text(
        string="Candidats du tirage", readonly=True,
        help="La liste exacte sur laquelle la roue a tiré, telle qu'elle était.")

    claim_name = fields.Char(string="Nom de la personne tirée")
    claim_contact = fields.Char(string="Coordonnée")
    claim_date = fields.Datetime(string="Coordonnées reçues le", readonly=True)
    purged = fields.Boolean(
        string="Élagué", readonly=True, copy=False,
        help="Les coordonnées et la liste des candidats ont été effacées au terme "
             "du délai de conservation. Le gagnant et les comptes sont restés.")

    @api.depends("wheel_id.name", "winner_label", "date")
    def _compute_display_name(self):
        for spin in self:
            spin.display_name = "%s : %s" % (spin.wheel_id.name or "", spin.winner_label or "")

    # ------------------------------------------------------------------
    # Un registre ne se récrit pas
    # ------------------------------------------------------------------
    def write(self, vals):
        """Seules les coordonnées de la personne tirée s'ajoutent après coup."""
        if not self.env.su:
            interdits = set(vals) - CLAIM_FIELDS
            if interdits:
                raise UserError(_(
                    "Un tirage ne se récrit pas. Champs refusés : %(champs)s.",
                    champs=", ".join(sorted(interdits))))
            # Une coordonnée se note UNE fois. La corriger ensuite demande un
            # responsable : sans cela, qui peut écrire une coordonnée peut
            # remplacer celle d'un autre sans trace.
            if not self.env.user.has_group("bf_wheel.group_bf_wheel_manager"):
                deja = self.filtered(lambda s: s.claim_name)
                if deja:
                    raise UserError(_(
                        "La personne tirée de « %(tirage)s » est déjà notée. La "
                        "corriger demande un responsable.", tirage=deja[0].display_name))
        return super().write(vals)

    def unlink(self):
        """Personne n'efface un tirage à la main.

        ⚠️ Effacer la ROUE emporte ses tirages : la cascade est posée dans la
        base (`ondelete="cascade"`), et PostgreSQL ne passe pas par cette
        garde. C'est le seul chemin qui vide le registre, et il est volontaire.
        """
        if not self.env.su:
            raise UserError(_(
                "Un tirage ne s'efface pas : c'est la preuve du tirage. Effacer la "
                "roue entière emporte son registre."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Les coordonnées de la personne tirée
    # ------------------------------------------------------------------
    def action_claim(self, name, contact=None):
        """Note le nom (et une coordonnée) de la personne tirée."""
        self.ensure_one()
        if not self.wheel_id.claim_enabled:
            raise UserError(_(
                "La roue « %(name)s » ne demande pas les coordonnées de la personne tirée.",
                name=self.wheel_id.name))
        if self.purged:
            raise UserError(_("Ce tirage a été élagué : ses coordonnées sont parties."))
        if self.claim_name and not self.env.user.has_group("bf_wheel.group_bf_wheel_manager"):
            raise UserError(_(
                "La personne tirée est déjà notée : « %(nom)s ».", nom=self.claim_name))
        name = (name or "").strip()
        if not name:
            raise UserError(_("Il faut au moins un nom."))
        # `readonly=True` ne tient que l'écran : l'ORM accepte l'écriture, et la
        # garde du modèle laisse passer les trois champs de coordonnées.
        self.write({
            "claim_name": name,
            "claim_contact": (contact or "").strip() or False,
            "claim_date": fields.Datetime.now(),
        })
        return True

    def candidate_list(self):
        """La liste des candidats, relue du registre."""
        self.ensure_one()
        if not self.candidates:
            return []
        try:
            return json.loads(self.candidates)
        except ValueError:
            return []

    # ------------------------------------------------------------------
    # L'élagage
    # ------------------------------------------------------------------
    @api.model
    def _gc_claims(self, now=None):
        """Efface coordonnées et liste des candidats au terme du délai de la roue.

        Le gagnant, la date et les comptes restent : c'est ce qui prouve le
        tirage. Ce qui part, c'est le renseignement personnel : le nom de la
        personne tirée, sa coordonnée, et les pseudonymes de toute la salle.
        """
        now = now or fields.Datetime.now()
        wheels = self.env["bf.wheel"].sudo().with_context(active_test=False).search(
            [("claim_retention_days", ">", 0)])
        elagues = 0
        for wheel in wheels:
            limite = now - timedelta(days=wheel.claim_retention_days)
            spins = self.sudo().search([
                ("wheel_id", "=", wheel.id),
                ("date", "<", limite),
                ("purged", "=", False),
                "|", "|",
                ("claim_name", "!=", False),
                ("claim_contact", "!=", False),
                ("candidates", "!=", False),
            ])
            if spins:
                spins.write({
                    "claim_name": False,
                    "claim_contact": False,
                    "claim_date": False,
                    "candidates": False,
                    "purged": True,
                })
                elagues += len(spins)
        return elagues
