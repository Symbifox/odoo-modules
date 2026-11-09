"""La purge du journal : ce qu'elle emporte, ce qu'elle garde, ce qui l'arrête."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged

CAL_DETAIL = "bf_impersonate_privacy.retention_impersonation_detail"
CAL_SESSION = "bf_impersonate_privacy.retention_impersonation_session"


@tagged("post_install", "-at_install")
class TestImpersonatePrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.origin = new_test_user(cls.env, login="bfimp_vp_origine", groups="base.group_user")
        cls.target = new_test_user(cls.env, login="bfimp_vp_cible", groups="base.group_user")
        cls.Session = cls.env["bf.impersonate.session"]
        cls.Register = cls.env["privacy.destruction.register"]

    def _session(self, ended_days_ago=None, lines=2):
        start = fields.Datetime.now() - timedelta(days=(ended_days_ago or 0) + 1)
        journal = self.Session.sudo().create({
            "user_id": self.origin.id,
            "target_user_id": self.target.id,
            "reason": "Essai de la purge",
            "mode": "write",
            "date_start": start,
            "expires_at": start + timedelta(minutes=30),
        })
        if ended_days_ago is not None:
            journal.write({
                "date_end": fields.Datetime.now() - timedelta(days=ended_days_ago),
                "end_reason": "manual",
            })
        for index in range(lines):
            self.env["bf.impersonate.session.line"].sudo().create({
                "session_id": journal.id, "kind": "write", "model": "res.partner",
                "method": "write", "record_ids": str(index + 1),
            })
        return journal

    def test_detail_goes_at_one_year_and_the_session_stays(self):
        vieille = self._session(ended_days_ago=400)
        recente = self._session(ended_days_ago=10)
        detruites = self.Session._bf_privacy_purge_detail()
        self.assertEqual(detruites, 2)
        self.assertTrue(vieille.exists(), "l'en-tête survit au détail : il prouve la séance")
        self.assertFalse(vieille.line_ids)
        self.assertEqual(len(recente.line_ids), 2, "une séance d'il y a dix jours reste entière")

    def test_the_session_goes_at_three_years(self):
        vieille = self._session(ended_days_ago=1200)
        jeune = self._session(ended_days_ago=400)
        detruites = self.Session._bf_privacy_purge_sessions()
        self.assertEqual(detruites, 1)
        self.assertFalse(vieille.exists())
        self.assertTrue(jeune.exists())

    def test_an_open_session_is_never_purged(self):
        """Même échue : c'est le cron de bf_impersonate qui la ferme, et une
        séance sans fin n'a pas d'ancrage de conservation."""
        ouverte = self.Session.sudo().create({
            "user_id": self.origin.id,
            "target_user_id": self.target.id,
            "reason": "Séance ouverte depuis des lustres",
            "mode": "read",
            "date_start": fields.Datetime.now() - timedelta(days=1500),
            "expires_at": fields.Datetime.now() - timedelta(days=1499),
        })
        self.env["bf.impersonate.session.line"].sudo().create({
            "session_id": ouverte.id, "kind": "read", "model": "res.partner",
            "read_count": 3,
        })
        self.Session._cron_privacy_purge_journal()
        self.assertTrue(ouverte.exists())
        self.assertTrue(ouverte.line_ids)

    def test_an_archived_rule_stops_the_purge(self):
        journal = self._session(ended_days_ago=1200)
        self.env.ref(CAL_DETAIL).active = False
        self.env.ref(CAL_SESSION).active = False
        self.assertEqual(self.Session._cron_privacy_purge_journal(), (0, 0))
        self.assertTrue(journal.exists())
        self.assertTrue(journal.line_ids)

    def test_a_rule_that_asks_for_an_approval_stops_the_purge(self):
        """Détruire sans rien dire serait pire que ne pas détruire."""
        journal = self._session(ended_days_ago=1200)
        self.env.ref(CAL_DETAIL).requires_approval = True
        self.env.ref(CAL_SESSION).requires_approval = True
        self.assertEqual(self.Session._cron_privacy_purge_journal(), (0, 0))
        self.assertTrue(journal.exists())

    def test_every_batch_is_written_to_the_register(self):
        self._session(ended_days_ago=1200)
        avant = self.Register.sudo().search_count([])
        lignes, seances = self.Session._cron_privacy_purge_journal()
        self.assertEqual((lignes, seances), (2, 1))
        entrees = self.Register.sudo().search([], order="id desc", limit=2)
        self.assertEqual(self.Register.sudo().search_count([]), avant + 2,
                         "une entrée par règle exécutée, pas une par fiche")
        self.assertEqual(entrees.mapped("destruction_scope"), ["full", "full"],
                         "une ligne et une séance disparaissent EN ENTIER, chacune "
                         "sur son modèle : « partie de l'enregistrement » dirait "
                         "qu'il en subsiste un morceau")
        self.assertEqual(sorted(entrees.mapped("res_model")),
                         ["bf.impersonate.session", "bf.impersonate.session.line"])
        for entree in entrees:
            self.assertTrue(entree.legal_basis)
            self.assertTrue(entree.pi_categories)
            self.assertEqual(entree.destruction_method, "delete")
            self.assertEqual(entree.subject_count, 1)
            self.assertTrue(entree.retention_calendar_id)

    def test_the_two_calendars_say_what_they_cover(self):
        detail = self.env.ref(CAL_DETAIL)
        session = self.env.ref(CAL_SESSION)
        self.assertEqual(detail.applies_to_model, "bf.impersonate.session.line")
        self.assertEqual(session.applies_to_model, "bf.impersonate.session")
        self.assertEqual(detail.total_retention_days, 365)
        self.assertEqual(session.total_retention_days, 1095)
        self.assertEqual(
            self.env.ref("bf_impersonate_privacy.purpose_impersonation").requires_consent,
            False, "un accès de soutien encadré n'est pas une faveur qu'on accorde")
