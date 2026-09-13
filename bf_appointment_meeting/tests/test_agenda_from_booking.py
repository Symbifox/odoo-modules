# -*- coding: utf-8 -*-
"""Un rendez-vous confirmé fabrique son ordre du jour — et ce qui l'en empêche.

Les cas heureux tiennent en trois essais. Le reste de ce fichier existe pour
les quatre pièges relevés en instruisant ce lot, dont aucun ne se serait vu
en jouant seulement le chemin normal :

* `meeting.agenda.project_id` est REQUIS, et la plupart des types de
  rendez-vous ne portent pas de projet. Sans repli, la confirmation lèverait.
* La fenêtre de contribution n'ouvrait qu'à l'ENVOI d'un ordre du jour. Un
  ordre du jour créé ici n'est jamais envoyé : sans `contributions_preopened`,
  le lien qu'on vient de mettre dans la confirmation rendrait « fenêtre
  fermée » au premier clic.
* `meeting.agenda.create()` lance un raffinage par le pont IA sur tout
  brouillon qui porte un projet. Le lien public part dans la seconde qui suit.
* `action_cancel` peut SUPPRIMER le `calendar.event` : l'ordre du jour doit
  être relevé avant, sinon son lien passe à NULL et on ne le retrouve plus.
"""

from datetime import timedelta
from unittest.mock import patch

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment_meeting")
class TestAgendaFromBooking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context,
                                       tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "All day %d" % d,
                "dayofweek": str(d),
                "hour_from": 0.0,
                "hour_to": 24.0,
                "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 OdJ", "attendance_ids": attendances, "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Matériel OdJ", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.client = cls.env["res.partner"].create({
            "name": "Cliente qui réserve", "email": "odj@test.invalid",
        })
        cls.projet = cls.env["project.project"].create({"name": "Projet du type"})
        cls.projet_repli = cls.env["project.project"].create({"name": "Projet de repli"})
        cls.type = cls._make_type("Type avec OdJ", projet=cls.projet)

    @classmethod
    def _make_type(cls, nom, projet=None, cree_odj=True):
        return cls.env["resource.booking.type"].create({
            "name": nom,
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "video_provider": "none",
            "requires_recording_consent": False,
            "bf_create_agenda": cree_odj,
            "project_id": projet.id if projet else False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _slot(self, rang=0):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = self.type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=rang + 1)
        self.assertTrue(creneaux, "un calendrier 24/7 doit produire des créneaux")
        return creneaux[rang].astimezone(pytz.utc).replace(tzinfo=None)

    def _booking(self, booking_type=None, rang=0):
        booking_type = booking_type or self.type
        return booking_type._bf_create_booking(
            self._slot(rang), partners=self.client, confirm=False)

    # -- le chemin normal ---------------------------------------------------

    def test_confirmation_fabrique_l_ordre_du_jour(self):
        booking = self._booking()
        self.assertFalse(booking._bf_agenda())
        booking.action_confirm()
        agenda = booking._bf_agenda()
        self.assertTrue(agenda, "une confirmation doit fabriquer l'ordre du jour")
        self.assertEqual(agenda.project_id, self.projet)
        self.assertEqual(agenda.calendar_event_id, booking.meeting_id)
        self.assertEqual(agenda.date, booking.start)
        self.assertEqual(agenda.duration_planned, 60)
        self.assertIn(self.client, agenda.participant_ids)

    def test_la_case_decochee_ne_fabrique_rien(self):
        """L'état sûr est celui qui ne crée rien."""
        muet = self._make_type("Type sans OdJ", projet=self.projet, cree_odj=False)
        booking = self._booking(muet)
        booking.action_confirm()
        self.assertFalse(booking._bf_agenda())

    def test_une_deuxieme_confirmation_ne_double_pas(self):
        booking = self._booking()
        booking.action_confirm()
        premier = booking._bf_agenda()
        booking.action_confirm()
        self.assertEqual(booking._bf_agenda(), premier,
                         "la fabrication doit être idempotente")
        self.assertEqual(len(booking.meeting_id.meeting_agenda_ids), 1)

    # -- le projet, qui est requis -----------------------------------------

    def test_sans_projet_au_type_le_repli_de_la_societe_sert(self):
        self.env.company.bf_appointment_agenda_project_id = self.projet_repli
        orphelin = self._make_type("Type sans projet", projet=None)
        booking = self._booking(orphelin)
        booking.action_confirm()
        self.assertEqual(booking._bf_agenda().project_id, self.projet_repli)

    def test_sans_projet_du_tout_rien_ne_se_cree_et_la_confirmation_tient(self):
        """🔴 `project_id` est requis : sans garde, la confirmation lèverait.

        Et c'est le point : le rendez-vous est pris, le créneau retenu, la
        confirmation partie. Faire échouer tout ça pour une configuration
        manquante serait disproportionné.
        """
        self.env.company.bf_appointment_agenda_project_id = False
        orphelin = self._make_type("Type orphelin", projet=None)
        booking = self._booking(orphelin)
        booking.action_confirm()          # ne doit pas lever
        self.assertEqual(booking.state, "confirmed")
        self.assertFalse(booking._bf_agenda())
        corps = " ".join(booking.message_ids.mapped("body"))
        self.assertIn("Aucun ordre du jour", corps,
                      "l'absence doit se lire au fil de la réservation")

    # -- la fenêtre de contribution ----------------------------------------

    def test_la_fenetre_est_ouverte_sans_qu_aucun_courriel_soit_parti(self):
        """🔴 Le cœur du lot.

        `contributions_open` valait `sent_date and state == 'draft'`. Un ordre
        du jour créé ici n'est jamais envoyé — le lien serait mort-né.
        """
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        self.assertFalse(agenda.sent_date,
                         "ce module n'envoie AUCUN courriel d'ordre du jour")
        self.assertTrue(agenda.contributions_preopened)
        self.assertTrue(agenda.contributions_open)
        self.assertTrue(agenda.access_token)

    def test_la_fenetre_se_referme_a_la_confirmation_de_l_ordre_du_jour(self):
        """Le drapeau n'ouvre la fenêtre que par le bas."""
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        agenda.state = "confirmed"
        agenda.invalidate_recordset(["contributions_open"])
        self.assertFalse(agenda.contributions_open)

    def test_allow_contributions_ferme_toujours_d_un_coup(self):
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        agenda.allow_contributions = False
        self.assertFalse(agenda.contributions_open)

    # -- le lien, sur les quatre surfaces ----------------------------------

    def test_le_lien_apparait_dans_les_liens_supplementaires(self):
        booking = self._booking()
        booking.action_confirm()
        liens = booking.bf_extra_links()
        self.assertEqual(len(liens), 1)
        self.assertIn("/meeting/agenda/", liens[0]["url"])
        self.assertIn(booking._bf_agenda().access_token, liens[0]["url"])

    def test_le_lien_disparait_quand_la_fenetre_se_ferme(self):
        """Une page publique consultée après coup ne doit pas offrir un lien mort."""
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        agenda.state = "confirmed"
        agenda.invalidate_recordset(["contributions_open"])
        self.assertEqual(booking.bf_extra_links(), [])

    def test_le_lien_est_dans_l_ics_et_dans_la_description_de_l_evenement(self):
        booking = self._booking()
        booking.action_confirm()
        jeton = booking._bf_agenda().access_token
        ics = booking._generate_ics_data().decode("utf-8")
        self.assertIn(jeton, ics.replace("\r\n ", ""),
                      "l'invitation .ics doit porter le lien")
        self.assertIn(jeton, booking._bf_meeting_description() or "")

    def test_le_bouton_de_courriel_est_vide_sans_ordre_du_jour(self):
        """La surface d'accroche doit être inerte là où aucun satellite ne parle."""
        muet = self._make_type("Type muet", projet=self.projet, cree_odj=False)
        booking = self._booking(muet)
        booking.action_confirm()
        self.assertEqual(str(booking.bf_extra_cta_html()), "")

    def test_le_bouton_de_courriel_porte_le_lien(self):
        booking = self._booking()
        booking.action_confirm()
        html = str(booking.bf_extra_cta_html())
        self.assertIn(booking._bf_agenda().access_token, html)
        self.assertIn("<a href=", html)

    # -- le premier sujet ---------------------------------------------------

    def test_la_reponse_du_formulaire_devient_le_premier_sujet(self):
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.type.id,
            "name": "De quoi s'agit-il ?",
            "field_type": "textarea",
            "sequence": 10,
        })
        booking = self._booking()
        self.env["appointment.intake.answer"].create({
            "booking_id": booking.id,
            "field_id": champ.id,
            "value": "Reprendre le calendrier de migration",
        })
        booking.action_confirm()
        sujets = booking._bf_agenda().topic_ids
        self.assertEqual(len(sujets), 1)
        self.assertEqual(sujets.name, "Reprendre le calendrier de migration")
        self.assertEqual(sujets.moderation_state, "accepted",
                         "ce sujet vient de NOTRE formulaire, pas de la page publique")

    def test_aucun_sujet_quand_le_formulaire_est_vide(self):
        booking = self._booking()
        booking.action_confirm()
        self.assertFalse(booking._bf_agenda().topic_ids)

    # -- replanification et annulation --------------------------------------

    def test_une_replanification_deplace_l_ordre_du_jour(self):
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        ancienne = agenda.date
        booking.start = self._slot(3)
        self.assertNotEqual(agenda.date, ancienne)
        self.assertEqual(agenda.date, booking.start)

    def test_une_annulation_annule_un_ordre_du_jour_vide(self):
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        booking.action_cancel()
        self.assertEqual(agenda.state, "cancelled")
        self.assertFalse(agenda.contributions_open)

    def test_une_annulation_conserve_un_ordre_du_jour_qui_porte_du_travail(self):
        booking = self._booking()
        booking.action_confirm()
        agenda = booking._bf_agenda()
        agenda.objectives = "Arrêter le périmètre"
        booking.action_cancel()
        self.assertEqual(agenda.state, "draft")
        self.assertFalse(agenda.contributions_open,
                         "la fenêtre se referme même quand le document survit")

    # -- le raffinage automatique -------------------------------------------

    def test_la_creation_ne_lance_pas_le_raffinage(self):
        """🔴 Le lien public part dans la seconde qui suit la création.

        `meeting.agenda.create()` lance sinon le pont IA sur tout brouillon
        qui porte un projet : du texte de machine que personne n'a relu
        deviendrait la première chose qu'un client lit de nous.
        """
        booking = self._booking()
        booking.action_confirm()
        self.assertNotEqual(booking._bf_agenda().refine_state, "queued")


@tagged("bf_appointment_meeting", "bf_appointment_meeting_render")
class TestRenduDesGabarits(TestAgendaFromBooking):
    """🔴 Rendre les gabarits pour de vrai, et pas seulement appeler la méthode.

    Le bloc de liens est appelé depuis le `body_html` de cinq `mail.template`,
    c'est-à-dire depuis du QWeb évalué sur le record. Un appel de méthode qui
    serait refusé par ce moteur ne casserait AUCUN test Python — il casserait
    la confirmation de rendez-vous, en production, pour tout le monde, et le
    seul symptôme serait un courriel qui ne part pas.

    C'est la famille de défauts que le module connaît déjà : une virgule dans
    un `patch()` rendait un fichier d'actifs mort sans qu'un test bronche.
    """

    GABARITS = [
        "bf_appointment.mail_template_appointment_confirmation",
        "bf_appointment.mail_template_reminder_2d",
        "bf_appointment.mail_template_reminder_1d",
        "bf_appointment.mail_template_reminder_2h",
        "bf_appointment.mail_template_reminder_1h",
    ]

    def test_les_cinq_gabarits_rendent_et_portent_le_lien(self):
        booking = self._booking()
        booking.action_confirm()
        jeton = booking._bf_agenda().access_token
        self.assertTrue(jeton)
        for xmlid in self.GABARITS:
            gabarit = self.env.ref(xmlid)
            corps = gabarit._render_field("body_html", booking.ids)[booking.id]
            self.assertIn("bf_extra_cta_html", gabarit.body_html or "",
                          "%s doit appeler le bloc de liens" % xmlid)
            self.assertIn(jeton, corps,
                          "%s doit porter le lien de l'ordre du jour" % xmlid)
            self.assertNotIn("bf_extra_cta_html", corps,
                             "%s : l'appel doit être ÉVALUÉ, pas recopié" % xmlid)

    def test_les_cinq_gabarits_rendent_sans_ordre_du_jour(self):
        """Le cas du locataire sans satellite, joué ici faute de mieux.

        On ne peut pas désinstaller le pont dans un test, mais un type sans la
        case produit exactement la même chose : `bf_extra_links()` rend [], et
        les cinq gabarits doivent rendre sans une ligne de plus.
        """
        muet = self._make_type("Type muet rendu", projet=self.projet, cree_odj=False)
        booking = self._booking(muet)
        booking.action_confirm()
        for xmlid in self.GABARITS:
            corps = self.env.ref(xmlid)._render_field(
                "body_html", booking.ids)[booking.id]
            self.assertTrue(corps.strip(), "%s doit rendre quelque chose" % xmlid)
            self.assertNotIn("/meeting/agenda/", corps)
            self.assertNotIn("Traceback", corps)

    def test_le_courriel_de_confirmation_part_avec_le_lien(self):
        """De bout en bout : ce que le demandeur reçoit vraiment.

        ⚠️ L'ordre compte, et c'est tout le lot : l'ordre du jour doit exister
        AVANT l'envoi. Ici la confirmation part comme la page publique la fait
        partir, juste après `action_confirm`.

        ⚠️ `mail.mail.send()` est neutralisé — pas pour éviter d'écrire sur le
        réseau (rien ne sort d'un banc), mais parce que `_send_appointment_email`
        détache la pièce .ics et laisse `auto_delete` EFFACER le message une
        fois remis. Chercher le `mail.mail` après coup rend un recordset vide,
        et le test conclurait « aucun courriel » sur un envoi parfaitement
        réussi. On le retient donc en file, le temps de le lire.
        """
        booking = self._booking()
        booking.action_confirm()
        gabarit = self.env.ref(
            "bf_appointment.mail_template_appointment_confirmation")
        avant = self.env["mail.mail"].search([], order="id desc", limit=1)
        with patch.object(type(self.env["mail.mail"]), "send",
                          lambda self, *a, **kw: None):
            booking._send_appointment_email(gabarit, recipient="booker")
        envoye = self.env["mail.mail"].search(
            [("id", ">", avant.id or 0)], order="id desc", limit=1)
        self.assertTrue(envoye, "un courriel doit avoir été mis en file")
        self.assertIn(booking._bf_agenda().access_token, envoye.body_html or "")
        # ⚠️ L'apostrophe ressort échappée (`l&#39;ordre`) : le corps est du
        # HTML sérialisé, pas le Markup qu'on a écrit. Chercher la chaîne
        # telle qu'on l'a tapée donnait un échec sur un courriel parfait.
        self.assertIn("Préparer l&#39;ordre du jour", envoye.body_html or "")
        self.assertIn("/meeting/agenda/", envoye.body_html or "")


@tagged("bf_appointment_meeting", "bf_appointment_meeting_langue")
class TestLangueDuLien(TestAgendaFromBooking):
    """Le bouton parle la langue du demandeur, ou il dément le courriel.

    La page de contribution a été rendue bilingue dans le même lot ; un
    bouton resté français dans un courriel anglais aurait conduit un client
    anglophone d'une phrase française vers une page anglaise, ce qui est la
    même incohérence, juste déplacée d'un cran.
    """

    def test_le_libelle_suit_la_langue_du_demandeur(self):
        """🔴 `en_US` est la langue SOURCE d'Odoo, pas une traduction.

        `odoo/tools/translate.py` court-circuite : `if lang == 'en_US':
        translation = source`. Aucun catalogue n'est consulté. Le code de ce
        module étant écrit en français, un locataire dont la SEULE langue
        anglaise est `en_US` lira donc un bouton français dans un courriel
        anglais, et aucun `en_US.po` n'y changera rien — d'où son absence de
        `i18n/`, qui n'aurait donné qu'une illusion de couverture.

        C'est pour ça que BF fait installer `en_CA`, comme le fait déjà
        `bf_appointment`. Le test le vérifie là où c'est vérifiable, et dit
        pourquoi il se saute ailleurs.
        """
        anglais = self.env["res.lang"].search(
            [("code", "like", "en%"), ("code", "!=", "en_US"),
             ("active", "=", True)], limit=1)
        if not anglais:
            self.skipTest(
                "aucune langue anglaise NON-SOURCE active : en_US rend le "
                "texte source, ici du français, et c'est le comportement "
                "d'Odoo, pas un défaut de ce module")
        self.client.lang = anglais.code
        booking = self._booking()
        booking.action_confirm()
        liens = booking.with_context(lang=anglais.code).bf_extra_links()
        self.assertTrue(liens)
        libelle = liens[0]["label"]
        self.assertEqual(libelle, "Prepare the agenda")
        self.assertNotIn("ordre du jour", libelle.lower(),
                         "un courriel anglais ne doit pas porter un bouton français")

    def test_la_page_de_contribution_suit_la_langue_de_l_ordre_du_jour(self):
        anglais = self.env["res.lang"].search([("code", "like", "en%"),
                                               ("active", "=", True)], limit=1)
        if not anglais:
            self.skipTest("aucune langue anglaise active sur cette base")
        self.client.lang = anglais.code
        booking = self._booking()
        booking.action_confirm()
        self.assertEqual(booking._bf_agenda().lang, anglais.code,
                         "l'ordre du jour prend la langue du DEMANDEUR")
