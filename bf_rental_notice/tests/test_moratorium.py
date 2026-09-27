"""🔴 Le moratoire : un état constaté, et ce qu'il refuse.

Le piège que ces essais tiennent n'est pas « le moratoire existe » — c'est qu'il
ne finit PAS forcément le 6 juin 2027, et qu'un module qui porterait cette date
en constante refuserait des évictions redevenues licites sans que personne voie
pourquoi.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import NoticeCase


@tagged("post_install", "-at_install")
class TestMoratorium(NoticeCase):

    def _moratorium(self):
        return self.env.ref("bf_rental_notice.moratorium_d1301")

    def test_an_eviction_during_the_moratorium_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self._notice(kind="eviction", date_given="2026-10-01",
                         target_date="2027-06-30")
        self.assertIn("évincé", str(caught.exception))

    def test_the_refusal_says_when_it_was_last_checked(self):
        """⚠️ Le message porte la date de CONSTAT, pas seulement la date de fin.
        Un état vieux de six mois doit se dire vieux de six mois, sans quoi
        l'utilisateur le prend pour la vérité du jour."""
        with self.assertRaises(ValidationError) as caught:
            self._notice(kind="eviction", date_given="2026-10-01",
                         target_date="2027-06-30")
        self.assertIn("constaté", str(caught.exception))

    def test_an_eviction_after_the_statutory_end_passes(self):
        notice = self._notice(kind="eviction", date_given="2027-07-01",
                              target_date="2028-06-30")
        self.assertTrue(notice)

    def test_a_gazette_notice_ends_the_moratorium_sixty_days_later(self):
        """🔴 Le cas que la date en dur manquerait. Un avis publié le 1er mars
        2026 fait cesser le moratoire le 60e jour, soit bien avant 2027."""
        mor = self._moratorium()
        mor.gazette_notice_date = "2026-03-01"
        self.assertEqual(str(mor.effective_end), "2026-04-30")
        notice = self._notice(kind="eviction", date_given="2026-05-15",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_sixty_days_are_counted_in_days_not_two_months(self):
        """⚠️ Le texte compte en JOURS. Deux mois civils tombent ailleurs selon
        le mois de départ, et la différence décide de la licéité d'un avis."""
        mor = self._moratorium()
        mor.gazette_notice_date = "2026-01-01"
        self.assertEqual(str(mor.effective_end), "2026-03-02")

    def test_the_effective_end_never_moves_past_the_statutory_one(self):
        """Un avis tardif ne prolonge pas le moratoire : l'art. 1 reste un
        plafond."""
        mor = self._moratorium()
        mor.gazette_notice_date = "2027-06-01"
        self.assertEqual(str(mor.effective_end), "2027-06-06")

    def test_an_eviction_passes_when_the_moratorium_is_lifted(self):
        mor = self._moratorium()
        mor.in_force = False
        notice = self._notice(kind="eviction", date_given="2026-10-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_nothing_is_blocked_when_no_state_is_recorded(self):
        """⚠️ Refuser faute de savoir imposerait l'ignorance du module comme si
        c'était la loi. C'est au bandeau de l'écran de dire qu'on ne sait pas."""
        self.env["bf.rental.moratorium"].search([]).write({"active": False})
        notice = self._notice(kind="eviction", date_given="2026-10-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_a_repossession_is_never_blocked_by_the_moratorium(self):
        """🔴 Le moratoire vise l'ÉVICTION de l'art. 1959, pas la reprise de
        l'art. 1957. Les confondre priverait un propriétaire d'un droit que la
        loi ne lui retire pas."""
        notice = self._notice(kind="repossession", date_given="2026-10-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_the_staleness_of_the_state_is_measurable(self):
        mor = self._moratorium()
        self.assertIsNotNone(mor._bf_staleness_days())
