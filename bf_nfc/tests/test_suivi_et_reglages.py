"""Deux défauts qui ne se voyaient qu'à l'écran."""
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSuiviFicheVisee(TransactionCase):

    def test_changer_la_fiche_visee_passe_le_vidage_final(self):
        # Le suivi d'historique ne tourne qu'au vidage final : sans ce flush, une
        # écriture qui plante au premier vrai clic passe au vert ici.
        a = self.env["res.partner"].create({"name": "Fiche A"})
        b = self.env["res.partner"].create({"name": "Fiche B"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Porte", "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": a.id})
        self.env.cr.flush()
        tag.write({"res_id": b.id})
        self.env.cr.flush()
        tag.cible = a
        self.env.cr.flush()
        self.assertEqual(tag.res_id, a.id)


@tagged("post_install", "-at_install")
class TestReglagesALecran(HttpCase):

    def test_le_formulaire_compte_les_types_de_fiche(self):
        self.env["bf.nfc.target.type"].search([]).unlink()
        for nom in ("res.partner", "res.company"):
            self.env["bf.nfc.target.type"].create({"model": nom})
        new_test_user(self.env, login="reglages-gestion", password="reglages-gestion-mdp",
                      groups="base.group_user,bf_nfc.group_nfc_manager")
        self.authenticate("reglages-gestion", "reglages-gestion-mdp")
        # Ce que le client web fait à l'ouverture : un onchange sur un formulaire neuf.
        valeur = self.make_jsonrpc_request("/web/dataset/call_kw", {
            "model": "bf.nfc.config", "method": "onchange",
            "args": [[], {}, [], {"type_count": {}, "cles_ids": {"fields": {}}}],
            "kwargs": {}})["value"]
        self.assertEqual(valeur["type_count"], 2)
