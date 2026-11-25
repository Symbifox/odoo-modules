"""La ronde incendie du mois : un point, une grille, un passage et un relevé."""
from datetime import timedelta

from odoo import fields
from odoo.tools.translate import code_translations
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestTourneeReleve(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.agent = new_test_user(cls.env, login="ronde-agent", groups="base.group_user")
        cls.chef = new_test_user(cls.env, login="ronde-chef", groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.grille = cls.env.ref("bf_nfc_inspection.grille_extincteur")
        cls.grille.responsible_id = cls.chef
        cls.tournee = cls.env["bf.nfc.round"].create({
            "name": "Ronde incendie", "responsible_id": cls.chef.id,
            "checkpoint_ids": [(0, 0, {"name": "Extincteur 1", "place": "Hall", "checklist_id": cls.grille.id}),
                               (0, 0, {"name": "Porte arrière"})],
        })
        cls.p1, cls.p2 = cls.tournee.checkpoint_ids
        geste = cls.env.ref("bf_nfc_round.gesture_round_checkpoint")
        cls.t1, cls.t2 = cls.env["bf.nfc.tag"].create([
            {"name": p.name, "gesture_id": geste.id, "res_model": "bf.nfc.round.checkpoint", "res_id": p.id}
            for p in (cls.p1, cls.p2)])

    def _conforme(self):
        # « oui » pour un élément `conforme` est une valeur de PROTOCOLE (l'app l'envoie
        # telle quelle). Un `choix`, lui, se répond par une des options AFFICHÉES, donc dans
        # la langue de la personne : on prend la première qui ne signale pas d'anomalie,
        # lue comme le code la lit (avant : « Oui » écrit en dur).
        def _sans_anomalie(e):
            return next(o for o in e._liste(e.options) if o not in e._liste(e.anomaly_options))
        return {e._cle(): (_sans_anomalie(e) if e.kind == "choix" else "oui")
                for e in self.grille.item_ids if e.kind != "texte"}

    def test_le_point_avec_grille_demande_d_abord_et_n_ecrit_rien(self):
        r = self.t1.with_user(self.agent).taper("app")
        self.assertEqual(r["statut"], "choice")
        self.assertTrue(r["formulaire"])
        self.assertFalse(self.env["bf.nfc.round.run"].search([("round_id", "=", self.tournee.id)]))

    def test_enregistrer_note_le_passage_et_ecrit_le_releve(self):
        r = self.t1.with_user(self.agent).taper("app", choix="enregistrer", reponses=self._conforme())
        self.assertEqual(r["statut"], "ok", r.get("message"))
        # Les deux phrases viennent du code : le relevé (`_phrase()`) puis le rang du point.
        # On les lit par les MÊMES appels, et l'intention « tout est conforme » par la clé.
        releve = self.env["bf.nfc.reading"].search([], order="id desc", limit=1)
        self.assertEqual(releve.state, "conforme")
        self.assertIn(releve._phrase(), r["message"])
        # ⚠️ Le rang est un texte de `bf_nfc_round`, rendu dans la langue de QUI TAPE (le
        # geste la fixe avant d'agir). Un `_()` écrit ICI chercherait dans le catalogue de
        # CE module-ci et rendrait la source : Odoo range les traductions de code par
        # module. On lit donc la traduction là où le code la prend.
        msgid = "Checkpoint %(n)s of %(total)s. Next: %(suivant)s."
        attendu = code_translations.get_python_translations(
            "bf_nfc_round", self.agent.lang or "en_US").get(msgid, msgid)
        self.assertIn(attendu.split("%(n)s")[0], r["message"])
        passage = self.env["bf.nfc.round.run"].search([("round_id", "=", self.tournee.id)])
        self.assertEqual(len(passage.passage_ids), 1)
        releve = self.env["bf.nfc.reading"].search([("tag_id", "=", self.t1.id)])
        self.assertEqual((releve.state, releve.tag_name, releve.place, releve.user_id),
                         ("conforme", "Extincteur 1", "Hall", self.agent))

    def test_le_point_sans_grille_ne_change_pas(self):
        r = self.t2.with_user(self.agent).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertFalse(self.env["bf.nfc.reading"].search([("tag_id", "=", self.t2.id)]))

    def test_le_guet_voit_l_extincteur_pas_releve_ce_mois_ci(self):
        self.env.cr.execute("UPDATE bf_nfc_tag SET create_date = %s WHERE id IN %s",
                            (fields.Datetime.now() - timedelta(days=40), (self.t1.id, self.t2.id)))
        self.env["bf.nfc.tag"].invalidate_model()
        self.env["bf.nfc.tag"]._cron_guetter_releves()
        self.assertEqual(len(self.t1.activity_ids), 1)
        self.assertEqual(self.t1.activity_ids.user_id, self.chef)
        self.assertFalse(self.t2.activity_ids, "Un point sans grille n'a pas de rythme de relevé.")
