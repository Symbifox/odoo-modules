"""Les portes de la captation, et la preuve qu'elles sont fermées.

🔴 Une relecture adverse a trouvé que toutes les méthodes publiques de
``bf.capture`` s'appelaient par ``/web/dataset/call_kw``, route ouverte à tout
usager authentifié, portail compris. Un ``AbstractModel`` n'a pas de table :
aucun contrôle d'accès ne se déclenche au passage. ``deposer_rencontre`` posait
ainsi, au nom du compte de service, le fichier et le titre qu'un portail
choisissait dans le dossier surveillé du processeur de rencontres, en
contournant la garde « usager interne » des routes mobiles.

Chaque essai vérifie une moitié du correctif : la porte RPC est fermée, la garde
du modèle refuse un portail, et le chemin des routes marche toujours.
"""
import os
import time
from collections import namedtuple
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import new_test_user
from odoo.tests.common import TransactionCase, tagged

from ..models import televersement as televersement_module
from ..models.bf_capture import ServiceIndisponible

PUBLIQUES = ("is_configured", "transcription_disponible", "cibles",
             "deposer_rencontre", "deposer_memo", "televersement_ouvrir",
             "televersement_morceau", "televersement_etat",
             "televersement_terminer", "televersement_abandonner")

Disque = namedtuple("Disque", "total used free")


@tagged("post_install", "-at_install", "bf_capture")
class TestGardesRpc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interne = new_test_user(cls.env, login="capture-gardes-interne",
                                    groups="base.group_user")
        cls.portail = new_test_user(cls.env, login="capture-gardes-portail",
                                    groups="base.group_portal")
        cls.Capture = cls.env["bf.capture"]

    def setUp(self):
        super().setUp()
        # La préparation touche Nextcloud et le calendrier : hors sujet ici.
        rustine = patch.object(type(self.Capture), "_preparer_rencontre",
                               lambda self, *a, **k: {})
        rustine.start()
        self.addCleanup(rustine.stop)
        self.ouverts = []
        self.addCleanup(self._nettoyer)

    def _nettoyer(self):
        for upload_id in self.ouverts:
            for chemin in self.Capture._chemins(upload_id):
                if os.path.exists(chemin):
                    os.remove(chemin)

    def _ouvrir(self, usager):
        reponse = self.Capture.with_user(usager).televersement_ouvrir(
            "rencontre.m4a", 1000, titre="Essai")
        self.ouverts.append(reponse["upload_id"])
        return reponse["upload_id"]

    # ── La porte RPC ──────────────────────────────────────────────────

    def test_aucune_methode_ne_s_appelle_par_rpc(self):
        for methode in PUBLIQUES:
            with self.subTest(methode=methode), self.assertRaises(AccessError):
                get_public_method(self.Capture, methode)

    # ── La garde du modèle ────────────────────────────────────────────

    def test_un_portail_ne_depose_pas_de_rencontre(self):
        with self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).deposer_rencontre(b"son", titre="Essai")

    def test_un_portail_ne_fait_pas_tourner_la_dictee(self):
        Modele = type(self.Capture)
        with patch.object(Modele, "transcription_disponible", lambda self: True), \
                patch.object(Modele, "_transcrire") as dictee, \
                self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).deposer_memo(b"son", nom_source="memo.m4a")
        dictee.assert_not_called()

    def test_un_portail_n_ouvre_pas_de_televersement(self):
        with self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).televersement_ouvrir(
                "rencontre.m4a", 1000, titre="Essai")

    def test_un_interne_ouvre_toujours(self):
        upload_id = self._ouvrir(self.interne)
        etat = self.Capture.with_user(self.interne).televersement_etat(upload_id)
        self.assertEqual(etat["recus"], 0)

    # ── Les bornes du disque ──────────────────────────────────────────

    def test_le_plus_ancien_cede_la_place(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.televersement_max_ouverts", "3")
        premiers = [self._ouvrir(self.interne) for _i in range(3)]
        # L'ordre se lit à la date des fiches : on vieillit la première.
        _part, meta = self.Capture._chemins(premiers[0])
        os.utime(meta, (time.time() - 3600, time.time() - 3600))
        self._ouvrir(self.interne)
        ouverts = self.Capture._ouverts_de(self.interne.id)
        self.assertEqual(len(ouverts), 3)
        self.assertNotIn(premiers[0], ouverts)

    def test_un_disque_plein_est_une_panne_pas_un_refus(self):
        with patch.object(televersement_module.shutil, "disk_usage",
                          lambda chemin: Disque(10, 10, 1000)), \
                self.assertRaises(ServiceIndisponible):
            self._ouvrir(self.interne)

    def test_le_menage_quotidien_purge_l_abandonne(self):
        upload_id = self._ouvrir(self.interne)
        vieux = time.time() - 49 * 3600
        for chemin in self.Capture._chemins(upload_id):
            os.utime(chemin, (vieux, vieux))
        self.Capture._gc_televersements_abandonnes()
        for chemin in self.Capture._chemins(upload_id):
            self.assertFalse(os.path.exists(chemin))

    def test_un_morceau_rajeunit_la_fiche(self):
        upload_id = self._ouvrir(self.interne)
        _part, meta = self.Capture._chemins(upload_id)
        vieux = time.time() - 47 * 3600
        os.utime(meta, (vieux, vieux))
        self.Capture.with_user(self.interne).televersement_morceau(upload_id, 0, b"x" * 10)
        self.assertGreater(os.path.getmtime(meta), vieux + 3600)

    def test_un_identifiant_non_textuel_est_un_refus(self):
        for brut in (123, None, "a" * 32 + "\n", "../" + "a" * 29):
            with self.subTest(brut=brut), self.assertRaises(UserError):
                self.Capture.with_user(self.interne).televersement_etat(brut)
