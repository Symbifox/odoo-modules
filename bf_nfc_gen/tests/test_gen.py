"""Une pastille qui demande à Gen : jouée sans pont, le transport remplacé.

Ce qui compte ici n'est pas la passe de Gen (elle se joue au pont) mais tout ce qui
la précède : la question avant l'appel, rien avant le commit, la liste permise,
la porte signée refusée, et un seul lancement à la fois.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_nfc_gen.models import bf_nfc_gen


@tagged("post_install", "-at_install")
class TestDemanderAGen(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_nfc.fenetre_doublon_secondes", "0")
        icp.set_param("bf_ai_bridge.tenant", "bf")
        cls.gestion = new_test_user(cls.env, login="gen-gestion",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.interne = new_test_user(cls.env, login="gen-interne", groups="base.group_user")
        cls.skill = cls.env["bf.nfc.gen.skill"].create({
            "skill": "refine-agenda", "name": "Préparer l'ordre du jour",
            "modeles": "res.partner", "allowed": True,
        })
        cls.fiche = cls.env["res.partner"].create({"name": "Rencontre de lundi"})
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Table de réunion", "gesture_id": cls.env.ref("bf_nfc_gen.gesture_gen_skill").id,
            "gen_skill_id": cls.skill.id, "res_model": "res.partner", "res_id": cls.fiche.id,
        })

    def setUp(self):
        super().setUp()
        self.envois = []
        disponible = patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=True)
        disponible.start()
        self.addCleanup(disponible.stop)

    def _demandes(self):
        return self.env["bf.nfc.gen.request"].search([("tag_id", "=", self.pastille.id)])

    def _taper(self, utilisateur=None, **kw):
        return self.pastille.with_user(utilisateur or self.gestion).taper("app", **kw)

    def test_le_premier_tapotement_demande_et_ne_lance_rien(self):
        r = self._taper()
        self.assertEqual(r["statut"], "choice")
        self.assertEqual(r["choix"][0]["cle"], "lancer")
        self.assertFalse(self._demandes())

    def test_lancer_cree_la_demande_et_n_appelle_le_pont_qu_apres_le_commit(self):
        with patch.object(bf_nfc_gen.transport, "post", return_value={"status": "ok"}) as post:
            r = self._taper(choix="lancer")
            self.assertEqual(r["statut"], "ok", r.get("message"))
            self.assertEqual(len(self._demandes()), 1)
            self.assertFalse(post.called, "🔴 Le pont ne doit pas être appelé avant le commit.")
            self.assertTrue(self.env.cr.postcommit._funcs, "L'appel doit attendre le commit.")
        self.env.cr.postcommit.clear()

    def test_un_seul_lancement_a_la_fois(self):
        self._taper(choix="lancer")
        self.env.cr.postcommit.clear()
        r = self._taper(choix="lancer")
        self.assertEqual(r["statut"], "info")
        self.assertIn("déjà", r["message"])
        self.assertEqual(len(self._demandes()), 1)

    def test_un_skill_non_permis_ne_se_lance_pas(self):
        self.skill.allowed = False
        r = self._taper(choix="lancer")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._demandes())

    def test_un_type_de_fiche_non_accepte_est_refuse(self):
        self.skill.modeles = "meeting.agenda"
        r = self._taper(choix="lancer")
        self.assertEqual(r["statut"], "refused")

    def test_la_porte_signee_ne_lance_pas_d_agent(self):
        self.pastille.write({"sdm_enabled": True, "sdm_uid": "04AABBCCDDEE11", "user_id": self.gestion.id})
        r = self.pastille.with_user(self.gestion).taper("signed", compteur=3, choix="lancer")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._demandes())

    def test_reserve_a_la_gestion_par_defaut(self):
        r = self._taper(utilisateur=self.interne, choix="lancer")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._demandes())

    def test_le_pont_ecrit_l_etat_et_previent_la_personne(self):
        self._taper(choix="lancer")
        self.env.cr.postcommit.clear()
        demande = self._demandes()
        demande.with_user(self.gestion).set_state("done", "Passe terminée.")
        self.assertEqual(demande.state, "done")
        self.assertEqual(demande.activity_ids.user_id, self.gestion)
        with self.assertRaises(AccessError):
            demande.with_user(self.interne).set_state("error", "faux")

    def test_l_actualisation_suit_le_pont(self):
        pont = {"skills": [
            {"skill": "refine-agenda", "libelle": "OdJ", "description": "d", "modeles": ["meeting.agenda"]},
            {"skill": "refine-meeting", "libelle": "CR", "description": "d", "modeles": ["meeting.record"]},
        ]}
        with patch.object(type(self.env["bf.ai.bridge"]), "call", return_value=pont):
            self.env["bf.nfc.gen.skill"].with_user(self.gestion).action_actualiser()
        skills = self.env["bf.nfc.gen.skill"].search([])
        self.assertEqual(set(skills.mapped("skill")), {"refine-agenda", "refine-meeting"})
        self.assertTrue(self.skill.allowed, "Actualiser ne retire pas une permission déjà donnée.")
        self.assertFalse(skills.filtered(lambda s: s.skill == "refine-meeting").allowed,
                         "Un skill neuf arrive NON permis.")
        # 🔴 Une liste vide n'est pas une réponse : elle arrive aussi quand le
        # locataire déclaré est inconnu du pont. Elle est refusée, et rien ne bouge.
        with patch.object(type(self.env["bf.ai.bridge"]), "call", return_value={"skills": []}):
            with self.assertRaises(UserError):
                self.env["bf.nfc.gen.skill"].with_user(self.gestion).action_actualiser()
        self.assertTrue(self.skill.published)
        self.assertTrue(self.skill.allowed)


@tagged("post_install", "-at_install")
class TestGenGardes(TransactionCase):
    """Ce que la relecture adverse a trouvé ouvert côté Gen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_ai_bridge.tenant", "bf")
        cls.gestion = new_test_user(cls.env, login="gen-gardes-gestion",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.autre = new_test_user(cls.env, login="gen-gardes-autre", groups="base.group_user")
        cls.skill = cls.env["bf.nfc.gen.skill"].create({
            "skill": "refine-agenda", "name": "Ordre du jour", "modeles": "res.partner",
            "allowed": True})
        cls.fiche = cls.env["res.partner"].create({"name": "Rencontre confidentielle"})
        cls.demande = cls.env["bf.nfc.gen.request"].create({
            "skill_id": cls.skill.id, "user_id": cls.gestion.id, "res_model": "res.partner",
            "res_id": cls.fiche.id, "target_name": cls.fiche.name})

    def test_une_liste_vide_du_pont_ne_change_rien(self):
        """🔴 Le pont répond « aucun skill » quand le locataire déclaré lui est inconnu."""
        with patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=True), \
             patch.object(type(self.env["bf.ai.bridge"]), "call", return_value={"skills": []}):
            with self.assertRaises(UserError):
                self.env["bf.nfc.gen.skill"].with_user(self.gestion).action_actualiser()
        self.assertTrue(self.skill.allowed)
        self.assertTrue(self.skill.published)

    def test_un_skill_retire_garde_sa_permission(self):
        """🔴 Une absence temporaire effaçait des choix d'administration à refaire un par un."""
        pont = {"skills": [{"skill": "refine-meeting", "libelle": "CR", "description": "",
                            "modeles": ["meeting.record"]}]}
        with patch.object(type(self.env["bf.ai.bridge"]), "available", return_value=True), \
             patch.object(type(self.env["bf.ai.bridge"]), "call", return_value=pont):
            self.env["bf.nfc.gen.skill"].with_user(self.gestion).action_actualiser()
        self.assertFalse(self.skill.published)
        self.assertTrue(self.skill.allowed, "La permission survit à une absence du pont.")

    def test_une_demande_ne_se_lit_pas_par_n_importe_qui(self):
        """🔴 « Fiche » nomme des rencontres et des ordres du jour."""
        self.assertFalse(self.env["bf.nfc.gen.request"].with_user(self.autre).search([]))
        self.assertTrue(self.env["bf.nfc.gen.request"].with_user(self.gestion).search([]))

    def test_une_demande_sans_nouvelle_est_declassee(self):
        """🔴 Sans ce guet, une demande que le pont n'a pas pu clore reste « Transmise »."""
        self.env.cr.execute("UPDATE bf_nfc_gen_request SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(minutes=30), self.demande.id))
        self.demande.invalidate_recordset()
        self.assertEqual(self.env["bf.nfc.gen.request"]._cron_perimer(), 1)
        self.assertEqual(self.demande.state, "error")
        self.assertIn("Sans nouvelle", self.demande.message)
