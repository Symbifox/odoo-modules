# -*- coding: utf-8 -*-
"""La section « Sujets surveillés » du briefing.

⚠️ `bf_flux` est sous BUSL-1.1 et ce module est sous LGPL-3 : la lecture est
faite à l'exécution, sans dépendance de manifeste. Les essais se sautent
d'eux-mêmes là où `bf_flux` n'est pas installé.

Pourquoi seulement les listes qui surveillent un NOM : mesuré sur une
veille réelle, les listes de lecture retenaient des centaines d'éléments par
semaine quand celle des sujets surveillés en portait une poignée.
Verser les autres au briefing ferait l'inverse de ce que la demande demande.
"""

from unittest import SkipTest
from unittest.mock import patch

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDigestSujetsSurveilles(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "bf.flux.retenue" not in cls.env or "bf.flux.sujet" not in cls.env:
            raise SkipTest("bf_flux n'est pas installé : le briefing n'a pas de section Flux")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.olive = cls.env["res.users"].create({
            "name": "Olive Veille", "login": "olive.veille@example.com",
            "email": "olive.veille@example.com", "tz": "Pacific/Auckland",
        })
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Briefing de la veille",
            "user_ids": [Command.set([cls.olive.id])],
            # La citation donne au briefing une raison de partir même quand
            # la section Flux est vide : les essais d'absence lisent alors un
            # vrai corps, au lieu de constater qu'aucun courriel n'est parti.
            "include_weather": False, "include_quote": True,
            "include_meetings": False, "include_calendar_events": False,
            "include_week_preview": False,
            "include_overdue_activities": False, "include_today_activities": False,
            "include_overdue_tasks": False, "include_today_tasks": False,
        })
        # Une liste qui surveille un nom, et une qui n'en surveille aucun.
        cls.liste_sujets = cls.env["bf.flux.liste"].create({
            "name": "Sujets surveillés (essai 27254)",
            "user_ids": [Command.set([cls.olive.id])],
        })
        cls.liste_lecture = cls.env["bf.flux.liste"].create({
            "name": "News (essai 27254)",
            "user_ids": [Command.set([cls.olive.id])],
        })
        cls.env["bf.flux.sujet"].create({
            "name": "Entreprise Exemple",
            "liste_id": cls.liste_sujets.id,
        })
        cls.source = cls.env["bf.flux.source"].create({
            "name": "Source d'essai 27254",
            "url": "https://example.invalid/essai.xml",
        })

    def _retenue(self, liste, titre, lien="https://example.invalid/a"):
        # `cle` est l'identifiant de dédoublonnage, posé par la relève : il
        # est NOT NULL et aucun défaut ne le remplit à la main.
        element = self.env["bf.flux.element"].create({
            "titre": titre, "lien": lien, "emetteur": "Le Devoir d'essai",
            "cle": f"essai-27254-{titre}-{lien}",
            "source_ids": [Command.set([self.source.id])],
        })
        return self.env["bf.flux.retenue"].create({
            "liste_id": liste.id, "element_id": element.id,
            "etat": "retenu", "motifs": "Entreprise Exemple",
        })

    def _corps(self):
        with patch.object(type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=self.olive)
        mail = self.env["mail.mail"].search(
            [("email_to", "=", self.olive.email)], order="id desc", limit=1)
        self.assertTrue(mail, "aucun courriel produit")
        return mail.body_html

    def test_la_liste_qui_surveille_un_nom_entre_au_briefing(self):
        self._retenue(self.liste_sujets, "Exemple décroche un contrat")
        corps = self._corps()
        self.assertIn("Exemple décroche un contrat", corps)
        self.assertIn("Le Devoir d&#39;essai", corps)

    def test_les_listes_de_lecture_restent_dehors(self):
        self._retenue(self.liste_lecture, "Résultats du hockey")
        corps = self._corps()
        self.assertNotIn("Résultats du hockey", corps)

    def test_un_desabonnement_coupe_la_section(self):
        self._retenue(self.liste_sujets, "Exemple décroche un contrat")
        preference = self.env["bf.flux.preference"].search([
            ("liste_id", "=", self.liste_sujets.id), ("user_id", "=", self.olive.id)])
        if not preference:
            preference = self.env["bf.flux.preference"].create({
                "liste_id": self.liste_sujets.id, "user_id": self.olive.id})
        preference.desabonne = True
        self.assertNotIn("Exemple décroche un contrat", self._corps())

    def test_la_case_du_gabarit_coupe_la_section(self):
        self._retenue(self.liste_sujets, "Exemple décroche un contrat")
        self.config.include_flux_subjects = False
        self.assertNotIn("Exemple décroche un contrat", self._corps())

    def test_la_personne_coupe_la_section_pour_elle(self):
        self._retenue(self.liste_sujets, "Exemple décroche un contrat")
        self.olive.digest_opt_flux_subjects = "no"
        self.assertNotIn("Exemple décroche un contrat", self._corps())

    def test_rien_d_anterieur_au_dernier_briefing(self):
        retenue = self._retenue(self.liste_sujets, "Vieille nouvelle")
        # Le dernier envoi est postérieur à la retenue : elle a déjà été vue.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_flux_retenue SET create_date = %s WHERE id = %s",
            ("2026-01-01 00:00:00", retenue.id))
        retenue.invalidate_recordset(["create_date"])
        log = self.config._get_send_log(self.olive)
        log.last_sent = "2026-06-01 00:00:00"
        self.assertNotIn("Vieille nouvelle", self._corps())

    def test_le_texte_du_titre_est_echappe_et_seules_les_adresses_web_sont_des_liens(self):
        self._retenue(self.liste_sujets, "<b>Exemple</b>", lien="javascript:alert(1)")
        corps = self._corps()
        self.assertNotIn("<b>Exemple</b>", corps)
        self.assertIn("&lt;b&gt;Bor", corps)
        self.assertNotIn('href="javascript:', corps)

    def test_le_rattrapage_ne_remplit_pas_le_briefing(self):
        """🔴 Ajouter un sujet surveillé fait retenir tout l'arrière-catalogue
        de ses sources (`rattrapage`). Le résumé de `bf_flux` l'écarte de ses
        propres courriels ; le briefing s'ouvrait dessus — le bruit même que la
        demande retire."""
        neuve = self._retenue(self.liste_sujets, "Exemple signe aujourd'hui")
        vieille = self._retenue(self.liste_sujets, "Exemple en 2019",
                                lien="https://example.invalid/vieux")
        if "rattrapage" not in vieille._fields:
            self.skipTest("cette version de bf_flux n'a pas de drapeau de rattrapage")
        vieille.rattrapage = True
        corps = self._corps()
        self.assertIn("Exemple signe aujourd", corps)
        self.assertNotIn("Exemple en 2019", corps)
        self.assertTrue(neuve.exists())

    def test_ce_qui_est_deja_lu_ne_revient_pas_le_matin(self):
        """Ce que la personne a déjà lu dans Flux ou dans Discuss est lu."""
        Lecture = self.env.get("bf.flux.lecture")
        if Lecture is None:
            self.skipTest("cette version de bf_flux ne tient pas les lectures")
        lue = self._retenue(self.liste_sujets, "Exemple déjà lue")
        autre = self._retenue(self.liste_sujets, "Exemple pas encore lue",
                              lien="https://example.invalid/b")
        Lecture.create({"user_id": self.olive.id, "element_id": lue.element_id.id})
        corps = self._corps()
        self.assertNotIn("Exemple déjà lue", corps)
        self.assertIn("Exemple pas encore lue", corps)
        self.assertTrue(autre.exists())

    def test_un_ecarte_ne_parait_pas(self):
        retenue = self._retenue(self.liste_sujets, "Écarté par le jugement")
        retenue.etat = "ecarte"
        self.assertNotIn("Écarté par le jugement", self._corps())
