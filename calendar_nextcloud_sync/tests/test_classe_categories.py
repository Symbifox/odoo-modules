# -*- coding: utf-8 -*-
"""CLASS et CATEGORIES entre Odoo et Nextcloud (2.26.0).

Avant de partager un agenda par lien, la
confidentialité doit tenir dans les deux sens : jusqu'à la 2.25, un événement
rendu privé dans Nextcloud revenait « sans confidentialité » dans Odoo, et le
rendre privé dans Odoo ne changeait rien dans Nextcloud. Les étiquettes
d'agenda (`categ_ids`), elles, ne voyageaient pas du tout ; les plages typées
s'appuient sur elles.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.calendar_nextcloud_sync.models import caldav_backend as cb

from .test_retouche import SERIE, SIMPLE, BaseRetouche, _prop, _vevents

CONFIG = "odoo.addons.calendar_nextcloud_sync.models.nextcloud_sync_config"


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestClasseCategories(BaseRetouche):

    def _etiquette(self, nom):
        # Trouver ou créer : la base peut déjà porter ces noms (types de plage
        # de bf_calendar_block, étiquettes venues de Nextcloud).
        Type = self.env["calendar.event.type"].with_context(active_test=False)
        return Type.search([("name", "=", nom)], limit=1) or Type.create({"name": nom})

    def _donnees_simple(self, **autres):
        data = {
            "uid": "simple-n232",
            "summary": "PROD",
            "start": "2026-10-20 13:00:00",
            "end": "2026-10-20 16:00:00",
            "allday": False,
            "location": "",
            "description": "",
            "attendees": [],
            "alarms": None,
            "status": None,
            "etag": "v0",
            "href": "/remote.php/dav/calendars/alice/perso/simple-n232.ics",
        }
        data.update(autres)
        return data

    def _importer(self, **autres):
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_simple(**autres), self.config.id)
        return self.env["calendar.event"].browse(res["id"])

    # -- lecture du .ics ----------------------------------------------------

    def test_decouper_liste_respecte_la_virgule_echappee(self):
        self.assertEqual(cb.decouper_liste("Production,Suivi\\, client, ,Rattrapage"),
                         ["Production", "Suivi, client", "Rattrapage"])
        self.assertEqual(cb.decouper_liste(""), [])

    def test_lecture_de_class_et_de_plusieurs_categories(self):
        texte = SIMPLE.replace(
            "STATUS:TENTATIVE",
            "STATUS:TENTATIVE\r\nCLASS:private\r\nCATEGORIES:Production,Suivi\\, client\r\n"
            "CATEGORIES:Rattrapage")
        data = self.config._parse_ics_vevent(texte)
        self.assertEqual(data["classe"], "PRIVATE")
        self.assertEqual(data["categories"], ["Production", "Suivi, client", "Rattrapage"])

    def test_ics_sans_class_ni_categories_n_en_dit_rien(self):
        data = self.config._parse_ics_vevent(SIMPLE)
        self.assertIsNone(data["classe"])
        self.assertIsNone(data["categories"])

    def test_class_d_une_alarme_n_est_pas_celle_de_l_evenement(self):
        texte = SIMPLE.replace("TRIGGER:-PT15M", "TRIGGER:-PT15M\r\nCLASS:PRIVATE")
        self.assertIsNone(self.config._parse_ics_vevent(texte)["classe"])

    # -- import -------------------------------------------------------------

    def test_import_private(self):
        event = self._importer(classe="PRIVATE", categories=["Production"])
        self.assertEqual(event.privacy, "private")
        self.assertEqual(event.categ_ids.mapped("name"), ["Production"])

    def test_import_public(self):
        self.assertEqual(self._importer(classe="PUBLIC").privacy, "public")

    def test_confidential_neuf_devient_private(self):
        # CONFIDENTIAL = « montrer l'occupation seulement » : `confidential`
        # d'Odoo montrerait tout aux internes.
        self.assertEqual(self._importer(classe="CONFIDENTIAL").privacy, "private")

    def test_confidential_garde_la_nuance_d_odoo(self):
        event = self._importer()
        event.with_context(skip_nc_sync=True).write({"privacy": "confidential"})
        self._importer(classe="CONFIDENTIAL", etag="v1")
        self.assertEqual(event.privacy, "confidential")

    def test_class_absente_ne_touche_pas(self):
        event = self._importer(classe="PRIVATE", categories=["Production"])
        self._importer(etag="v1", summary="PROD renommée")
        self.assertEqual(event.name, "PROD renommée")
        self.assertEqual(event.privacy, "private")
        self.assertEqual(event.categ_ids.mapped("name"), ["Production"])

    def test_class_relue_change_odoo(self):
        event = self._importer(classe="PRIVATE")
        self._importer(classe="PUBLIC", etag="v1")
        self.assertEqual(event.privacy, "public")

    def test_etiquette_existante_reprise_sans_doublon(self):
        production = self._etiquette("Production")
        event = self._importer(categories=["production", "PRODUCTION"])
        self.assertEqual(event.categ_ids, production)
        self.assertEqual(self.env["calendar.event.type"].search_count(
            [("name", "=ilike", "production")]), 1)

    def test_joker_d_ilike_ne_prend_pas_une_autre_etiquette(self):
        autre = self._etiquette("SuiviXA")
        event = self._importer(categories=["Suivi_A"])
        self.assertNotEqual(event.categ_ids, autre)
        self.assertEqual(event.categ_ids.mapped("name"), ["Suivi_A"])

    def test_import_ne_repousse_rien(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        self._importer(classe="PRIVATE", categories=["Production"])
        self.assertFalse(magasin.puts)

    # -- poussée complète ---------------------------------------------------

    def test_poussee_complete_porte_class_et_categories(self):
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "Concentration",
            "start": datetime(2026, 10, 21, 13, 0),
            "stop": datetime(2026, 10, 21, 15, 0),
            "privacy": "private",
            "categ_ids": [(6, 0, (self._etiquette("Concentration")
                                  | self._etiquette("Suivi, client")).ids)],
            "x_nc_uid": "odoo-n232",
            "x_nc_calendar_id": self.config.id,
        })
        (bloc,) = _vevents(self.backend.build_ics(event))
        self.assertEqual(_prop(bloc, "CLASS"), ["CLASS:PRIVATE"])
        (categories,) = _prop(bloc, "CATEGORIES")
        self.assertEqual(sorted(cb.decouper_liste(cb.valeur_prop(categories))),
                         ["Concentration", "Suivi, client"])

    def test_poussee_sans_etiquette_n_ecrit_pas_categories(self):
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "Sans étiquette",
            "start": datetime(2026, 10, 21, 13, 0),
            "stop": datetime(2026, 10, 21, 15, 0),
            "x_nc_uid": "odoo-n232-b",
            "x_nc_calendar_id": self.config.id,
        })
        self.assertNotIn("CATEGORIES", self.backend.build_ics(event))

    # -- retouche d'un événement venu de Nextcloud ---------------------------

    def test_rendre_prive_dans_odoo_repart_en_class(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.write({"privacy": "private"})
        self.assertEqual(len(magasin.puts), 1)
        (bloc,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(bloc, "CLASS"), ["CLASS:PRIVATE"])
        # Le reste de la ressource n'a pas bougé.
        self.assertEqual(_prop(bloc, "STATUS"), ["STATUS:TENTATIVE"])
        self.assertIn("X-MOZ-GENERATION:3", magasin.puts[0][1])

    def test_retirer_la_confidentialite_retire_class(self):
        texte = SIMPLE.replace("STATUS:TENTATIVE", "STATUS:TENTATIVE\r\nCLASS:PRIVATE")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.with_context(skip_nc_sync=True).write({"privacy": "private"})
        event.write({"privacy": False})
        self.assertEqual(len(magasin.puts), 1)
        (bloc,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(bloc, "CLASS"), [])

    def test_etiquettes_retouchees_en_une_seule_ligne(self):
        texte = SIMPLE.replace("STATUS:TENTATIVE",
                               "STATUS:TENTATIVE\r\nCATEGORIES:Ancienne\r\nCATEGORIES:Autre")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.write({"categ_ids": [(6, 0, self._etiquette("Rattrapage").ids)]})
        (bloc,) = _vevents(magasin.puts[-1][1])
        self.assertEqual(_prop(bloc, "CATEGORIES"), ["CATEGORIES:Rattrapage"])
        event.write({"categ_ids": [(5, 0, 0)]})
        (bloc,) = _vevents(magasin.puts[-1][1])
        self.assertEqual(_prop(bloc, "CATEGORIES"), [])

    def test_renommer_ne_touche_ni_class_ni_categories(self):
        texte = SIMPLE.replace("STATUS:TENTATIVE",
                               "STATUS:TENTATIVE\r\nCLASS:CONFIDENTIAL\r\nCATEGORIES:Garder")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.write({"name": "Titre changé"})
        (bloc,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(bloc, "CLASS"), ["CLASS:CONFIDENTIAL"])
        self.assertEqual(_prop(bloc, "CATEGORIES"), ["CATEGORIES:Garder"])

    # -- séries -------------------------------------------------------------

    def test_empreinte_de_serie_inchangee(self):
        # Les ajouter à l'empreinte aurait recréé toutes les séries déjà lues.
        Event = self.env["calendar.event"]
        self.assertEqual(
            Event._bf_nc_signature(self._donnees_serie()),
            Event._bf_nc_signature(self._donnees_serie(classe="PRIVATE",
                                                       categories=["Production"])))

    def test_serie_inchangee_prend_class_sur_place(self):
        base, occurrences = self._serie()
        ids = sorted(occurrences.ids)
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v1", classe="PRIVATE", categories=["Statutaire"]),
            self.config.id)
        self.assertEqual(res["action"], "updated")
        self.assertEqual(sorted(base.recurrence_id.calendar_event_ids.ids), ids)
        self.assertEqual(set(occurrences.mapped("privacy")), {"private"})
        self.assertEqual(set(occurrences.mapped("categ_ids.name")), {"Statutaire"})

    def test_serie_creee_avec_class_la_donne_a_ses_occurrences(self):
        _base, occurrences = self._serie(classe="PRIVATE", categories=["Statutaire"])
        self.assertEqual(len(occurrences), 6)
        self.assertEqual(set(occurrences.mapped("privacy")), {"private"})
        self.assertTrue(all(o.categ_ids.mapped("name") == ["Statutaire"] for o in occurrences))

    def test_exception_porte_sa_propre_classe(self):
        base, occurrences = self._serie(classe="PUBLIC")
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        exception = {
            "uid": "serie-essai", "summary": "Rencontre statutaire",
            "start": "2026-10-12 20:00:00", "end": "2026-10-12 21:00:00",
            "allday": False, "recurrence_id": datetime(2026, 10, 12, 20, 0),
            "recurrence_id_allday": False, "status": None,
            "classe": "PRIVATE", "categories": ["Personnel"],
        }
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v2", classe="PUBLIC", overrides=[exception]),
            self.config.id)
        self.assertEqual(lundi.privacy, "private")
        self.assertEqual(lundi.categ_ids.mapped("name"), ["Personnel"])
        autres = base.recurrence_id.calendar_event_ids - lundi
        self.assertEqual(set(autres.mapped("privacy")), {"public"})

    # -- cas limites -----------------------------------------------------------

    def test_t1_confidential_sur_un_evenement_public_devient_private(self):
        event = self._importer(classe="PUBLIC")
        self._importer(classe="CONFIDENTIAL", etag="v1")
        self.assertEqual(event.privacy, "private")

    def test_t1_confidential_sur_un_evenement_sans_confidentialite(self):
        event = self._importer()
        self.assertFalse(event.privacy)
        self._importer(classe="CONFIDENTIAL", etag="v1")
        self.assertEqual(event.privacy, "private")

    def test_m2_classe_inconnue_vaut_private(self):
        self.assertEqual(self._importer(classe="X-MAISON-SECRET").privacy, "private")

    def test_m6_etiquette_a_barre_oblique_et_joker(self):
        oblique = self._etiquette("A\\B")
        joker = self._etiquette("SuiviXA")
        event = self._importer(categories=["a\\b", "Suivi_A"])
        self.assertIn(oblique, event.categ_ids)
        self.assertNotIn(joker, event.categ_ids)
        self.assertEqual(len(event.categ_ids), 2)

    def test_m6_nfc_et_longueur(self):
        long = "É" * 80
        compose = "e\u0301tiquette"  # « étiquette » en forme décomposée
        event = self._importer(categories=[long, compose])
        self.assertIn(long, event.categ_ids.mapped("name"))
        self.assertEqual(self.env["calendar.event"]._bf_nc_etiquettes(["étiquette"]),
                         event.categ_ids.filtered(lambda t: t.name != long))

    def test_m1_rattrapage_d_un_etag_inchange(self):
        event = self._importer()
        donnees = self._donnees_simple(etag="v0", classe="PRIVATE", categories=["Production"])
        reponse = SimpleNamespace(status_code=207, text="", headers={})
        with patch(CONFIG + ".requests.request", return_value=reponse), \
                patch.object(type(self.config), "_parse_report_response", return_value=[donnees]), \
                patch.object(type(self.env["calendar.event"]), "create_from_nextcloud",
                             side_effect=AssertionError("relecture complète interdite")):
            self.config.action_pull_from_nextcloud()
        self.assertEqual(event.privacy, "private")
        self.assertEqual(event.categ_ids.mapped("name"), ["Production"])
        # Éteint : la condition ne tient plus.
        self.assertFalse(event._bf_nc_rattraper_classe(donnees))

    def test_m1_rattrapage_ne_touche_pas_un_evenement_deja_prive(self):
        event = self._importer(classe="PRIVATE")
        self.assertFalse(event._bf_nc_rattraper_classe(self._donnees_simple(classe="CONFIDENTIAL")))
        self.assertEqual(event.privacy, "private")

    def test_m4_serie_recreee_sans_class_garde_sa_confidentialite(self):
        base, _occ = self._serie(classe="PRIVATE", categories=["Statutaire"])
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v5", summary="Statutaire renommée"), self.config.id)
        nouvelle = self.env["calendar.event"].browse(res["id"]).recurrence_id.calendar_event_ids
        self.assertEqual(set(nouvelle.mapped("privacy")), {"private"})
        self.assertEqual(set(nouvelle.mapped("categ_ids.name")), {"Statutaire"})

    def test_c3_exception_sans_class_herite_du_maitre_prive(self):
        base, occurrences = self._serie(classe="PRIVATE")
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        exception = {
            "uid": "serie-essai", "summary": "Rencontre statutaire",
            "start": "2026-10-12 20:00:00", "end": "2026-10-12 21:00:00",
            "allday": False, "recurrence_id": datetime(2026, 10, 12, 20, 0),
            "recurrence_id_allday": False, "status": None, "classe": None, "categories": None,
        }
        donnees = self._donnees_serie(etag="v2", classe="PRIVATE", overrides=[exception])
        self.env["calendar.event"].create_from_nextcloud(donnees, self.config.id)
        # Second tour, C3 : muette, elle hérite (Nextcloud la cache aussi).
        self.assertEqual(set(base.recurrence_id.calendar_event_ids.mapped("privacy")), {"private"})
        # Converge : une seconde lecture identique n'écrit plus rien.
        with patch.object(type(lundi), "write", side_effect=AssertionError("écriture")) as ecrire:
            self.env["calendar.event"]._bf_nc_classe_serie(
                base.recurrence_id, donnees, [exception], {})
        ecrire.assert_not_called()

    def test_m5_occurrence_confidential_gardee_par_son_exception(self):
        base, occurrences = self._serie(classe="PUBLIC")
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.with_context(skip_nc_sync=True).write({"privacy": "confidential"})
        exception = {
            "uid": "serie-essai", "summary": "Rencontre statutaire",
            "start": "2026-10-12 20:00:00", "end": "2026-10-12 21:00:00",
            "allday": False, "recurrence_id": datetime(2026, 10, 12, 20, 0),
            "recurrence_id_allday": False, "status": None, "classe": "CONFIDENTIAL",
        }
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v2", classe="PUBLIC", overrides=[exception]), self.config.id)
        self.assertEqual(lundi.privacy, "confidential")

    def _serie_nextcloud(self, texte):
        magasin = self._magasin(**{self.url_serie: texte})
        base, occurrences = self._serie()
        return magasin, base, occurrences

    def test_m2_serie_rendue_privee_pour_tous_part_en_class(self):
        magasin, base, occurrences = self._serie_nextcloud(SERIE)
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.write({"privacy": "private", "recurrence_update": "all_events"})
        self.assertEqual(set(base.recurrence_id.calendar_event_ids.mapped("privacy")), {"private"})
        self.assertEqual(len(magasin.puts), 1)
        (maitre,) = _vevents(magasin.puts[0][1])
        self.assertEqual(_prop(maitre, "CLASS"), ["CLASS:PRIVATE"])
        self.assertEqual(_prop(maitre, "RRULE"), ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6"])
        porteur = base.recurrence_id.calendar_event_ids.filtered("x_caldav_href")
        self.assertNotEqual(porteur.x_nc_serie_sig, "a-recreer")

    def test_m2_serie_avec_exception_toutes_les_vevent_retouchees(self):
        texte = SERIE.replace("END:VEVENT\nEND:VCALENDAR",
            "END:VEVENT\nBEGIN:VEVENT\nUID:serie-essai\nRECURRENCE-ID;TZID=America/Toronto:20261012T160000\n"
            "DTSTART;TZID=America/Toronto:20261013T153000\nDTEND;TZID=America/Toronto:20261013T163000\n"
            "SUMMARY:Rencontre statutaire\nCATEGORIES:Vieille\nEND:VEVENT\nEND:VCALENDAR")
        magasin, base, occurrences = self._serie_nextcloud(texte)
        etiquette = self._etiquette("Statutaire")
        occurrences[0].write({"categ_ids": [(6, 0, etiquette.ids)], "recurrence_update": "all_events"})
        blocs = _vevents(magasin.puts[-1][1])
        self.assertEqual(len(blocs), 2)
        for bloc in blocs:
            self.assertEqual(_prop(bloc, "CATEGORIES"), ["CATEGORIES:Statutaire"])

    def test_t2_occurrence_rendue_privee_devient_exception_avec_class(self):
        magasin, base, occurrences = self._serie_nextcloud(SERIE.replace(
            "RRULE:", "CLASS:CONFIDENTIAL\nRRULE:"))
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.write({"name": "Statutaire du lundi"})
        blocs = _vevents(magasin.puts[-1][1])
        self.assertEqual(len(blocs), 2)
        # Copiée du maître, l'exception garde sa CLASS tant que privacy n'est pas touché.
        self.assertEqual(_prop(blocs[1], "CLASS"), ["CLASS:CONFIDENTIAL"])
        lundi.write({"privacy": "private"})
        blocs = _vevents(magasin.puts[-1][1])
        self.assertEqual(_prop(blocs[1], "CLASS"), ["CLASS:PRIVATE"])
        self.assertEqual(_prop(blocs[0], "CLASS"), ["CLASS:CONFIDENTIAL"])
        lundi.write({"privacy": False})
        blocs = _vevents(magasin.puts[-1][1])
        # Second tour, C3 : sous un maître qui n'est pas PUBLIC, le retrait se dit
        # par un CLASS:PUBLIC explicite (muette, l'exception hériterait).
        self.assertEqual(_prop(blocs[1], "CLASS"), ["CLASS:PUBLIC"])

    # -- écritures de série ------------------------------------------------------

    def test_c1_rattrapage_n_abaisse_pas_la_confidentialite(self):
        event = self._importer()
        event.with_context(skip_nc_sync=True).write({"privacy": "private"})
        donnees = self._donnees_simple(classe="PUBLIC", categories=["Production"])
        self.assertTrue(event._bf_nc_rattraper_classe(donnees))
        self.assertEqual(event.privacy, "private")
        self.assertEqual(event.categ_ids.mapped("name"), ["Production"])

    def test_c1_rattrapage_de_serie_n_abaisse_pas(self):
        base, occurrences = self._serie()
        occurrences.with_context(skip_nc_sync=True).write({"privacy": "private"})
        donnees = self._donnees_serie(classe="PUBLIC", categories=["Statutaire"])
        base._bf_nc_rattraper_classe(donnees)
        self.assertEqual(set(occurrences.mapped("privacy")), {"private"})
        self.assertEqual(set(occurrences.mapped("categ_ids.name")), {"Statutaire"})

    def test_c5_rattrapage_rend_faux_sans_ecriture(self):
        base, _occ = self._serie(classe="PRIVATE")
        exception = {
            "uid": "serie-essai", "summary": "Rencontre statutaire",
            "start": "2026-10-05 20:00:00", "end": "2026-10-05 21:00:00",
            "allday": False, "recurrence_id": datetime(2026, 10, 5, 20, 0),
            "recurrence_id_allday": False, "classe": "PUBLIC"}
        porteur = base.recurrence_id.calendar_event_ids.filtered("x_caldav_href")
        porteur.with_context(skip_nc_sync=True).write({"privacy": "public"})
        donnees = self._donnees_serie(classe="PRIVATE", overrides=[exception])
        # Le porteur répond à une exception PUBLIC : rien à écrire, donc faux.
        self.assertFalse(porteur._bf_nc_rattraper_classe(donnees))

    def test_c2_plusieurs_fiches_prennent_l_ancien_chemin(self):
        magasin, base, occurrences = self._serie_nextcloud(SERIE)
        occurrences[:2].write({"privacy": "private", "recurrence_update": "all_events"})
        self.assertFalse([put for put in magasin.puts if "CLASS:PRIVATE" in put[1]])

    def test_c10_ecriture_mixte_marque_et_pousse_class(self):
        magasin, base, occurrences = self._serie_nextcloud(SERIE)
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.write({"privacy": "private", "name": "Statutaire renommée",
                     "recurrence_update": "all_events"})
        (maitre,) = _vevents(magasin.puts[-1][1])
        self.assertEqual(_prop(maitre, "CLASS"), ["CLASS:PRIVATE"])
        porteur = base.recurrence_id.with_context(active_test=False) \
            .calendar_event_ids.filtered("x_caldav_href")
        self.assertEqual(porteur.x_nc_serie_sig, "a-recreer")

    def test_c9_poussee_eteinte_ancien_chemin(self):
        magasin, base, occurrences = self._serie_nextcloud(SERIE)
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.push_via_caldav", "0")
        occurrences[1].write({"privacy": "private", "recurrence_update": "all_events"})
        self.assertFalse(magasin.puts)
        porteur = base.recurrence_id.with_context(active_test=False) \
            .calendar_event_ids.filtered("x_caldav_href")
        self.assertEqual(porteur.x_nc_serie_sig, "a-recreer")

    def test_c7_recreation_prend_une_occurrence_qui_suit_la_regle(self):
        base, occurrences = self._serie()
        porteur = occurrences.filtered("x_caldav_href")
        porteur.with_context(skip_nc_sync=True).write({"privacy": "private", "follow_recurrence": False})
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v7", summary="Statutaire renommée"), self.config.id)
        nouvelle = self.env["calendar.event"].browse(res["id"]).recurrence_id.calendar_event_ids
        self.assertNotIn("private", nouvelle.mapped("privacy"))

    # -- heure et confidentialité ensemble ---------------------------------------

    def test_b1_heure_et_confidentialite_pour_tous_ne_poussent_rien(self):
        texte = SERIE.replace("END:VEVENT\nEND:VCALENDAR",
            "END:VEVENT\nBEGIN:VEVENT\nUID:serie-essai\nRECURRENCE-ID;TZID=America/Toronto:20261019T160000\n"
            "DTSTART;TZID=America/Toronto:20261019T160000\nDTEND;TZID=America/Toronto:20261019T170000\n"
            "SUMMARY:Rencontre statutaire\nCLASS:PRIVATE\nEND:VEVENT\nEND:VCALENDAR")
        magasin, base, occurrences = self._serie_nextcloud(texte)
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        self.assertNotEqual(lundi, occurrences[0])
        lundi.write({"privacy": "confidential", "start": datetime(2026, 10, 12, 21, 0),
                     "stop": datetime(2026, 10, 12, 22, 0), "recurrence_update": "all_events"})
        # Aucune ressource n'est réécrite : l'exception du 19 garde sa CLASS:PRIVATE.
        self.assertFalse(magasin.puts)
        self.assertIn("CLASS:PRIVATE", magasin.ressources[self.url_serie])
