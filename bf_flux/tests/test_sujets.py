# -*- coding: utf-8 -*-
from datetime import timedelta
from email.utils import format_datetime
from urllib.parse import unquote

from psycopg2 import IntegrityError

from odoo.addons.bf_flux.models.flux_source import FluxErreur

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import FluxCase, fixture


def dates(nom, **kw):
    """Une fixture dont les dates suivent l'horloge : la fraîcheur d'un sujet
    se juge à l'heure de l'essai, pas à celle où la fixture a été écrite."""
    maintenant = fields.Datetime.now()
    brut = fixture(nom).decode()
    brut = brut.replace("{RECENT}", format_datetime(
        (maintenant - timedelta(hours=kw.get("recent_h", 3))).replace(tzinfo=None), usegmt=False))
    brut = brut.replace("{VIEUX}", format_datetime(
        (maintenant - timedelta(days=40)).replace(tzinfo=None), usegmt=False))
    return brut.replace("-0000", "+0000").encode()


@tagged("post_install", "-at_install")
class TestSujets(FluxCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].with_user(self.u_gestion).create({
            "name": "Sujets surveillés",
            "user_ids": [(6, 0, self.u_gestion.ids)],
        })
        self.sujet = self.env["bf.flux.sujet"].with_user(self.u_gestion).create({
            "name": "Blue Fox", "liste_id": self.liste.id,
            "description": "Firme de consultation TI de Sherbrooke.",
        })

    def _src(self, lg):
        return self.sujet.source_ids.filtered(lambda s: s.langue_preferee == lg)

    def _relever(self, src, contenu):
        with self.reseau({src.url: contenu}):
            # Le chemin du cron, tel quel : c'est la source qui sait si
            # l'arriéré est dû, pas l'essai.
            self.env["bf.flux.source"].browse(src.id)._flux_relever()._flux_trier_et_diffuser()

    def _cles(self, liste=None):
        return set((liste or self.liste).retenue_ids.mapped("element_id.cle"))

    def test_une_recherche_par_langue(self):
        fr, en = self._src("fr"), self._src("en")
        self.assertTrue(fr and en)
        self.assertIn("hl=fr-CA", fr.url)
        self.assertIn("ceid=CA:en", en.url)
        self.assertIn('"Blue Fox" when:7d', unquote(fr.url), "le nom exact, jamais ses mots")
        self.assertEqual(fr.cadence_minutes, 30)
        self.assertIn(self.liste, fr.liste_ids)
        self.sujet.variantes = "Blue Fox Inc\nSymbifox"
        self.assertIn('"Blue Fox" OR "Blue Fox Inc" OR "Symbifox"', unquote(fr.url))
        self.sujet.langues = "fr"
        self.assertFalse(en.active, "la langue retirée n'est plus cherchée")
        self.assertTrue(fr.active)
        self.sujet.action_mettre_en_attente()
        self.assertFalse(fr.active, "un sujet suspendu n'est plus cherché")
        self.sujet.action_valider()
        self.assertTrue(fr.active)
        self.assertEqual(len(self.sujet.source_ids), 2, "les sources se reprennent, pas de doublon")

    def test_le_nom_retient_et_rien_d_autre(self):
        self._relever(self._src("fr"), dates("google.xml"))
        # FLOU : Google ramène un résultat sans le nom, une recherche n'est pas
        # une source prise en entier. VIEUX : 40 jours, hors fraîcheur.
        self.assertEqual(self._cles(), {"HOCKEY", "NOUS"})
        ret = self.liste.retenue_ids.filtered(lambda r: r.element_id.cle == "NOUS")
        self.assertEqual(ret.motifs, "Sujet : Blue Fox")
        self.assertEqual(ret.element_id.emetteur, "Journal Exemple", "l'agrégateur nomme le diffuseur")

    def test_exclusions_du_sujet(self):
        self.sujet.exclusions = "Herning"
        self._relever(self._src("fr"), dates("google.xml"))
        self.assertEqual(self._cles(), {"NOUS"})

    def test_premiere_releve_est_un_rattrapage(self):
        src = self._src("fr")
        self._relever(src, dates("google.xml"))
        self.assertTrue(all(r.rattrapage for r in self.liste.retenue_ids))
        commentaires = self.liste.channel_id.message_ids.filtered(lambda m: m.message_type == "comment")
        self.assertFalse(commentaires, "la semaine d'arriéré n'est pas diffusée")
        # La relève suivante apporte du neuf : lui est diffusé.
        suivante = dates("google.xml").replace(b"NOUS", b"NEUF")
        self._relever(src, suivante)
        neuf = self.liste.retenue_ids.filtered(lambda r: r.element_id.cle == "NEUF")
        self.assertFalse(neuf.rattrapage)
        self.assertTrue(neuf.diffusee)

    def test_le_nom_se_cherche_aussi_dans_les_autres_sources(self):
        actu = self.env["bf.flux.source"].create({
            "name": "Actualités", "url": "https://actu.example.com/rss"})
        autre = self.env["bf.flux.liste"].create({
            "name": "Actualités", "source_ids": [(6, 0, actu.ids)]})
        self.env["bf.flux.sujet"].create({"name": "Groupe Témoin", "liste_id": self.liste.id})
        self._relever(actu, dates("actualites.xml"))
        self.assertEqual(self._cles(), {"ACTU-1"}, "la liste des sujets ne lit pas cette source")
        self.assertEqual(self._cles(autre), {"ACTU-1", "ACTU-2"})

    def test_propose_ne_cherche_rien(self):
        propose = self.env["bf.flux.sujet"].create({
            "name": "Programme Témoin", "liste_id": self.liste.id, "etat": "propose"})
        self.assertFalse(propose.source_ids)
        actu = self.env["bf.flux.source"].create({
            "name": "Actualités", "url": "https://actu.example.com/rss"})
        self.env["bf.flux.sujet"].create({
            "name": "Groupe Témoin", "liste_id": self.liste.id, "etat": "propose"})
        self._relever(actu, dates("actualites.xml"))
        self.assertFalse(self._cles(), "un sujet proposé ne retient rien")

    def test_proposer_les_clients_actifs(self):
        Partner = self.env["res.partner"]
        client = Partner.create({"name": "Atelier Témoin inc.", "is_company": True})
        contact = Partner.create({"name": "Personne chez Témoin", "parent_id": client.id})
        deja = Partner.create({"name": "Blue Fox", "is_company": True})
        seul = Partner.create({"name": "Particulier"})
        court = Partner.create({"name": "AB", "is_company": True})
        Projet = self.env["project.project"]
        Projet.create({"name": "Soudure", "partner_id": contact.id})
        Projet.create({"name": "Interne", "partner_id": deja.id})
        Projet.create({"name": "Perso", "partner_id": seul.id})
        Projet.create({"name": "Court", "partner_id": court.id})
        self.liste.with_user(self.u_gestion).action_proposer_clients()
        proposes = self.liste.sujet_ids.filtered(lambda s: s.etat == "propose")
        self.assertIn(client, proposes.partner_id, "la société du contact")
        self.assertNotIn(deja, proposes.partner_id, "déjà surveillé sous ce nom")
        self.assertNotIn(seul, proposes.partner_id, "un particulier n'est pas proposé")
        self.assertNotIn(court, proposes.partner_id, "un nom trop court ne fait pas tout échouer")
        self.assertFalse(proposes.source_ids)
        avant = len(self.liste.sujet_ids)
        self.liste.with_user(self.u_gestion).action_proposer_clients()
        self.assertEqual(len(self.liste.sujet_ids), avant, "rien de proposé deux fois")
        atelier = proposes.filtered(lambda s: s.partner_id == client)
        atelier.with_user(self.u_gestion).action_valider()
        self.assertEqual(len(atelier.source_ids), 2)

    def test_reserve_a_la_gestion(self):
        with self.assertRaises(AccessError):
            self.liste.with_user(self.u_atelier).action_proposer_clients()
        with self.assertRaises(AccessError):
            self.sujet.with_user(self.u_atelier).action_mettre_en_attente()
        with self.assertRaises(AccessError):
            self.env["bf.flux.sujet"].with_user(self.u_atelier).create({
                "name": "Autre", "liste_id": self.liste.id})

    def test_nom_trop_court(self):
        with self.assertRaises(ValidationError):
            self.env["bf.flux.sujet"].create({"name": "BF", "liste_id": self.liste.id})


@tagged("post_install", "-at_install")
class TestCadence(FluxCase):

    def test_cadence_en_minutes(self):
        self.src_defense.cadence_minutes = 30
        avant = fields.Datetime.now()
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
        ecart = self.src_defense.prochaine_releve - avant
        self.assertTrue(timedelta(minutes=29) <= ecart <= timedelta(minutes=31))

    def test_echec_passager_ne_revient_pas_plus_tard_que_la_cadence(self):
        from odoo.addons.bf_flux.models.flux_source import FluxErreur
        self.src_defense.cadence_minutes = 20
        avant = fields.Datetime.now()
        with self.reseau({self.src_defense.url: FluxErreur("HTTP 429", passager=True)}):
            self.src_defense._flux_relever()
        self.assertLessEqual(self.src_defense.prochaine_releve - avant, timedelta(minutes=21))

    @mute_logger("odoo.sql_db")
    def test_pas_sous_le_pas_de_la_releve(self):
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.src_defense.cadence_minutes = 10
            self.src_defense.flush_recordset()


@tagged("post_install", "-at_install")
class TestPublicationsMaison(FluxCase):

    def test_le_site_du_locataire_n_est_pas_une_nouvelle(self):
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://exemple-maison.com")
        liste = self.env["bf.flux.liste"].create({"name": "Sujets"})
        self.env["bf.flux.sujet"].create({"name": "Groupe Témoin", "liste_id": liste.id})
        blogue = self.env["bf.flux.source"].create({
            "name": "Notre blogue", "url": "https://www.exemple-maison.com/blog/feed"})
        actu = self.env["bf.flux.source"].create({
            "name": "Actualités", "url": "https://actu.example.com/rss"})
        maison = dates("actualites.xml").replace(
            b"https://actu.exemple.test/temoin", b"https://www.exemple-maison.com/blog/temoin")
        with self.reseau({blogue.url: maison, actu.url: dates("actualites.xml").replace(b"ACTU-", b"AUTRE-")}):
            blogue._flux_relever()._flux_trier_et_diffuser()
            actu._flux_relever()._flux_trier_et_diffuser()
        self.assertEqual(set(liste.retenue_ids.mapped("element_id.cle")), {"AUTRE-1"},
                         "notre propre billet n'est pas une mention ; le même nom ailleurs, oui")


@tagged("post_install", "-at_install")
class TestSujetsRelecture(FluxCase):
    """Les défauts d'une relecture adverse, un essai chacun."""

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({"name": "Sujets"})
        self.sujet = self.env["bf.flux.sujet"].create({"name": "Blue Fox", "liste_id": self.liste.id})
        self.src = self.sujet.source_ids.filtered(lambda s: s.langue_preferee == "fr")

    def _relever(self, src, contenu):
        with self.reseau({src.url: contenu}):
            src._flux_relever()._flux_trier_et_diffuser()

    def _cles(self, liste=None, rattrapage=None):
        rets = (liste or self.liste).retenue_ids
        if rattrapage is not None:
            rets = rets.filtered(lambda r: r.rattrapage == rattrapage)
        return set(rets.mapped("element_id.cle"))

    def test_regle_cassee_refusee_a_la_saisie(self):
        for vals in ({"exclusions": "re:("}, {"variantes": "re:Blue.*"}, {"variantes": "AI"}):
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.sujet.write(vals)

    def test_regle_cassee_n_arrete_pas_les_autres(self):
        # Posée par SQL, comme une donnée d'avant la contrainte.
        self.env.cr.execute("UPDATE bf_flux_sujet SET exclusions = 're:(' WHERE id = %s", (self.sujet.id,))
        self.sujet.invalidate_recordset(["exclusions"])
        actu = self.env["bf.flux.source"].create({
            "name": "Actualités", "url": "https://actu.example.com/rss"})
        autre = self.env["bf.flux.liste"].create({
            "name": "Actualités", "source_ids": [(6, 0, actu.ids)]})
        # Des éléments récents qui portent le nom : la règle cassée est lue.
        brut = dates("actualites.xml").replace(b"Groupe T\xc3\xa9moin", b"Blue Fox")
        with self.reseau({actu.url: brut}):
            actu._flux_relever()._flux_trier_et_diffuser()
        self.assertEqual(len(autre.retenue_ids), 2, "la relève des autres listes continue")

    def test_premiere_releve_en_echec_garde_l_arriere(self):
        with self.reseau({self.src.url: FluxErreur("HTTP 429", passager=True)}):
            self.src._flux_relever()._flux_trier_et_diffuser()
        self.assertTrue(self.src.rattrapage_du, "un échec ne solde pas l'arriéré")
        self._relever(self.src, dates("google.xml"))
        self.assertFalse(self.src.rattrapage_du)
        self.assertEqual(self._cles(rattrapage=False), set(), "tout ce qui arrive là est de l'arriéré")

    def test_requete_changee_ou_reprise_est_un_arriere(self):
        self._relever(self.src, dates("google.xml"))
        self.sujet.variantes = "Blue Fox Inc"
        self.assertTrue(self.src.rattrapage_du, "une requête neuve ramène d'abord un arriéré")
        self._relever(self.src, dates("google.xml").replace(b"NOUS", b"NOUS2"))
        self.assertEqual(self._cles(rattrapage=False), set())
        self.sujet.action_mettre_en_attente()
        self.sujet.action_valider()
        self.assertTrue(self.src.rattrapage_du, "une recherche qui reprend aussi")

    def test_le_bouton_relever_trie(self):
        with self.reseau({self.src.url: dates("google.xml")}):
            self.src.with_user(self.u_gestion).action_relever()
        self.assertEqual(self._cles(), {"HOCKEY", "NOUS"},
                         "relever à la main trie aussi : sinon ces éléments sont perdus")
        self.assertEqual(self._cles(rattrapage=False), set(), "et l'arriéré reste un arriéré")

    def test_google_news_vers_notre_site(self):
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://exemple-maison.com")
        brut = dates("google.xml").replace(
            b'<source url="https://www.journal-exemple.test">', b'<source url="https://blogue.exemple-maison.com">')
        self._relever(self.src, brut)
        self.assertNotIn("NOUS", self._cles(), "notre billet indexé par Google, sous-domaine compris")
        self.assertIn("HOCKEY", self._cles())

    def test_cloison_de_societe(self):
        autre_soc = self.env["res.company"].create({"name": "Société B"})
        liste_b = self.env["bf.flux.liste"].create({"name": "Sujets de B", "company_id": autre_soc.id})
        self.env["bf.flux.sujet"].create({"name": "Groupe Témoin", "liste_id": liste_b.id})
        liste_a = self.env["bf.flux.liste"].create({"name": "Sujets de A", "company_id": self.env.company.id})
        self.env["bf.flux.sujet"].create({"name": "Groupe Témoin", "liste_id": liste_a.id})
        actu = self.env["bf.flux.source"].create({
            "name": "Actualités A", "url": "https://actu.example.com/rss",
            "company_id": self.env.company.id})
        with self.reseau({actu.url: dates("actualites.xml")}):
            actu._flux_relever()._flux_trier_et_diffuser()
        self.assertFalse(self._cles(liste_b), "la liste de B ne lit pas les sources de A")
        self.assertEqual(self._cles(liste_a), {"ACTU-1"}, "celle de A, oui")

    def test_date_future_ramenee_a_maintenant(self):
        brut = dates("google.xml").decode()
        import re as _re
        brut = _re.sub(r"<pubDate>[^<]*</pubDate>", "<pubDate>Thu, 01 Jan 2099 00:00:00 +0000</pubDate>", brut, count=1)
        self._relever(self.src, brut.encode())
        self.assertLessEqual(max(self.src.element_ids.mapped("date_publication")), fields.Datetime.now())

    def test_la_recherche_suit_sa_liste(self):
        autre = self.env["bf.flux.liste"].create({"name": "Autre"})
        self.sujet.liste_id = autre
        self.assertEqual(self.src.liste_ids, autre, "l'ancienne liste ne lit plus la recherche")
        autre.active = False
        self.assertFalse(self.src.active, "liste archivée : on ne cherche plus")
        autre.active = True
        self.assertTrue(self.src.active)
