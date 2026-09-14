"""Ce que fait une pastille, et surtout ce qu'elle refuse de faire."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.bf_nfc_tag import ALPHABET, LONGUEUR_CODE, PARAM_FENETRE


@tagged("post_install", "-at_install")
class TestPastille(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.geste_open = cls.env.ref("bf_nfc.gesture_open")
        cls.partenaire = cls.env["res.partner"].create({"name": "Cible d'essai"})
        cls.tag = cls.env["bf.nfc.tag"].create({
            "name": "Pastille d'essai",
            "gesture_id": cls.geste_open.id,
            "res_model": "res.partner",
            "res_id": cls.partenaire.id,
        })
        cls.personne = new_test_user(cls.env, login="tapeuse", groups="base.group_user")

    # ------------------------------------------------------------------
    # Le code
    # ------------------------------------------------------------------
    def test_code_tire_dans_un_alphabet_sans_ambiguite(self):
        code = self.tag.code
        self.assertEqual(len(code), LONGUEUR_CODE)
        self.assertTrue(set(code) <= set(ALPHABET))
        for caractere in "IO01":
            self.assertNotIn(caractere, code,
                             "Un code se lit à voix haute : ni I, ni O, ni 0, ni 1.")

    def test_deux_pastilles_ne_partagent_pas_un_code(self):
        autre = self.env["bf.nfc.tag"].create({
            "name": "Autre", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })
        self.assertNotEqual(autre.code, self.tag.code)

    def test_changer_le_code_rend_la_pastille_physique_inerte(self):
        ancien = self.tag.code
        self.tag.action_regenerer_code()
        self.assertNotEqual(self.tag.code, ancien)
        self.assertFalse(self.env["bf.nfc.tag"]._resoudre(ancien))

    # ------------------------------------------------------------------
    # La résolution
    # ------------------------------------------------------------------
    def test_code_inconnu_rend_vide(self):
        self.assertFalse(self.env["bf.nfc.tag"]._resoudre("ZZZZZZZZ"))
        self.assertFalse(self.env["bf.nfc.tag"]._resoudre(""))
        self.assertFalse(self.env["bf.nfc.tag"]._resoudre(None))

    def test_code_retrouve_sans_egard_a_la_casse(self):
        self.assertEqual(self.env["bf.nfc.tag"]._resoudre(self.tag.code.lower()), self.tag)

    def test_pastille_retiree_reste_introuvable_mais_garde_son_journal(self):
        self.tag.taper("app")
        self.tag.active = False
        self.assertFalse(self.env["bf.nfc.tag"]._resoudre(self.tag.code))
        self.assertTrue(self.tag.with_context(active_test=False).tap_ids)

    # ------------------------------------------------------------------
    # Le geste
    # ------------------------------------------------------------------
    def test_ouvrir_une_fiche_rend_son_adresse_et_n_ecrit_rien(self):
        avant = self.env["bf.nfc.tap"].search_count([])
        resultat = self.tag.taper("app")
        self.assertEqual(resultat["statut"], "ok")
        self.assertIn("res.partner", resultat["url"])
        self.assertIn(str(self.partenaire.id), resultat["url"])
        self.assertEqual(self.env["bf.nfc.tap"].search_count([]), avant + 1)

    def test_pastille_sans_cible_refuse_au_lieu_de_lever(self):
        orpheline = self.env["bf.nfc.tag"].create({
            "name": "Sans cible", "gesture_id": self.geste_open.id,
        })
        resultat = orpheline.taper("app")
        self.assertEqual(resultat["statut"], "refused")
        self.assertIn("désigne aucune fiche", resultat["message"])

    def test_pastille_expiree_refuse(self):
        self.tag.date_expiry = "2020-01-01"
        resultat = self.tag.taper("app")
        self.assertEqual(resultat["statut"], "refused")
        self.assertIn("expiré", resultat["message"])

    def test_cible_lue_avec_les_droits_de_qui_tape(self):
        """🔴 La pastille est retrouvée en sudo, le geste ne l'est jamais.

        Sans le ``sudo(False)`` de ``_cible``, une pastille pointant vers un
        paramètre système rendrait son contenu à n'importe quel interne, parce
        que la pastille, elle, a bien été retrouvée en superutilisateur.
        """
        parametre = self.env["ir.config_parameter"].create({
            "key": "bf_nfc.essai_secret", "value": "ne doit pas fuir",
        })
        tag = self.env["bf.nfc.tag"].create({
            "name": "Vers un paramètre", "gesture_id": self.geste_open.id,
            "res_model": "ir.config_parameter", "res_id": parametre.id,
        })
        # ⚠️ Le cache de la transaction porte déjà la clé qu'on vient d'écrire.
        # Sans ce vidage, l'essai passerait au vert quoi qu'il arrive : la
        # lecture n'irait pas jusqu'au contrôle d'accès.
        self.env.invalidate_all()
        resultat = tag.with_user(self.personne).taper("app")
        self.assertEqual(resultat["statut"], "refused")

        # Et il passe aussi cache chaud, parce que le contrôle est explicite.
        parametre.key  # recharge la valeur dans le cache
        resultat_chaud = tag.with_user(self.personne).taper("app")
        self.assertEqual(resultat_chaud["statut"], "refused")

    # ------------------------------------------------------------------
    # Le doublon
    # ------------------------------------------------------------------
    def test_deux_tapotements_de_suite_ne_font_le_geste_qu_une_fois(self):
        premier = self.tag.taper("app")
        second = self.tag.taper("app")
        self.assertEqual(premier["statut"], "ok")
        self.assertEqual(second["statut"], "duplicate")
        self.assertEqual(second["tap_id"], premier["tap_id"],
                         "Le doublon rend la ligne du premier, il n'en crée pas une autre.")
        self.assertEqual(self.tag.tap_count, 1)

    def test_deux_personnes_qui_tapent_font_bien_deux_gestes(self):
        premier = self.tag.taper("app")
        second = self.tag.with_user(self.personne).taper("app")
        self.assertEqual(premier["statut"], "ok")
        self.assertEqual(second["statut"], "ok",
                         "La fenêtre anti-doublon se compte par personne.")

    def test_fenetre_a_zero_desarme_la_garde(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_FENETRE, "0")
        self.tag.taper("app")
        self.assertEqual(self.tag.taper("app")["statut"], "ok")

    # ------------------------------------------------------------------
    # Le point de reprise
    # ------------------------------------------------------------------
    def _geste_qui_ecrit_puis_casse(self, code_python):
        action = self.env["ir.actions.server"].create({
            "name": "Essai",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "state": "code",
            "code": code_python,
        })
        return self.env["bf.nfc.gesture"].create({
            "name": "Casse au milieu", "code": "essai_casse",
            "kind": "server_action", "writes": True,
            "server_action_id": action.id,
        })

    def test_echec_a_mi_chemin_ne_laisse_rien_derriere(self):
        """🔴 C'est le défaut exact du webhook natif d'Odoo, et il est ici fermé.

        Sans point de reprise, la première écriture reste en base pendant que
        l'écran annonce un échec : la personne retape, et cette écriture-là part
        une deuxième fois.
        """
        geste = self._geste_qui_ecrit_puis_casse(
            "record.write({'ref': 'ECRIT-AVANT-BOUM'})\n"
            "raise UserError('ça casse ici')"
        )
        tag = self.env["bf.nfc.tag"].create({
            "name": "Casse", "gesture_id": geste.id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })
        resultat = tag.taper("app")
        self.assertEqual(resultat["statut"], "refused")
        self.partenaire.invalidate_recordset(["ref"])
        self.assertFalse(self.partenaire.ref,
                         "L'écriture d'avant l'erreur doit avoir été reprise.")
        self.assertTrue(tag.tap_ids, "Le refus doit quand même laisser une ligne de journal.")

    def test_erreur_inattendue_journalisee_sans_fuite_technique(self):
        geste = self._geste_qui_ecrit_puis_casse(
            "record.write({'ref': 'ECRIT-AVANT-BOUM'})\n"
            "log(1 / 0)"
        )
        tag = self.env["bf.nfc.tag"].create({
            "name": "Division", "gesture_id": geste.id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })
        resultat = tag.taper("app")
        self.assertEqual(resultat["statut"], "error")
        self.assertNotIn("ZeroDivision", resultat["message"],
                         "L'écran ne montre pas la trace Python.")
        self.partenaire.invalidate_recordset(["ref"])
        self.assertFalse(self.partenaire.ref)

    def test_action_serveur_refusee_sans_droit_d_ecriture(self):
        """⚠️ Odoo exige l'ÉCRITURE sur le modèle visé, même pour une action qui lit.

        Un interne peut consulter ``ir.model`` sans pouvoir l'écrire : le geste
        doit alors se faire refuser proprement, pas lever.
        """
        action = self.env["ir.actions.server"].create({
            "name": "Lecture seule",
            "model_id": self.env["ir.model"]._get_id("ir.model"),
            "state": "code",
            "code": "log(record.display_name)",
        })
        geste = self.env["bf.nfc.gesture"].create({
            "name": "Sur un modèle en lecture seule", "code": "essai_lecture",
            "kind": "server_action", "writes": True, "server_action_id": action.id,
        })
        modele = self.env["ir.model"].search([("model", "=", "res.partner")], limit=1)
        tag = self.env["bf.nfc.tag"].create({
            "name": "Lecture seule", "gesture_id": geste.id,
            "res_model": "ir.model", "res_id": modele.id,
        })
        self.env.invalidate_all()
        resultat = tag.with_user(self.personne).taper("app")
        self.assertEqual(resultat["statut"], "refused")

    # ------------------------------------------------------------------
    # Le journal
    # ------------------------------------------------------------------
    def test_le_journal_retient_la_personne_et_la_porte(self):
        self.tag.with_user(self.personne).taper("session", appareil="Pixel 10")
        ligne = self.env["bf.nfc.tap"].search([("tag_id", "=", self.tag.id)], limit=1)
        self.assertEqual(ligne.user_id, self.personne)
        self.assertEqual(ligne.door, "session")
        self.assertEqual(ligne.device_label, "Pixel 10")
        self.assertEqual(ligne.tag_code, self.tag.code)

    def test_une_personne_ne_voit_que_ses_propres_tapotements(self):
        self.tag.taper("app")
        self.tag.with_user(self.personne).taper("app")
        vus = self.env["bf.nfc.tap"].with_user(self.personne).search([])
        self.assertTrue(vus)
        self.assertEqual(vus.mapped("user_id"), self.personne)

    def test_le_journal_ne_se_reecrit_pas(self):
        self.tag.taper("app")
        ligne = self.env["bf.nfc.tap"].with_user(self.personne).search([], limit=1)
        if ligne:
            with self.assertRaises(AccessError):
                ligne.write({"message": "réécrit"})
