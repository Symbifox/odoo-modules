"""L'avis « agenda » et les puces « sans préparation » à la création.

Ce qui casserait une implémentation naïve : un champ sans effet sur la grille
qui pousse quand même, une personne retirée des participants qu'on oublie, un
vieil appareil qui reçoit un type qu'il ne connaît pas, une rafale de la synchro
qui pousse à chaque événement.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_calendar_mobile.models import avis_agenda


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestAvisAgenda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = [(6, 0, [cls.env.ref("base.group_user").id])]
        cls.premiere = cls.env["res.users"].create({
            "name": "Essai Avis", "login": "essai.avis.agenda", "email": "avis@example.org",
            "groups_id": groupes})
        cls.deuxieme = cls.env["res.users"].create({
            "name": "Essai Avis Deux", "login": "essai.avis.agenda2", "email": "avis2@example.org",
            "groups_id": groupes})
        debut = datetime(2026, 10, 12, 14, 0)
        cls.rencontre = cls.env["calendar.event"].with_user(cls.premiere).create({
            "name": "Point d'équipe", "start": debut, "stop": debut + timedelta(hours=1),
            "partner_ids": [(6, 0, [cls.premiere.partner_id.id, cls.deuxieme.partner_id.id])],
        })

    def setUp(self):
        super().setUp()
        self.env.cr.postcommit.data.pop(avis_agenda.CLE_POSTCOMMIT, None)

    def _retenus(self):
        return self.env.cr.postcommit.data.get(avis_agenda.CLE_POSTCOMMIT, set())

    def test_deplacer_une_rencontre_previent_les_participants(self):
        self.rencontre.write({"start": datetime(2026, 10, 12, 15, 0),
                              "stop": datetime(2026, 10, 12, 16, 0)})
        self.assertEqual(self._retenus(), {self.premiere.id, self.deuxieme.id})

    def test_un_champ_hors_de_la_grille_ne_pousse_pas(self):
        self.rencontre.write({"description": "<p>Ordre du jour à venir</p>"})
        self.assertFalse(self._retenus())

    def test_une_personne_retiree_est_prevenue(self):
        """Avant ET après : la deuxième personne doit voir la rencontre DISPARAÎTRE de son agenda."""
        self.rencontre.write({"partner_ids": [(6, 0, [self.premiere.partner_id.id])]})
        self.assertIn(self.deuxieme.id, self._retenus())

    def test_supprimer_previent_aussi(self):
        self.rencontre.unlink()
        self.assertEqual(self._retenus(), {self.premiere.id, self.deuxieme.id})

    def test_une_reponse_previent(self):
        participant = self.rencontre.attendee_ids.filtered(lambda a: a.partner_id == self.deuxieme.partner_id)
        participant.write({"state": "accepted"})
        self.assertIn(self.premiere.id, self._retenus())

    def test_une_rafale_pousse_tout_de_suite_puis_en_fin_de_fenetre(self):
        base = "essai-avis-%s" % id(self)
        self.assertEqual(avis_agenda._a_pousser(base, {7, 8}, maintenant=1000.0), ([7, 8], {}))
        # Un second changement 5 s plus tard : rien tout de suite, un avis prévu
        # à la fin de la fenêtre, pas jeté.
        maintenant, plus_tard = avis_agenda._a_pousser(base, {7}, maintenant=1005.0)
        self.assertEqual(maintenant, [])
        self.assertAlmostEqual(plus_tard[7], avis_agenda.INTERVALLE_S - 5)
        # Un troisième dans la même fenêtre n'en prévoit pas un deuxième.
        self.assertEqual(avis_agenda._a_pousser(base, {7}, maintenant=1010.0), ([], {}))
        # L'avis différé part et compte comme le dernier.
        avis_agenda._fin_de_fenetre(base, 7, maintenant=1000.0 + avis_agenda.INTERVALLE_S)
        self.assertEqual(avis_agenda._a_pousser(base, {7}, maintenant=1001.0 + avis_agenda.INTERVALLE_S), ([], {7: avis_agenda.INTERVALLE_S - 1.0}))

    def test_l_avis_est_annonce_chiffre(self):
        self.assertIn("agenda", self.env["bf.email.unifiedpush"]._webpush_types())

    def test_seuls_les_appareils_a_jour_le_recoivent(self):
        # L'interrupteur du locataire : une base d'essai peut l'avoir éteint.
        self.env["ir.config_parameter"].sudo().set_param("bf_email.push_enabled", "1")
        self.env["ir.config_parameter"].sudo().set_param(avis_agenda.PARAM_AVIS, "")
        Appareil = self.env["bf.email.mobile.device"]
        ancien = Appareil._issue(self.premiere.id, name="Vieux téléphone")
        neuf = Appareil._issue(self.premiere.id, name="Téléphone à jour")
        for appareil, version in ((ancien, "3.21.0"), (neuf, "3.22.0")):
            appareil.sudo().write({"push_endpoint": "https://ntfy.example.org/up%s" % appareil.id,
                                   "app_version": version})
        envoyes = []
        Push = type(self.env["bf.email.unifiedpush"])

        def _faux_envoi(this, appareils, charge):
            envoyes.append((appareils, charge))
            return len(appareils)

        with patch.object(Push, "_envoyer_a", _faux_envoi):
            recus = self.env["bf.email.unifiedpush"]._bf_avis_agenda(self.premiere)
        self.assertEqual(recus, 1)
        self.assertEqual(envoyes[0][0], neuf)
        # 🔴 L'avis ne porte rien d'autre que son type : il réveille, il ne dit rien.
        self.assertEqual(envoyes[0][1], {"type": "agenda"})


    def _appareil_a_jour(self):
        appareil = self.env["bf.email.mobile.device"]._issue(self.premiere.id, name="À jour")
        appareil.sudo().write({"push_endpoint": "https://ntfy.example.org/up%s" % appareil.id,
                               "app_version": "3.22.0"})
        return appareil

    def _envoyes_avec(self, push_enabled, avis):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_email.push_enabled", push_enabled)
        ICP.set_param(avis_agenda.PARAM_AVIS, avis)
        envoyes = []
        Push = type(self.env["bf.email.unifiedpush"])
        with patch.object(Push, "_envoyer_a", lambda this, a, c: envoyes.append(a) or len(a)):
            self.env["bf.email.unifiedpush"]._bf_avis_agenda(self.premiere)
        return envoyes

    def test_l_avis_a_son_interrupteur_quand_les_courriels_sont_eteints(self):
        """Une instance où la poussée des courriels est éteinte, l'avis allumé."""
        self._appareil_a_jour()
        self.assertTrue(self._envoyes_avec("0", "1"))

    def test_l_avis_eteint_ne_part_pas_meme_si_les_courriels_poussent(self):
        self._appareil_a_jour()
        self.assertFalse(self._envoyes_avec("1", "0"))

    def test_sans_reglage_l_avis_suit_les_courriels(self):
        self._appareil_a_jour()
        self.assertFalse(self._envoyes_avec("0", ""))
        self.assertTrue(self._envoyes_avec("1", ""))


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestPucesALaCreation(TransactionCase):

    def test_creer_sans_odj_formel(self):
        Event = self.env["calendar.event"]
        if "bf_skip_agenda" not in Event._fields:
            self.skipTest("bf_meeting absent")
        debut = datetime(2026, 10, 13, 9, 0)
        rendu = Event.mobile_create({
            "name": "Café", "start": debut.strftime("%Y-%m-%d %H:%M:%S"),
            "stop": (debut + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "skip_agenda": True,
        })
        event = Event.browse(rendu["event"]["id"])
        self.assertTrue(event.bf_skip_agenda)
        self.assertFalse(event.bf_skip_dashboard)
        self.assertTrue(rendu["event"]["skip_agenda"])

    def test_sans_puce_rien_ne_change(self):
        Event = self.env["calendar.event"]
        if "bf_skip_agenda" not in Event._fields:
            self.skipTest("bf_meeting absent")
        debut = datetime(2026, 10, 13, 11, 0)
        rendu = Event.mobile_create({
            "name": "Revue", "start": debut.strftime("%Y-%m-%d %H:%M:%S"),
            "stop": (debut + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.assertFalse(Event.browse(rendu["event"]["id"]).bf_skip_agenda)
