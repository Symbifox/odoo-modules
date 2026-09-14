"""Les seuils : ce qui s'affiche, ce qui reste muet, et le calcul de l'eNPS."""

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import PulseCase


@tagged("post_install", "-at_install")
class TestPulseSeuils(PulseCase):

    def test_sous_le_seuil_le_score_ne_dit_ni_zero_ni_approximation(self):
        campaign = self._campaign(score_threshold=5, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:3]:
            self._repondre(invitation, note=9)
        campaign.action_close()
        scores = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
        ])
        self.assertTrue(scores)
        muets = scores.filtered(lambda s: not s.is_displayable)
        self.assertTrue(muets, "aucun score n'est retenu sous le seuil")
        for score in muets:
            self.assertEqual(score.display_score, "Pas assez de réponses")

    def test_au_dessus_du_seuil_le_score_sort(self):
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:6]:
            self._repondre(invitation, note=8)
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertTrue(score.is_displayable)
        self.assertEqual(score.respondent_count, 6)
        self.assertEqual(score.display_score, "8.0 / 10")

    def test_les_commentaires_ont_leur_propre_seuil(self):
        """Trois verbatims passent le seuil d'un score et pas le leur."""
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:4]:
            self._repondre(invitation, note=7, texte="La charge monte.")
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertTrue(score.is_displayable, "le score chiffré devrait sortir")
        self.assertEqual(score.verbatim_count, 4)
        self.assertFalse(
            score.verbatims_displayable,
            "quatre commentaires ne devraient pas passer un seuil de cinq",
        )
        self.assertFalse(score.verbatim_html)

    def test_les_commentaires_sortent_au_dessus_de_leur_seuil(self):
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(invitation, note=7, texte="La charge monte.")
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertTrue(score.verbatims_displayable)
        self.assertIn("La charge monte.", score.verbatim_html)

    def test_le_verbatim_est_echappe(self):
        """Un commentaire est du texte écrit par un humain, pas du HTML."""
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(
                invitation, note=7,
                texte="<script>alert('salut')</script> ça va mal",
            )
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertTrue(score.verbatims_displayable)
        self.assertNotIn("<script>", score.verbatim_html)

    def test_enps_promoteurs_moins_detracteurs(self):
        """Trois promoteurs, un passif, un détracteur : +40."""
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        notes = [10, 9, 9, 8, 3]
        for invitation, note in zip(campaign.invitation_ids, notes):
            self._repondre(invitation, note=7, enps=note)
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_enps.metric_id.id),
        ], limit=1)
        self.assertTrue(score.is_enps_metric)
        self.assertAlmostEqual(score.enps, 40.0, places=1)
        self.assertEqual(score.display_score, "+40")

    def test_un_seuil_de_score_sous_trois_est_refuse(self):
        with self.assertRaises(ValidationError):
            self._campaign(score_threshold=2)

    def test_un_seuil_de_commentaire_plus_bas_que_le_score_est_refuse(self):
        with self.assertRaises(ValidationError):
            self._campaign(score_threshold=5, text_threshold=3)

    def test_une_question_enps_doit_etre_une_echelle(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.pulse.question"].create({
                "title": "Un eNPS en texte libre",
                "metric_id": self.question_enps.metric_id.id,
                "question_kind": "text",
                "is_enps": True,
            })

    def test_le_decoupage_par_departement_segmente(self):
        campaign = self._campaign(segment_mode="department",
                                  score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids:
            self._repondre(invitation, note=6)
        campaign.action_close()
        scores = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ])
        segments = set(scores.mapped("segment_label"))
        self.assertEqual(segments, {"Atelier", "Bureau"})
        for score in scores:
            self.assertEqual(score.respondent_count, 4)

    def test_deux_questions_dans_un_axe_ne_doublent_pas_les_repondants(self):
        """🔴 Le seuil compte des personnes, pas des réponses.

        Deux personnes qui répondent chacune à deux questions du même axe
        produisent quatre lignes. Compter les lignes les ferait franchir un
        seuil de trois à deux.
        """
        deuxieme = self.env.ref(
            "bf_employee_experience_pulse.question_workload_2")
        self.assertEqual(deuxieme.metric_id, self.question_scale.metric_id)
        campaign = self._campaign(
            score_threshold=3, text_threshold=5,
            question_ids=[(6, 0, [self.question_scale.id, deuxieme.id])],
        )
        campaign.action_open()
        Staging = self.env["bf.ex.pulse.staging"]
        for invitation in campaign.invitation_ids[:2]:
            Staging.create([
                {
                    "campaign_id": campaign.id,
                    "question_id": question.id,
                    "token": invitation.token,
                    "segment_key": invitation.segment_key,
                    "value_scale": 9,
                }
                for question in (self.question_scale, deuxieme)
            ])
            invitation.write({"used": True})
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertEqual(
            score.respondent_count, 2,
            "quatre réponses de deux personnes ont été comptées comme quatre "
            "répondants",
        )
        self.assertFalse(
            score.is_displayable,
            "deux personnes ont franchi un seuil de trois",
        )

    def test_deux_commentaires_par_personne_ne_doublent_pas_le_seuil(self):
        """Même défaut, côté verbatims."""
        axe = self.question_text.metric_id
        autre_texte = self.env["bf.ex.pulse.question"].create({
            "title": "Et qu'est-ce qui aide, ces temps-ci?",
            "metric_id": axe.id,
            "question_kind": "text",
        })
        campaign = self._campaign(
            score_threshold=3, text_threshold=5,
            question_ids=[(6, 0, [self.question_text.id, autre_texte.id])],
        )
        campaign.action_open()
        Staging = self.env["bf.ex.pulse.staging"]
        for invitation in campaign.invitation_ids[:3]:
            Staging.create([
                {
                    "campaign_id": campaign.id,
                    "question_id": question.id,
                    "token": invitation.token,
                    "segment_key": invitation.segment_key,
                    "value_text": "Un mot de plus.",
                }
                for question in (self.question_text, autre_texte)
            ])
            invitation.write({"used": True})
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", axe.id),
        ], limit=1)
        self.assertEqual(score.verbatim_count, 6)
        self.assertFalse(
            score.verbatims_displayable,
            "six commentaires de trois personnes ont franchi un seuil de cinq",
        )

    def test_le_plancher_prend_la_question_la_plus_repondue(self):
        """🔴 Trou de mutation : `min` au lieu de `max` restait vert.

        Tous mes répondants répondaient à toutes les questions, donc le
        minimum et le maximum donnaient le même nombre. Il faut des réponses
        inégales pour que le choix se voie : quatre personnes répondent à la
        première question, deux d'entre elles seulement à la seconde. Le
        plancher est quatre, pas deux, et pas six.
        """
        deuxieme = self.env.ref(
            "bf_employee_experience_pulse.question_workload_2")
        campaign = self._campaign(
            score_threshold=3, text_threshold=5,
            question_ids=[(6, 0, [self.question_scale.id, deuxieme.id])],
        )
        campaign.action_open()
        Staging = self.env["bf.ex.pulse.staging"]
        for rang, invitation in enumerate(campaign.invitation_ids[:4]):
            lignes = [{
                "campaign_id": campaign.id,
                "question_id": self.question_scale.id,
                "token": invitation.token,
                "segment_key": invitation.segment_key,
                "value_scale": 8,
            }]
            if rang < 2:
                lignes.append({
                    "campaign_id": campaign.id,
                    "question_id": deuxieme.id,
                    "token": invitation.token,
                    "segment_key": invitation.segment_key,
                    "value_scale": 8,
                })
            Staging.create(lignes)
            invitation.write({"used": True})
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertEqual(
            score.respondent_count, 4,
            "le plancher devrait être la question la plus répondue",
        )
        self.assertTrue(
            score.is_displayable,
            "quatre répondants passent un seuil de trois",
        )

    def test_deux_vagues_du_meme_jour_ne_doublent_pas_les_scores(self):
        """🔴 Vu en production, pas par un essai.

        Un score appartient à la FENÊTRE glissante, pas à la vague : deux
        vagues ouvertes le même jour partagent la même fenêtre, donc les mêmes
        chiffres. Rangées par vague, elles produisaient deux jeux de lignes
        identiques et l'écran des scores montrait chaque axe en double.
        """
        premiere = self._campaign(score_threshold=3, text_threshold=5)
        premiere.action_open()
        for invitation in premiere.invitation_ids[:5]:
            self._repondre(invitation, note=8)
        premiere.action_close()

        seconde = self._campaign(
            name="Seconde vague du même jour",
            score_threshold=3, text_threshold=5,
        )
        seconde.action_open()
        for invitation in seconde.invitation_ids[:5]:
            self._repondre(invitation, note=6)
        seconde.action_close()

        Score = self.env["bf.ex.pulse.score"]
        lignes = Score.search([
            ("company_id", "=", self.company.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ])
        cles = [(l.metric_id.id, l.segment_key, l.period_start, l.period_end)
                for l in lignes]
        self.assertEqual(
            len(cles), len(set(cles)),
            "deux lignes de score couvrent le même axe sur la même fenêtre : %s"
            % cles,
        )

        # Et la ligne qui reste porte bien TOUTES les réponses de la fenêtre.
        ligne = lignes[:1]
        self.assertEqual(
            ligne.respondent_count, 10,
            "la fenêtre devrait compter les répondants des deux vagues",
        )
        self.assertAlmostEqual(ligne.score, 7.0, places=1)

    def test_deux_vagues_de_jours_differents_gardent_leur_fenetre(self):
        """Le regroupement ne doit pas écraser des fenêtres distinctes."""
        from datetime import timedelta

        # ⚠️ La date se recule AVANT le versement : c'est elle qui date les
        # réponses. La reculer après coup ne déplace pas ce qui est déjà versé,
        # et l'essai mesurait alors sa propre erreur de montage.
        ancienne = self._campaign(score_threshold=3, text_threshold=5)
        ancienne.action_open()
        ancienne.date_open = ancienne.date_open - timedelta(days=30)
        for invitation in ancienne.invitation_ids[:5]:
            self._repondre(invitation, note=9)
        ancienne.action_close()

        recente = self._campaign(name="Vague d'aujourd'hui",
                                 score_threshold=3, text_threshold=5)
        recente.action_open()
        for invitation in recente.invitation_ids[:5]:
            self._repondre(invitation, note=5)
        recente.action_close()

        fins = self.env["bf.ex.pulse.score"].search([
            ("company_id", "=", self.company.id),
        ]).mapped("period_end")
        self.assertEqual(
            len(set(fins)), 2,
            "deux vagues de jours différents doivent garder deux fenêtres",
        )
