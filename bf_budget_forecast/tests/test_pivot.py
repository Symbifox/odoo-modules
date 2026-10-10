"""Les vues pivot ne mesurent que des champs stockés.

Un champ calculé non stocké n'a pas d'agrégat : le client web refuse alors
d'ouvrir la vue (« No aggregate function has been provided for the measure »).
Le pivot de la prévision par mois mesurait `amount` (le retenu), calculé
depuis la comptabilité, et ne s'ouvrait pas.
"""

from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPivot(TransactionCase):

    def test_les_mesures_sont_stockees(self):
        vues = self.env["ir.ui.view"].search([
            ("model", "=like", "bf.budget.forecast%"), ("type", "=", "pivot"),
        ])
        self.assertTrue(vues)
        for vue in vues:
            modele = self.env[vue.model]
            for noeud in etree.fromstring(vue.arch_db).iter("field"):
                if noeud.get("type") == "measure":
                    champ = modele._fields[noeud.get("name")]
                    self.assertTrue(champ.store and champ.aggregator, (vue.name, champ.name))

    def test_le_pivot_par_mois_se_regroupe(self):
        """Le regroupement que demande la vue : poste par mois, en somme."""
        self.env["bf.budget.forecast.period"].read_group(
            [], ["amount_forecast:sum"], ["position_id", "name"], lazy=False)
