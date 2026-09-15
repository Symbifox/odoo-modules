"""Le relevé : une grille remplie sur place, un registre qui ne se réécrit pas.

Joué par un interne ORDINAIRE qui fait la ronde, et par une gestionnaire des
pastilles qui n'est pas administratrice : ce sont les deux rôles qui s'en servent.
"""
import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class _Base:

    @classmethod
    def _preparer(cls):
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.agent = new_test_user(cls.env, login="releve-agent", groups="base.group_user")
        cls.gestion = new_test_user(cls.env, login="releve-gestion",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.grille = cls.env["bf.nfc.checklist"].create({
            "name": "Extincteur mensuel", "periode": "mois", "responsible_id": cls.gestion.id,
            "item_ids": [
                (0, 0, {"name": "Accessible", "kind": "conforme", "sequence": 1}),
                (0, 0, {"name": "Pression", "kind": "nombre", "unit": "bar", "has_range": True,
                        "min_value": 8, "max_value": 12, "sequence": 2}),
                (0, 0, {"name": "Manomètre", "kind": "choix", "options": "Oui;Non;Sans manomètre",
                        "anomaly_options": "Non", "sequence": 3}),
                (0, 0, {"name": "Remarque", "kind": "texte", "required": False, "sequence": 4}),
            ],
        })
        cls.elements = {e.name: e._cle() for e in cls.grille.item_ids}
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Extincteur du hall", "place": "Hall, près de l'entrée",
            "gesture_id": cls.env.ref("bf_nfc_inspection.gesture_reading").id,
            "checklist_id": cls.grille.id,
        })

    def _reponses(self, **valeurs):
        return {self.elements[nom]: valeur for nom, valeur in valeurs.items()}

    def _releves(self):
        return self.env["bf.nfc.reading"].search([("tag_id", "=", self.pastille.id)])


@tagged("post_install", "-at_install")
class TestReleve(_Base, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer()

    def _taper(self, **kw):
        return self.pastille.with_user(self.agent).taper("app", **kw)

    def test_le_premier_tapotement_montre_la_grille_et_n_ecrit_rien(self):
        r = self._taper()
        self.assertEqual(r["statut"], "choice")
        self.assertEqual([c["libelle"] for c in r["formulaire"]],
                         ["Accessible", "Pression", "Manomètre", "Remarque"])
        self.assertEqual(r["formulaire"][1]["min"], 8)
        self.assertFalse(self._releves())
        self.assertFalse(self.pastille.tap_ids)

    def test_une_grille_conforme_s_enregistre_au_nom_de_qui_tape(self):
        r = self._taper(choix="enregistrer", reponses=self._reponses(
            Accessible="oui", Pression="10,5", Manomètre="Oui"))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        releve = self._releves()
        self.assertEqual(len(releve), 1)
        self.assertEqual((releve.state, releve.user_id, releve.verifier),
                         ("conforme", self.agent, self.agent.name))
        self.assertEqual((releve.tag_name, releve.place), ("Extincteur du hall", "Hall, près de l'entrée"),
                         "Le registre doit dire CE QUI a été vérifié, pas seulement où.")
        self.assertEqual(len(releve.line_ids), 4)
        self.assertEqual(releve.line_ids.filtered(lambda l: l.name == "Pression").value_float, 10.5)
        self.assertFalse(releve.line_ids.filtered(lambda l: l.name == "Remarque").answered)
        self.assertFalse(releve.activity_ids)

    def test_une_anomalie_va_a_la_personne_responsable(self):
        r = self._taper(choix="enregistrer", reponses=self._reponses(
            Accessible="non", Pression="6", Manomètre="Non", Remarque="Boîte devant"))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        releve = self._releves()
        self.assertEqual((releve.state, releve.anomaly_count), ("anomalie", 3))
        self.assertIn(self.gestion.name, r["message"])
        self.assertEqual(releve.activity_ids.user_id, self.gestion)

    def test_tous_les_champs_manquants_sont_nommes_d_un_coup(self):
        r = self._taper(choix="enregistrer", reponses=self._reponses(Pression="beaucoup"))
        self.assertEqual(r["statut"], "refused")
        for nom in ("Accessible", "Pression", "Manomètre"):
            self.assertIn(nom, r["message"])
        self.assertFalse(self._releves())

    def test_une_ancienne_application_n_ecrit_pas_un_releve_vide(self):
        """🔴 « enregistrer » sans réponses aurait écrit un relevé vide qui a l'air conforme."""
        r = self._taper(choix="enregistrer")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("mettez à jour", r["message"])
        self.assertFalse(self._releves())

    def test_le_releve_differe_garde_l_heure_du_telephone(self):
        il_y_a = fields.Datetime.now() - timedelta(hours=3)
        self._taper(choix="enregistrer", quand=il_y_a.isoformat(), nonce="n-releve-1",
                    reponses=self._reponses(Accessible="oui", Pression="9", Manomètre="Oui"))
        releve = self._releves()
        self.assertTrue(releve.offline)
        self.assertEqual(releve.tapped_at.replace(microsecond=0), il_y_a.replace(microsecond=0))

    def test_par_la_porte_signee_la_grille_demande_un_nom(self):
        self.pastille.write({"sdm_enabled": True, "sdm_uid": "04AABBCCDDEEFF", "user_id": self.gestion.id})
        r = self.pastille.with_user(self.gestion).taper("signed", compteur=5)
        self.assertEqual(r["formulaire"][0]["cle"], "_nom")
        reponses = self._reponses(Accessible="oui", Pression="9", Manomètre="Oui")
        refus = self.pastille.with_user(self.gestion).taper("signed", compteur=6, choix="enregistrer",
                                                            reponses=reponses)
        self.assertEqual(refus["statut"], "refused")
        reponses["_nom"] = "Gérald, entretien"
        r = self.pastille.with_user(self.gestion).taper("signed", compteur=7, choix="enregistrer",
                                                        reponses=reponses)
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._releves().verifier, "Gérald, entretien")

    def test_un_releve_ne_se_reecrit_pas(self):
        self._taper(choix="enregistrer", reponses=self._reponses(Accessible="non", Pression="9", Manomètre="Oui"))
        releve = self._releves()
        with self.assertRaises(AccessError):
            releve.with_user(self.gestion).write({"place": "Ailleurs"})
        with self.assertRaises(AccessError):
            releve.line_ids.with_user(self.gestion).write({"value_bool": True})
        with self.assertRaises(AccessError):
            releve.with_user(self.agent).write({"correction": "Rien vu"})

    def test_la_correction_se_consigne_avec_ce_qui_a_ete_fait(self):
        self._taper(choix="enregistrer", reponses=self._reponses(Accessible="non", Pression="9", Manomètre="Oui"))
        releve = self._releves().with_user(self.gestion)
        with self.assertRaises(UserError):
            releve.action_consigner_correction()
        releve.write({"correction": "Boîte déplacée", "correction_date": "2026-09-15"})
        releve.action_consigner_correction()
        self.assertEqual(releve.state, "corrige")
        self.assertEqual(releve.corrected_by_id, self.gestion)
        self.assertFalse(releve.activity_ids)
        with self.assertRaises(AccessError):
            self._releves().with_user(self.agent).action_consigner_correction()

    def test_un_agent_ne_lit_que_ses_releves(self):
        autre = new_test_user(self.env, login="releve-autre", groups="base.group_user")
        self.pastille.with_user(autre).taper("app", choix="enregistrer", reponses=self._reponses(
            Accessible="oui", Pression="9", Manomètre="Oui"))
        self.assertFalse(self.env["bf.nfc.reading"].with_user(self.agent).search([]))
        self.assertEqual(len(self.env["bf.nfc.reading"].with_user(self.gestion).search(
            [("tag_id", "=", self.pastille.id)])), 1)
        self.assertFalse(self.env["bf.nfc.reading.line"].with_user(self.agent).search([]))

    def test_le_guet_alerte_une_fois_par_periode(self):
        self.env.cr.execute("UPDATE bf_nfc_tag SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=40), self.pastille.id))
        self.pastille.invalidate_recordset()
        Tag = self.env["bf.nfc.tag"]
        Tag._cron_guetter_releves()
        self.assertEqual(len(self.pastille.activity_ids), 1)
        self.assertEqual(self.pastille.activity_ids.user_id, self.gestion)
        Tag._cron_guetter_releves()
        self.assertEqual(len(self.pastille.activity_ids), 1, "Une seule alerte par période manquée.")

    def test_une_pastille_releve_ne_declenche_pas_le_guet(self):
        self.env.cr.execute("UPDATE bf_nfc_tag SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=40), self.pastille.id))
        self.pastille.invalidate_recordset()
        self._taper(choix="enregistrer", reponses=self._reponses(Accessible="oui", Pression="9", Manomètre="Oui"))
        self.env["bf.nfc.tag"]._cron_guetter_releves()
        self.assertFalse(self.pastille.activity_ids)

    def test_le_registre_porte_la_correction(self):
        self._taper(choix="enregistrer", reponses=self._reponses(Accessible="non", Pression="9", Manomètre="Oui"))
        releve = self._releves().with_user(self.gestion)
        releve.write({"correction": "Boîte déplacée"})
        releve.action_consigner_correction()
        html, _format = self.env["ir.actions.report"]._render_qweb_html(
            "bf_nfc_inspection.report_registre", releve.ids)
        texte = html.decode()
        for attendu in ("Extincteur mensuel", "Extincteur du hall", "Hall, près de l&#39;entrée",
                        "Boîte déplacée", self.agent.name):
            self.assertIn(attendu, texte)

    def test_les_grilles_livrees_sont_valides(self):
        grille = self.env.ref("bf_nfc_inspection.grille_extincteur")
        self.assertEqual(grille.periode, "mois")
        self.assertTrue(all(c["libelle"] for c in grille._formulaire()))
        self.assertTrue(self.env.ref("bf_nfc_inspection.grille_aire_jeu_quotidienne").a_valider)


@tagged("post_install", "-at_install")
class TestReleveNavigateur(_Base, HttpCase):
    """La même grille, remplie dans le navigateur (code QR, téléphone sans l'application)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer()

    def test_la_page_montre_la_grille_puis_enregistre(self):
        self.authenticate("admin", "admin")
        page = self.url_open("/nfc/%s" % self.pastille.code).text
        jeton = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        question = self.url_open("/nfc/%s/agir" % self.pastille.code, data={"csrf_token": jeton}).text
        for nom in self.elements.values():
            self.assertIn('name="r.%s"' % nom, question)
        jeton = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', question).group(1)
        donnees = {"csrf_token": jeton, "choix": "enregistrer", "piege": "ignore"}
        donnees.update({"r.%s" % cle: valeur for cle, valeur in self._reponses(
            Accessible="oui", Pression="11", Manomètre="Sans manomètre").items()})
        resultat = self.url_open("/nfc/%s/agir" % self.pastille.code, data=donnees).text
        self.assertIn("tout est conforme", resultat)
        self.assertEqual(self._releves().state, "conforme")


@tagged("post_install", "-at_install")
class TestReleveGardes(_Base, TransactionCase):
    """Les gardes que la relecture adverse a trouvées ouvertes, chacune avec son essai."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer()

    def test_l_etat_ne_se_marque_pas_corrige_par_ecriture(self):
        """🔴 Marquer « corrigé » sans dire ce qui a été fait sortait un registre vide."""
        self.pastille.with_user(self.agent).taper("app", choix="enregistrer", reponses=self._reponses(
            Accessible="non", Pression="9", Manomètre="Oui"))
        releve = self._releves().with_user(self.gestion)
        with self.assertRaises(AccessError):
            releve.write({"state": "corrige"})
        with self.assertRaises(AccessError):
            releve.write({"corrected_by_id": self.gestion.id})
        self.assertEqual(releve.state, "anomalie")

    def test_un_releve_sans_aucune_reponse_est_refuse(self):
        """🔴 Une grille aux éléments facultatifs écrivait « tout est conforme »."""
        grille = self.env["bf.nfc.checklist"].create({
            "name": "Tout facultatif",
            "item_ids": [(0, 0, {"name": "Remarque", "kind": "texte", "required": False})],
        })
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Facultative", "gesture_id": self.env.ref("bf_nfc_inspection.gesture_reading").id,
            "checklist_id": grille.id,
        })
        r = pastille.with_user(self.agent).taper("app", choix="enregistrer", reponses={"inconnu": "x"})
        self.assertEqual(r["statut"], "refused")
        self.assertIn("vide", r["message"])
        self.assertFalse(self.env["bf.nfc.reading"].search([("tag_id", "=", pastille.id)]))

    def test_les_resultats_d_une_autre_societe_ne_se_lisent_pas(self):
        """🔴 Le relevé était cloisonné, ses lignes non : elles se lisaient une à une."""
        autre = self.env["res.company"].create({"name": "Société voisine"})
        self.pastille.with_user(self.agent).taper("app", choix="enregistrer", reponses=self._reponses(
            Accessible="oui", Pression="9", Manomètre="Oui"))
        releve = self._releves()
        releve.sudo().write({"company_id": autre.id})
        self.env.flush_all()
        self.gestion.write({"company_ids": [(6, 0, [self.env.company.id])], "company_id": self.env.company.id})
        lues = self.env["bf.nfc.reading.line"].with_user(self.gestion).search([])
        self.assertFalse(lues.filtered(lambda l: l.reading_id == releve),
                         "Les résultats d'une autre société ne doivent pas se lire.")

    def test_la_grille_livree_a_bien_ses_elements(self):
        grille = self.env.ref("bf_nfc_inspection.grille_extincteur")
        champs = grille._formulaire()
        self.assertEqual(len(champs), len(grille.item_ids))
        self.assertGreaterEqual(len(champs), 6)
        self.assertTrue(all(c["libelle"] and c["type"] for c in champs))

    def test_poser_un_gabarit_ne_touche_pas_la_grille_partagee(self):
        """🔴 Le responsable choisi à la pose écrasait celui d'une grille partagée."""
        grille = self.env.ref("bf_nfc_inspection.grille_premiers_secours")
        grille.responsible_id = False
        gabarit = self.env.ref("bf_nfc_inspection.gabarit_premiers_secours")
        pose = self.env["bf.nfc.template.apply"].with_user(self.gestion).with_context(
            default_template_id=gabarit.id).create({"lignes": "Trousse ; cuisine",
                                                    "responsible_id": self.gestion.id})
        pose.action_appliquer()
        self.assertFalse(grille.responsible_id,
                         "Poser un gabarit n'écrit rien sur une grille partagée.")
