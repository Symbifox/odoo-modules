"""Le banc éprouve ce qui casse, pas ce qui marche déjà.

Chaque essai porte une valeur qui ferait passer une implémentation naïve :
un événement d'autrui, une tâche close mais en retard, une échéance dont le
jour n'est pas le même en UTC et à Montréal, un identifiant périmé.
"""

from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestAgendaMobile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Banc Agenda",
            "login": "banc.agenda.mobile",
            "email": "banc.agenda.mobile@example.org",
            # Montréal à dessein : le fuseau du compte doit départager le jour
            # d'une échéance, et il diffère d'UTC toute l'année.
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("project.group_project_user").id,
            ])],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Banc Autre",
            "login": "banc.agenda.autre",
            "email": "banc.agenda.autre@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.Event = cls.env["calendar.event"]
        cls.Task = cls.env["project.task"]
        cls.base = datetime(2026, 10, 5, 12, 0, 0)

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------

    def test_fenetre_refuse_trop_large(self):
        with self.assertRaises(UserError):
            self.Event._mobile_window("2026-01-01 00:00:00", "2026-06-01 00:00:00")

    def test_fenetre_refuse_ordre_inverse(self):
        with self.assertRaises(UserError):
            self.Event._mobile_window("2026-02-01 00:00:00", "2026-01-01 00:00:00")

    def test_fenetre_par_defaut_commence_hier(self):
        """La veille est comprise : une rencontre commencée hier soir dure.

        Sans ce jour de recul, une rencontre de 22:00 à 01:00 disparaîtrait de
        la grille dès minuit passé.
        """
        now = fields.Datetime.now()
        start, stop = self.Event._mobile_window(None, None)
        self.assertEqual((stop - start).days, 8)
        self.assertLess(start, now)

    # ------------------------------------------------------------------
    # Portée : l'agenda d'autrui n'est pas le mien
    # ------------------------------------------------------------------

    def _event(self, name, start, user, minutes=60, attendees=None):
        return self.Event.with_user(user).create({
            "name": name,
            "start": fields.Datetime.to_string(start),
            "stop": fields.Datetime.to_string(start + timedelta(minutes=minutes)),
            "partner_ids": [(6, 0, (attendees or user.partner_id).ids)],
        })

    def test_portee_exclut_l_agenda_d_autrui(self):
        mien = self._event("À moi", self.base, self.user)
        autrui = self._event("À l'autre", self.base, self.autre)
        result = self.Event.with_user(self.user).mobile_range(
            fields.Datetime.to_string(self.base - timedelta(hours=1)),
            fields.Datetime.to_string(self.base + timedelta(hours=5)),
        )
        ids = [row["id"] for row in result["events"]]
        self.assertIn(mien.id, ids)
        self.assertNotIn(autrui.id, ids, "L'agenda d'un autre usager a fuité.")

    def test_portee_prend_l_evenement_a_cheval_sur_la_borne(self):
        """Une rencontre commencée avant la fenêtre y est toujours en cours."""
        a_cheval = self._event("Commencée avant", self.base, self.user, minutes=180)
        result = self.Event.with_user(self.user).mobile_range(
            fields.Datetime.to_string(self.base + timedelta(hours=1)),
            fields.Datetime.to_string(self.base + timedelta(hours=5)),
        )
        self.assertIn(a_cheval.id, [row["id"] for row in result["events"]])

    def test_portee_exclut_l_evenement_hors_fenetre(self):
        dehors = self._event("Trop tôt", self.base - timedelta(days=10), self.user)
        result = self.Event.with_user(self.user).mobile_range(
            fields.Datetime.to_string(self.base),
            fields.Datetime.to_string(self.base + timedelta(days=2)),
        )
        self.assertNotIn(dehors.id, [row["id"] for row in result["events"]])

    # ------------------------------------------------------------------
    # Clé stable
    # ------------------------------------------------------------------

    def test_cle_natif_porte_l_identifiant(self):
        """Sans UID de série, la clé retombe sur l'identifiant.

        ⚠️ `calendar_nextcloud_sync` pose un `x_nc_uid` à la création, donc il
        faut l'effacer pour éprouver cette branche : la laisser en place
        faisait passer l'essai pour la mauvaise raison.
        """
        event = self._event("Natif", self.base, self.user)
        if "x_nc_uid" in event._fields:
            event.sudo().write({"x_nc_uid": False})
        self.assertEqual(event._mobile_key(), "odoo:%s" % event.id)

    def test_resolution_rattrape_un_identifiant_perime(self):
        """Le cas qui compte : l'app garde un id que la synchro a détruit.

        L'UID porte un « @ », comme tout UID iCalendar réel
        (`dd1558aa-…@odoo.example.com`). Couper la clé au PREMIER « @ »
        rendait un début de la forme « domaine@2026-10-05 12:00:00 » et ne
        retrouvait jamais rien.
        """
        event = self._event("Série", self.base, self.user)
        if "x_nc_uid" not in event._fields:
            self.skipTest("calendar_nextcloud_sync absent")
        event.sudo().write({"x_nc_uid": "uid-banc@agenda.example.com"})
        key = event._mobile_key()
        self.assertTrue(key.startswith("nc:uid-banc@agenda.example.com@"))
        # Un identifiant qui n'existe plus, plus la bonne clé.
        trouve = self.Event.with_user(self.user)._mobile_resolve(999999999, key)
        self.assertEqual(trouve, event)

    def test_resolution_rend_vide_sur_cle_abimee(self):
        vide = self.Event.with_user(self.user)._mobile_resolve(None, "n'importe quoi")
        self.assertFalse(vide)

    # ------------------------------------------------------------------
    # Présence : jamais de courriel
    # ------------------------------------------------------------------

    def test_confirmation_ne_publie_pas_sous_le_sous_type_invitation(self):
        """`do_accept` d'Odoo poste sous « Invitation », qui n'est pas interne.

        Sur une rencontre suivie par un client, ça lui aurait écrit.
        """
        event = self._event("À confirmer", self.base, self.user)
        attendee = event.with_user(self.user)._mobile_attendee()
        self.assertTrue(attendee, "Le participant devrait exister.")
        attendee.write({"state": "accepted"})
        event.message_post(body="essai", subtype_xmlid="mail.mt_note")
        invitation = self.env.ref("calendar.subtype_invitation")
        posted = event.message_ids.mapped("subtype_id")
        self.assertNotIn(invitation, posted)

    # ------------------------------------------------------------------
    # Échéances
    # ------------------------------------------------------------------

    def _task(self, name, deadline, state="01_in_progress"):
        """⚠️ `bf_time_of_day` réécrit l'HEURE de l'échéance sur la plage
        horaire choisie. Une tâche créée à 01:00 ressortait à 12:00, ce qui
        déplaçait le jour et faisait mentir l'essai du fuseau. On efface donc
        la plage avant de poser l'heure, puis on VÉRIFIE ce qui est stocké
        plutôt que de supposer que l'écriture a tenu.
        """
        project = self.env["project.project"].sudo().create({"name": "Banc agenda"})
        task = self.Task.sudo().create({
            "name": name,
            "project_id": project.id,
            "user_ids": [(6, 0, [self.user.id])],
            "state": state,
        })
        if "time_of_day_id" in task._fields:
            task.time_of_day_id = False
        if deadline:
            task.write({"date_deadline": fields.Datetime.to_string(deadline)})
            self.assertEqual(
                task.date_deadline, deadline,
                "L'heure de l'échéance a été réécrite : l'essai porterait "
                "sur une autre valeur que celle qu'il pose.")
        return task

    def test_seau_retard_ignore_une_tache_close(self):
        now = fields.Datetime.now()
        ouverte = self._task("En retard ouverte", now - timedelta(days=3))
        close = self._task("En retard mais faite", now - timedelta(days=3),
                           state="1_done")
        result = self.Task.with_user(self.user).mobile_todo()
        ids = [row["id"] for row in result["overdue"]]
        self.assertIn(ouverte.id, ids)
        self.assertNotIn(close.id, ids, "Une tâche faite est restée en retard.")

    def test_seau_attente_compte_comme_ouvert(self):
        now = fields.Datetime.now()
        attente = self._task("Attend le client", now - timedelta(days=1),
                             state="05_waiting_client")
        result = self.Task.with_user(self.user).mobile_todo()
        self.assertIn(attente.id, [row["id"] for row in result["overdue"]])

    def test_compte_par_jour_suit_le_fuseau_du_compte(self):
        """21:00 à Montréal, c'est le 5 en UTC et le 4 pour l'usager."""
        deadline = datetime(2026, 10, 5, 1, 0, 0)  # UTC
        tache = self._task("Tard le soir", deadline)
        result = self.Task.with_user(self.user).mobile_deadline_counts(
            "2026-10-01 00:00:00", "2026-10-10 00:00:00")
        self.assertEqual(result["tz"], "America/Toronto")
        self.assertEqual(result["counts"].get("2026-10-04"), 1,
                         "L'échéance a été rangée au jour UTC, pas au jour "
                         "de l'usager.")
        self.assertNotIn("2026-10-05", result["counts"])
        self.assertTrue(tache.exists())

    def test_compte_par_jour_suit_le_fuseau_de_l_appareil(self):
        """Compte à Montréal, téléphone à Auckland.

        01:00 UTC le 5 octobre, c'est le 4 à 21:00 pour le compte et le 5 à
        14:00 pour l'appareil. L'app pose la clé sur SA grille : la clé doit
        donc être le jour de l'appareil, et la réponse doit dire quel fuseau a
        servi.
        """
        self._task("À cheval sur minuit", datetime(2026, 10, 5, 1, 0, 0))
        Task = self.Task.with_user(self.user)
        result = Task.mobile_deadline_counts(
            "2026-10-01 00:00:00", "2026-10-10 00:00:00", tz="Pacific/Auckland")
        self.assertEqual(result["tz"], "Pacific/Auckland")
        self.assertEqual(result["counts"].get("2026-10-05"), 1)
        self.assertNotIn("2026-10-04", result["counts"],
                         "Le jour du compte a servi au lieu de celui de l'appareil.")

    def test_fuseau_inconnu_ou_absent_retombe_sur_le_compte(self):
        """Une chaîne arbitraire venue du téléphone ne change rien, et ne lève
        pas : c'est le comportement d'avant, pour une app qui n'envoie rien."""
        self._task("À cheval sur minuit", datetime(2026, 10, 5, 1, 0, 0))
        Task = self.Task.with_user(self.user)
        for tz in (None, "", "Mars/Olympus_Mons", "../../etc/passwd", 42):
            with self.subTest(tz=tz):
                result = Task.mobile_deadline_counts(
                    "2026-10-01 00:00:00", "2026-10-10 00:00:00", tz=tz)
                self.assertEqual(result["tz"], "America/Toronto")
                self.assertEqual(result["counts"].get("2026-10-04"), 1)


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestAgendaMobileV2(TransactionCase):
    """Les gestes ajoutés en 18.0.2.0.0 : couleur, exclusion, écriture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Banc Agenda v2",
            "login": "banc.agenda.v2",
            "email": "banc.agenda.v2@example.org",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("project.group_project_user").id,
            ])],
        })
        cls.Event = cls.env["calendar.event"]
        cls.Task = cls.env["project.task"]
        cls.projet = cls.env["project.project"].sudo().create({"name": "Banc v2"})

    # ── Couleur ──────────────────────────────────────────────────────────

    def test_la_couleur_est_celle_qu_odoo_peint(self):
        """🔴 Le point de la demande : la grille doit reprendre les couleurs de
        la vue Calendrier, quelle que soit la clé que porte l'événement."""
        from odoo.addons.bf_calendar_mobile.models import odoo_palette
        self.assertEqual(odoo_palette.couleur(1), "#ee2d2d")
        self.assertEqual(odoo_palette.couleur(4), "#5794dd")
        self.assertEqual(odoo_palette.couleur(11), "#9872e6")

    def test_la_couleur_boucle_comme_chez_odoo(self):
        """`getColor` fait ((clé - 1) % 55) + 1, donc 56 retombe sur 1."""
        from odoo.addons.bf_calendar_mobile.models import odoo_palette
        self.assertEqual(odoo_palette.couleur(56), odoo_palette.couleur(1))
        self.assertEqual(odoo_palette.index_odoo(0), 0)

    def test_le_fond_est_le_ton_adouci_pas_le_ton_plein(self):
        """La feuille de style peint `mix($o-white, $color, 55%)`. Rendre le
        ton plein donnerait un agenda bien plus saturé que le bureau."""
        from odoo.addons.bf_calendar_mobile.models import odoo_palette
        self.assertEqual(odoo_palette.couleur_douce(4), "#b3cff0")
        self.assertNotEqual(odoo_palette.couleur_douce(4), odoo_palette.couleur(4))

    def test_la_charge_utile_porte_la_couleur(self):
        event = self.Event.with_user(self.user).create({
            "name": "Coloré",
            "start": "2026-11-02 14:00:00",
            "stop": "2026-11-02 15:00:00",
            "partner_ids": [(6, 0, self.user.partner_id.ids)],
        })
        if "color" not in event._fields:
            self.skipTest("calendar_nextcloud_sync absent")
        event.sudo().write({"color": 4})
        charge = event._mobile_payload()
        self.assertEqual(charge["color"], "#5794dd")
        self.assertEqual(charge["color_soft"], "#b3cff0")

    # ── Exclusion ────────────────────────────────────────────────────────

    def test_l_exclusion_se_pose_et_se_retire(self):
        event = self.Event.with_user(self.user).create({
            "name": "À exclure",
            "start": "2026-11-03 14:00:00",
            "stop": "2026-11-03 15:00:00",
            "partner_ids": [(6, 0, self.user.partner_id.ids)],
        })
        if "bf_skip_agenda" not in event._fields:
            self.skipTest("bf_meeting absent")
        pose = event.mobile_set_flags(skip_agenda=True)
        self.assertTrue(pose["event"]["skip_agenda"])
        retire = event.mobile_set_flags(skip_agenda=False)
        self.assertFalse(retire["event"]["skip_agenda"])

    def test_les_deux_exclusions_sont_distinctes(self):
        """Confondre les deux ferait disparaître du tableau de bord une
        rencontre qu'on voulait seulement dispenser d'ordre du jour."""
        event = self.Event.with_user(self.user).create({
            "name": "Deux drapeaux",
            "start": "2026-11-04 14:00:00",
            "stop": "2026-11-04 15:00:00",
            "partner_ids": [(6, 0, self.user.partner_id.ids)],
        })
        if "bf_skip_agenda" not in event._fields:
            self.skipTest("bf_meeting absent")
        res = event.mobile_set_flags(skip_agenda=True)
        self.assertTrue(res["event"]["skip_agenda"])
        self.assertFalse(res["event"]["skip_dashboard"])

    # ── Création d'événement ─────────────────────────────────────────────

    def test_creer_une_rencontre_me_met_dedans(self):
        """Sans participant explicite, l'événement serait créé puis invisible
        dans « mon agenda »."""
        res = self.Event.with_user(self.user).mobile_create({
            "name": "Neuve",
            "start": "2026-11-05 14:00:00",
            "stop": "2026-11-05 15:00:00",
        })
        self.assertTrue(res["ok"])
        event = self.Event.browse(res["event"]["id"])
        self.assertIn(self.user.partner_id, event.partner_ids)
        fenetre = self.Event.with_user(self.user).mobile_range(
            "2026-11-05 00:00:00", "2026-11-06 00:00:00")
        self.assertIn(event.id, [e["id"] for e in fenetre["events"]])

    def test_creer_sans_titre_est_refuse(self):
        with self.assertRaises(UserError):
            self.Event.with_user(self.user).mobile_create({
                "start": "2026-11-05 14:00:00", "stop": "2026-11-05 15:00:00"})

    def test_un_champ_hors_liste_blanche_est_ignore_a_la_creation(self):
        """`user_id` est posé par le serveur, pas par l'appelant."""
        res = self.Event.with_user(self.user).mobile_create({
            "name": "Liste blanche",
            "start": "2026-11-06 14:00:00",
            "stop": "2026-11-06 15:00:00",
            "user_id": 1,
        })
        event = self.Event.browse(res["event"]["id"])
        self.assertEqual(event.user_id, self.user)

    # ── Tâches ───────────────────────────────────────────────────────────

    def _tache(self, name="Banc"):
        return self.Task.sudo().create({
            "name": name,
            "project_id": self.projet.id,
            "user_ids": [(6, 0, [self.user.id])],
            "state": "01_in_progress",
        })

    def test_la_tache_porte_ses_etiquettes_avec_leur_couleur(self):
        """Sans ça l'écran n'affichait ni couleur ni étiquette, et le seul
        geste restant était d'ouvrir Odoo."""
        etiquette = self.env["project.tags"].sudo().create(
            {"name": "Banc étiquette", "color": 8})
        tache = self._tache()
        tache.sudo().write({"tag_ids": [(6, 0, etiquette.ids)]})
        charge = tache.with_user(self.user)._mobile_payload()
        self.assertEqual(len(charge["tags"]), 1)
        self.assertEqual(charge["tags"][0]["color"], "#304be0")
        self.assertTrue(charge["state_label"])

    def test_completer_une_tache_sans_passer_par_le_navigateur(self):
        tache = self._tache()
        res = tache.with_user(self.user).mobile_done()
        self.assertTrue(res["task"]["done"])
        self.assertEqual(tache.state, "1_done")
        rouvre = tache.with_user(self.user).mobile_done(done=False)
        self.assertFalse(rouvre["task"]["done"])

    def test_un_champ_hors_liste_blanche_est_refuse_a_l_ecriture(self):
        tache = self._tache()
        with self.assertRaises(UserError):
            tache.with_user(self.user).mobile_write({"user_ids": [(6, 0, [1])]})
        self.assertIn(self.user.id, tache.user_ids.ids)

    def test_un_etat_inconnu_est_refuse(self):
        tache = self._tache()
        with self.assertRaises(UserError):
            tache.with_user(self.user).mobile_write({"state": "fini_pas_fini"})

    def test_poser_une_echeance_donne_l_heure_demandee(self):
        """⚠️ `bf_time_of_day` réécrit l'heure sur la plage horaire de la tâche.
        Sans effacer la plage, le mobile poserait 01:00 et la base garderait
        12:00, sans le dire."""
        tache = self._tache()
        voulu = "2026-11-07 01:00:00"
        tache.with_user(self.user).mobile_write({"date_deadline": voulu})
        self.assertEqual(fields.Datetime.to_string(tache.date_deadline), voulu)

    def test_creer_une_tache_sans_projet_est_refuse(self):
        with self.assertRaises(UserError):
            self.Task.with_user(self.user).mobile_create({"name": "Orpheline"})

    def test_creer_une_tache_me_l_assigne(self):
        res = self.Task.with_user(self.user).mobile_create({
            "name": "Neuve", "project_id": self.projet.id})
        tache = self.Task.browse(res["task"]["id"])
        self.assertIn(self.user.id, tache.user_ids.ids)
        seaux = self.Task.with_user(self.user).mobile_todo()
        self.assertEqual(seaux["undated_count"] >= 1, True)

    def test_les_etapes_offertes_sont_celles_du_projet(self):
        """Servir toutes les étapes ferait choisir une étape qui n'existe pas
        là où la tâche vit."""
        options = self.Task.with_user(self.user).mobile_options(
            project_id=self.projet.id)
        self.assertTrue(options["ok"])
        self.assertTrue(options["states"])
        for etape in options["stages"]:
            self.assertIn(
                self.projet.id,
                self.env["project.task.type"].browse(etape["id"]).project_ids.ids)

    # ── Ordre des projets ────────────────────────────────────────────────

    def test_les_projets_sortent_du_plus_recemment_frequente(self):
        """🔴 L'ordre alphabétique était le pire pour ce geste : le projet
        cherché en créant une tâche est presque toujours celui où l'on vient de
        travailler."""
        vieux = self.env["project.project"].sudo().create({"name": "AAA Vieux"})
        recent = self.env["project.project"].sudo().create({"name": "ZZZ Récent"})
        t1 = self.Task.sudo().create({
            "name": "Ancienne", "project_id": vieux.id,
            "user_ids": [(6, 0, [self.user.id])], "state": "01_in_progress"})
        t2 = self.Task.sudo().create({
            "name": "Fraîche", "project_id": recent.id,
            "user_ids": [(6, 0, [self.user.id])], "state": "01_in_progress"})
        # ⚠️ Écrire par l'ORM ne suffit pas : toutes les écritures d'une même
        # transaction partagent le `write_date` de la transaction, donc l'essai
        # ne mesurerait rien. On pose deux dates distinctes en SQL, ce qui est
        # le seul moyen d'éprouver le tri qu'on prétend faire.
        self.env.cr.execute(
            "UPDATE project_task SET write_date = %s WHERE id = %s",
            ("2026-01-02 10:00:00", t1.id))
        self.env.cr.execute(
            "UPDATE project_task SET write_date = %s WHERE id = %s",
            ("2026-06-30 10:00:00", t2.id))
        self.env.invalidate_all()

        options = self.Task.with_user(self.user).mobile_options()
        ids = [p["id"] for p in options["projects"]]
        self.assertIn(recent.id, ids)
        self.assertIn(vieux.id, ids)
        self.assertLess(
            ids.index(recent.id), ids.index(vieux.id),
            "Le projet touché en dernier devrait précéder, malgré son nom en Z.")

    def test_une_tache_close_compte_dans_la_frequentation(self):
        """Avoir fermé une tâche hier est un bon signe qu'on va en rouvrir une
        aujourd'hui : l'exclure ferait disparaître le projet du haut."""
        projet = self.env["project.project"].sudo().create({"name": "MMM Fermé"})
        self.Task.sudo().create({
            "name": "Faite", "project_id": projet.id,
            "user_ids": [(6, 0, [self.user.id])], "state": "1_done"})
        options = self.Task.with_user(self.user).mobile_options()
        self.assertIn(projet.id, [p["id"] for p in options["projects"]])

    def test_un_projet_jamais_frequente_reste_offert(self):
        """Il passe après, pas à la trappe."""
        orphelin = self.env["project.project"].sudo().create({"name": "Jamais vu"})
        options = self.Task.with_user(self.user).mobile_options()
        self.assertIn(orphelin.id, [p["id"] for p in options["projects"]])


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestRechercheTaches(TransactionCase):
    """18.0.3.2.0 : chercher dans mes tâches ouvertes, pas dans les seaux que
    le téléphone tient déjà."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = [(6, 0, [cls.env.ref("base.group_user").id,
                           cls.env.ref("project.group_project_user").id])]
        cls.user = cls.env["res.users"].create({
            "name": "Banc Recherche", "login": "banc.agenda.recherche",
            "email": "banc.agenda.recherche@example.org", "groups_id": groupes,
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Banc Recherche Autre", "login": "banc.agenda.recherche.autre",
            "email": "banc.agenda.recherche.autre@example.org", "groups_id": groupes,
        })
        cls.Task = cls.env["project.task"]
        cls.projet = cls.env["project.project"].sudo().create(
            {"name": "Banc Zorglub Nord"})
        cls.client = cls.env["res.partner"].sudo().create(
            {"name": "Quincaillerie Exemple"})

    def _tache(self, name, user=None, state="01_in_progress", deadline=None, **extra):
        tache = self.Task.sudo().create(dict({
            "name": name, "project_id": self.projet.id,
            "user_ids": [(6, 0, [(user or self.user).id])], "state": state,
        }, **extra))
        if deadline is not None:
            if "time_of_day_id" in tache._fields:
                tache.time_of_day_id = False
            tache.write({"date_deadline": fields.Datetime.to_string(deadline)})
        return tache

    def _chercher(self, q, **kw):
        res = self.Task.with_user(self.user).mobile_search(q, **kw)
        return res, [row["id"] for row in res["tasks"]]

    def test_trouve_une_tache_hors_de_tout_horizon(self):
        """La raison d'être : 200 jours, c'est au-delà des 30 jours de l'écran."""
        loin = self._tache("Renouveler le bail xylophone",
                           deadline=fields.Datetime.now() + timedelta(days=200))
        _res, ids = self._chercher("xylophone")
        self.assertEqual(ids, [loin.id])

    def test_une_tache_close_ou_d_autrui_ne_sort_pas(self):
        ouverte = self._tache("Xylophone ouverte")
        self._tache("Xylophone faite", state="1_done")
        self._tache("Xylophone annulée", state="1_canceled")
        self._tache("Xylophone d'un autre", user=self.autre)
        _res, ids = self._chercher("xylophone")
        self.assertEqual(ids, [ouverte.id])

    def test_chaque_mot_peut_venir_d_un_champ_different(self):
        """« facture zorglub » : le premier mot dans le titre, le second dans
        le projet. Une chaîne cherchée d'un bloc dans le titre ne trouve rien."""
        facture = self._tache("Facture de septembre")
        self._tache("Rapport de septembre")
        _res, ids = self._chercher("facture zorglub")
        self.assertEqual(ids, [facture.id])

    def test_tous_les_mots_doivent_se_trouver(self):
        self._tache("Facture de septembre")
        _res, ids = self._chercher("facture introuvablissime")
        self.assertEqual(ids, [])

    def test_le_client_et_l_etiquette_se_cherchent_aussi(self):
        chez_client = self._tache("Appel", partner_id=self.client.id)
        etiquette = self.env["project.tags"].sudo().create({"name": "Banc Pivoine"})
        etiquetee = self._tache("Relance", tag_ids=[(6, 0, etiquette.ids)])
        self.assertEqual(self._chercher("quincaillerie")[1], [chez_client.id])
        self.assertEqual(self._chercher("pivoine")[1], [etiquetee.id])

    def test_un_numero_trouve_la_tache_par_identifiant(self):
        """Le titre ne contient pas le numéro : seul l'identifiant peut la sortir."""
        tache = self._tache("Sans numéro dans le titre")
        self.assertIn(tache.id, self._chercher("#%d" % tache.id)[1])
        self.assertIn(tache.id, self._chercher(str(tache.id))[1])

    def test_un_numero_trop_grand_ne_casse_pas(self):
        res, _ids = self._chercher("#99999999999999")
        self.assertTrue(res["ok"])

    def test_un_seul_caractere_ne_cherche_pas(self):
        self._tache("X")
        res, ids = self._chercher(" x ")
        self.assertEqual((ids, res["query"]), ([], "x"))

    def test_la_limite_dit_qu_il_en_reste(self):
        for n in range(3):
            self._tache("Xylophone %d" % n)
        res, ids = self._chercher("xylophone", limit=2)
        self.assertEqual((len(ids), res["more"]), (2, True))
        res, ids = self._chercher("xylophone", limit=3)
        self.assertEqual((len(ids), res["more"]), (3, False))

    def test_les_datees_d_abord_les_sans_echeance_ensuite(self):
        """Les sans-échéance à la fin : une recherche sur un mot courant ne doit
        pas rendre cinquante tâches sans date avant celle de demain. PostgreSQL
        les range déjà en dernier sur un tri ascendant ; `nulls last` le dit
        dans le code, et cet essai tombe si quelqu'un écrit `nulls first`."""
        sans = self._tache("Xylophone sans date")
        demain = self._tache("Xylophone demain",
                             deadline=fields.Datetime.now() + timedelta(days=1))
        _res, ids = self._chercher("xylophone")
        self.assertEqual(ids, [demain.id, sans.id])


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestAgendaMobileV3(TransactionCase):
    """Ce qu'ajoute 18.0.3.0.0 : le rappel configuré, les participants
    modifiables, la recherche de contacts, la tâche par identifiant.

    Chaque essai porte la valeur qui ferait passer une écriture naïve : un
    rappel qui n'a pas encore sonné, un ajout qui enverrait l'invitation sans
    qu'on l'ait demandée, l'organisateur qu'on tente de retirer, une tâche dans
    un projet privé.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Banc Agenda v3",
            "login": "banc.agenda.v3",
            "email": "banc.agenda.v3@example.org",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("project.group_project_user").id,
            ])],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Banc Invité v3",
            "login": "banc.invite.v3",
            "email": "banc.invite.v3@example.org",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("project.group_project_user").id,
            ])],
        })
        cls.Event = cls.env["calendar.event"]
        cls.Task = cls.env["project.task"]
        cls.Alarm = cls.env["calendar.alarm"]
        cls.quinze = cls.Alarm.sudo().search(
            [("alarm_type", "=", "notification"), ("duration_minutes", "=", 15)],
            limit=1) or cls.Alarm.sudo().create({
                "name": "Banc 15 min", "alarm_type": "notification",
                "duration": 15, "interval": "minutes"})

    def _invitations(self, event):
        """Les invitations émises sur l'événement : `message_notify` les pose
        en `user_notification`, courriel ou boîte Odoo selon la personne.

        ⚠️ Cherchées dans `mail.message`, pas dans `message_ids` : ce champ
        EXCLUT justement les `user_notification` par son domaine. Un essai qui
        lisait `message_ids` concluait « aucune invitation » alors que le
        message existait — payé au banc.
        """
        return self.env["mail.message"].sudo().search([
            ("model", "=", "calendar.event"),
            ("res_id", "=", event.id),
            ("message_type", "=", "user_notification"),
        ])

    def _event(self, name, start, minutes=60, alarms=None):
        vals = {
            "name": name,
            "start": fields.Datetime.to_string(start),
            "stop": fields.Datetime.to_string(start + timedelta(minutes=minutes)),
            "partner_ids": [(6, 0, self.user.partner_id.ids)],
        }
        if alarms is not None:
            vals["alarm_ids"] = [(6, 0, alarms.ids)]
        return self.Event.with_user(self.user).with_context(
            no_mail_to_attendees=True).create(vals)

    # ── Rappel ───────────────────────────────────────────────────────────

    def test_rappel_a_venir_dit_l_heure_et_n_a_pas_sonne(self):
        """🔴 Le point de la demande : la fiche offrait « reporter » et « vu »
        à une rencontre de la semaine prochaine. Le serveur dit maintenant
        quand le rappel sonne, et qu'il n'a pas encore sonné."""
        start = fields.Datetime.now() + timedelta(days=3)
        event = self._event("Semaine prochaine", start, alarms=self.quinze)
        data = event.with_user(self.user)._mobile_payload()
        self.assertFalse(data["reminder_fired"])
        self.assertEqual(len(data["alarms"]), 1)
        self.assertEqual(data["alarms"][0]["minutes"], 15)
        attendu = fields.Datetime.to_datetime(fields.Datetime.to_string(start)) \
            - timedelta(minutes=15)
        self.assertEqual(data["alarms"][0]["notify_at"],
                         attendu.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_rappel_passe_a_sonne(self):
        """Une rencontre dans cinq minutes avec un rappel de quinze : le
        rappel est parti, les deux gestes ont un sens."""
        start = fields.Datetime.now() + timedelta(minutes=5)
        event = self._event("Tout de suite", start, alarms=self.quinze)
        data = event.with_user(self.user)._mobile_payload()
        self.assertTrue(data["reminder_fired"])

    def test_sans_rappel_la_fiche_le_dit(self):
        """Ni sonné ni à venir : la liste est vide et le verdict est faux.
        L'app affiche « aucun rappel » plutôt que des boutons sans objet."""
        event = self._event("Muette", fields.Datetime.now() + timedelta(days=1),
                            alarms=self.Alarm)
        data = event.with_user(self.user)._mobile_payload()
        self.assertEqual(data["alarms"], [])
        self.assertFalse(data["reminder_fired"])

    def test_un_rappel_par_courriel_ne_compte_pas(self):
        """Un rappel courriel part une fois et ne se reporte pas d'un geste."""
        courriel = self.Alarm.sudo().create({
            "name": "Banc courriel", "alarm_type": "email",
            "duration": 5, "interval": "minutes"})
        event = self._event("Courriel", fields.Datetime.now() + timedelta(minutes=1),
                            alarms=courriel)
        data = event.with_user(self.user)._mobile_payload()
        self.assertEqual(data["alarms"], [])
        self.assertFalse(data["reminder_fired"])

    # ── Participants ────────────────────────────────────────────────────

    def test_la_fiche_porte_l_identifiant_du_participant(self):
        event = self._event("Fiche", fields.Datetime.now() + timedelta(days=1))
        detail = event.with_user(self.user).mobile_detail()
        self.assertEqual(detail["organizer_partner_id"], self.user.partner_id.id)
        self.assertTrue(detail["can_edit_attendees"])
        moi = [a for a in detail["attendee_list"] if a["is_me"]]
        self.assertEqual(len(moi), 1)
        self.assertEqual(moi[0]["partner_id"], self.user.partner_id.id)
        self.assertEqual(moi[0]["email"], "banc.agenda.v3@example.org")

    def test_ajouter_un_participant_n_envoie_rien(self):
        """🔴 Odoo envoie l'invitation en `force_send` à tout participant
        ajouté. Sans `notify`, aucun courriel ne doit être créé."""
        event = self._event("Ajout", fields.Datetime.now() + timedelta(days=1))
        # ⚠️ Compter les `mail.mail` ne prouve rien : Odoo 18 passe par
        # `message_notify`, qui écrit d'abord un `mail.message` de type
        # « user_notification » sur l'événement, et le courriel lui-même
        # dépend du réglage de la personne (boîte Odoo ou courriel). C'est ce
        # message qu'on compte : il n'existe que si l'invitation est partie.
        result = event.with_user(self.user).mobile_set_attendees(
            add_ids=[self.autre.partner_id.id])
        self.assertIn(self.autre.partner_id, event.partner_ids)
        self.assertFalse(self._invitations(event),
                         "Une invitation est partie sans qu'on l'ait demandée.")
        noms = [a["name"] for a in result["event"]["attendee_list"]]
        self.assertIn(self.autre.partner_id.display_name, noms)
        # La trace est une note interne, pas une publication aux abonnés.
        note = event.message_ids.filtered(
            lambda m: "depuis l'application mobile" in (m.body or ""))[:1]
        self.assertTrue(note)
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))

    def test_ajouter_avec_notify_cree_l_invitation(self):
        """La porte existe, mais il faut la pousser."""
        event = self._event("Invité", fields.Datetime.now() + timedelta(days=1))
        event.with_user(self.user).mobile_set_attendees(
            add_ids=[self.autre.partner_id.id], notify=True)
        invitations = self._invitations(event)
        self.assertTrue(invitations, "`notify` devait produire l'invitation.")
        self.assertIn(self.autre.partner_id, invitations.mapped("partner_ids"))

    def test_retirer_un_participant(self):
        event = self._event("Retrait", fields.Datetime.now() + timedelta(days=1))
        event.with_user(self.user).mobile_set_attendees(
            add_ids=[self.autre.partner_id.id])
        event.with_user(self.user).mobile_set_attendees(
            remove_ids=[self.autre.partner_id.id])
        self.assertNotIn(self.autre.partner_id, event.partner_ids)

    def test_l_organisateur_ne_se_retire_pas(self):
        """Sans lui, la rencontre sortirait de « mon agenda » et deviendrait
        introuvable depuis l'app qui vient de la modifier."""
        event = self._event("Organisateur", fields.Datetime.now() + timedelta(days=1))
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_set_attendees(
                remove_ids=[self.user.partner_id.id])

    def test_un_ajout_deja_present_ne_fait_rien(self):
        """Ni doublon, ni note : rien n'a changé."""
        event = self._event("Idempotent", fields.Datetime.now() + timedelta(days=1))
        notes = len(event.message_ids)
        event.with_user(self.user).mobile_set_attendees(
            add_ids=[self.user.partner_id.id])
        self.assertEqual(len(event.attendee_ids), 1)
        self.assertEqual(len(event.message_ids), notes)

    # ── Recherche de contacts ───────────────────────────────────────────

    def test_la_recherche_exige_deux_caracteres(self):
        self.assertEqual(self.Event.with_user(self.user).mobile_partners("b"), [])
        self.assertEqual(self.Event.with_user(self.user).mobile_partners(""), [])

    def test_la_recherche_trouve_par_courriel(self):
        """On connaît souvent l'adresse avant l'orthographe du nom."""
        trouves = self.Event.with_user(self.user).mobile_partners("banc.invite.v3@")
        self.assertIn(self.autre.partner_id.id, [p["id"] for p in trouves])
        self.assertTrue(all("email" in p and "name" in p for p in trouves))

    # ── Tâche par identifiant ───────────────────────────────────────────

    def test_la_tache_s_ouvre_par_identifiant_meme_hors_de_mes_taches(self):
        projet = self.env["project.project"].sudo().create({"name": "Banc v3 public"})
        tache = self.Task.sudo().create({
            "name": "Pas à moi", "project_id": projet.id,
            "user_ids": [(6, 0, [self.autre.id])]})
        data = tache.with_user(self.user).mobile_detail()
        self.assertTrue(data["ok"])
        self.assertEqual(data["task"]["id"], tache.id)
        self.assertEqual(data["task"]["name"], "Pas à moi")

    def test_une_tache_d_un_projet_prive_reste_fermee(self):
        """Ce que mes droits ne laissent pas lire ne s'ouvre pas : l'ORM
        refuse, et le contrôleur en fait un 403."""
        from odoo.exceptions import AccessError
        prive = self.env["project.project"].sudo().create({
            "name": "Banc v3 privé", "privacy_visibility": "followers",
            "user_id": self.autre.id})
        tache = self.Task.sudo().create({
            "name": "Secrète", "project_id": prive.id,
            "user_ids": [(6, 0, [self.autre.id])]})
        with self.assertRaises(AccessError):
            tache.with_user(self.user).mobile_detail()

    # ── Portée de la fiche et des calendriers ───────────────────────────

    def test_la_fiche_reste_dans_mon_agenda(self):
        """Un identifiant deviné n'ouvre pas la rencontre d'un collègue, même
        si la règle d'accès d'Odoo la laisserait lire."""
        autrui = self.Event.with_user(self.autre).with_context(
            no_mail_to_attendees=True).create({
                "name": "Pas à moi",
                "start": fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=2)),
                "stop": fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=2, hours=1)),
                "partner_ids": [(6, 0, self.autre.partner_id.ids)],
            })
        self.assertFalse(self.Event.with_user(self.user)._mobile_resolve(autrui.id, None))
        self.assertFalse(self.Event.with_user(self.user)._mobile_resolve(
            None, "odoo:%s" % autrui.id))
        mien = self._event("À moi", fields.Datetime.now() + timedelta(days=2))
        self.assertEqual(self.Event.with_user(self.user)._mobile_resolve(mien.id, None), mien)

    def test_les_calendriers_offerts_sont_les_miens(self):
        if "nextcloud.calendar.sync.config" not in self.env:
            self.skipTest("calendar_nextcloud_sync absent")
        Config = self.env["nextcloud.calendar.sync.config"].sudo()
        base = {"nextcloud_base_url": "https://nc.example.org", "nextcloud_user": "x"}
        mienne = Config.create(dict(base, name="Banc mienne", calendar_owner_id=self.user.id))
        autre = Config.create(dict(base, name="Banc autre", calendar_owner_id=self.autre.id))
        libre = Config.create(dict(base, name="Banc libre"))
        ids = [c["id"] for c in self.Event.with_user(self.user).mobile_calendars()]
        self.assertIn(mienne.id, ids)
        self.assertIn(libre.id, ids)
        self.assertNotIn(autre.id, ids, "Le calendrier d'un collègue est offert à l'écriture.")
        with self.assertRaises(UserError):
            self.Event.with_user(self.user).mobile_create({
                "name": "Chez l'autre",
                "start": fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=1)),
                "stop": fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=1, hours=1)),
                "calendar_config_id": autre.id,
            })



@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestCompteParFuseauHttp(HttpCase):
    """Le fuseau par la route : ``tz`` doit traverser le contrôleur jusqu'au modèle.

    Les essais du modèle ne voient pas un contrôleur qui oublierait de passer
    le paramètre ; celui-ci, oui.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Banc Fuseau HTTP",
            "login": "banc.agenda.fuseau",
            "email": "banc.agenda.fuseau@example.org",
            "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("project.group_project_user").id,
            ])],
        })
        projet = cls.env["project.project"].sudo().create({"name": "Banc fuseau"})
        tache = cls.env["project.task"].sudo().create({
            "name": "À cheval sur minuit", "project_id": projet.id,
            "user_ids": [(6, 0, cls.user.ids)],
        })
        if "time_of_day_id" in tache._fields:
            tache.time_of_day_id = False
        tache.write({"date_deadline": "2026-10-05 01:00:00"})
        cls.jeton = cls.env["bf.email.mobile.device"]._issue(
            cls.user.id, name="Banc fuseau").device_token

    def _compter(self, suffixe=""):
        reponse = self.url_open(
            "/bf_calendar/mobile/v1/task_counts?from=2026-10-01%2000:00:00"
            "&to=2026-10-10%2000:00:00" + suffixe,
            headers={"Authorization": "Bearer %s" % self.jeton}, timeout=30)
        self.assertEqual(reponse.status_code, 200, reponse.text)
        return reponse.json()

    def test_la_route_groupe_dans_le_fuseau_de_l_appareil(self):
        corps = self._compter("&tz=Pacific/Auckland")
        self.assertEqual(corps["tz"], "Pacific/Auckland")
        self.assertEqual(corps["counts"], {"2026-10-05": 1})

    def test_sans_fuseau_la_route_garde_celui_du_compte(self):
        corps = self._compter()
        self.assertEqual(corps["tz"], "America/Toronto")
        self.assertEqual(corps["counts"], {"2026-10-04": 1})

    def test_la_recherche_traverse_la_route(self):
        """Le paramètre `q` doit atteindre le modèle, et la route rester fermée
        sans jeton."""
        reponse = self.url_open(
            "/bf_calendar/mobile/v1/tasks/search?q=cheval%20minuit",
            headers={"Authorization": "Bearer %s" % self.jeton}, timeout=30)
        self.assertEqual(reponse.status_code, 200, reponse.text)
        self.assertEqual([t["name"] for t in reponse.json()["tasks"]],
                         ["À cheval sur minuit"])
        ferme = self.url_open("/bf_calendar/mobile/v1/tasks/search?q=cheval", timeout=30)
        self.assertEqual(ferme.status_code, 401)
        ping = self.url_open("/bf_calendar/mobile/v1/ping", timeout=30).json()
        self.assertGreaterEqual(ping["api"], 4)
