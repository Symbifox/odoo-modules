# Part of bf_recruitment_source. Voir LICENSE.
"""Ce qu'on prouve ici.

1. Une source créée reçoit son lien tracé toute seule, et sa création ne va
   JAMAIS chercher la page sur le réseau.
2. Le lien tracé compte les clics, et ces clics arrivent sur la bonne source.
3. Une candidature refusée reste une candidature reçue : l'archivage que fait
   l'assistant de refus ne fait pas fondre le taux de conversion.
4. Les décomptes sont bornés au poste ET à la source : une `utm.source`
   partagée entre deux postes ne fait pas déborder les chiffres.
5. Le chiffre dit sur quoi il porte : pas de clic, pas de lien, poste non
   publié, pas de domaine d'alias, candidatures sans source.
6. 🔴 Le clic d'un chercheur d'emploi ne laisse PAS son adresse IP, et la paire
   le prouve : un clic hors recrutement garde la sienne.
"""

from unittest.mock import patch

from odoo.addons.link_tracker.models.link_tracker import LinkTracker
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRecruitmentSource(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.job = cls.env["hr.job"].create({
            "name": "Technicienne réseau",
            "company_id": cls.company.id,
            "is_published": True,
        })
        cls.other_job = cls.env["hr.job"].create({
            "name": "Analyste de données",
            "company_id": cls.company.id,
            "is_published": True,
        })
        cls.seek = cls.env["utm.source"].create({"name": "SEEK"})
        cls.indeed = cls.env["utm.source"].create({"name": "Indeed"})

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def _make_source(self, utm_source=None, job=None):
        return self.env["hr.recruitment.source"].create({
            "source_id": (utm_source or self.seek).id,
            "job_id": (job or self.job).id,
        })

    def _make_applicant(self, source=None, job=None, name="Camille Postule"):
        candidate = self.env["hr.candidate"].create({
            "partner_name": name,
            "email_from": "camille@example.invalid",
            "company_id": self.company.id,
        })
        values = {
            "candidate_id": candidate.id,
            "job_id": (job or self.job).id,
            "company_id": self.company.id,
        }
        if source is not None:
            values["source_id"] = source.id
        return self.env["hr.applicant"].create(values)

    def _click(self, source, times=1, ip="203.0.113.7"):
        """Le chemin du contrôleur `/r/<code>`, sans passer par le web."""
        code = source.link_tracker_id.sudo().code
        clicks = self.env["link.tracker.click"]
        for _index in range(times):
            clicks |= self.env["link.tracker.click"].sudo().add_click(
                code, ip=ip, country_code="CA",
            )
        source.invalidate_recordset()
        source.link_tracker_id.sudo().invalidate_recordset(["count"])
        return clicks

    # ------------------------------------------------------------------
    # 1. Le lien tracé naît avec la source
    # ------------------------------------------------------------------

    def test_creating_a_source_creates_its_tracked_link(self):
        source = self._make_source()
        self.assertTrue(
            source.link_tracker_id,
            "Une source sans compteur ne mesure rien : c'est tout l'objet du "
            "module.",
        )
        self.assertIn(
            "/r/", source.tracked_url,
            "Le lien à publier doit être l'adresse courte qui compte, pas "
            "l'adresse nue du coeur.",
        )

    def test_the_tracked_link_never_fetches_the_page_title(self):
        """🔴 Le chemin, pas le résultat.

        `link.tracker.create()` va CHERCHER la page sur le réseau pour en lire
        le titre quand on ne lui en donne pas. Créer une source déclencherait
        alors un appel sortant dans la transaction de l'utilisateur. Ce test
        tombe si quelqu'un retire le `title` des valeurs.
        """
        with patch.object(
            LinkTracker, "_get_title_from_url", autospec=True,
        ) as fetched:
            source = self._make_source()
        self.assertFalse(
            fetched.called,
            "La création d'une source est allée chercher la page sur le "
            "réseau. Le titre doit être posé à la main.",
        )
        self.assertEqual(source.link_tracker_id.sudo().title, self.job.name)

    def test_the_tracked_link_points_at_the_job_page(self):
        source = self._make_source()
        self.assertTrue(
            source.link_tracker_id.sudo().url.endswith(self.job.website_url),
            "Le lien tracé doit rediriger vers la page du poste.",
        )

    def test_the_tracked_link_carries_the_utm_triple(self):
        source = self._make_source()
        tracker = source.link_tracker_id.sudo()
        self.assertEqual(tracker.source_id, self.seek)
        self.assertEqual(
            tracker.campaign_id, self.env.ref("hr_recruitment.utm_campaign_job"),
        )
        self.assertTrue(tracker.medium_id)

    def test_two_sources_that_are_the_same_measure_share_one_tracker(self):
        """Même poste, même site, même support : une seule mesure.

        Le coeur interdit deux `link.tracker` identiques. Plutôt que de laisser
        la contrainte lever à la création d'une source, on rend le compteur
        existant.
        """
        first = self._make_source()
        second = self._make_source()
        self.assertEqual(
            first.link_tracker_id, second.link_tracker_id,
            "Deux sources qui visent la même chose doivent partager leur "
            "compteur, pas faire échouer la création.",
        )

    def test_action_create_tracked_link_catches_up_an_old_source(self):
        """Une source créée avant le module reste rattrapable."""
        source = self._make_source()
        source.sudo().link_tracker_id = False
        self.assertFalse(source.tracked_url)
        source.action_create_tracked_link()
        self.assertTrue(source.link_tracker_id)

    # ------------------------------------------------------------------
    # 2. Les clics
    # ------------------------------------------------------------------

    def test_clicks_land_on_their_source(self):
        source = self._make_source()
        self._click(source, times=3)
        self.assertEqual(source.click_count, 3)

    def test_clicks_do_not_bleed_between_sources(self):
        seek = self._make_source(self.seek)
        indeed = self._make_source(self.indeed)
        self._click(seek, times=2)
        self.assertEqual(seek.click_count, 2)
        self.assertEqual(indeed.click_count, 0)

    # ------------------------------------------------------------------
    # 3. 🔴 L'adresse IP du chercheur d'emploi
    # ------------------------------------------------------------------

    def test_a_recruitment_click_never_stores_an_ip(self):
        source = self._make_source()
        click = self._click(source)
        self.assertFalse(
            click.sudo().ip,
            "Le clic d'un chercheur d'emploi a laissé son adresse IP. Le "
            "compteur a besoin d'un clic, pas d'une identité.",
        )
        self.assertTrue(
            click.sudo().country_id,
            "Le pays ne désigne personne et sert à savoir si l'annonce porte "
            "hors du marché visé : il doit rester.",
        )

    def test_a_click_outside_recruitment_keeps_its_ip(self):
        """La PAIRE. Sans elle, le test précédent passerait aussi si le module
        cassait `link.tracker.click` pour tout le parc."""
        tracker = self.env["link.tracker"].sudo().create({
            "url": "https://example.invalid/infolettre",
            "title": "Infolettre",
        })
        click = self.env["link.tracker.click"].sudo().add_click(
            tracker.code, ip="203.0.113.9", country_code="CA",
        )
        self.assertEqual(
            click.ip, "203.0.113.9",
            "Un lien tracé qui n'est pas de recrutement doit garder le "
            "comportement du coeur.",
        )

    def test_the_guard_covers_a_click_created_directly(self):
        """La garde est sur `create`, donc sur TOUS les chemins, pas seulement
        sur celui du contrôleur `/r/`."""
        source = self._make_source()
        click = self.env["link.tracker.click"].sudo().create({
            "link_id": source.link_tracker_id.id,
            "ip": "203.0.113.11",
        })
        self.assertFalse(click.ip)

    # ------------------------------------------------------------------
    # 4. 🔴 Une candidature refusée reste une candidature reçue
    # ------------------------------------------------------------------

    def test_a_refused_application_still_counts_as_received(self):
        """Le défaut que ce test existe pour empêcher.

        L'assistant de refus du coeur ARCHIVE la candidature. Un décompte qui
        lit les candidatures actives verrait donc le taux de conversion d'une
        source fondre au fur et à mesure qu'on traite les dossiers, c'est-à-dire
        exactement quand on veut le mesurer.
        """
        source = self._make_source()
        applicant = self._make_applicant(source=self.seek)
        self._click(source, times=10)
        self.assertEqual(source.applicant_count, 1)

        reason = self.env["hr.applicant.refuse.reason"].search([], limit=1)
        applicant.write({
            "refuse_reason_id": reason.id,
            "decision_note": "Ne remplit pas la condition d'admissibilité.",
        })
        applicant.action_archive()
        source.invalidate_recordset()

        self.assertFalse(applicant.active, "Le montage du test n'archive pas.")
        self.assertEqual(
            source.applicant_count, 1,
            "La candidature refusée a disparu du décompte de sa source.",
        )
        self.assertEqual(source.refused_count, 1)

    # ------------------------------------------------------------------
    # 5. Les décomptes sont bornés
    # ------------------------------------------------------------------

    def test_counts_are_bound_to_the_job_and_the_source(self):
        """Une `utm.source` se partage entre postes. La nommer seule ferait
        déborder les chiffres d'un poste sur l'autre."""
        source = self._make_source(self.seek, job=self.job)
        self._make_applicant(source=self.seek, job=self.job)
        self._make_applicant(source=self.seek, job=self.other_job)
        self.assertEqual(
            source.applicant_count, 1,
            "Le décompte d'une source a compté une candidature d'un autre "
            "poste.",
        )

    def test_an_application_without_a_source_belongs_to_no_source(self):
        source = self._make_source()
        self._make_applicant(source=None)
        self.assertEqual(source.applicant_count, 0)

    # ------------------------------------------------------------------
    # 6. Les taux
    # ------------------------------------------------------------------

    def test_conversion_rate_is_applications_over_clicks(self):
        source = self._make_source()
        self._click(source, times=4)
        self._make_applicant(source=self.seek)
        self.assertAlmostEqual(source.conversion_rate, 25.0, places=1)

    def test_hire_rate_is_hires_over_applications(self):
        source = self._make_source()
        hired = self._make_applicant(source=self.seek, name="Embauchée")
        self._make_applicant(source=self.seek, name="Pas embauchée")
        hired.write({"date_closed": "2026-09-01 12:00:00"})
        source.invalidate_recordset()
        self.assertEqual(source.hired_count, 1)
        self.assertAlmostEqual(source.hire_rate, 50.0, places=1)

    def test_no_click_means_no_rate_at_all(self):
        source = self._make_source()
        self.assertEqual(
            source.conversion_rate, 0.0,
            "Sans clic il n'y a pas de taux ; zéro n'en est pas un, et c'est "
            "l'avertissement qui le dit.",
        )
        self.assertTrue(source.stat_is_partial)
        self.assertIn("Aucun clic", source.stat_warning)

    # ------------------------------------------------------------------
    # 7. Ce que le chiffre avoue
    # ------------------------------------------------------------------

    def test_applications_without_a_single_click_are_named(self):
        """Des candidatures sans clic veulent dire que l'annonce porte
        l'adresse nue, pas que la source ne convertit pas."""
        source = self._make_source()
        self._make_applicant(source=self.seek)
        self.assertTrue(source.stat_is_partial)
        self.assertIn("adresse nue", source.stat_warning)

    def test_an_unpublished_job_warns_before_the_link_is_pasted(self):
        self.job.is_published = False
        source = self._make_source()
        self.assertIn("n'est pas publié", source.stat_warning)

    def test_a_missing_alias_domain_is_named_not_hidden(self):
        """Mesuré sur la démo : zéro domaine d'alias, donc l'adresse courriel
        par source est indisponible. Le module le dit."""
        self.company.alias_domain_id = False
        source = self._make_source()
        self.assertFalse(source.has_domain)
        self.assertIn("domaine d'alias", source.stat_warning)

    def test_a_source_without_a_tracked_link_is_named(self):
        source = self._make_source()
        source.sudo().link_tracker_id = False
        source.invalidate_recordset()
        self.assertIn("pas de lien tracé", source.stat_warning)

    # ------------------------------------------------------------------
    # 8. L'agrégat du poste, et l'écart
    # ------------------------------------------------------------------

    def test_the_job_names_the_applications_no_source_explains(self):
        source = self._make_source()
        self._click(source, times=5)
        self._make_applicant(source=self.seek, name="Avec source")
        self._make_applicant(source=None, name="Sans source 1")
        self._make_applicant(source=None, name="Sans source 2")
        self.job.invalidate_recordset()

        self.assertEqual(self.job.sourced_applicant_count, 1)
        self.assertEqual(self.job.untracked_applicant_count, 2)
        self.assertAlmostEqual(self.job.source_coverage_rate, 33.3, places=1)
        self.assertIn(
            "2 des 3 candidatures", self.job.source_warning,
            "L'écart doit s'écrire en candidatures, pas en pourcentage seul.",
        )

    def test_the_job_sums_the_clicks_of_its_sources(self):
        seek = self._make_source(self.seek)
        indeed = self._make_source(self.indeed)
        self._click(seek, times=2)
        self._click(indeed, times=3)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.source_click_count, 5)

    def test_a_job_with_no_application_has_no_warning_to_write(self):
        self._make_source()
        self.job.invalidate_recordset()
        self.assertNotIn("candidatures reçues", self.job.source_warning or "")

    # ------------------------------------------------------------------
    # 9. Sous un vrai compte
    # ------------------------------------------------------------------

    def test_a_recruiter_reads_the_figures(self):
        """Les chiffres passent par `sudo` sur `link.tracker`, que le coeur ne
        montre qu'à `base.group_user`. Un recruteur doit les lire sans erreur,
        et sans que le total dépende de qui regarde."""
        recruiter = self.env["res.users"].create({
            "name": "recruteuse",
            "login": "recruteuse",
            "email": "recruteuse@example.invalid",
            "company_id": self.company.id,
            "company_ids": [(6, 0, [self.company.id])],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })
        source = self._make_source()
        self._click(source, times=2)
        self._make_applicant(source=self.seek)

        seen = source.with_user(recruiter)
        seen.invalidate_recordset()
        self.assertEqual(seen.click_count, 2)
        self.assertEqual(seen.applicant_count, 1)
        self.assertAlmostEqual(seen.conversion_rate, 50.0, places=1)
