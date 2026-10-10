"""La relecture adverse de la 1.0.0 : chaque trouvaille fermée, par le refus qui la ferme.

Chaque essai vérifie le MESSAGE du refus quand une garde voisine pourrait refuser à
sa place : un essai vert pour la mauvaise raison ne prouve rien.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from odoo.addons.base.models import res_users
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .. import uninstall_hook
from .common import CreditCase

MODELE = "bf.credit.reminder"
SECRET = "Gel TransUnion pour le prêt auto"


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestRelecture(CreditCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rappel_anne = cls.env_anne[MODELE].create({
            "name": SECRET, "kind": "freeze_back", "bureau": "transunion",
            "next_date": "2031-03-01", "recurring": False})
        cls.rappel_bruno = cls.env_bruno[MODELE].create({
            "name": "Relevés de Bruno", "kind": "statements",
            "next_date": "2031-03-01", "interval_number": 1})
        cls.rappel_admin = cls.env_admin[MODELE].create({
            "name": "Relevés de l'administratrice", "kind": "statements",
            "next_date": "2031-03-01", "interval_number": 1})
        cls.modele_id = cls.env["ir.model"]._get_id(MODELE)
        cls.todo = cls.env.ref("mail.mail_activity_data_todo")

    # ------------------------------------------------------------- 1 et 2 : le nom
    def test_le_nom_est_neutre_pour_autrui(self):
        # sudo() garde l'uid : c'est ainsi qu'Odoo lit le nom pour un tiers.
        self.assertEqual(self.rappel_anne.with_user(self.bruno).sudo().display_name, "Private reminder")
        self.assertEqual(self.rappel_anne.with_user(self.admin).sudo().display_name, "Private reminder")
        self.assertEqual(self.rappel_anne.with_user(self.anne).display_name, SECRET)
        self.assertEqual(self.rappel_anne.sudo().display_name, SECRET, "la tâche planifiée lit le vrai nom")

    def test_l_erreur_d_acces_en_debug_ne_nomme_pas_le_rappel(self):
        # has_group("base.group_no_one") = appartenance ET session en mode debug.
        self.bruno.groups_id += self.env.ref("base.group_no_one")
        session_debug = MagicMock()
        session_debug.session.debug = "1"
        with patch.object(res_users, "request", session_debug):
            self.assertTrue(self.env_bruno.user.has_group("base.group_no_one"), "précondition : debug actif")
            with self.assertRaises(AccessError) as refus:
                self.rappel_anne.with_env(self.env_bruno).read(["id"])
        self.assertIn("%s: %d" % (MODELE, self.rappel_anne.id), str(refus.exception),
                      "précondition : le message debug liste bien les fiches refusées")
        self.assertNotIn(SECRET, str(refus.exception))

    # ------------------------------------------------- 1 b : l'activité déplacée
    def test_une_activite_deplacee_sur_le_rappel_d_autrui_est_refusee(self):
        depuis_son_rappel = self.env_bruno["mail.activity"].create({
            "res_model_id": self.modele_id, "res_id": self.rappel_bruno.id,
            "activity_type_id": self.todo.id, "user_id": self.bruno.id})
        with self.assertRaisesRegex(AccessError, "only be placed on your own credit reminder"):
            depuis_son_rappel.write({"res_id": self.rappel_anne.id})
        # Le groupe par défaut d'un compte neuf : il peut poser une activité sur un contact.
        self.bruno.groups_id += self.env.ref("base.group_partner_manager")
        depuis_son_contact = self.env_bruno["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.bruno.partner_id.id,
            "activity_type_id": self.todo.id, "user_id": self.bruno.id})
        with self.assertRaisesRegex(AccessError, "only be placed on your own credit reminder"):
            depuis_son_contact.write({"res_model_id": self.modele_id, "res_id": self.rappel_anne.id})
        self.assertFalse(self.rappel_anne.sudo().activity_ids)

    def test_une_activite_reste_au_proprietaire(self):
        with self.assertRaisesRegex(UserError, "stays with the reminder's owner"):
            self.env_anne["mail.activity"].create({
                "res_model_id": self.modele_id, "res_id": self.rappel_anne.id,
                "activity_type_id": self.todo.id, "user_id": self.bruno.id})
        activite = self.env_anne["mail.activity"].create({
            "res_model_id": self.modele_id, "res_id": self.rappel_anne.id,
            "activity_type_id": self.todo.id, "user_id": self.anne.id})
        avant = self.env["mail.mail"].sudo().search_count([])
        with self.assertRaisesRegex(UserError, "stays with the reminder's owner"):
            activite.write({"user_id": self.bruno.id})
        self.assertEqual(activite.sudo().user_id, self.anne)
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), avant)

    # ------------------------------------------------------------ 3 et 4 : abonnés
    def _ligne_heritee(self):
        """Une ligne d'abonné comme la 1.0.0 en laissait (avant la migration)."""
        self.env.cr.execute(
            "INSERT INTO mail_followers (res_model, res_id, partner_id) VALUES (%s, %s, %s) RETURNING id",
            (MODELE, self.rappel_anne.id, self.anne.partner_id.id))
        return self.env["mail.followers"].browse(self.env.cr.fetchone()[0])

    def test_les_abonnes_ne_se_cherchent_pas(self):
        self._ligne_heritee()
        Abonne = self.env_bruno["mail.followers"]
        self.assertFalse(Abonne.search([("res_model", "=", MODELE)]))
        self.assertEqual(Abonne.search_count([("res_model", "=", MODELE)]), 0)
        self.assertEqual(Abonne.read_group([("res_model", "=", MODELE)], ["partner_id"], ["partner_id"]), [])

    def test_les_abonnes_ne_se_lisent_pas_par_id(self):
        ligne = self._ligne_heritee()
        with self.assertRaisesRegex(AccessError, "has no followers"):
            ligne.with_env(self.env_bruno).check_access("read")
        self.assertFalse(ligne.with_env(self.env_bruno).has_access("read"))

    def test_le_cœur_n_insere_aucun_abonne(self):
        self.env["mail.followers"]._insert_followers(
            MODELE, self.rappel_anne.ids, [self.bruno.partner_id.id, self.anne.partner_id.id])
        self.assertFalse(self.env["mail.followers"].sudo().search([("res_model", "=", MODELE)]))

    def test_message_subscribe_ne_dit_pas_a_qui_est_un_rappel(self):
        # Avant : refus si le rappel est à Anne, « vrai » s'il est à quelqu'un d'autre.
        for rappel in (self.rappel_anne, self.rappel_admin):
            with self.subTest(rappel=rappel.sudo().user_id.name), self.assertRaises(AccessError):
                rappel.with_env(self.env_bruno).message_subscribe(partner_ids=[self.anne.partner_id.id])

    def test_une_administratrice_ne_s_abonne_pas_a_la_main(self):
        with self.assertRaisesRegex(AccessError, "has no followers"):
            self.env_admin["mail.followers"].create({
                "res_model": MODELE, "res_id": self.rappel_anne.id,
                "partner_id": self.admin.partner_id.id})
        ligne = self.env_admin["mail.followers"].create({
            "res_model": "res.partner", "res_id": self.admin.partner_id.id,
            "partner_id": self.admin.partner_id.id})
        with self.assertRaisesRegex(AccessError, "has no followers"):
            ligne.write({"res_model": MODELE, "res_id": self.rappel_anne.id})
        self.assertFalse(self.env["mail.followers"].sudo().search([("res_model", "=", MODELE)]))

    def test_on_ne_nomme_personne_dans_le_fil(self):
        with self.assertRaisesRegex(UserError, "cannot mention or notify anyone"):
            self.rappel_anne.with_env(self.env_anne).message_post(
                body="Pour Bruno", partner_ids=[self.bruno.partner_id.id])
        # Se nommer soi-même reste permis.
        self.rappel_anne.with_env(self.env_anne).message_post(
            body="Pour moi", partner_ids=[self.anne.partner_id.id])

    # ------------------------------------------------------ 5 : la portée sensible
    def test_la_portee_sensible_est_declaree(self):
        self.assertEqual(type(self.env[MODELE])._gen_scope, "bf_credit_identity")
        # Ce que lit le résumé quotidien (daily_todo_digest 18.0.2.4.0), sans en dépendre.
        sensibles = [nom for nom, cls in self.env.registry.items()
                     if getattr(cls, "_gen_scope", None) and not cls._abstract]
        self.assertIn(MODELE, sensibles)

    # ------------------------------------------------ 6 : faire l'activité, relever
    @freeze_time("2031-02-26")
    def test_faire_l_activite_fait_le_rappel(self):
        self.env[MODELE]._cron_raise_activities()
        activite = self.rappel_bruno.sudo().activity_ids
        self.assertEqual(len(activite), 1)
        activite.with_env(self.env_bruno).action_feedback(feedback="Fait")
        self.assertEqual(self.rappel_bruno.sudo().last_done_date, date(2031, 2, 26))
        self.assertEqual(self.rappel_bruno.sudo().next_date, date(2031, 3, 26))
        # Un rappel ponctuel fait par son activité s'archive.
        ponctuelle = self.rappel_anne.sudo().activity_ids
        self.assertEqual(len(ponctuelle), 1)
        ponctuelle.with_env(self.env_anne).action_feedback()
        self.assertFalse(self.rappel_anne.sudo().active)

    @freeze_time("2031-02-26")
    def test_un_rappel_du_sans_activite_est_releve(self):
        self.env[MODELE]._cron_raise_activities()
        self.assertTrue(self.rappel_bruno.sudo().activity_ids)
        self.rappel_bruno.with_env(self.env_bruno).write({"active": False})
        self.assertFalse(self.rappel_bruno.sudo().activity_ids, "précondition : l'archivage retire l'activité")
        self.rappel_bruno.with_env(self.env_bruno).write({"active": True})
        self.env[MODELE]._cron_raise_activities()
        self.assertEqual(len(self.rappel_bruno.sudo().activity_ids), 1)

    # --------------------------------------------- 9 : la pièce jointe déplacée
    def test_une_piece_jointe_deplacee_chez_autrui_est_refusee(self):
        piece = self.env_bruno["ir.attachment"].create({
            "name": "x.txt", "raw": b"x", "res_model": MODELE, "res_id": self.rappel_bruno.id})
        with self.assertRaisesRegex(AccessError, "only be attached to your own credit reminder"):
            piece.write({"res_id": self.rappel_anne.id})

    # ---------------------------------------------- 11 et 12 : désinstaller, lien
    @freeze_time("2031-02-26")
    def test_la_desinstallation_retire_d_abord_les_activites(self):
        self.env[MODELE]._cron_raise_activities()
        self.assertTrue(self.env["mail.activity"].sudo().search([("res_model", "=", MODELE)]))
        uninstall_hook(self.env)
        self.assertFalse(self.env["mail.activity"].sudo().search([("res_model", "=", MODELE)]))

    def test_la_page_officielle_suit_la_langue_de_qui_clique(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        rappel = self.env(user=self.anne, context={"lang": "fr_CA"})[MODELE].create({
            "name": "Equifax", "kind": "report_equifax", "next_date": "2031-03-01"})
        rappel._onchange_kind()
        self.assertIn("/fr/", rappel.link_url, "précondition : créé en français")
        en = rappel.with_context(lang="en_US").action_open_link()["url"]
        self.assertIn("equifax.ca/personal/", en)
        rappel.link_url = "https://exemple.test/ma-page"
        self.assertEqual(rappel.with_context(lang="en_US").action_open_link()["url"],
                         "https://exemple.test/ma-page")

    def test_l_assistant_de_mise_en_route_est_prive(self):
        # Porte de publication : un TransientModel n'est pas isolé d'office. Sans la
        # règle, rien d'autre ne refuserait : tout interne a l'accès à l'assistant.
        assistant = self.env["bf.credit.setup"].with_user(self.anne).create(
            {"alert_date": "2031-03-01", "alert_bureau": "transunion"})
        self.assertFalse(self.env["bf.credit.setup"].with_user(self.bruno).search([("id", "=", assistant.id)]))
        with self.assertRaises(AccessError):
            assistant.with_user(self.bruno).read(["alert_date", "alert_bureau"])
        self.assertEqual(
            assistant.with_user(self.anne).read(["alert_bureau"])[0]["alert_bureau"], "transunion")
