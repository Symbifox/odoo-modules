# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import unittest
from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.models import MAGIC_COLUMNS
from odoo.tests import TransactionCase, tagged

from odoo.addons.web_calendar_ergonomics.models.web_calendar_ergonomics import (
    CHAMPS_EVENEMENT_EXCLUS, CHAMPS_EVENEMENT_RECOPIES, CHAMPS_JAMAIS, CHAMPS_JAMAIS_PAR_NOM,
    texte_sans_liens_visio,
)

# Écritures d'essai sans pousser vers Nextcloud ni écrire aux invités.
SANS_ENVOI = {"skip_nc_sync": True, "no_mail_to_attendees": True}


@tagged("post_install", "-at_install")
class TestWorkHours(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.E = cls.env["web.calendar.ergonomics"]
        cls.company = cls.env.company
        # Horaire maison : lun.-ven. 8-12 et 13-17, pause du midi exclue.
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Horaire essai",
            "tz": "America/Toronto",
            "company_id": cls.company.id,
            "attendance_ids": [
                (0, 0, {"name": "j%d %s" % (d, p), "dayofweek": str(d), "hour_from": de,
                        "hour_to": a, "day_period": p})
                for d in range(5)
                for de, a, p in ((8, 12, "morning"), (12, 13, "lunch"), (13, 17, "afternoon"))
            ],
        })
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Essai Horaire", "login": "essai.horaire", "tz": "America/Toronto",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Essai Horaire", "user_id": cls.user.id, "resource_type": "user",
            "calendar_id": cls.calendar.id, "tz": "America/Toronto", "company_id": cls.company.id,
        })
        # Semaine du dimanche 2026-11-08 au samedi 2026-11-14, minuit de Toronto (EST, UTC-5).
        cls.debut, cls.fin = "2026-11-08 05:00:00", "2026-11-15 04:59:59"

    def heures(self, tz="America/Toronto", user=None, debut=None, fin=None):
        return self.E.with_user(user or self.user).work_hours(debut or self.debut, fin or self.fin, tz)

    def test_semaine_au_fuseau_de_la_ressource(self):
        regles = self.heures()
        self.assertEqual(len(regles), 10)
        self.assertIn({"daysOfWeek": [1], "startTime": "08:00", "endTime": "12:00"}, regles)
        self.assertIn({"daysOfWeek": [5], "startTime": "13:00", "endTime": "17:00"}, regles)
        # La pause du midi n'est pas une heure de travail.
        self.assertFalse([r for r in regles if r["startTime"] == "12:00"])
        # Ni samedi (6) ni dimanche (0).
        self.assertFalse([r for r in regles if r["daysOfWeek"][0] in (0, 6)])

    def test_converti_dans_le_fuseau_de_la_grille(self):
        # 8 h à Toronto (UTC-5) = 2 h le LENDEMAIN à Auckland (UTC+13) : le jour change.
        regles = self.heures("Pacific/Auckland")
        self.assertIn({"daysOfWeek": [2], "startTime": "02:00", "endTime": "06:00"}, regles)
        self.assertFalse([r for r in regles if r["daysOfWeek"] == [1]], regles)

    def test_coupe_a_minuit(self):
        # 13 h-17 h à Toronto = 23 h 30-3 h 30 à Kolkata (UTC+5:30) : deux règles, une par jour.
        regles = self.heures("Asia/Kolkata")
        self.assertIn({"daysOfWeek": [1], "startTime": "23:30", "endTime": "24:00"}, regles)
        self.assertIn({"daysOfWeek": [2], "startTime": "00:00", "endTime": "03:30"}, regles)

    def test_jour_ferie_retire(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Férié essai", "calendar_id": self.calendar.id,
            "date_from": datetime(2026, 11, 11, 5, 0), "date_to": datetime(2026, 11, 12, 4, 59, 59),
        })
        regles = self.heures()
        self.assertFalse([r for r in regles if r["daysOfWeek"] == [3]], regles)
        self.assertEqual(len(regles), 8)

    def test_semaine_sans_travail_rend_une_liste_vide(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Fermeture", "calendar_id": self.calendar.id,
            "date_from": datetime(2026, 11, 8, 0, 0), "date_to": datetime(2026, 11, 16, 0, 0),
        })
        self.assertEqual(self.heures(), [])

    def test_plage_trop_longue_ou_inverse(self):
        self.assertFalse(self.heures(fin="2026-11-20 04:59:59"))
        self.assertFalse(self.heures(debut=self.fin, fin=self.debut))
        self.assertFalse(self.heures(debut="pas une date"), "une date illisible ne lève pas")

    def test_fuseau_inconnu_retombe_sur_utc(self):
        self.assertEqual(self.heures("Nulle/Part"), self.heures("UTC"))

    def test_portail_rien(self):
        portail = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Portail essai", "login": "portail.essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        self.assertFalse(self.heures(user=portail))

    def test_sans_ressource_horaire_de_la_societe(self):
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Sans ressource", "login": "sans.ressource.essai", "tz": "America/Toronto",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.env["resource.resource"].search([("user_id", "=", autre.id)]).unlink()
        self.company.resource_calendar_id = self.calendar
        self.assertEqual(self.heures(user=autre), self.heures())

    def test_fiche_employe_avant_les_autres_ressources(self):
        if "hr.employee" not in self.env:
            self.skipTest("module RH absent")
        nuit = self.calendar.copy({"name": "Nuit essai", "attendance_ids": [
            (0, 0, {"name": "nuit", "dayofweek": "0", "hour_from": 20, "hour_to": 23, "day_period": "afternoon"})]})
        # Une ressource plus ANCIENNE (prise de rendez-vous) et la fiche d'employé.
        employe = self.env["hr.employee"].create({
            "name": "Essai Horaire", "user_id": self.user.id, "company_id": self.company.id,
            "resource_calendar_id": nuit.id,
        })
        employe.resource_id.tz = "America/Toronto"
        self.assertLess(self.resource.id, employe.resource_id.id)
        self.assertEqual(self.heures(), [{"daysOfWeek": [1], "startTime": "20:00", "endTime": "23:00"}])


@tagged("post_install", "-at_install")
class TestDuplicate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "calendar.event" not in cls.env:
            raise unittest.SkipTest("Agenda absent")
        cls.E = cls.env["web.calendar.ergonomics"]
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Essai Copie", "login": "essai.copie", "email": "essai.copie@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.invite = cls.env["res.partner"].create({"name": "Invité essai", "email": "invite.essai@example.com"})
        cls.etiquette = cls.env["calendar.event.type"].create({"name": "Étiquette essai"})
        # Créé en administrateur au nom du compte d'essai : un événement lié à une fiche
        # y pose une activité, que l'employé de l'essai n'a pas le droit de créer.
        cls.event = cls.env["calendar.event"].with_context(no_mail_to_attendees=True).create({
            "user_id": cls.user.id,
            "name": "Revue essai", "start": datetime(2026, 11, 9, 14), "stop": datetime(2026, 11, 9, 15),
            "partner_ids": [(6, 0, [cls.user.partner_id.id, cls.invite.id])],
            "categ_ids": [(6, 0, [cls.etiquette.id])],
            "res_model_id": cls.env["ir.model"]._get_id("res.partner"),
            "res_id": cls.invite.id,
        })

    def defauts(self, res_model="calendar.event", res_id=None, user=None):
        return self.E.with_user(user or self.user).duplicate_defaults(res_model, res_id or self.event.id)

    def test_valeurs_recopiees(self):
        d = self.defauts()
        self.assertEqual(d["name"], "Revue essai")
        self.assertEqual(d["start"], "2026-11-09 14:00:00")
        self.assertEqual(d["partner_ids"], [(6, 0, self.event.partner_ids.ids)])
        self.assertIn(self.invite.id, d["partner_ids"][0][2])
        self.assertEqual(d["categ_ids"], [(6, 0, [self.etiquette.id])])

    def test_visio_organisateur_et_identifiants_jamais_recopies(self):
        # La salle Talk publique de la rencontre d'origine,
        # son UID, son opportunité et son organisateur restent à elle.
        Event = self.env["calendar.event"]
        vals = {"videocall_location": "https://exemple.invalid/call/salle-essai"}
        # `event_identifier` n'existe que là où un module l'ajoute (pas dans le cœur).
        if "event_identifier" in Event._fields:
            vals["event_identifier"] = "uid-essai@example.com"
        if "opportunity_id" in Event._fields:
            vals["opportunity_id"] = self.env["crm.lead"].create({"name": "Piste essai"}).id
        self.event.write(vals)
        collegue = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Collègue copie", "login": "collegue.copie.essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        d = self.defauts(user=collegue)
        self.assertEqual(d["name"], "Revue essai")
        for nom in ("videocall_location", "videocall_channel_id", "videocall_source", "event_identifier",
                    "access_token", "opportunity_id", "user_id"):
            self.assertNotIn(nom, d)

    def test_champs_copiables_tous_classes(self):
        # Un module installé plus tard qui ajoute un champ `copy=True` à l'Agenda ne doit pas
        # rouvrir la fuite en silence : chaque champ copiable est revu (recopié) ou exclu.
        # Borné à `calendar` et aux modules de Blue Fox : un module tiers
        # (recrutement, synchro Google ou Microsoft) n'a pas à faire échouer l'essai chez
        # qui l'installe ; ses champs restent écartés par la liste revue (assertion finale).
        self.assertFalse(CHAMPS_EVENEMENT_RECOPIES & CHAMPS_EVENEMENT_EXCLUS)
        nos_modules = {"calendar", *self.env["ir.module.module"].search([
            ("state", "=", "installed"), ("author", "ilike", "Blue Fox")]).mapped("name")}
        self.assertIn("web_calendar_ergonomics", nos_modules)
        Event = self.env["calendar.event"]
        non_classes = sorted(
            nom for nom, champ in Event._fields.items()
            if champ.copy and not champ.inherited and nom not in MAGIC_COLUMNS
            and champ.type != "one2many" and champ._module in nos_modules
            and nom not in (CHAMPS_EVENEMENT_RECOPIES | CHAMPS_EVENEMENT_EXCLUS | CHAMPS_JAMAIS
                            | CHAMPS_JAMAIS_PAR_NOM)
        )
        self.assertEqual(non_classes, [], "champs copiables de calendar.event à classer dans "
                         "CHAMPS_EVENEMENT_RECOPIES ou CHAMPS_EVENEMENT_EXCLUS")
        # Et rien hors de la liste revue ne passe, même un champ copiable non classé.
        d = self.defauts()
        self.assertFalse(set(d) - CHAMPS_EVENEMENT_RECOPIES, set(d) - CHAMPS_EVENEMENT_RECOPIES)

    def test_lieu_lien_de_visio_pas_recopie(self):
        # Sur une base réelle, le lieu de la plupart des rencontres à visio EST le lien
        # Talk (public) ; il ne passe pas plus par le lieu que par `videocall_location`.
        event = self.event.with_context(**SANS_ENVOI)
        salle = "https://nextcloud.exemple.invalid/call/y2ngorgz"
        event.write({"videocall_location": salle, "location": salle})
        self.assertNotIn("location", self.defauts())
        # Une salle connue, sans `videocall_location`, avec ou sans « https:// ».
        for lieu in ("https://meet.google.com/abc-defg-hij", "meet.google.com/abc-defg-hij",
                     "https://us02web.zoom.us/j/1234", "https://teams.microsoft.com/l/meetup-join/1234",
                     "https://teams.live.com/meet/1234", "https://meet.jit.si/SalleEssai",
                     "https://whereby.com/salle-essai", "https://nextcloud.exemple.invalid/index.php/call/abcd1234"):
            with self.subTest(lieu=lieu):
                event.write({"videocall_location": False, "location": lieu})
                self.assertNotIn("location", self.defauts())
        # La salle propre à la fiche, quelle qu'elle soit (lien de visio saisi à la main).
        event.write({"videocall_location": "https://visio.exemple.invalid/r/1234",
                     "location": "https://visio.exemple.invalid/r/1234"})
        self.assertNotIn("location", self.defauts())
        # Une salle ET un lien : la salle reste. Un lieu ordinaire, même une adresse, reste.
        event.write({"videocall_location": False, "location": "Salle B - https://meet.google.com/abc-defg-hij"})
        self.assertEqual(self.defauts()["location"], "Salle B")
        event.write({"location": "https://exemple.invalid/plan-des-lieux"})
        self.assertEqual(self.defauts()["location"], "https://exemple.invalid/plan-des-lieux")

    def test_description_sans_lien_de_visio(self):
        # les liens de visio sortent de la description (le lien et ce qui
        # le porte), le reste du HTML est gardé.
        salle = "https://nextcloud.exemple.invalid/call/y2ngorgz"
        self.event.with_context(**SANS_ENVOI).write({"videocall_location": salle, "description": (
            "<p>Ordre du jour : budget.</p>"
            f'<p>Rejoindre : <a href="{salle}">la salle Talk</a> ou https://meet.google.com/abc-defg-hij.</p>'
            "<p>Copie : https%3A%2F%2Fteams.microsoft.com%2Fl%2Fmeetup-join%2F1234 fin</p>"
            '<p>Plan : <a href="https://exemple.invalid/plan">plan des lieux</a> &amp; <b>stationnement</b></p>'
        )})
        desc = self.defauts()["description"]
        for trace in ("/call/", "y2ngorgz", "la salle Talk", "meet.google", "teams.microsoft"):
            self.assertNotIn(trace, desc)
        for garde in ("budget", "Rejoindre : ", " fin", 'href="https://exemple.invalid/plan"',
                      "plan des lieux", "&amp;", "<b>stationnement</b>"):
            self.assertIn(garde, desc)
        self.assertEqual(desc.count("<p>"), 4, desc)
        self.assertEqual(desc.count("</p>"), 4, desc)
        # La salle propre à la fiche, quelle qu'elle soit (lien saisi à la main), sort aussi.
        propre = "https://visio.exemple.invalid/r/1234"
        self.event.with_context(**SANS_ENVOI).write({"videocall_location": propre, "description": (
            f'<p>Budget.</p><p><a href="{propre}">Entrer</a> ou {propre}</p>')})
        desc = self.defauts()["description"]
        self.assertNotIn("visio.exemple.invalid", desc)
        self.assertNotIn("Entrer", desc)
        self.assertIn("<p>Budget.</p>", desc)
        # Une description en texte simple : l'adresse sort, le texte reste.
        self.assertEqual(texte_sans_liens_visio(f"Salle B / {salle} (code 123)."), "Salle B /  (code 123).")
        self.assertEqual(texte_sans_liens_visio("Voir https://exemple.invalid/doc."), "Voir https://exemple.invalid/doc.")

    def test_reservation_sans_visio_ni_organisateur(self):
        # même classe sur les réservations (leur vue calendrier).
        if "resource.booking" not in self.env:
            self.skipTest("Réservations (resource_booking) absentes")
        Resa = self.env["resource.booking"]
        salle = "https://nextcloud.exemple.invalid/index.php/call/dmdmgzi3"
        vals = {
            "type_id": self.env["resource.booking.type"].create({"name": "Type essai"}).id,
            "partner_ids": [(6, 0, [self.invite.id])], "name": "Réservation essai",
            "user_id": self.user.id,
        }
        for nom, valeur in (("videocall_location", salle), ("location", salle),
                            ("description", f'<p>Préparer le budget.</p><p><a href="{salle}">Rejoindre</a></p>')):
            if nom in Resa._fields:
                vals[nom] = valeur
        resa = Resa.with_context(**SANS_ENVOI).create(vals)
        # copy() les recopierait : l'essai discrimine.
        self.assertEqual(resa.copy_data()[0].get("user_id"), self.user.id)
        d = self.defauts("resource.booking", resa.id, user=self.env.ref("base.user_admin"))
        self.assertEqual(d["type_id"], resa.type_id.id)
        for nom in ("videocall_location", "user_id", "location"):
            self.assertNotIn(nom, d)
        if "description" in d:
            self.assertIn("Préparer le budget.", d["description"])
            self.assertNotIn("/call/", d["description"])
            self.assertNotIn("Rejoindre", d["description"])

    def test_celui_qui_duplique_est_invite(self):
        # `partner_ids` recopié remplace le défaut qui invitait l'usager.
        collegue = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Collègue invité", "login": "collegue.invite.essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        invites = self.defauts(user=collegue)["partner_ids"][0][2]
        self.assertIn(collegue.partner_id.id, invites)
        self.assertLessEqual(set(self.event.partner_ids.ids), set(invites))
        self.assertEqual(len(invites), len(set(invites)))
        # Déjà invité : pas deux fois.
        self.assertEqual(self.defauts()["partner_ids"], [(6, 0, self.event.partner_ids.ids)])

    def test_champs_jamais_recopies_par_nom(self):
        # visio, jeton, UID, sous-dossier Nextcloud, sur tout modèle.
        E = self.E.with_user(self.user)
        trouves = [(nom_modele, champ) for nom_modele in self.env.registry
                   for champ in self.env[nom_modele]._fields.values() if champ.name in CHAMPS_JAMAIS_PAR_NOM]
        self.assertTrue(trouves)
        for nom_modele, champ in trouves:
            self.assertTrue(E._duplicate_exclu(self.env[nom_modele], champ), (nom_modele, champ.name))
        if "project.task" in self.env and "nc_task_subfolder" in self.env["project.task"]._fields:
            self.assertIn(("project.task", "nc_task_subfolder"), [(m, c.name) for m, c in trouves])

    def test_champ_reserve_a_un_groupe(self):
        # copy_data() ignore `groups=` ; un employé ne reçoit pas un champ réservé aux
        # administrateurs, que read() lui refuserait.
        if "discuss.channel" not in self.env:
            self.skipTest("Discussion absente")
        canal = self.env["discuss.channel"].create({
            "name": "Canal essai", "channel_type": "channel", "sfu_server_url": "https://sfu.exemple.invalid"})
        self.assertTrue(self.env["discuss.channel"]._fields["sfu_server_url"].groups)
        d = self.defauts("discuss.channel", canal.id)
        self.assertEqual(d["name"], "Canal essai")
        self.assertNotIn("sfu_server_url", d)
        # L'administrateur, lui, le reçoit : l'essai discrimine.
        d = self.defauts("discuss.channel", canal.id, user=self.env.ref("base.user_admin"))
        self.assertEqual(d["sfu_server_url"], "https://sfu.exemple.invalid")

    def test_liens_vers_une_rencontre_exclus(self):
        # Un many2one vers un événement, un OdJ ou un compte rendu n'est jamais recopié,
        # quel que soit le modèle.
        E = self.E.with_user(self.user)
        liens = [(nom_modele, champ) for nom_modele in self.env.registry
                 for champ in self.env[nom_modele]._fields.values()
                 if champ.type == "many2one"
                 and champ.comodel_name in ("calendar.event", "meeting.agenda", "meeting.record")]
        self.assertTrue(liens)
        for nom_modele, champ in liens:
            self.assertTrue(E._duplicate_exclu(self.env[nom_modele], champ), (nom_modele, champ.name))

    def test_activite_et_conge_refuses(self):
        # Une activité naît de sa fiche, un congé de son circuit : refus propre.
        activite = self.env["mail.activity"].search([], limit=1)
        with self.assertRaises(UserError):
            self.defauts("mail.activity", activite.id or 1)
        with self.assertRaises(UserError):
            self.defauts("hr.leave", 1)

    def test_rien_n_est_cree(self):
        avant = self.env["calendar.event"].search_count([("name", "=", "Revue essai")])
        self.defauts()
        self.assertEqual(self.env["calendar.event"].search_count([("name", "=", "Revue essai")]), avant)

    def test_exclus(self):
        d = self.defauts()
        for nom in ("res_id", "res_model", "res_model_id", "id", "attendee_ids", "message_ids",
                    "activity_ids", "recurrency", "recurrence_id", "event_tz", "rrule"):
            self.assertNotIn(nom, d)
        self.assertFalse([n for n in d if self.env["calendar.event"]._fields[n].type == "one2many"])
        # Hors de l'Agenda (pas de liste revue), le lien d'origine reste exclu.
        piece = self.env["ir.attachment"].create({
            "name": "piece-essai.txt", "raw": b"essai", "res_model": "res.partner", "res_id": self.invite.id})
        d = self.defauts("ir.attachment", piece.id)
        self.assertEqual(d["name"], "piece-essai.txt")
        for nom in ("res_id", "res_model", "id", "datas", "raw"):
            self.assertNotIn(nom, d)

    def test_occurrence_d_une_serie_copiee_seule(self):
        serie = self.env["calendar.event"].with_user(self.user).with_context(no_mail_to_attendees=True).create({
            "name": "Série essai", "start": datetime(2026, 11, 9, 14), "stop": datetime(2026, 11, 9, 15),
            "recurrency": True, "rrule_type": "weekly", "mon": True, "end_type": "count", "count": 3,
            "event_tz": "America/Toronto",
        })
        occurrence = serie.recurrence_id.calendar_event_ids[1]
        d = self.defauts(res_id=occurrence.id)
        self.assertNotIn("recurrency", d)
        self.assertNotIn("count", d)
        self.assertNotIn("event_tz", d)

    def test_portail_refuse(self):
        portail = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Portail copie", "login": "portail.copie.essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        # Le portail lit ET crée ici : seule la garde « utilisateur interne » le refuse.
        self.env["ir.model.access"].create({
            "name": "portail essai", "model_id": self.env["ir.model"]._get_id("calendar.event.type"),
            "group_id": self.env.ref("base.group_portal").id, "perm_read": True, "perm_create": True,
        })
        with self.assertRaises(AccessError):
            self.defauts("calendar.event.type", self.etiquette.id, user=portail)

    def test_sans_droit_de_creer_refuse(self):
        # Un employé lit les pays sans pouvoir en créer : « Dupliquer » ne sert pas à ce qu'on ne peut pas créer.
        pays = self.env.ref("base.ca")
        pays.with_user(self.user).check_access("read")
        with self.assertRaises(AccessError):
            self.defauts("res.country", pays.id)

    def test_lignes_one2many_jamais_recopiees(self):
        # Les présences d'un horaire sont recopiées par copy() : ici, elles ne doivent pas l'être.
        horaire = self.env["resource.calendar"].create({"name": "Horaire copie", "attendance_ids": [
            (0, 0, {"name": "lun", "dayofweek": "0", "hour_from": 8, "hour_to": 12, "day_period": "morning"})]})
        self.assertIn("attendance_ids", horaire.copy_data()[0])
        # Ni recopiées ni même lues (copy_data des lignes jamais appelé).
        Lignes = type(self.env["resource.calendar.attendance"])
        with patch.object(Lignes, "copy_data", side_effect=AssertionError("lignes lues")):
            d = self.defauts("resource.calendar", horaire.id, user=self.env.ref("base.user_admin"))
        self.assertEqual(d["name"], horaire.copy_data()[0]["name"])
        self.assertNotIn("attendance_ids", d)

    def test_modele_ou_fiche_inconnus(self):
        self.assertEqual(self.defauts("modele.inexistant", 1), {})
        self.assertEqual(self.defauts(res_id=10**9), {})
