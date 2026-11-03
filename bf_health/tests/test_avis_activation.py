"""Avis d'activation de Healthy Fox et consentement exprès.

Sur une instance qui l'exige, Healthy Fox est fermé à la personne tant qu'elle
n'a pas consenti à l'avis en vigueur, à l'écran. Éprouvé ici par l'ORM, par de
VRAIS appels XML-RPC et JSON-RPC sous une clé d'API (comme Gen), et par les
routes du client web sous une session. Données inventées.
"""
import hashlib
import json
import time
import xmlrpc.client
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import polib
from psycopg2 import IntegrityError

from odoo import api, fields
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..models import activation_consent
from ..models.activation_consent import (
    AVIS_ACTIVATION_VERSION, CLE_ECRAN, PARAM_EXIGE, garder_ouverts, ouverte,
)

GROUPES = "base.group_user,bf_health.group_health_user"
#: Empreinte de l'avis 2026-10-11 (source et en_CA) : voir test_version_suit_le_texte.
EMPREINTE_AVIS = "ac145026caa761542d745284510a6cda81eaac19d0fa8a970f81099783b5e86e"


def _exiger(env, valeur="True"):
    env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, valeur)


def _consentir(env, user, version=AVIS_ACTIVATION_VERSION):
    """Ce que fait le bouton de l'avis, une fois l'écran vérifié (la requête du
    bouton est simulée : les essais HTTP prouvent le vrai chemin)."""
    with patch.object(activation_consent, "_requete_du_bouton", lambda uid: uid == user.id):
        return env["health.activation.consent"].sudo().with_context(**{CLE_ECRAN: True}).create({
            "user_id": user.id, "version": version, "lang": "fr_CA",
            "notice_sha256": "0" * 64, "consent_date": fields.Datetime.now(),
        })


class _Donnees:

    @classmethod
    def _monter(cls, env, prefixe):
        # Les saisies d'avant l'avis : l'instance ne l'exige pas encore (sur une base de
        # foyer, le profil a posé le paramètre ; le superutilisateur le retire ici).
        env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        tz = "America/Montreal"
        cls.a = new_test_user(env, login=f"{prefixe}_a", password=f"{prefixe}_a_mdp", groups=GROUPES,
                              name="Personne A", tz=tz)
        cls.b = new_test_user(env, login=f"{prefixe}_b", password=f"{prefixe}_b_mdp", groups=GROUPES,
                              name="Personne B", tz=tz)
        cls.admin = new_test_user(env, login=f"{prefixe}_admin", password=f"{prefixe}_admin_mdp",
                                  groups=GROUPES + ",base.group_system", name="Administratrice", tz=tz)
        cls.bf = new_test_user(env, login=f"{prefixe}_bf", password=f"{prefixe}_bf_mdp",
                               groups="base.group_user,base.group_system", name="Blue Fox", tz=tz)
        # Saisies faites AVANT que l'instance exige le consentement.
        cls.vital_a = env["health.vital"].with_user(cls.a).create(
            {"vital_type": "weight", "value": 71.5, "notes": "SECRET-VITAL-A"})
        cls.med_a = env["health.medication"].with_user(cls.a).create(
            {"name": "SECRET-MED-A", "frequency": "daily", "state": "active",
             "date_start": fields.Date.today() - timedelta(days=3)})
        cls.med_b = env["health.medication"].with_user(cls.b).create(
            {"name": "SECRET-MED-B", "frequency": "daily", "state": "active",
             "date_start": fields.Date.today() - timedelta(days=3)})
        cls.humeur_a = env["health.mood.entry"].with_user(cls.a).create(
            {"level": "4", "note": "SECRET-HUMEUR-A", "date": fields.Date.today() - timedelta(days=1)})
        # Une activité assignée à A sur son médicament, et une note d'elle dessus.
        cls.activite_a = cls.med_a.sudo().activity_schedule(
            "mail.mail_activity_data_todo", summary="SECRET-ACT-A", user_id=cls.a.id)
        cls.partenaire = env["res.partner"].create({"name": "Contact fictif"})
        cls.note_hors_sante = cls.partenaire.sudo().message_post(
            body="Note hors santé", message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=cls.a.partner_id.id)


@tagged("post_install", "-at_install", "avis_activation")
class TestAvisActivation(_Donnees, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._monter(cls.env, "avis_orm")

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    # -- Sans le paramètre : rien ne change ------------------------------------------
    def test_sans_parametre_rien_ne_change(self):
        # Explicite : sur une base de foyer, le profil pose le paramètre.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        self.assertTrue(ouverte(self.env(user=self.a)))
        self.assertEqual(self._en(self.a, "health.vital").search([]), self.vital_a)
        action = self._en(self.a, "health.activation.consent")._bf_action_entree()
        self.assertEqual(action.get("tag"), "health_dashboard")
        menus = self.env["ir.ui.menu"].with_user(self.a).load_menus(False)
        mien = self.env.ref("bf_health.health_menu_activation_consent").id
        meds = self.env.ref("bf_health.health_menu_medication_all").id
        self.assertIn(meds, menus)
        self.assertNotIn(mien, menus, "« Mon consentement » n'a pas lieu d'être sans le paramètre")

    # -- Fermé par l'ORM -------------------------------------------------------------
    def test_ferme_sans_consentement(self):
        _exiger(self.env)
        Vital = self._en(self.a, "health.vital")
        self.assertFalse(Vital.search([]))
        self.assertEqual(Vital.search_count([]), 0)
        self.assertFalse(Vital.read_group([], ["value:sum"], [])[0].get("__count"))
        with self.assertRaises(AccessError):
            Vital.browse(self.vital_a.id).read(["notes"])
        with self.assertRaises(AccessError):
            Vital.create({"vital_type": "weight", "value": 70})
        with self.assertRaises(AccessError):
            self._en(self.a, "health.medication").browse(self.med_a.id).write({"name": "x"})
        self.assertFalse(self._en(self.a, "health.mood.entry").search([]))
        with self.assertRaises(AccessError):
            self._en(self.a, "health.daily.log.wizard").create({})
        # Rien n'est effacé : la fiche est toujours là.
        self.assertTrue(self.vital_a.sudo().exists())

    def test_compagnons_fermes(self):
        _exiger(self.env)
        Act = self._en(self.a, "mail.activity")
        self.assertFalse(Act.search([("res_model", "=", "health.medication")]))
        self.assertFalse(Act.search([("summary", "=", "SECRET-ACT-A")]))
        Msg = self._en(self.a, "mail.message")
        self.assertFalse(Msg.search([("model", "=", "health.medication")]))
        # Ce qui n'est pas la santé reste lisible, messages sans modèle compris.
        self.assertIn(self.note_hors_sante, Msg.search([("model", "=", "res.partner")]))
        sans_modele = self.env["mail.message"].create({"body": "Sans modèle", "author_id": self.a.partner_id.id})
        self.assertIn(sans_modele, self._en(self.a, "mail.message").search([("id", "=", sans_modele.id)]))
        self.assertFalse(self._en(self.a, "mail.followers").search([("res_model", "=", "health.medication")]))

    def test_administratrice_fermee_aussi(self):
        _exiger(self.env)
        Vital = self._en(self.admin, "health.vital")
        self.assertFalse(Vital.search([]))
        # Et Blue Fox garde ce que sa règle lui donne : les enfants qui ont 14 ans.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        today = fields.Date.today()
        enfant = self._en(self.a, "health.dependent").create({
            "name": "Enfant fictif", "birth_month": str(today.month),
            "birth_year": today.year - 10, "consentement_parent": True})
        jeune = self._en(self.a, "health.dependent").create({
            "name": "Plus jeune", "birth_month": str(today.month),
            "birth_year": today.year - 5, "consentement_parent": True})
        # Il a eu 14 ans depuis (le modèle refuse d'en créer un de 14 ans ou plus).
        self.env.cr.execute("UPDATE health_dependent SET birth_year = %s, passage_date = %s WHERE id = %s",
                            [today.year - 15, today - timedelta(days=200), enfant.id])
        self.env.invalidate_all()
        _exiger(self.env)
        vus = self._en(self.bf, "health.dependent").search([])
        self.assertIn(enfant, vus)
        self.assertNotIn(jeune, vus)
        # Les consentements des enfants ne bougent pas.
        self.assertTrue(enfant.sudo().consent_version)

    # -- Le consentement ne se donne pas par l'ORM -------------------------------------
    def test_consentement_hors_ecran_refuse(self):
        _exiger(self.env)
        with self.assertRaises(AccessError):
            self._en(self.a, "health.activation.consent").create({
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.now()})
        with self.assertRaises(AccessError):
            self.env["health.activation.consent"].sudo().create({
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.now()})
        # Même avec la marque d'écran, sans la requête du bouton de CETTE personne :
        # une action serveur, une automatisation ou une tâche d'un administrateur.
        with self.assertRaises(AccessError):
            self.env["health.activation.consent"].sudo().with_context(**{CLE_ECRAN: True}).create({
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.now()})
        # La requête du bouton sans la marque d'écran (ni superutilisateur) : refusé aussi.
        with patch.object(activation_consent, "_requete_du_bouton", lambda uid: True), \
                self.assertRaises(AccessError):
            self.env["health.activation.consent"].sudo().create({
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.now()})
        with patch.object(activation_consent, "_requete_du_bouton", lambda uid: uid == self.admin.id), \
                self.assertRaises(AccessError):
            self.env["health.activation.consent"].sudo().with_context(**{CLE_ECRAN: True}).create({
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.now()})
        # Hors du formulaire à l'écran, la case ne se coche même pas.
        with self.assertRaises(AccessError):
            self._en(self.a, "health.activation.notice").create({"lu": True})
        avis = self._en(self.a, "health.activation.notice").create({})
        with self.assertRaises(AccessError):
            avis.write({"lu": True})
        with self.assertRaises(AccessError):
            avis.action_accept()
        self.assertNotIn("lu", self._en(self.a, "health.activation.notice").with_context(
            default_lu=True).default_get(["lu", "version"]))
        self.assertFalse(self.env["health.activation.consent"].sudo().search([("user_id", "=", self.a.id)]))
        self.assertFalse(ouverte(self.env(user=self.a)))

    def test_preuve_a_la_personne_seule(self):
        _exiger(self.env)
        preuve = _consentir(self.env, self.a)
        self.assertFalse(self._en(self.admin, "health.activation.consent").search([]))
        self.assertFalse(self._en(self.b, "health.activation.consent").search([]))
        with self.assertRaises(AccessError):
            self._en(self.admin, "health.activation.consent").browse(preuve.id).action_withdraw()
        with self.assertRaises(AccessError):
            self._en(self.a, "health.activation.consent").browse(preuve.id).write(
                {"withdrawn_date": fields.Datetime.now()})
        with self.assertRaises(AccessError):
            preuve.sudo().write({"version": "autre"})
        with self.assertRaises(AccessError):
            self._en(self.a, "health.activation.consent").browse(preuve.id).unlink()
        self.assertEqual(preuve.consent_date.date(), fields.Date.today(), "date du serveur, pas celle donnée")
        self.assertEqual(self._en(self.a, "health.activation.consent").search([]), preuve)

    # -- Ouvert, puis refermé ----------------------------------------------------------
    def test_consentement_ouvre_et_retrait_referme(self):
        _exiger(self.env)
        preuve = _consentir(self.env, self.a)
        self.assertTrue(ouverte(self.env(user=self.a)))
        self.assertEqual(self._en(self.a, "health.vital").search([]), self.vital_a)
        self.assertFalse(self._en(self.b, "health.vital").search([]), "B n'a pas consenti")
        action = self._en(self.a, "health.activation.consent")._bf_action_entree()
        self.assertEqual(action.get("tag"), "health_dashboard")
        mon = self._en(self.a, "health.activation.consent")._bf_action_mon_consentement()
        self.assertEqual(mon.get("res_id"), preuve.id)
        self._en(self.a, "health.activation.consent").browse(preuve.id).action_withdraw()
        self.assertTrue(preuve.withdrawn_date)
        self.assertEqual(preuve.withdrawn_via, "api", "hors du bouton : par l'API ou le code")
        self.assertFalse(ouverte(self.env(user=self.a)))
        self.assertFalse(self._en(self.a, "health.vital").search([]))
        self.assertTrue(self.vital_a.sudo().exists(), "le retrait n'efface rien")
        with self.assertRaises(AccessError):
            preuve.sudo().write({"withdrawn_date": fields.Datetime.now()})
        action = self._en(self.a, "health.activation.consent")._bf_action_entree()
        self.assertEqual(action.get("res_model"), "health.activation.notice")

    def test_retrait_referme_toutes_les_preuves(self):
        _exiger(self.env)
        ancienne = _consentir(self.env, self.a, version="2000-01-01")
        courante = _consentir(self.env, self.a)
        self._en(self.a, "health.activation.consent").browse(courante.id).action_withdraw()
        self.assertTrue(courante.withdrawn_date)
        self.assertTrue(ancienne.withdrawn_date, "le geste referme aussi ce qui reste d'avant")

    def test_version_neuve_redemande(self):
        _exiger(self.env)
        _consentir(self.env, self.a, version="2000-01-01")
        self.assertFalse(ouverte(self.env(user=self.a)))
        avis = self._en(self.a, "health.activation.notice").create({})
        self.assertTrue(avis.avis_change)
        self.assertFalse(avis.deja_ouvert)
        self.assertIn("vieprivee@bluefoxconsultant.com", str(avis.notice_html))

    def test_formulaire_neuf_porte_l_avis(self):
        """Le client web ouvre l'avis par ``onchange`` sur un formulaire neuf :
        sans dépendance, les champs calculés y revenaient vides (vu au parcours)."""
        _exiger(self.env)
        valeur = self._en(self.a, "health.activation.notice").onchange(
            {}, [], {"exige": {}, "deja_ouvert": {}, "avis_change": {}, "notice_html": {}})["value"]
        self.assertTrue(valeur["exige"])
        self.assertFalse(valeur["deja_ouvert"])
        self.assertIn("vieprivee@bluefoxconsultant.com", str(valeur["notice_html"]))

    def test_menus(self):
        _exiger(self.env)
        racine = self.env.ref("bf_health.health_menu_root").id
        meds = self.env.ref("bf_health.health_menu_medication_all").id
        mien = self.env.ref("bf_health.health_menu_activation_consent").id
        menus = self.env["ir.ui.menu"].with_user(self.a).load_menus(False)
        self.assertIn(racine, menus)
        self.assertNotIn(meds, menus)
        self.assertNotIn(mien, menus)
        _consentir(self.env, self.a)
        menus = self.env["ir.ui.menu"].with_user(self.a).load_menus(False)
        self.assertIn(meds, menus)
        self.assertIn(mien, menus)

    def test_tableau_de_bord_ferme(self):
        _exiger(self.env)
        with self.assertRaisesRegex(AccessError, "lu l'avis"):
            self._en(self.a, "health.dashboard").get_dashboard_data()
        _consentir(self.env, self.a)
        self.assertIn("med_compliance", self._en(self.a, "health.dashboard").get_dashboard_data())

    # -- Relectures adverses du 2026-10-11 --------------------------------------------
    def _enfant_de_a(self, ans=10):
        """Un enfant suivi par A, créé avant que l'instance exige l'avis."""
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        today = fields.Date.today()
        enfant = self._en(self.a, "health.dependent").create({
            "name": "Enfant fictif", "birth_month": str(today.month),
            "birth_year": today.year - ans, "consentement_parent": True})
        _exiger(self.env)
        return enfant

    def test_gestes_de_la_personne_a_charge_fermes(self):
        enfant = self._enfant_de_a()
        with self.assertRaisesRegex(AccessError, "lu l'avis"):
            self._en(self.a, "health.dependent").browse(enfant.id).action_effacer()
        self.assertTrue(enfant.sudo().exists(), "rien n'est effacé par un parent fermé")
        self.env.cr.execute("UPDATE health_dependent SET state = 'offert', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env.invalidate_all()
        with self.assertRaisesRegex(AccessError, "lu l'avis"):
            self._en(self.b, "health.dependent").browse(enfant.id).action_recevoir()
        self.assertEqual(enfant.sudo().state, "offert")

    def test_passage_a_14_ans_rien_pour_un_parent_ferme(self):
        enfant = self._enfant_de_a()
        today = fields.Date.context_today(self.env["health.dependent"].with_user(self.a))
        enfant.sudo().write({"birth_year": today.year - 14, "birth_month": str(today.month)})
        self.env.invalidate_all()
        self.assertTrue(enfant.sudo().passage_atteint)
        self.env["health.dependent"]._cron_passage_14_ans()
        Act = self.env["mail.activity"].sudo()
        domaine = [("res_model", "=", "health.dependent"), ("res_id", "=", enfant.id)]
        self.assertTrue(Act.search(domaine), Act.search([("res_model", "=", "health.dependent")]).read(["user_id"]))
        self.assertFalse(Act.search(domaine + [("user_id", "=", self.a.id)]), "A est fermée")
        admin = self.env.ref("base.user_admin")
        self.assertTrue(Act.search(domaine + [("user_id", "=", admin.id)]), "Blue Fox reçoit le sien")
        # Et Blue Fox, administratrice qui n'a pas consenti, le voit.
        self.assertTrue(self.env["mail.activity"].with_user(admin).search(domaine))

    def test_partage_suspendu_quand_l_ado_est_ferme(self):
        enfant = self._enfant_de_a()
        self.env.cr.execute("UPDATE health_dependent SET state = 'transfere', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env.invalidate_all()
        self.env["health.share"].sudo().create(
            {"owner_id": self.b.id, "parent_id": self.a.id, "categorie": "vital"})
        vital_b = self.env["health.vital"].with_user(self.b).sudo().create(
            {"vital_type": "weight", "value": 52.0, "notes": "SECRET-VITAL-B"})
        # Sans le paramètre : le partage marche par la recherche ET par le contrôle par
        # fiche (fil, pièces jointes, nom affiché), comme en 2.6.0.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        fiche_a = self._en(self.a, "health.vital").browse(vital_b.id)
        self.assertTrue(fiche_a.has_access("read"))
        self.assertNotEqual(fiche_a.display_name, "Fiche santé privée")
        _exiger(self.env)
        _consentir(self.env, self.a)
        lu_par_a = lambda: self._en(self.a, "health.vital").search([("id", "=", vital_b.id)])  # noqa: E731
        self.assertFalse(self._en(self.a, "health.vital").browse(vital_b.id).has_access("read"))
        self.assertFalse(lu_par_a(), "B (l'ado) est fermée : son partage est suspendu")
        preuve_b = _consentir(self.env, self.b)
        self.assertEqual(lu_par_a(), vital_b, "B ouverte : son parent lit ce qu'elle partage")
        self.assertFalse(self.b.with_user(self.a).bf_health_ouvert, "A n'apprend pas que B a consenti")
        self.assertTrue(self.b.with_user(self.b).bf_health_ouvert)
        self.assertTrue(self._en(self.a, "health.vital").browse(vital_b.id).has_access("read"))
        preuve_b.with_user(self.b).action_withdraw()
        self.assertFalse(lu_par_a(), "B retire son consentement : le partage se suspend")
        # Le champ ne se lit que pour soi, et ne se cherche que dans les règles.
        self.assertFalse(self.b.with_user(self.a).bf_health_ouvert)
        with self.assertRaises(AccessError):
            self.env["res.users"].with_user(self.a).search([("bf_health_ouvert", "=", True)])

    def test_valeurs_suivies_fermees(self):
        _exiger(self.env)
        admin = self.env.ref("base.user_admin")
        champ = self.env["ir.model.fields"]._get("health.medication", "name")
        msg = self.med_a.sudo().message_post(body="Note", message_type="comment", subtype_xmlid="mail.mt_note")
        suivi = self.env["mail.tracking.value"].sudo().create({
            "field_id": champ.id, "old_value_char": "SECRET-ANCIEN", "new_value_char": "SECRET-NOUVEAU",
            "mail_message_id": msg.id})
        self.assertFalse(self.env["mail.tracking.value"].with_user(admin).search([("id", "=", suivi.id)]))

    def test_blue_fox_n_apprend_ni_le_nombre_ni_les_partages(self):
        enfant = self._enfant_de_a()
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        self._en(self.a, "health.vital").create({"vital_type": "weight", "value": 30.0,
                                                 "dependent_id": enfant.id})
        _exiger(self.env)
        enfant.sudo().write({"birth_year": fields.Date.today().year - 14,
                             "birth_month": str(fields.Date.today().month)})
        self.env.cr.execute("UPDATE health_dependent SET state = 'transfere', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env["health.share"].sudo().create(
            {"owner_id": self.b.id, "parent_id": self.a.id, "categorie": "vital"})
        self.env.invalidate_all()
        vu = self._en(self.bf, "health.dependent").browse(enfant.id)
        self.assertEqual(vu.name, "Enfant fictif", "Blue Fox lit la personne à charge de 14 ans")
        self.assertEqual(vu.record_count, 0)
        self.assertFalse(vu.partage_vital)
        # L'ado consent : Blue Fox, fermée, n'en apprend pas plus ; le superutilisateur
        # voit la vraie valeur (contre-épreuve).
        _consentir(self.env, self.b)
        self.env.invalidate_all()
        self.assertEqual(self._en(self.bf, "health.dependent").browse(enfant.id).record_count, 0)
        self.assertFalse(self._en(self.bf, "health.dependent").browse(enfant.id).partage_vital)
        self.assertTrue(enfant.sudo().record_count)
        self.assertTrue(enfant.sudo().partage_vital)

    def test_rendez_vous_lie_a_une_fiche(self):
        if "calendar.event" not in self.env:
            self.skipTest("calendar n'est pas installé")
        _exiger(self.env)
        modele = self.env["ir.model"]._get("health.medication")
        evenement = self.env["calendar.event"].sudo().create({
            "name": "SECRET-RDV", "start": fields.Datetime.now(), "stop": fields.Datetime.now(),
            "user_id": self.a.id, "res_model_id": modele.id, "res_id": self.med_a.id,
            "partner_ids": [(6, 0, self.a.partner_id.ids)]})
        self.assertFalse(self._en(self.a, "calendar.event").search([("id", "=", evenement.id)]))
        self.assertFalse(self._en(self.a, "calendar.attendee").search([("event_id", "=", evenement.id)]))
        _consentir(self.env, self.a)
        self.assertTrue(self._en(self.a, "calendar.event").search([("id", "=", evenement.id)]))

    def test_blue_fox_ne_lit_pas_le_suivi_des_enfants(self):
        enfant = self._enfant_de_a()
        admin = self.env.ref("base.user_admin")
        msg = enfant.sudo().message_post(body="Note", message_type="comment", subtype_xmlid="mail.mt_note")
        self.env["mail.tracking.value"].sudo().create({
            "field_id": self.env["ir.model.fields"]._get("health.dependent", "name").id,
            "old_value_char": "", "new_value_char": "SECRET-PRENOM", "mail_message_id": msg.id})
        suivis = self.env["mail.tracking.value"].with_user(admin).search(
            [("mail_message_id.model", "=", "health.dependent"), ("mail_message_id.res_id", "=", enfant.id)])
        self.assertFalse(suivis, "le prénom d'un enfant de 10 ans ne sort pas par l'historique")
        self.assertFalse(self.env["mail.message"].with_user(admin).search(
            [("model", "=", "health.dependent"), ("res_id", "=", enfant.id)]))

    def test_preuve_garde_le_texte_et_survit_a_la_personne(self):
        _exiger(self.env)
        preuve = _consentir(self.env, self.a)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.a.sudo().unlink()

    def test_preuve_a_soi_meme_sans_la_regle(self):
        """La règle en données coupée par un administrateur : la garde du code tient."""
        _exiger(self.env)
        preuve = _consentir(self.env, self.a)
        self.env.ref("bf_health.health_activation_consent_rule_global").sudo().active = False
        self.assertFalse(self._en(self.admin, "health.activation.consent").search([("id", "=", preuve.id)]))
        self.assertFalse(self._en(self.b, "health.activation.consent").search([]))

    def test_version_suit_le_texte(self):
        """Changer l'avis (source ou traduction) sans changer AVIS_ACTIVATION_VERSION doit
        faire tomber cet essai : mettre alors à jour la version ET l'empreinte ci-dessous."""
        vue = self.env.ref("bf_health.health_activation_notice_body")
        source = vue.with_context(lang="en_US").arch_db
        po = polib.pofile(get_module_path("bf_health") + "/i18n/en_CA.po")
        cle = "model_terms:ir.ui.view,arch_db:bf_health.health_activation_notice_body"
        traduction = sorted((e.msgid, e.msgstr) for e in po if any(o[0] == cle for o in e.occurrences))
        empreinte = hashlib.sha256(json.dumps([source, traduction]).encode()).hexdigest()
        self.assertEqual((AVIS_ACTIVATION_VERSION, empreinte), ("2026-10-11", EMPREINTE_AVIS))

    # -- Portée de la fermeture --------------------------------------------------------
    def test_annulation_sur_un_vrai_curseur(self):
        """Une requête qui a calculé une règle santé puis échoue. Sur un VRAI curseur,
        la vraie erreur remonte et la connexion revient au pool (le crochet d'annulation
        s'exécutait après la suppression du cache du curseur : récursion infinie)."""
        requete = object()
        cr = self.registry.cursor()
        self.assertEqual(type(cr).__name__, "Cursor", "un vrai curseur, pas celui des essais")
        pool, cnx = cr._Cursor__pool, cr._cnx

        def pretee():
            return [bool(u) for c, u, _t in pool._connections if c is cnx]

        self.assertEqual(pretee(), [True], "la connexion du curseur est prêtée par le pool")
        # Une personne qui existe hors de la transaction de l'essai (validée en base).
        uid = self.env.ref("base.user_admin").id
        try:
            with patch.object(activation_consent, "_requete_courante", lambda: requete), \
                    patch.object(activation_consent, "consentement_exige", lambda env: True):
                env2 = api.Environment(cr, uid, {})
                # Un compagnon : la règle santé s'y calcule, sans droit d'accès particulier.
                env2["mail.message"].search([("model", "=", "health.vital")], limit=1)
                self.assertTrue(cr.postrollback._funcs, "le crochet d'annulation est posé")
                with self.assertRaisesRegex(ZeroDivisionError, "la vraie erreur"):
                    try:
                        raise ZeroDivisionError("la vraie erreur")
                    finally:
                        cr.close()
        finally:
            if not cr.__dict__.get("_closed"):
                cr.__dict__["_closed"] = True  # ne pas relancer la récursion en nettoyant
        self.assertTrue(cr.closed)
        self.assertNotIn("cache", cr.__dict__)
        self.assertEqual(pretee(), [False], "la connexion est rendue au pool, libre")

    def test_administrateur_systeme_jamais_ouvert(self):
        """Avec le paramètre, une administratrice système qui se donne le groupe
        santé et une preuve reste fermée partout."""
        enfant = self._enfant_de_a()
        _consentir(self.env, self.admin)
        self.assertFalse(ouverte(self.env(user=self.admin)))
        self.assertFalse(self._en(self.admin, "health.vital").search([]))
        vu = self._en(self.admin, "health.dependent").search([("id", "=", enfant.id)])
        self.assertFalse(vu, "un enfant de 10 ans reste hors de sa portée")
        self.assertFalse(self.admin.with_user(self.admin).bf_health_ouvert)
        # Sans le paramètre (BF, PMEC) : rien ne change, elle lit ses propres fiches.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        self.assertTrue(ouverte(self.env(user=self.admin)))

    def test_blue_fox_qui_se_donne_le_groupe_reste_fermee(self):
        """Blue Fox s'ajoute le groupe santé et détient
        une preuve. Elle ne lit ni le nombre de fiches ni les notes d'un enfant, ni les
        valeurs suivies d'une fermée, ni ce que partage une ado fermée, ni le rendez-vous lié."""
        enfant = self._enfant_de_a()
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        self._en(self.a, "health.vital").create({"vital_type": "weight", "value": 30.0,
                                                 "dependent_id": enfant.id})
        _exiger(self.env)
        enfant.sudo().write({"birth_year": fields.Date.today().year - 15,
                             "birth_month": str(fields.Date.today().month)})
        self.env.cr.execute("UPDATE health_dependent SET state = 'transfere', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env["health.share"].sudo().create(
            {"owner_id": self.b.id, "parent_id": self.a.id, "categorie": "vital"})
        note = enfant.sudo().message_post(body="SECRET-NOTE-ENFANT", message_type="comment",
                                          subtype_xmlid="mail.mt_note")
        msg = self.med_a.sudo().message_post(body="Note", message_type="comment", subtype_xmlid="mail.mt_note")
        suivi = self.env["mail.tracking.value"].sudo().create({
            "field_id": self.env["ir.model.fields"]._get("health.medication", "name").id,
            "old_value_char": "SECRET-ANCIEN", "new_value_char": "SECRET-NOUVEAU", "mail_message_id": msg.id})
        evenement = None
        if "calendar.event" in self.env:
            evenement = self.env["calendar.event"].sudo().create({
                "name": "SECRET-RDV-A", "start": fields.Datetime.now(), "stop": fields.Datetime.now(),
                "user_id": self.a.id, "res_model_id": self.env["ir.model"]._get("health.medication").id,
                "res_id": self.med_a.id, "partner_ids": [(6, 0, (self.a.partner_id | self.bf.partner_id).ids)]})
        # Elle s'ajoute le groupe, et une preuve existe à son nom (le bouton la lui refuse :
        # voir l'essai HTTP), l'ado consent ; A reste fermée.
        self.bf.sudo().write({"groups_id": [(4, self.env.ref("bf_health.group_health_user").id)]})
        _consentir(self.env, self.bf)
        _consentir(self.env, self.b)
        self.env.invalidate_all()
        self.assertTrue(self.bf.has_group("bf_health.group_health_user"))
        self.assertFalse(ouverte(self.env(user=self.bf)))
        vu = self._en(self.bf, "health.dependent").browse(enfant.id)
        self.assertEqual(vu.record_count, 0)
        self.assertFalse(vu.partage_vital)
        self.assertFalse(self._en(self.bf, "mail.message").search([("id", "=", note.id)]))
        self.assertFalse(self._en(self.bf, "mail.tracking.value").search([("id", "=", suivi.id)]))
        if evenement:
            self.assertFalse(self._en(self.bf, "calendar.event").search([("id", "=", evenement.id)]))
        self.assertFalse(self._en(self.bf, "health.vital").search([]))
        # Contre-épreuve : la vraie valeur existe.
        self.assertTrue(enfant.sudo().record_count)

    def test_valeurs_suivies_fermees_meme_par_le_champ(self):
        """Les valeurs suivies d'un modèle santé ne se lisent ni par le message
        ni par le champ, même pour une lectrice ouverte."""
        _exiger(self.env)
        champ = self.env["ir.model.fields"]._get("health.medication", "name")
        msg = self.partenaire.sudo().message_post(body="Note", message_type="comment",
                                                  subtype_xmlid="mail.mt_note")
        suivi = self.env["mail.tracking.value"].sudo().create({
            "field_id": champ.id, "old_value_char": "SECRET-A", "new_value_char": "SECRET-B",
            "mail_message_id": msg.id})
        _consentir(self.env, self.b)
        lectrice = new_test_user(self.env, login="avis_lectrice_sys", groups="base.group_user,base.group_system")
        self.assertFalse(self.env["mail.tracking.value"].with_user(lectrice).search([("id", "=", suivi.id)]))

    def test_partages_de_l_ado_fermee_suspendus(self):
        """Le parent ouvert ne lit ni les partages ni les drapeaux d'une ado fermée."""
        enfant = self._enfant_de_a()
        self.env.cr.execute("UPDATE health_dependent SET state = 'transfere', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env.invalidate_all()
        partage = self.env["health.share"].sudo().create(
            {"owner_id": self.b.id, "parent_id": self.a.id, "categorie": "vital"})
        _consentir(self.env, self.a)
        self.assertFalse(self._en(self.a, "health.share").search([("id", "=", partage.id)]))
        self.assertFalse(self._en(self.a, "health.dependent").browse(enfant.id).partage_vital)
        _consentir(self.env, self.b)
        self.assertEqual(self._en(self.a, "health.share").search([("id", "=", partage.id)]), partage)
        self.assertTrue(self._en(self.a, "health.dependent").browse(enfant.id).partage_vital)

    def test_rendez_vous_d_une_fermee_cache_aux_ouverts(self):
        """Le rendez-vous lié à la fiche de A (fermée) ne se lit pas par B (ouverte)."""
        if "calendar.event" not in self.env:
            self.skipTest("calendar n'est pas installé")
        _exiger(self.env)
        evenement = self.env["calendar.event"].sudo().create({
            "name": "SECRET-RDV-A", "start": fields.Datetime.now(), "stop": fields.Datetime.now(),
            "user_id": self.a.id, "res_model_id": self.env["ir.model"]._get("health.medication").id,
            "res_id": self.med_a.id, "partner_ids": [(6, 0, (self.a.partner_id | self.b.partner_id).ids)]})
        _consentir(self.env, self.b)
        self.assertFalse(self._en(self.b, "calendar.event").search([("id", "=", evenement.id)]))
        self.assertFalse(self._en(self.b, "calendar.attendee").search([("event_id", "=", evenement.id)]))
        _consentir(self.env, self.a)
        self.assertTrue(self._en(self.b, "calendar.event").search([("id", "=", evenement.id)]))
        # Lié à la fiche d'une administratrice système qui détient une preuve : jamais « ouverte ».
        _consentir(self.env, self.admin)
        med_admin = self.env["health.medication"].with_user(self.admin).sudo().create({"name": "SECRET-MED-ADMIN"})
        par_admin = self.env["calendar.event"].sudo().create({
            "name": "SECRET-RDV-ADMIN", "start": fields.Datetime.now(), "stop": fields.Datetime.now(),
            "user_id": self.admin.id, "res_model_id": self.env["ir.model"]._get("health.medication").id,
            "res_id": med_admin.id, "partner_ids": [(6, 0, (self.admin.partner_id | self.b.partner_id).ids)]})
        self.assertFalse(self._en(self.b, "calendar.event").search([("id", "=", par_admin.id)]))

    def test_compte_des_modeles_ferme(self):
        """Réglages > Technique > Modèles ne compte pas les fiches santé."""
        _exiger(self.env)
        modele = self.env["ir.model"]._get("health.vital")
        self.assertEqual(modele.with_user(self.admin).count, 0)
        self.assertTrue(modele.sudo().count)
        # Ni les preuves : qui a consenti, c'est qui suit sa santé.
        _consentir(self.env, self.a)
        preuves = self.env["ir.model"]._get("health.activation.consent")
        self.assertEqual(preuves.with_user(self.admin).count, 0)
        self.assertEqual(preuves.with_user(self.a).count, 0)
        self.assertTrue(preuves.sudo().count)
        self.assertTrue(self.env["ir.model"]._get("res.partner").with_user(self.admin).count,
                        "les autres modèles gardent leur compte")
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        self.env.invalidate_all()
        self.assertTrue(self.env["ir.model"].with_user(self.admin).browse(modele.id).count)

    def test_ado_ouverte_recoit_meme_si_le_titulaire_est_ferme(self):
        """Comme pour le second parent, l'ado ouverte à qui le passage est
        proposé lit sa personne à charge et reçoit ses fiches, même si le titulaire est
        fermé : les données la concernent, elle a consenti, et le passage est son geste."""
        enfant = self._enfant_de_a()
        self.env["ir.config_parameter"].sudo().set_param(PARAM_EXIGE, "False")
        vital = self._en(self.a, "health.vital").create({"vital_type": "weight", "value": 40.0,
                                                         "dependent_id": enfant.id})
        _exiger(self.env)
        self.env.cr.execute("UPDATE health_dependent SET state = 'offert', ado_id = %s WHERE id = %s",
                            [self.b.id, enfant.id])
        self.env.invalidate_all()
        _consentir(self.env, self.b)
        vu = self._en(self.b, "health.dependent").browse(enfant.id)
        self.assertTrue(vu.record_count)
        vu.action_recevoir()
        self.assertEqual(vital.sudo().create_uid, self.b, "les fiches passent dans son compte")

    # -- Ce qui compte pour « à l'écran » ------------------------------------------------
    def test_a_l_ecran(self):
        """La route des boutons, sous la session de la personne, hors incarnation."""
        class Session(dict):
            uid = None

        def requete(chemin, uid, appel=("health.activation.notice", "action_accept"), **cles):
            session = Session(cles)
            session.uid = uid
            corps = {"params": {"model": appel[0], "method": appel[1]}}
            return SimpleNamespace(httprequest=SimpleNamespace(path=chemin), session=session,
                                   get_json_data=lambda: corps)

        env_a = self.env(user=self.a)
        bouton = "/web/dataset/call_button/health.activation.notice/action_accept"
        cas = [
            ("le bouton d'une autre méthode", requete("/web/dataset/call_button/health.vital/write", self.a.id), env_a, False),
            ("la bonne adresse, un autre appel", requete(
                "/web/dataset/call_button/health.activation.notice/action_accept", self.a.id,
                appel=("health.activation.notice", "write")), env_a, False),
            ("bouton, sa session", requete(bouton, self.a.id), env_a, True),
            ("bouton, la session d'une autre", requete(bouton, self.admin.id), env_a, False),
            ("bouton, sans session", requete(bouton, None), env_a, False),
            ("bouton, pendant une incarnation", requete(bouton, self.a.id, bf_impersonate={"x": 1}), env_a, False),
            ("call_kw", requete("/web/dataset/call_kw/health.activation.notice/action_accept", self.a.id), env_a, False),
            ("route quelconque", requete("/healthy-fox/humeur", self.a.id), env_a, False),
            ("superutilisateur", requete(bouton, self.a.id), env_a(su=True), False),
        ]
        for nom, req, env, attendu in cas:
            with self.subTest(nom), patch.object(activation_consent, "request", req):
                self.assertEqual(activation_consent._a_l_ecran(env), attendu)
        with patch.object(activation_consent, "request", None):
            self.assertFalse(activation_consent._a_l_ecran(env_a), "sans requête (XML-RPC, cron)")

    # -- Tâches planifiées -------------------------------------------------------------
    def test_taches_planifiees(self):
        _exiger(self.env)
        _consentir(self.env, self.b)
        Log = self.env["health.medication.log"].sudo()
        Log.search([("medication_id", "in", (self.med_a | self.med_b).ids)]).unlink()
        self.env["health.medication"]._cron_create_daily_med_logs()
        self.assertFalse(Log.search([("medication_id", "=", self.med_a.id)]), "A est fermée")
        self.assertTrue(Log.search([("medication_id", "=", self.med_b.id)]), "B a consenti")
        self.assertEqual(garder_ouverts(self.med_a | self.med_b), self.med_b)
        # Le rappel d'humeur ne part pas pour une personne fermée, et l'ancien s'en va.
        reglages = self.env["health.mood.settings"].with_user(self.a).sudo()._bf_mes_reglages()
        reglages.write({"reminder_enabled": True, "reminder_time": 0.0})
        type_rappel = self.env["health.mood.settings"]._bf_type_rappel()
        reglages.with_user(self.a).sudo().activity_schedule(
            activity_type_id=type_rappel.id, user_id=self.a.id, date_deadline=fields.Date.today())
        self.env["health.mood.settings"]._cron_mood_reminders()
        self.assertFalse(self.env["mail.activity"].sudo().search([
            ("res_model", "=", "health.mood.settings"), ("user_id", "=", self.a.id)]))


@tagged("post_install", "-at_install", "avis_activation")
class TestAvisActivationHttp(_Donnees, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._monter(cls.env, "avis_http")
        cls.cle_a = cls.env["res.users.apikeys"].with_user(cls.a).sudo()._generate(None, "gen-a", None)
        _exiger(cls.env)

    def rpc(self, user, cle, model, method, *args, **kw):
        return self.xmlrpc_object.execute_kw(self.env.cr.dbname, user.id, cle, model, method,
                                             list(args), kw)

    def jsonrpc(self, user, cle, model, method, *args, **kw):
        r = self.url_open("/jsonrpc", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {
                "service": "object", "method": "execute_kw",
                "args": [self.env.cr.dbname, user.id, cle, model, method, list(args), kw]}}),
            headers={"Content-Type": "application/json"})
        return r.json()

    def web(self, route, model, method, args, kwargs=None):
        return self.url_open(route % (model, method), data=json.dumps({
            "jsonrpc": "2.0", "method": "call",
            "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}}}),
            headers={"Content-Type": "application/json"}).json()

    def bouton(self, model, method, ids):
        return self.web("/web/dataset/call_button/%s/%s", model, method, [ids])

    def call_kw(self, model, method, args, kwargs=None):
        return self.web("/web/dataset/call_kw/%s/%s", model, method, args, kwargs)

    def preuves(self, user):
        self.env.invalidate_all()
        return self.env["health.activation.consent"].sudo().search([("user_id", "=", user.id)])

    # -- L'API ne lit rien et ne consent pas ----------------------------------------------
    def test_api_fermee(self):
        self.assertEqual(self.rpc(self.a, self.cle_a, "health.vital", "search_read", [], fields=["notes"]), [])
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.vital", "read", [self.vital_a.id], fields=["notes"])
        corps = self.jsonrpc(self.a, self.cle_a, "health.mood.entry", "search_read", [], fields=["note"])
        self.assertEqual(corps.get("result"), [])
        self.assertEqual(self.rpc(self.a, self.cle_a, "mail.activity", "search_read",
                                  [("res_model", "=", "health.medication")], fields=["summary"]), [])

    def test_consentement_par_api_refuse(self):
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.activation.notice", "create", {"lu": True})
        avis = self.rpc(self.a, self.cle_a, "health.activation.notice", "create", {})
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.activation.notice", "write", [avis], {"lu": True})
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.activation.notice", "action_accept", [avis])
        corps = self.jsonrpc(self.a, self.cle_a, "health.activation.notice", "action_accept", [avis])
        self.assertIn("error", corps)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.activation.consent", "create", {
                "user_id": self.a.id, "version": AVIS_ACTIVATION_VERSION,
                "consent_date": fields.Datetime.to_string(fields.Datetime.now())})
        self.assertFalse(self.preuves(self.a))

    # -- À l'écran -------------------------------------------------------------------------
    def test_call_kw_ne_suffit_pas(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        avis = self.call_kw("health.activation.notice", "create", [{"lu": True}])["result"]
        corps = self.call_kw("health.activation.notice", "action_accept", [[avis]])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))

    def test_texte_change_pendant_la_lecture(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        avis = self.call_kw("health.activation.notice", "create",
                            [{"lu": True, "notice_sha256": "0" * 64}])["result"]
        corps = self.bouton("health.activation.notice", "action_accept", [avis])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))

    def test_page_humeur_fermee_oublie_l_etat(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        r = self.url_open("/healthy-fox/humeur/api/etat", data=json.dumps(
            {"jsonrpc": "2.0", "method": "call", "params": {}}),
            headers={"Content-Type": "application/json"}).json()
        self.assertTrue(r["result"].get("ferme"), r)
        self.assertIn("action_health_entree", r["result"].get("entree", ""))

    def test_version_perimee_refusee(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        avis = self.call_kw("health.activation.notice", "create",
                            [{"lu": True, "version": "2000-01-01"}])["result"]
        corps = self.bouton("health.activation.notice", "action_accept", [avis])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))

    def test_case_non_cochee(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        avis = self.call_kw("health.activation.notice", "create", [{}])["result"]
        corps = self.bouton("health.activation.notice", "action_accept", [avis])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))

    def test_consentement_a_l_ecran(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        corps = self.call_kw("health.vital", "search_read", [[]], {"fields": ["notes"]})
        self.assertEqual(corps.get("result"), [])
        avis = self.call_kw("health.activation.notice", "create", [{"lu": True}])["result"]
        corps = self.bouton("health.activation.notice", "action_accept", [avis])
        self.assertEqual(corps.get("result", {}).get("tag"), "reload", corps)
        preuve = self.preuves(self.a)
        self.assertEqual(len(preuve), 1)
        self.assertEqual(preuve.version, AVIS_ACTIVATION_VERSION)
        self.assertEqual(len(preuve.notice_sha256 or ""), 64)
        self.assertIn("vieprivee@bluefoxconsultant.com", preuve.notice_html or "")
        self.assertEqual(preuve.notice_sha256, hashlib.sha256(str(preuve.notice_html).encode()).hexdigest())
        self.assertTrue(preuve.lang)
        corps = self.call_kw("health.vital", "search_read", [[]], {"fields": ["notes"]})
        self.assertEqual([r["notes"] for r in corps["result"]], ["SECRET-VITAL-A"])
        # Le retrait, par l'API même : il ne peut que refermer.
        self.rpc(self.a, self.cle_a, "health.activation.consent", "action_withdraw", [preuve.id])
        self.assertTrue(self.preuves(self.a).withdrawn_date)
        corps = self.call_kw("health.vital", "search_read", [[]], {"fields": ["notes"]})
        self.assertEqual(corps.get("result"), [])

    def test_administratrice_ne_consent_pas_pour_autrui(self):
        # L'avis de A, ouvert par A, ne s'accepte pas depuis la session de l'administratrice.
        self.authenticate(self.a.login, "avis_http_a_mdp")
        avis_a = self.call_kw("health.activation.notice", "create", [{"lu": True}])["result"]
        self.authenticate(self.admin.login, "avis_http_admin_mdp")
        corps = self.bouton("health.activation.notice", "action_accept", [avis_a])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))
        # Ni pour elle-même : sur un foyer, un compte d'administration système n'est
        # pas celui d'une personne.
        avis_admin = self.call_kw("health.activation.notice", "create", [{"lu": True}])["result"]
        corps = self.bouton("health.activation.notice", "action_accept", [avis_admin])
        self.assertIn("error", corps)
        self.assertRegex(corps["error"]["data"]["message"], "administration système|system administration")
        self.assertFalse(self.preuves(self.a))
        self.assertFalse(self.preuves(self.admin))

    def test_incarnation_refusee(self):
        charge = {"target_uid": self.a.id, "target_partner_id": self.a.partner_id.id,
                  "from_uid": self.admin.id, "mode": "write", "expires": time.time() + 3600,
                  "journal_id": False}
        if "bf.impersonate.session" in self.env:
            charge["journal_id"] = self.env["bf.impersonate.session"].sudo().create({
                "user_id": self.admin.id, "target_user_id": self.a.id, "reason": "Essai fictif",
                "mode": "write", "expires_at": fields.Datetime.now() + timedelta(hours=1)}).id
        self.authenticate(self.a.login, "avis_http_a_mdp", session_extra={"bf_impersonate": charge})
        # Pendant une incarnation, la case ne se coche pas, et le bouton refuse.
        self.assertIn("error", self.call_kw("health.activation.notice", "create", [{"lu": True}]))
        corps = self.call_kw("health.activation.notice", "create", [{}])
        if "result" in corps:
            # Sans bf_impersonate, l'avis se crée ; c'est le bouton qui refuse. Avec lui,
            # la santé est déjà refusée pendant une incarnation.
            corps = self.bouton("health.activation.notice", "action_accept", [corps["result"]])
        self.assertIn("error", corps)
        self.assertFalse(self.preuves(self.a))

    def test_page_humeur_mene_a_l_avis(self):
        self.authenticate(self.a.login, "avis_http_a_mdp")
        for route in ("/healthy-fox/humeur", "/healthy-fox/humeur/export.csv"):
            r = self.url_open(route, allow_redirects=False)
            self.assertIn(r.status_code, (302, 303), route)
            self.assertIn("action-bf_health.action_health_entree", r.headers.get("Location", ""))
