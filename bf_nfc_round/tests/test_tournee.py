"""La tournée : les passages dans l'ordre, l'heure du téléphone, une alerte par oubli."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestTournee(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.agent = new_test_user(cls.env, login="tournee-agent", groups="base.group_user")
        cls.chef = new_test_user(cls.env, login="tournee-chef", groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.chef.tz = "America/Toronto"
        cls.tournee = cls.env["bf.nfc.round"].create({
            "name": "Ronde du soir", "responsible_id": cls.chef.id, "strict_order": True,
            "duration_minutes": 60,
            "checkpoint_ids": [(0, 0, {"name": "Entrée", "sequence": 1}),
                               (0, 0, {"name": "Sous-sol", "sequence": 2}),
                               (0, 0, {"name": "Toit", "sequence": 3})],
        })
        cls.pastilles = {}
        for point in cls.tournee.checkpoint_ids:
            cls.pastilles[point.name] = cls.env["bf.nfc.tag"].create({
                "name": point.name, "gesture_id": cls.env.ref("bf_nfc_round.gesture_round_checkpoint").id,
                "res_model": "bf.nfc.round.checkpoint", "res_id": point.id,
            })

    def _taper(self, nom, **kw):
        return self.pastilles[nom].with_user(self.agent).taper("app", **kw)

    def _activites(self):
        return self.env["mail.activity"].search([("res_model", "=", "bf.nfc.round"), ("res_id", "=", self.tournee.id)])

    def test_trois_points_dans_l_ordre_ferment_la_tournee(self):
        r = self._taper("Entrée")
        self.assertIn("Point 1 sur 3", r["message"])
        self.assertIn("Sous-sol", r["message"])
        self._taper("Sous-sol")
        r = self._taper("Toit")
        self.assertIn("complète", r["message"])
        passage = self.tournee.run_ids
        self.assertEqual(len(passage), 1)
        self.assertEqual(passage.state, "done")

    def test_un_point_saute_est_marque_hors_ordre(self):
        self._taper("Entrée")
        r = self._taper("Toit")
        self.assertIn("Hors ordre", r["message"])
        self.assertTrue(self.tournee.run_ids.passage_ids.filtered("out_of_order"))

    def test_le_point_tape_sans_reseau_garde_son_heure(self):
        quand = fields.Datetime.now() - timedelta(minutes=40)
        r = self._taper("Entrée", quand=quand.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        passage = self.tournee.run_ids.passage_ids
        self.assertTrue(passage.offline)
        self.assertEqual(passage.tapped_at.replace(microsecond=0), quand.replace(microsecond=0))

    def test_tournee_pas_finie_une_seule_alerte(self):
        self._taper("Entrée")
        self.tournee.run_ids.date_start = fields.Datetime.now() - timedelta(hours=2)
        self.env["bf.nfc.round"]._cron_guetter()
        self.env["bf.nfc.round"]._cron_guetter()
        self.assertEqual(self.tournee.run_ids.state, "incomplete")
        self.assertEqual(len(self._activites()), 1)
        self.assertIn("Sous-sol", self._activites().note)
        self.assertEqual(self._activites().user_id, self.chef)

    def test_tournee_a_horaire_oubliee_une_alerte_par_jour(self):
        self.tournee.write({"schedule": "daily", "start_hour": 1.0, "duration_minutes": 30})
        maintenant = fields.Datetime.now()
        # La fenêtre d'aujourd'hui est passée : on la place une heure avant maintenant.
        fenetre = (maintenant - timedelta(hours=1), maintenant - timedelta(minutes=30))
        with patch.object(type(self.tournee), "_fenetre_du_jour", lambda self_, m: fenetre):
            self.env["bf.nfc.round"]._cron_guetter()
            self.env["bf.nfc.round"]._cron_guetter()
        self.assertEqual(len(self._activites()), 1)
        self.assertIn("non faite", self._activites().summary)

    def test_tournee_a_horaire_faite_pas_d_alerte(self):
        self.tournee.write({"schedule": "daily"})
        maintenant = fields.Datetime.now()
        fenetre = (maintenant - timedelta(hours=1), maintenant - timedelta(minutes=30))
        self._taper("Entrée", quand=(maintenant - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with patch.object(type(self.tournee), "_fenetre_du_jour", lambda self_, m: fenetre):
            self.env["bf.nfc.round"]._cron_guetter()
        self.assertFalse(self._activites().filtered(lambda a: "non faite" in (a.summary or "")))

    def test_la_fenetre_suit_le_fuseau_du_responsable(self):
        self.tournee.write({"schedule": "daily", "start_hour": 22.0, "duration_minutes": 60})
        # 2026-01-15 03:30 UTC = 22:30 à Toronto (UTC-5) : la fenêtre du 14 au soir… non,
        # à 03:30 UTC c'est encore le 14 à Toronto, et la ronde commence à 22:00 = 03:00 UTC.
        debut, fin = self.tournee._fenetre_du_jour(fields.Datetime.to_datetime("2026-01-15 03:30:00"))
        self.assertEqual(debut, fields.Datetime.to_datetime("2026-01-15 03:00:00"))
        self.assertEqual(fin, fields.Datetime.to_datetime("2026-01-15 04:00:00"))

    def test_la_pastille_signee_ne_fait_pas_la_ronde(self):
        r = self.pastilles["Entrée"].with_user(self.agent).taper("signed")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self.tournee.run_ids)


@tagged("post_install", "-at_install")
class TestTourneeEnLot(TransactionCase):
    """Les pastilles d'une tournée se créent depuis la tournée, une par point."""

    def test_un_bouton_une_pastille_par_point_et_rien_en_double(self):
        chef = new_test_user(self.env, login="lot-chef", groups="base.group_user,bf_nfc.group_nfc_manager")
        tournee = self.env["bf.nfc.round"].with_user(chef).create({
            "name": "Ronde incendie", "responsible_id": chef.id,
            "checkpoint_ids": [(0, 0, {"name": "Extincteur 1", "place": "Hall"}),
                               (0, 0, {"name": "Extincteur 2"})],
        })
        action = tournee.action_creer_pastilles()
        lot = self.env[action["res_model"]].with_user(chef).with_context(action["context"]).create({})
        self.assertEqual(lot.gesture_id, self.env.ref("bf_nfc_round.gesture_round_checkpoint"),
                         "Le geste du point de tournée doit être proposé d'office.")
        lot.action_creer()
        self.assertEqual(tournee.nfc_tag_count, 2)
        pastilles = self.env["bf.nfc.tag"].search(tournee.action_voir_pastilles()["domain"])
        self.assertEqual(pastilles.filtered(lambda t: t.res_id == tournee.checkpoint_ids[0].id).place, "Hall")
        tournee.with_user(chef).write({"checkpoint_ids": [(0, 0, {"name": "Extincteur 3"})]})
        lot = self.env["bf.nfc.tag.lot"].with_user(chef).with_context(
            tournee.action_creer_pastilles()["context"]).create({})
        self.assertEqual((lot.fiche_count, lot.deja_count), (3, 2))
        lot.action_creer()
        # ⚠️ Compte non stocké : l'essai garde en cache le 2 d'avant. Le formulaire, lui,
        # se recharge après l'assistant et recalcule.
        tournee.invalidate_recordset(["nfc_tag_count"])
        self.assertEqual(tournee.nfc_tag_count, 3)
