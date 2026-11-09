# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

from odoo.addons.bf_live_base.models.color_utils import palette_color, text_color

# ⚠️ Six équipes au maximum, et pas parce que la palette en compte douze : un
# tableau projeté au fond d'une salle ne se lit plus au-delà de six blocs. La
# couleur vient de la palette mesurée de la roue, et l'encre de son calcul de
# contraste : une étiquette d'équipe se lit de loin ou ne sert à rien.
COULEURS = (
    ("bleue", "Bleue"),
    ("orange", "Orange"),
    ("verte", "Verte"),
    ("rouge", "Rouge"),
    ("violette", "Violette"),
    ("turquoise", "Turquoise"),
)

MAX_EQUIPES = len(COULEURS)


def couleur_fond(cle):
    """Le fond d'une clé de couleur, pris au rang de la palette de la roue."""
    rangs = [paire[0] for paire in COULEURS]
    return palette_color(rangs.index(cle) if cle in rangs else 0)


class BfLiveTeam(models.Model):
    _name = "bf.live.team"
    _description = "Équipe d'une séance en direct"
    _order = "survey_id, sequence, id"

    name = fields.Char(string="Nom", required=True)
    survey_id = fields.Many2one(
        "survey.survey", string="Sondage", required=True,
        ondelete="cascade", index=True)
    sequence = fields.Integer(string="Séquence", default=10)
    color_key = fields.Selection(
        COULEURS, string="Couleur", required=True, default=COULEURS[0][0])
    answer_id = fields.Many2one(
        "survey.question.answer", string="Réponse qui mène ici",
        ondelete="set null",
        help="Quand les équipes viennent d'une question, chaque réponse en ouvre une.")
    member_ids = fields.One2many(
        "survey.user_input", "bf_team_id", string="Participants")
    member_count = fields.Integer(
        string="Participants (toutes les séances)", compute="_compute_members")
    score_total = fields.Float(
        string="Points (toutes les séances)", compute="_compute_members", digits=(10, 2))
    color = fields.Char(string="Fond", compute="_compute_color")
    text_color = fields.Char(string="Encre", compute="_compute_color")

    _sql_constraints = [
        # Une réponse mène à UNE équipe. Postgres laisse passer autant de NULL
        # qu'on veut, donc les équipes tirées au hasard ne s'y heurtent jamais.
        ("answer_unique", "unique(survey_id, answer_id)",
         "Une réponse ne peut mener qu'à une seule équipe."),
    ]

    @api.depends("member_ids")
    def _compute_members(self):
        """Les comptes de TOUTES les séances du sondage, pas de celle en cours.

        ⚠️ Une équipe survit à sa séance : le tableau projeté, lui, se borne à la
        séance en cours (voir `_bf_live_scoreboard`). Les deux chiffres sont donc
        différents, et c'est voulu : ici c'est l'histoire, là c'est le direct.
        """
        comptes = dict.fromkeys(self.ids, (0, 0.0))
        if self.ids:
            for equipe, compte, points in self.env["survey.user_input"]._read_group(
                    [("bf_team_id", "in", self.ids)],
                    groupby=["bf_team_id"],
                    aggregates=["__count", "scoring_total:sum"]):
                comptes[equipe.id] = (compte, points or 0.0)
        for equipe in self:
            compte, points = comptes.get(equipe.id, (0, 0.0))
            equipe.member_count = compte
            equipe.score_total = points

    @api.depends("color_key")
    def _compute_color(self):
        for equipe in self:
            equipe.color = couleur_fond(equipe.color_key)
            equipe.text_color = text_color(equipe.color)

    def _bf_board_row(self, participations):
        """La ligne de cette équipe sur le tableau, bornée aux participations données."""
        self.ensure_one()
        miennes = participations.filtered(lambda p: p.bf_team_id == self)
        membres = len(miennes)
        total = sum(miennes.mapped("scoring_total"))
        return {
            "label": self.name,
            "color": self.color,
            "text_color": self.text_color,
            "members": membres,
            "score": total,
            # ⚠️ Le classement se fait à la MOYENNE : au total, « quel service ? »
            # ferait gagner le plus gros service, ce qui n'est pas un jeu.
            "average": (total / membres) if membres else 0.0,
        }

    @api.model
    def _bf_team_name(self, index):
        """Le nom d'une équipe tirée au hasard, à la couleur de son rang.

        ⚠️ Les six noms sont écrits en entier, et non assemblés à partir du
        libellé de la couleur. Deux raisons, et la traduction les a toutes les
        deux révélées : le libellé d'une Selection née d'une constante de module
        ne passe pas par `_()` (`dict(COULEURS)` rendait « bleue » même sur une
        base anglaise), et l'anglais met la couleur DEVANT le nom (« Blue team »),
        ce qu'un gabarit « Équipe %s » ne sait pas faire.
        """
        noms = {
            "bleue": _("Équipe bleue"),
            "orange": _("Équipe orange"),
            "verte": _("Équipe verte"),
            "rouge": _("Équipe rouge"),
            "violette": _("Équipe violette"),
            "turquoise": _("Équipe turquoise"),
        }
        return noms[COULEURS[index % MAX_EQUIPES][0]]
