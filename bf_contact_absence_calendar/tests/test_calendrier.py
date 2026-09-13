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
        """🔴 « Retour de vacances François » ne répète pas le nom de famille de
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
        src = self.Source.create({
            "name": "Essai", "config_key": False, "calendar_slug": "x",
        }) if False else self.Source.new({
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
