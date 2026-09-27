"""🔴 Le silence ne vaut pas la même chose selon l'avis.

C'est le cœur du module. Un tableau de bord qui traiterait « pas de réponse »
d'une seule façon se tromperait trois fois sur quatre, et il se tromperait dans
le sens le plus coûteux : en affichant « en attente » sur une situation déjà
tranchée.
"""
from odoo.tests.common import tagged

from .common import NoticeCase


@tagged("post_install", "-at_install")
class TestSilence(NoticeCase):

    def test_silence_accepts_a_modification(self):
        """Art. 1945 : « il est réputé avoir accepté la reconduction du bail aux
        conditions proposées par le locateur »."""
        notice = self._notice(kind="modification")
        self.assertEqual(notice.silence_effect, "accept")
        self.assertEqual(notice.silence_article, "art. 1945")

    def test_silence_refuses_a_repossession(self):
        """Art. 1962 : « il est réputé avoir refusé de quitter le logement »."""
        notice = self._notice(kind="repossession", date_given="2026-10-01",
                              target_date="2027-06-30")
        self.assertEqual(notice.silence_effect, "refuse")
        self.assertEqual(notice.silence_article, "art. 1962")

    def test_silence_refuses_an_end_of_sublet(self):
        """Art. 1944.1, ajouté par la Loi 31."""
        notice = self._notice(kind="sublet_end", target_date=False)
        self.assertEqual(notice.silence_effect, "refuse")
        self.assertEqual(notice.silence_article, "art. 1944.1")

    def test_silence_refuses_an_rpa_offer(self):
        """Art. 1959.2 : « s'il omet de le faire, il est réputé l'avoir
        refusée »."""
        notice = self._notice(kind="rpa_offer", target_date=False)
        self.assertEqual(notice.silence_effect, "refuse")
        self.assertEqual(notice.silence_article, "art. 1959.2")

    def test_only_one_kind_in_four_accepts(self):
        """🔴 Le contre-exemple qui tient la table entière.

        Sans lui, remplacer tous les effets par « accept » passerait au vert sur
        le seul test de la modification.
        """
        from odoo.addons.bf_rental_notice.models.bf_rental_notice import SILENCE
        effects = [e for e, _a in SILENCE.values() if e]
        self.assertEqual(effects.count("accept"), 1)
        self.assertEqual(effects.count("refuse"), 4)

    def test_an_information_notice_expects_no_answer(self):
        """L'avis au nouveau locataire informe ; il n'appelle pas de réponse, et
        son « silence » n'est donc pas un silence."""
        notice = self._notice(kind="new_tenant_rent", target_date=False)
        self.assertEqual(notice.silence_effect, "none")
        self.assertFalse(notice.response_deadline)

    def test_refusing_forces_departure_when_the_lease_is_restricted(self):
        """⚠️ Art. 1945 al. 2 : pour un logement visé à l'art. 1955, le locataire
        qui refuse doit QUITTER à la fin du bail. Refuser n'est pas rester."""
        lease = self._lease(fixation_restricted=True, restriction_kind="new",
                            ready_date="2025-03-01", max_rent_5y=1400.0)
        notice = self._notice(lease=lease, kind="modification")
        self.assertTrue(notice.tenant_must_leave_if_refusing)

    def test_refusing_does_not_force_departure_on_an_ordinary_lease(self):
        """Le pendant : sans restriction, refuser laisse le bail vivant et c'est
        au Tribunal de trancher. Sans ce test, poser le drapeau à True partout
        passerait au vert."""
        notice = self._notice(kind="modification")
        self.assertFalse(notice.tenant_must_leave_if_refusing)

    def test_the_response_deadline_runs_from_receipt_not_from_sending(self):
        """⚠️ C'est de la RÉCEPTION que court le mois."""
        notice = self._notice(date_received="2027-02-10")
        self.assertEqual(str(notice.response_deadline), "2027-03-10")

    def test_no_deadline_without_a_known_receipt(self):
        """Une échéance calculée sur une date supposée est pire qu'une échéance
        absente : elle se lit comme un fait."""
        notice = self._notice()
        self.assertFalse(notice.response_deadline)

    def test_the_landlord_has_one_month_to_seize_the_tribunal(self):
        """⚠️ Art. 1947 al. 2 : le seul délai du corpus qui joue contre le
        locateur. Passé, le bail se reconduit aux conditions antérieures et
        l'augmentation est perdue."""
        notice = self._notice(date_refused="2027-03-05")
        self.assertEqual(str(notice.landlord_tribunal_deadline), "2027-04-05")

    def test_that_deadline_exists_only_for_a_modification(self):
        """Un refus d'éviction n'ouvre pas le même délai : le locateur saisit le
        Tribunal au titre de l'art. 1963, qui a sa propre mécanique."""
        notice = self._notice(kind="repossession", date_given="2026-10-01",
                              target_date="2027-06-30", date_refused="2026-11-05")
        self.assertFalse(notice.landlord_tribunal_deadline)
