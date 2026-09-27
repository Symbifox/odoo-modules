"""La résiliation par le locataire : trois fondements, trois attestations.

🔴 Ce que ces essais tiennent avant tout : qu'on ne réclame pas la mauvaise
pièce, et qu'on ne publie pas le motif d'une personne en danger.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import NoticeCase


@tagged("post_install", "-at_install")
class TestResiliation(NoticeCase):

    def _resil(self, lease=None, ground="handicap", **kw):
        vals = {
            "kind": "tenant_resiliation",
            "resiliation_ground": ground,
            "date_given": "2026-09-01",
            "target_date": False,
            "attestation_received": True,
            "state": "given",
        }
        vals.update(kw)
        return self._notice(lease=lease, **vals)

    # ── L'attestation, et surtout LAQUELLE ──

    def test_each_ground_names_its_own_authority(self):
        """🔴 Elles ne sont pas interchangeables."""
        senior = self._resil(ground="senior_care")
        violence = self._resil(ground="violence")
        self.assertIn("personne autorisée", senior.attestation_authority)
        self.assertIn("MINISTRE DE LA JUSTICE", violence.attestation_authority)

    def test_the_violence_ground_never_asks_for_a_medical_certificate(self):
        """🔴 Le piège central. L'art. 1974.1 veut l'attestation d'un
        fonctionnaire désigné par le ministre de la Justice, sur le vu d'un
        jugement OU d'une déclaration sous serment. Réclamer un certificat
        médical demanderait une pièce que la loi n'exige pas, à la personne la
        moins en mesure de l'obtenir — en ayant l'air d'appliquer le droit."""
        violence = self._resil(ground="violence")
        authority = violence.attestation_authority.lower()
        self.assertNotIn("médical", authority)
        self.assertNotIn("certificat d'une personne autorisée", authority)
        self.assertIn("déclaration sous serment", authority)

    def test_a_given_notice_without_its_attestation_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self._resil(attestation_received=False)
        self.assertIn("1974", str(caught.exception))

    def test_a_draft_may_be_prepared_before_the_attestation_arrives(self):
        """⚠️ Refuser le brouillon obligerait à tenir le dossier ailleurs —
        hors du module, donc hors de toute garde."""
        notice = self._resil(attestation_received=False, state="draft")
        self.assertTrue(notice)

    def test_the_employment_ground_needs_no_attestation(self):
        """Art. 1976 : la fin du contrat de travail se constate entre les mêmes
        parties."""
        notice = self._resil(ground="employment", attestation_received=False)
        self.assertTrue(notice)

    # ── Le motif sensible ──

    def test_the_violence_ground_is_flagged_as_sensitive(self):
        self.assertTrue(self._resil(ground="violence").is_sensitive_ground)

    def test_the_other_grounds_are_not_flagged(self):
        """Le pendant : tout marquer sensible viderait le drapeau de son sens."""
        for ground in ("llm", "rehoused", "handicap", "senior_care", "employment"):
            self.assertFalse(
                self._resil(ground=ground).is_sensitive_ground, ground)

    def test_the_ground_is_not_tracked_in_the_chatter(self):
        """🔴 Un suivi publierait « Sécurité menacée » au fil de discussion et
        l'enverrait par courriel à tous les abonnés, dont la personne n'a aucune
        idée de la composition. Le champ porte donc tracking=False, et ce test
        garde cette décision contre un copier-coller distrait."""
        field = self.env["bf.rental.notice"]._fields["resiliation_ground"]
        self.assertFalse(field.tracking)

    # ── Les délais ──

    def test_a_twelve_month_lease_gives_two_months(self):
        notice = self._resil()
        self.assertEqual(str(notice.resiliation_effective_date), "2026-11-01")

    def test_a_short_lease_gives_one_month(self):
        lease = self._lease(date_start="2026-07-01", date_end="2026-12-31")
        notice = self._resil(lease=lease)
        self.assertEqual(str(notice.resiliation_effective_date), "2026-10-01")

    def test_an_indeterminate_lease_gives_one_month(self):
        lease = self._lease(duration_kind="indeterminate", date_end=False)
        notice = self._resil(lease=lease)
        self.assertEqual(str(notice.resiliation_effective_date), "2026-10-01")

    def test_the_employment_ground_is_always_one_month(self):
        """Art. 1976 n'a pas la règle des 12 mois : un mois, point."""
        notice = self._resil(ground="employment", attestation_received=False)
        self.assertEqual(str(notice.resiliation_effective_date), "2026-10-01")

    def test_the_delay_runs_from_sending_not_from_receipt(self):
        """⚠️ « deux mois après l'ENVOI » — l'inverse du délai de réponse de
        l'art. 1945, qui court de la réception. Deux délais du même corpus,
        deux points de départ."""
        notice = self._resil(date_received="2026-09-20")
        self.assertEqual(str(notice.resiliation_effective_date), "2026-11-01")

    # ── Relouer abrège ──

    def test_reletting_ends_the_lease_earlier(self):
        """🔴 Sans ce champ, le module ferait payer au locataire un loyer que la
        loi ne lui doit plus."""
        notice = self._resil(relet_date="2026-10-05")
        self.assertEqual(str(notice.resiliation_effective_date), "2026-10-05")

    def test_reletting_after_the_deadline_changes_nothing(self):
        """Le pendant : la relocation n'abrège que DANS le délai."""
        notice = self._resil(relet_date="2026-12-01")
        self.assertEqual(str(notice.resiliation_effective_date), "2026-11-01")

    def test_an_agreed_date_also_shortens(self):
        notice = self._resil(agreed_date="2026-09-30")
        self.assertEqual(str(notice.resiliation_effective_date), "2026-09-30")

    def test_reletting_before_the_notice_is_refused(self):
        with self.assertRaises(ValidationError):
            self._resil(relet_date="2026-08-01")

    # ── Cohérence ──

    def test_a_resiliation_must_say_on_what_it_rests(self):
        with self.assertRaises(ValidationError):
            self._notice(kind="tenant_resiliation", target_date=False,
                         resiliation_ground=False)

    def test_a_ground_on_another_kind_is_refused(self):
        with self.assertRaises(ValidationError):
            self._notice(kind="modification", resiliation_ground="handicap")

    def test_a_resiliation_expects_no_answer(self):
        """🔴 Le locateur ne peut pas la refuser : les art. 1974 et suivants
        ouvrent un DROIT, pas une demande. Lui donner un effet du silence
        suggérerait qu'il y a quelque chose à décider."""
        notice = self._resil()
        self.assertEqual(notice.silence_effect, "none")
        self.assertFalse(notice.response_deadline)

    # ── Ce que la sonde adversariale a fait trancher ──

    def _reader(self, login, manager=False):
        groups = [self.env.ref("base.group_user").id,
                  self.env.ref("bf_property_core.group_bf_property_user").id]
        if manager:
            groups.append(
                self.env.ref("bf_property_core.group_bf_property_manager").id)
        return self.env["res.users"].create({
            "name": login, "login": login, "groups_id": [(6, 0, groups)],
        })

    def test_consultation_cannot_read_that_someone_is_a_victim(self):
        """🔴 Le groupe Consultation est en lecture seule sur TOUTE la suite,
        donc large. La sonde a mesuré qu'il lisait « violence ».

        Un avis de résiliation doit être traité par quelqu'un — mais savoir
        qu'une personne est victime de violence n'est nécessaire à personne
        d'autre que celui qui traite la pièce.
        """
        notice = self._resil(ground="violence")
        reader = self._reader("qa_consult_resil")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            notice.with_user(reader).read(["resiliation_ground"])

    def test_management_still_reads_it(self):
        """Le pendant : restreindre à personne rendrait l'avis intraitable."""
        notice = self._resil(ground="violence")
        reader = self._reader("qa_mgr_resil", manager=True)
        got = notice.with_user(reader).read(["resiliation_ground"])
        self.assertEqual(got[0]["resiliation_ground"], "violence")

    def test_the_authority_carries_the_same_restriction(self):
        """⚠️ « un fonctionnaire désigné par le ministre de la Justice » ne
        désigne que l'art. 1974.1 : laisser ce champ ouvert rendrait la
        restriction de l'autre décorative."""
        notice = self._resil(ground="violence")
        reader = self._reader("qa_consult_auth")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            notice.with_user(reader).read(["attestation_authority"])

    def test_the_sensitivity_flag_carries_it_too(self):
        """⚠️ Un booléen vrai pour le seul cas de violence EST le
        renseignement, sous une autre forme."""
        notice = self._resil(ground="violence")
        reader = self._reader("qa_consult_flag")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            notice.with_user(reader).read(["is_sensitive_ground"])

    def test_consultation_still_sees_the_notice_itself(self):
        """La restriction porte sur le MOTIF, pas sur l'avis. Que le bail soit
        résilié et à quelle date reste lisible — c'est le pourquoi qui ne
        regarde pas tout le monde."""
        notice = self._resil(ground="violence")
        reader = self._reader("qa_consult_notice")
        got = notice.with_user(reader).read(
            ["kind", "date_given", "resiliation_effective_date"])
        self.assertEqual(got[0]["kind"], "tenant_resiliation")
        self.assertTrue(got[0]["resiliation_effective_date"])

    # ── La langue de l'autorité ──

    def test_the_authority_is_named_in_the_reader_language(self):
        """🔴 Les autorités venaient d'une constante de module sans `_()`.

        Un locataire anglophone lisait en français la pièce que la loi exige de
        lui. L'anglais est celui du texte officiel de l'art. 1974.1 : « a public
        servant or public officer designated by the Minister of Justice ».
        """
        for code in ("fr_CA", "en_CA"):
            self.env["res.lang"]._activate_lang(code)
        notice = self._resil(ground="violence")
        english = notice.with_context(lang="en_CA")
        english.invalidate_recordset(["attestation_authority"])
        self.assertIn("DESIGNATED BY THE MINISTER OF JUSTICE",
                      english.attestation_authority)
        french = notice.with_context(lang="fr_CA")
        french.invalidate_recordset(["attestation_authority"])
        self.assertIn("MINISTRE DE LA JUSTICE", french.attestation_authority)
