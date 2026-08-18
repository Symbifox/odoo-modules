# -*- coding: utf-8 -*-
"""D'où vient une étape : la décision, la rencontre, la personne.

Une carte vieillit mal quand elle ne dit que *ce qu'on fait*. Six mois plus
tard la question n'est plus « quelle est l'étape suivante », c'est « pourquoi
avait-on décidé de faire ça ici ». La réponse n'est pas dans le tracé : elle
est dans la décision qui l'a produite, et cette décision vit déjà dans la
matrice de connaissances, elle-même rattachée au compte rendu qui la porte.

Ce fichier ne recopie donc rien. Il pose le seul chaînon manquant — le nœud
vers l'élément de matrice — et laisse la matrice répondre pour le reste :
qui a décidé, quand, contre quelles alternatives, avec quelles conséquences,
et dans quelle rencontre ça s'est dit.

Le gel s'applique ici comme ailleurs : rattacher une décision à une version
validée, c'est modifier une pièce datée. On rouvre, ou on rattache sur la
version suivante.
"""
from odoo import _, api, fields, models

from .structure import ACTIVITES


class BfProcessNodeTracabilite(models.Model):
    _inherit = "bf.process.node"

    knowledge_item_ids = fields.Many2many(
        "project.knowledge.item",
        "bf_process_node_knowledge_item_rel",
        "node_id", "knowledge_item_id",
        string="Décisions et éléments de matrice",
        help="Ce qui explique cette étape : la décision qui l'a créée, la"
             " contrainte qu'elle satisfait, la question qui reste ouverte.")
    knowledge_item_count = fields.Integer(
        string="Éléments de matrice", compute="_compute_tracabilite")
    # ⚠️ PAS de champ typé sur `meeting.record` ici, et c'est délibéré.
    # Un Many2many vers ce modèle rend la dépendance à `bf_meeting` OBLIGATOIRE,
    # même calculé, même gardé : Odoo résout le comodèle au chargement du
    # registre, bien avant qu'un calcul défensif ait voix au chapitre. Une
    # installation neuve sans `bf_meeting` échouait donc sur
    # « unknown comodel_name 'meeting.record' », puis sur la vue qui lisait
    # ce champ — invisible sur un locataire où le module est présent.
    # Seul un COMPTE est stocké en champ ; les enregistrements se retrouvent à
    # la demande, quand on clique.
    meeting_count = fields.Integer(
        string="Rencontres", compute="_compute_tracabilite",
        help="Déduit des éléments de matrice : on ne rattache pas une étape à"
             " une rencontre à la main, on la rattache à ce qui s'y est décidé.")
    tracee = fields.Boolean(
        string="Tracée", compute="_compute_tracabilite",
        help="L'étape est adossée à au moins une décision consignée.")

    def _rencontres_ids(self):
        """Les comptes rendus d'où viennent les décisions de cette étape.

        Rend des identifiants, pas un jeu d'enregistrements : le module de
        cartographie ne dépend pas du module de rencontres. Sans lui, la
        matrice répond quand même « qui a décidé et pourquoi », c'est seulement
        le renvoi vers le compte rendu qui disparaît.
        """
        Item = self.env["project.knowledge.item"]
        if "meeting_ids" not in Item._fields:
            return []
        ids = set()
        for element in self.mapped("knowledge_item_ids"):
            ids.update(element.meeting_ids.ids)
        return sorted(ids)

    @api.depends("knowledge_item_ids")
    def _compute_tracabilite(self):
        for rec in self:
            elements = rec.knowledge_item_ids
            rec.knowledge_item_count = len(elements)
            rec.tracee = bool(elements)
            rec.meeting_count = len(rec._rencontres_ids())

    def action_ouvrir_matrice(self):
        """Les éléments de matrice de cette étape, dans leur propre vue.

        C'est le « pourquoi » du nœud : la fiche de l'élément porte le
        décideur, la date, le rationnel, les alternatives écartées et les
        conséquences, sans que la cartographie ait à les redire.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pourquoi cette étape — %s") % (self.name or self.code),
            "res_model": "project.knowledge.item",
            "view_mode": "list,form",
            "domain": [("id", "in", self.knowledge_item_ids.ids)],
            "context": {"create": False},
        }

    def action_ouvrir_rencontres(self):
        """Les comptes rendus où ces décisions ont été prises."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Où ça s'est dit — %s") % (self.name or self.code),
            "res_model": "meeting.record",
            "view_mode": "list,form",
            "domain": [("id", "in", self._rencontres_ids())],
            "context": {"create": False},
        }


class BfProcessTracabilite(models.Model):
    _inherit = "bf.process"

    activite_tracee_count = fields.Integer(
        string="Activités tracées", compute="_compute_tracabilite")
    taux_tracabilite = fields.Float(
        string="Traçabilité", compute="_compute_tracabilite",
        help="Part des activités adossées à au moins une décision consignée."
             " Le pendant du taux de validation : l'un dit que la carte est"
             " juste, l'autre dit qu'on sait pourquoi.")

    @api.depends("diagram_ids.node_ids.knowledge_item_ids",
                 "diagram_ids.node_ids.kind")
    def _compute_tracabilite(self):
        for rec in self:
            activites = rec.mapped("diagram_ids.node_ids").filtered(
                lambda n: n.kind in ACTIVITES)
            tracees = activites.filtered(lambda n: n.knowledge_item_ids)
            rec.activite_tracee_count = len(tracees)
            rec.taux_tracabilite = (
                100.0 * len(tracees) / len(activites)) if activites else 0.0
