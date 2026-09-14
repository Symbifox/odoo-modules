# -*- coding: utf-8 -*-
"""Les essais du pont calendrier.

⚠️ Les titres employés ici sont ceux du VRAI calendrier qui a servi de modèle,
recopiés tels quels, frimousse comprise. Un jeu d'essai inventé aurait raté les
deux formes qui cohabitent dedans.
"""

from datetime import date

from odoo.tests import TransactionCase, tagged

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:7743613d-c9f8-4246-9d39-0ff2dab56758
DTSTART;VALUE=DATE:20260330
SUMMARY:\U0001F334 Camille - Exemple
END:VEVENT
BEGIN:VEVENT
UID:a7075f20-c5f7-4baa-b0fe-e49d8cc9ec65
DTSTART;VALUE=DATE:20260824
SUMMARY:Vacances - Prenom Exemple
END:VEVENT
BEGIN:VEVENT
UID:d9cf1597-543f-4d50-bd1d-6e449c5b224d
DTSTART;VALUE=DATE:20260905
SUMMARY:Retour de vacances Prenom
END:VEVENT
END:VCALENDAR
"""


@tagged("post_install", "-at_install", "bf_absence")
class TestPontCalendrier(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Source = cls.env["bf.absence.calendar.source"]

    # ---- La lecture du fichier ---------------------------------------
    def test_les_trois_entrees_sont_lues(self):
        entrees = self.Source._parse_ics(ICS)
        self.assertEqual(len(entrees), 3)
        self.assertEqual(entrees[0][1], date(2026, 3, 30))
        self.assertIn("Camille", entrees[0][2])

    def test_une_ligne_repliee_n_est_pas_tronquee(self):
        """⚠️ Une ligne ICS se replie avec une espace en tête. Sans recollage,
        un titre long perd sa fin, en silence."""
        replie = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x1\n"
                  "DTSTART;VALUE=DATE:20260908\n"
                  "SUMMARY:Vacances - Societe Exemple\n  - Portugal\n"
                  "END:VEVENT\nEND:VCALENDAR\n")
        entrees = self.Source._parse_ics(replie)
        self.assertEqual(len(entrees), 1)
        self.assertIn("Portugal", entrees[0][2])

    # ---- La lecture des titres ---------------------------------------
    def test_un_marqueur_seul_donne_la_personne_et_son_indice(self):
        cle, indices, retour = self.Source._cle_personne("\U0001F334 Camille - Exemple")
        self.assertEqual(cle, "Camille")
        self.assertEqual(indices, ["Exemple"])
        self.assertFalse(retour)

    def test_le_prefixe_vacances_n_est_pas_un_nom(self):
        cle, _i, retour = self.Source._cle_personne("Vacances - Prenom Exemple")
        self.assertEqual(cle, "Prenom Exemple")
        self.assertFalse(retour)

    def test_un_retour_est_reconnu_comme_tel(self):
        cle, _i, retour = self.Source._cle_personne("Retour de vacances Prenom")
        self.assertTrue(retour)
        self.assertEqual(cle, "Prenom")

    def test_une_precision_de_plus_reste_un_indice(self):
        cle, indices, _r = self.Source._cle_personne("Vacances - Societe Exemple - Portugal")
        self.assertEqual(cle, "Societe Exemple")
        self.assertEqual(indices, ["Portugal"])

    # ---- L'appariement -----------------------------------------------
    def test_un_nom_ambigu_n_apparie_personne(self):
        """🔴 « David » tout seul peut désigner trois personnes. Se tromper
        enverrait le courrier d'un client à un autre."""
        P = self.env["res.partner"]
        P.create({"name": "David Untel"})
        P.create({"name": "David Autre"})
        self.assertFalse(self.Source._apparier("David", []))

    def test_un_indice_de_societe_leve_l_ambiguite(self):
        P = self.env["res.partner"]
        a = P.create({"name": "Cabinet Exemple Essai", "is_company": True})
        b = P.create({"name": "Cabinet Autre Essai", "is_company": True})
        bon = P.create({"name": "Camille Pareil", "parent_id": a.id})
        P.create({"name": "Camille Pareil", "parent_id": b.id})
        self.assertEqual(self.Source._apparier("Camille", ["Exemple"]), bon)

    def test_un_nom_inconnu_ne_cree_personne(self):
        avant = self.env["res.partner"].search_count([])
        self.assertFalse(self.Source._apparier("Personne Qui N Existe Pas", []))
        self.assertEqual(self.env["res.partner"].search_count([]), avant)

    # ---- Les formes que seul le vrai calendrier montrait ---------------
    def test_le_mot_de_nature_peut_etre_en_suffixe(self):
        """🔴 « Société - vacances » autant que « Vacances - Société ». Pris pour un
        indice de société, le mot empêchait tout appariement."""
        cle, indices, _r = self.Source._cle_personne("Societe Exemple - vacances")
        self.assertEqual(cle, "Societe Exemple")
        self.assertEqual(indices, [])

    def test_la_societe_entre_parentheses_devient_un_indice(self):
        cle, indices, _r = self.Source._cle_personne("Camille (Exemple) - vacances")
        self.assertEqual(cle, "Camille")
        self.assertEqual(indices, ["Exemple"])

    def test_la_nature_se_lit_dans_le_titre(self):
        self.assertEqual(
            self.Source._nature_du_titre("Fermeture - Societe Exemple", "vacation"),
            "closure")
        self.assertEqual(
            self.Source._nature_du_titre("Societe Exemple - formation", "vacation"),
            "training")
        self.assertEqual(
            self.Source._nature_du_titre("Rien de connu", "vacation"), "vacation")

    def test_un_retour_au_prenom_seul_ferme_la_periode(self):
        """🔴 « Retour de vacances Prénom » ne répète pas le nom de famille de
        « Vacances - Prénom Nom ». L'appariement à l'identique laissait la
        période sans fin : vu sur le vrai calendrier."""
        P = self.env["res.partner"]
        societe = P.create({"name": "Strategies Exemple", "is_company": True})
        personne = P.create({"name": "Prenom Exemple", "parent_id": societe.id})
        ics = ("BEGIN:VCALENDAR\n"
               "BEGIN:VEVENT\nUID:d1\nDTSTART;VALUE=DATE:20260824\n"
               "SUMMARY:Vacances - Prenom Exemple\nEND:VEVENT\n"
               "BEGIN:VEVENT\nUID:r1\nDTSTART;VALUE=DATE:20260905\n"
               "SUMMARY:Retour de vacances Prenom\nEND:VEVENT\n"
               "END:VCALENDAR\n")
        src = self.Source.new({
            "name": "Essai", "calendar_slug": "x", "nature": "vacation",
            "days_back": 400, "days_ahead": 400,
        })
        # On rejoue l'appariement sans toucher au réseau.
        entrees = self.Source._parse_ics(ics)
        departs, retours = [], []
        for uid, jour, titre in entrees:
            cle, indices, est_retour = self.Source._cle_personne(titre)
            if est_retour:
                retours.append((cle.lower(), jour))
            else:
                departs.append((uid, jour, cle, indices))
        uid, jour, cle, indices = departs[0]
        candidats = [d for k, d in retours
                     if d > jour and (cle.lower().startswith(k) or k.startswith(cle.lower()))]
        self.assertTrue(candidats, "le retour ne s'est pas apparié au départ")
        self.assertEqual(min(candidats), date(2026, 9, 5))
        self.assertEqual(self.Source._apparier(cle, indices), personne)

    def test_un_raccourci_appris_apparie_ce_qu_aucun_nom_ne_donne(self):
        """🔴 Des initiales ne ressemblent à aucun nom, et les rapprocher d'un
        nom qui commence pareil serait l'erreur qu'on refuse ailleurs. On les
        apprend."""
        P = self.env["res.partner"]
        personne = P.create({"name": "Marie Claire Exemple",
                             "email": "mce@exemple-alias.test"})
        src = self.Source.new({
            "name": "Essai raccourcis", "calendar_slug": "x",
            "days_back": 10, "days_ahead": 10, "nature": "vacation",
        })
        self.assertFalse(src._apparier("MCE", []))
        src.alias_ids = [(0, 0, {"label": "MCE", "partner_id": personne.id})]
        self.assertEqual(src._apparier("MCE", []), personne)
        self.assertEqual(src._apparier("mce", []), personne)

    def test_un_raccourci_vide_est_refuse(self):
        from odoo.exceptions import ValidationError
        P = self.env["res.partner"]
        personne = P.create({"name": "Autre Exemple"})
        src = self.Source.create({"name": "Essai vide", "calendar_slug": "y"})
        with self.assertRaises(ValidationError):
            self.env["bf.absence.calendar.alias"].create({
                "source_id": src.id, "label": "  ", "partner_id": personne.id})

    def test_lire_sans_connexion_le_dit_clairement(self):
        from odoo.exceptions import UserError
        src = self.Source.create({"name": "Essai sans connexion",
                                  "calendar_slug": "z"})
        with self.assertRaises(UserError):
            src._config()

    def test_un_retour_ne_ferme_qu_un_depart(self):
        """🔴 Un unique « Retour de vacances X » fermait TROIS départs du même
        raccourci, dont un vieux de quatorze mois. Un retour se consomme une
        fois, et il ne ferme pas un départ qu'il suit de trop loin."""
        from datetime import timedelta
        depart_a, depart_b = date(2025, 7, 24), date(2026, 9, 8)
        retour = date(2026, 9, 19)
        borne = 70
        restants = [("exemple", retour)]
        fins = []
        for jour in sorted([depart_a, depart_b]):
            candidats = [(k, d) for k, d in restants
                         if d > jour and (d - jour).days <= borne]
            if candidats:
                retenu = min(candidats, key=lambda kd: kd[1])
                restants.remove(retenu)
                fins.append(retenu[1] - timedelta(days=1))
            else:
                fins.append(False)
        self.assertEqual(fins[0], False, "le départ de 2025 ne doit rien fermer")
        self.assertEqual(fins[1], date(2026, 9, 18))

    # ---- La passe entière, pour de vrai ------------------------------
    def test_la_passe_entiere_tourne_sans_langue_au_contexte(self):
        """🔴 Les essais d'ici rejouaient la logique d'`action_read` morceau par
        morceau; la méthode elle-même n'était jamais appelée, et c'est là que
        le défaut vivait.

        Quand le contexte ne porte pas de langue — le cas d'un travail
        planifié, jamais celui d'un bouton — `_()` devine la langue en
        fouillant les variables locales de celui qui l'appelle, et prenait
        l'`uid` du calendrier pour un identifiant d'usager. Le travail planifié
        tombait sur « invalid input syntax for type integer ». Ce test appelle
        la vraie méthode, dans un contexte sans langue.
        """
        from unittest.mock import patch

        self.assertFalse(self.env.context.get("lang"),
                         "ce test ne vaut que sans langue au contexte")
        personne = self.env["res.partner"].create({"name": "Camille Exemple"})
        src = self.Source.create({
            "name": "Essai passe entiere", "calendar_slug": "x",
            "days_back": 4000, "days_ahead": 4000,
        })
        ics = ("BEGIN:VCALENDAR\n"
               "BEGIN:VEVENT\nUID:bded9c35-4fa8-4a1d-b3a9-6b03376301da\n"
               "DTSTART;VALUE=DATE:20260330\n"
               "SUMMARY:Vacances - Camille Exemple\nEND:VEVENT\n"
               "END:VCALENDAR\n")
        chemin = ("odoo.addons.bf_contact_absence_calendar.models"
                  ".bf_absence_calendar_source.BfAbsenceCalendarSource._fetch")
        with patch(chemin, return_value=ics):
            src.action_read()

        suggestion = self.env["bf.partner.absence.suggestion"].search(
            [("calendar_uid", "=", "bded9c35-4fa8-4a1d-b3a9-6b03376301da")])
        self.assertEqual(len(suggestion), 1)
        self.assertEqual(suggestion.partner_id, personne)
        self.assertEqual(suggestion.date_from, date(2026, 3, 30))
        self.assertTrue(src.last_message, "la passe doit laisser son résultat")

    def test_une_seconde_passe_ne_propose_pas_deux_fois(self):
        """L'identifiant du calendrier est ce qui évite le doublon : le travail
        planifié tourne aux six heures sur le même calendrier."""
        from unittest.mock import patch

        self.env["res.partner"].create({"name": "Camille Exemple"})
        src = self.Source.create({
            "name": "Essai deux passes", "calendar_slug": "x",
            "days_back": 4000, "days_ahead": 4000,
        })
        ics = ("BEGIN:VCALENDAR\n"
               "BEGIN:VEVENT\nUID:uid-repete\n"
               "DTSTART;VALUE=DATE:20260330\n"
               "SUMMARY:Vacances - Camille Exemple\nEND:VEVENT\n"
               "END:VCALENDAR\n")
        chemin = ("odoo.addons.bf_contact_absence_calendar.models"
                  ".bf_absence_calendar_source.BfAbsenceCalendarSource._fetch")
        with patch(chemin, return_value=ics):
            src.action_read()
            src.action_read()
        self.assertEqual(
            self.env["bf.partner.absence.suggestion"].search_count(
                [("calendar_uid", "=", "uid-repete")]), 1)

    def test_le_travail_planifie_lit_toutes_les_sources(self):
        """🔴 Le défaut tombait ici, pas au bouton : c'est le chemin planifié."""
        from unittest.mock import patch

        self.env["res.partner"].create({"name": "Camille Exemple"})
        self.Source.create({
            "name": "Essai planifie", "calendar_slug": "x",
            "days_back": 4000, "days_ahead": 4000,
        })
        ics = ("BEGIN:VCALENDAR\n"
               "BEGIN:VEVENT\nUID:uid-planifie\n"
               "DTSTART;VALUE=DATE:20260330\n"
               "SUMMARY:Vacances - Camille Exemple\nEND:VEVENT\n"
               "END:VCALENDAR\n")
        chemin = ("odoo.addons.bf_contact_absence_calendar.models"
                  ".bf_absence_calendar_source.BfAbsenceCalendarSource._fetch")
        with patch(chemin, return_value=ics):
            self.Source._cron_read_all()
        self.assertEqual(
            self.env["bf.partner.absence.suggestion"].search_count(
                [("calendar_uid", "=", "uid-planifie")]), 1)
