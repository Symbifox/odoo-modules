"""Les délais d'avis : art. 1942 (modification) et art. 1960 (reprise/éviction).

⚠️ Les deux articles ne coupent pas au même endroit — 12 mois pour l'un, 6 pour
l'autre — et seul l'art. 1942 pose un MAXIMUM. Chaque borne a son test dans les
deux sens : un test qui n'éprouve que le trop-tard laisse passer un maximum
cassé.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import NoticeCase


@tagged("post_install", "-at_install")
class TestDelays(NoticeCase):

    # ── Art. 1942 : modification ──

    def test_a_long_lease_takes_three_to_six_months(self):
        """Bail de 12 mois : avis entre 3 et 6 mois avant le terme."""
        notice = self._notice(date_given="2027-02-01", target_date="2027-06-30")
        self.assertTrue(notice)

    def test_a_modification_given_too_late_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self._notice(date_given="2027-05-01", target_date="2027-06-30")
        self.assertIn("trop tard", str(caught.exception))

    def test_a_modification_given_too_early_is_refused(self):
        """🔴 Le maximum compte autant que le minimum : « au moins trois mois,
        mais PAS PLUS DE SIX ». Un avis d'un an d'avance est nul."""
        with self.assertRaises(ValidationError) as caught:
            self._notice(date_given="2026-07-15", target_date="2027-06-30")
        self.assertIn("trop tôt", str(caught.exception))

    def test_a_short_lease_takes_one_to_two_months(self):
        lease = self._lease(date_start="2026-07-01", date_end="2026-12-31")
        notice = self._notice(lease=lease, date_given="2026-11-20",
                              target_date="2026-12-31")
        self.assertTrue(notice)

    def test_a_short_lease_refuses_a_three_month_notice(self):
        """Le délai du bail long donné sur un bail court est trop tôt."""
        lease = self._lease(date_start="2026-07-01", date_end="2026-12-31")
        with self.assertRaises(ValidationError):
            self._notice(lease=lease, date_given="2026-09-15",
                         target_date="2026-12-31")

    def test_a_room_counts_in_days_not_months(self):
        """🔴 Art. 1942 al. 3 : 10 et 20 JOURS pour une chambre. Calculer en
        mois donnerait un avis largement hors délai, et le locataire aurait
        raison de le contester."""
        lease = self._lease(is_room=True)
        notice = self._notice(lease=lease, date_given="2027-06-15",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_a_room_refuses_a_three_month_notice(self):
        """Le pendant : sur une chambre, le délai ordinaire est BEAUCOUP trop
        tôt. Sans ce test, oublier la branche « chambre » passerait au vert."""
        lease = self._lease(is_room=True)
        with self.assertRaises(ValidationError) as caught:
            self._notice(lease=lease, date_given="2027-02-01",
                         target_date="2027-06-30")
        self.assertIn("trop tôt", str(caught.exception))

    # ── Art. 1960 : reprise et éviction ──

    def test_a_repossession_needs_six_months(self):
        notice = self._notice(kind="repossession", date_given="2026-10-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_a_repossession_given_too_late_is_refused(self):
        with self.assertRaises(ValidationError):
            self._notice(kind="repossession", date_given="2027-04-01",
                         target_date="2027-06-30")

    def test_a_repossession_has_no_maximum(self):
        """⚠️ Contrairement à l'art. 1942, l'art. 1960 ne pose qu'un minimum :
        un avis de reprise donné un an d'avance ne se vicie pas. Sans ce test,
        appliquer un maximum aux deux articles passerait inaperçu."""
        notice = self._notice(kind="repossession", date_given="2026-01-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)

    def test_a_short_lease_repossession_needs_only_one_month(self):
        """Bail à durée fixe de six mois ou moins : un mois suffit."""
        lease = self._lease(date_start="2026-07-01", date_end="2026-12-31")
        notice = self._notice(lease=lease, kind="repossession",
                              date_given="2026-11-15", target_date="2026-12-31")
        self.assertTrue(notice)

    def test_an_indeterminate_lease_repossession_needs_six_months(self):
        """⚠️ Et le délai vise la DATE PROPOSÉE, pas un terme — un bail à durée
        indéterminée n'en a pas."""
        lease = self._lease(duration_kind="indeterminate", date_end=False)
        notice = self._notice(lease=lease, kind="repossession",
                              date_given="2026-10-01", target_date="2027-06-30")
        self.assertTrue(notice)

    # ── Ce qui n'a pas de délai ──

    def test_a_notice_to_a_new_tenant_has_no_prescribed_delay(self):
        """Il se remet à la conclusion du bail : il n'y a rien à compter."""
        notice = self._notice(kind="new_tenant_rent", date_given="2026-07-01",
                              target_date="2026-07-01")
        self.assertTrue(notice)

    def test_a_lease_of_exactly_six_months_takes_the_short_notice(self):
        """🔴 Art. 1960 : « si la durée du bail est de six mois OU MOINS, l'avis
        est d'un mois ». Un bail de six mois pile prend donc le délai court —
        le seuil est strict d'un côté et inclusif de l'autre, et s'en remettre
        à « >= 6 mois » donnerait six mois d'avis là où un seul suffit."""
        lease = self._lease(date_start="2026-07-01", date_end="2026-12-31")
        notice = self._notice(lease=lease, kind="repossession",
                              date_given="2026-11-15", target_date="2026-12-31")
        self.assertTrue(notice)

    def test_a_lease_of_exactly_twelve_months_takes_the_long_notice(self):
        """🔴 Le bail le plus courant au Québec : du 1er juillet au 30 juin.
        Il fait DOUZE mois, et non 11,96 — la date de fin est incluse. Compté
        en jours divisés par 30,44 il basculait dans le mauvais régime, et
        l'avis se donnait deux mois trop tard."""
        lease = self._lease(date_start="2026-07-01", date_end="2027-06-30")
        with self.assertRaises(ValidationError):
            # 2 mois d'avance : trop tôt pour un bail de 12 mois (3 à 6 mois)
            self._notice(lease=lease, date_given="2027-04-30",
                         target_date="2027-06-30")
        notice = self._notice(lease=lease, date_given="2027-03-01",
                              target_date="2027-06-30")
        self.assertTrue(notice)
