# -*- coding: utf-8 -*-
import json
import random

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import clean_context

from odoo.addons.bf_live_base.models.color_utils import normalize_hex, palette_color, text_color

# 🔴 Le tirage se fait ICI, pas dans le navigateur. Un générateur de module, pour
# qu'un essai puisse le remplacer sans qu'un appel RPC puisse forcer un gagnant :
# aucun paramètre, aucune clé de contexte n'ouvre cette porte en production.
_rng = random.SystemRandom()


class BfWheel(models.Model):
    _name = "bf.wheel"
    _description = "Roue de fortune"
    _order = "name, id"

    name = fields.Char(string="Nom", required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", index=True,
        default=lambda self: self.env.company)
    note = fields.Text(string="Note")

    # Les ponts ajoutent leurs sources par `selection_add` : la roue du socle ne
    # connaît que ses propres segments.
    source = fields.Selection(
        [("manual", "Les segments de la roue")],
        string="Garniture", required=True, default="manual",
        help="D'où la roue tire ses candidats.")

    segment_ids = fields.One2many("bf.wheel.segment", "wheel_id", string="Segments")
    spin_ids = fields.One2many("bf.wheel.spin", "wheel_id", string="Tirages", readonly=True)

    remove_drawn = fields.Boolean(
        string="Retirer le gagnant de la roue", default=False,
        help="Le segment tiré ne repart plus : la roue se vide tirage après tirage.")
    claim_enabled = fields.Boolean(
        string="Demander ses coordonnées à la personne tirée", default=False,
        help="Un tirage qui donne un prix doit pouvoir rejoindre qui l'a gagné.")
    claim_retention_days = fields.Integer(
        string="Conserver les coordonnées (jours)", default=90,
        help="Passé ce délai, une tâche planifiée efface le nom, la coordonnée et la "
             "liste des candidats des tirages. Le gagnant et les comptes restent. "
             "À zéro, rien n'est effacé automatiquement.")

    segment_count = fields.Integer(string="Nombre de segments", compute="_compute_counts")
    available_count = fields.Integer(string="Segments en jeu", compute="_compute_counts")
    spin_count = fields.Integer(string="Nombre de tirages", compute="_compute_counts")
    last_spin_id = fields.Many2one("bf.wheel.spin", string="Dernier tirage", compute="_compute_counts")

    _sql_constraints = [
        ("claim_retention_positive",
         "CHECK (claim_retention_days >= 0)",
         "Un délai de conservation ne peut pas être négatif."),
    ]

    @api.depends("segment_ids", "segment_ids.drawn", "spin_ids")
    def _compute_counts(self):
        for wheel in self:
            wheel.segment_count = len(wheel.segment_ids)
            wheel.available_count = len(wheel.segment_ids.filtered(lambda s: not s.drawn))
            wheel.spin_count = len(wheel.spin_ids)
            wheel.last_spin_id = wheel.spin_ids[:1]

    # ------------------------------------------------------------------
    # Les candidats
    # ------------------------------------------------------------------
    def _candidates(self):
        """La liste sur laquelle la roue tire, dans l'ordre où elle se dessine.

        Chaque candidat : `label` (ce qui s'écrit sur le segment), `weight`
        (entier ≥ 1), `color` (hex), `segment_id` (ou False quand le candidat ne
        vient pas d'un segment, par exemple des participants d'une séance).

        Les ponts surchargent cette méthode selon `source`. ⚠️ Un pont renvoie
        TOUJOURS une liste, jamais un ensemble vide déguisé en False.
        """
        self.ensure_one()
        if self.source != "manual":
            return []
        segments = self.segment_ids.filtered(lambda s: not s.drawn).sorted(
            key=lambda s: (s.sequence, s.id))
        return [{
            "label": segment.name,
            "weight": max(1, segment.weight),
            "color": segment.color_hex,
            "segment_id": segment.id,
        } for segment in segments]

    def get_display_data(self):
        """Ce que l'écran doit dessiner AVANT qu'on touche au bouton.

        Publique, donc atteignable par RPC : elle vérifie elle-même le droit de
        lecture au lieu de s'en remettre à l'écran qui l'appelle.
        """
        self.ensure_one()
        self.check_access("read")
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "claim_enabled": self.claim_enabled,
            "remove_drawn": self.remove_drawn,
            "can_spin": self._can_spin(raise_exception=False),
            "candidates": self._payload(self._candidates()),
        }

    @staticmethod
    def _payload(candidates):
        """Ce que l'écran reçoit d'un candidat, l'encre de l'étiquette comprise.

        🔴 L'encre se calcule ICI, pour toutes les garnitures : sans elle, l'écran
        posait `fill=""` et les étiquettes retombaient au noir par défaut, illisible
        sur les parts foncées. Mesuré au navigateur, invisible aux essais Python,
        qui lisaient l'encre sur le SEGMENT sans vérifier qu'elle parte à l'écran.
        """
        charge = []
        for index, candidate in enumerate(candidates):
            # Un candidat qui ne vient pas d'un segment (les participants d'une
            # séance, les réponses d'une question) n'a pas de couleur à lui : il
            # prend celle de son rang, sinon la roue sort d'une seule teinte.
            couleur = normalize_hex(candidate.get("color")) or palette_color(index)
            charge.append({
                "label": candidate["label"],
                "weight": candidate["weight"],
                "color": couleur,
                "text_color": text_color(couleur),
            })
        return charge

    # ------------------------------------------------------------------
    # Le tirage
    # ------------------------------------------------------------------
    def _can_spin(self, raise_exception=True):
        """Qui peut faire tourner : qui lit la roue et appartient au groupe."""
        self.ensure_one()
        if self.env.su:
            return True
        if not self.has_access("read") or not self.env.user.has_group("bf_wheel.group_bf_wheel_user"):
            if raise_exception:
                raise AccessError(_(
                    "Faire tourner une roue demande le groupe « Roue de fortune / "
                    "Utilisateur » et le droit de lire cette roue."))
            return False
        return True

    @staticmethod
    def _draw_index(weights):
        """Le rang gagnant, chaque poids pesant ce qu'il dit.

        On tire un entier dans le poids total et on marche la liste : pas de
        flottant, donc pas d'arrondi qui prêterait un cheveu de chance de plus
        au dernier segment.
        """
        total = sum(weights)
        if total <= 0:
            raise UserError(_("La roue n'a aucun candidat à tirer."))
        ticket = _rng.randrange(total)
        running = 0
        for index, weight in enumerate(weights):
            running += weight
            if ticket < running:
                return index
        return len(weights) - 1  # inatteignable : la somme borne le billet

    def action_spin(self):
        """Tire, écrit le registre, et rend à l'écran de quoi rejoindre le résultat."""
        self.ensure_one()
        self._can_spin()
        candidates = self._candidates()
        if not candidates:
            raise UserError(_(
                "La roue « %(name)s » n'a aucun candidat en jeu.", name=self.name))
        index = self._draw_index([c["weight"] for c in candidates])
        winner = candidates[index]

        # Le registre s'écrit en sudo : faire tourner n'est pas écrire. ⚠️ Et sans
        # le contexte de l'appelant, dont les `default_*` fixeraient des champs du
        # registre à l'insu de tout le monde.
        spin = self.env["bf.wheel.spin"].sudo().with_context(
            **clean_context(self.env.context)).create({
                "wheel_id": self.id,
                "winner_label": winner["label"],
                "segment_id": winner["segment_id"] or False,
                "winner_index": index,
                "candidate_count": len(candidates),
                "weight_total": sum(c["weight"] for c in candidates),
                "source": self.source,
                "spun_by_id": self.env.user.id,
                "candidates": json.dumps(
                    [{"label": c["label"], "weight": c["weight"]} for c in candidates],
                    ensure_ascii=False),
            })

        if self.remove_drawn and winner["segment_id"]:
            self.env["bf.wheel.segment"].sudo().browse(winner["segment_id"]).write({"drawn": True})

        return {
            "spin_id": spin.id,
            "winner_index": index,
            "winner_label": winner["label"],
            "claim_enabled": self.claim_enabled,
            # La liste EXACTE d'où sort le résultat : l'écran la redessine avant de
            # tourner. Entre l'affichage et le bouton, un participant a pu rejoindre
            # la séance, et la roue de l'écran ne serait plus celle du tirage.
            "candidates": self._payload(candidates),
        }

    def action_reset_drawn(self):
        """Remet tous les segments en jeu. Le registre, lui, ne bouge pas."""
        for wheel in self:
            wheel.segment_ids.filtered("drawn").write({"drawn": False})

    def action_open_projector(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.client",
            "tag": "bf_wheel.projector",
            "name": self.name,
            "params": {"wheel_id": self.id},
            "target": "fullscreen",
        }

    def action_view_spins(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tirages de « %(name)s »", name=self.name),
            "res_model": "bf.wheel.spin",
            "view_mode": "list,form",
            "domain": [("wheel_id", "=", self.id)],
            "context": {"default_wheel_id": self.id, "search_default_wheel_id": self.id},
        }
