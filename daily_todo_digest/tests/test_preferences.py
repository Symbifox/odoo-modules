# -*- coding: utf-8 -*-
"""Préférences par personne et gabarits visant des groupes.

Avant : une config partagée, rangée sous Paramètres > Technique, écrite par
les seuls administrateurs, et le seul réglage personnel était la ville de la
météo. Maintenant : le gabarit vise des groupes, la personne règle son heure,
son rythme et ses sections, et elle reçoit UN seul briefing.

Gelé au 2026-09-15 16:00 UTC = mardi 16 septembre 04:00 à Auckland.
"""

from datetime import datetime
from unittest.mock import patch

import pytz
from freezegun import freeze_time

from odoo.addons.daily_todo_digest.models.daily_digest import WEEKDAY_FIELDS
from odoo.fields import Command
from odoo.tests import TransactionCase, tagged

NOW = "2026-09-15 16:00:00"
AUCKLAND = pytz.timezone("Pacific/Auckland")


def local(moment):
    """`moment` en UTC, rendu dans le fuseau d'Auckland."""
    return pytz.UTC.localize(datetime.fromisoformat(moment)).astimezone(AUCKLAND)


@tagged("post_install", "-at_install")
class TestDigestPreferences(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        # 🔴 Un usager créé avec un seul groupe maison est un usager PARTAGÉ
        # (`share` = pas de `base.group_user`), et le briefing n'en veut pas.
        cls.interne = cls.env.ref("base.group_user")
        cls.groupe = cls.env["res.groups"].create({"name": "Briefing — essai 27254"})
        cls.autre_groupe = cls.env["res.groups"].create({"name": "Briefing — essai 27254 bis"})
        cls.olive = cls.env["res.users"].create({
            "name": "Olive Préférences", "login": "olive.pref@example.com",
            "email": "olive.pref@example.com", "tz": "Pacific/Auckland",
            "groups_id": [Command.set([cls.interne.id, cls.groupe.id])],
        })
        cls.sans_courriel = cls.env["res.users"].create({
            "name": "Sans Courriel", "login": "sans.courriel@example.com",
            "tz": "Pacific/Auckland",
            "groups_id": [Command.set([cls.interne.id, cls.groupe.id])],
        })
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Gabarit du groupe", "sequence": 50, "send_hour": 4,
            "group_ids": [Command.set([cls.groupe.id])],
        })

    def _mes_paires(self, user=None):
        user = user or self.olive
        return [
            (u, c) for u, c in self.env["daily.digest.config"]._resolve_recipients()
            if u == user
        ]

    # ------------------------------------------------- destinataires par groupe

    def test_un_membre_du_groupe_est_destinataire_sans_etre_nomme(self):
        paires = self._mes_paires()
        self.assertEqual(len(paires), 1)
        self.assertEqual(paires[0][1], self.config)

    def test_une_personne_sans_courriel_n_est_pas_destinataire(self):
        self.assertEqual(self._mes_paires(self.sans_courriel), [])

    def test_un_seul_briefing_par_personne(self):
        # Nommée ici ET membre du groupe de l'autre : sans la résolution, deux
        # courriels le même matin.
        prioritaire = self.env["daily.digest.config"].create({
            "name": "Gabarit nommé", "sequence": 10,
            "user_ids": [Command.set([self.olive.id])],
        })
        paires = self._mes_paires()
        self.assertEqual(len(paires), 1, "deux briefings pour une personne")
        self.assertEqual(paires[0][1], prioritaire, "la séquence n'a pas tranché")

    def test_la_sequence_tranche_et_pas_l_ordre_de_creation(self):
        tardif = self.env["daily.digest.config"].create({
            "name": "Gabarit tardif mais prioritaire", "sequence": 1,
            "group_ids": [Command.set([self.groupe.id])],
        })
        self.assertEqual(self._mes_paires()[0][1], tardif)

    def test_un_gabarit_archive_ne_vise_personne(self):
        self.config.active = False
        self.assertEqual(self._mes_paires(), [])

    def test_un_usager_de_portail_n_est_jamais_destinataire(self):
        portail = self.env.ref("base.group_portal", raise_if_not_found=False)
        if not portail:
            self.skipTest("aucun groupe portail")
        invite = self.env["res.users"].create({
            "name": "Invité Portail", "login": "invite.portail@example.com",
            "email": "invite.portail@example.com",
            "groups_id": [Command.set([portail.id])],
        })
        self.config.user_ids = [Command.link(invite.id)]
        self.assertEqual(self._mes_paires(invite), [])

    # --------------------------------------------------- heure et fréquence

    def test_l_heure_du_gabarit_s_applique_sans_preference(self):
        self.assertTrue(self.config._is_due(self.olive, local(NOW)))        # 04:00
        self.config.send_hour = 7
        self.assertFalse(self.config._is_due(self.olive, local(NOW)))

    def test_la_personne_impose_son_heure(self):
        self.olive.digest_send_hour = "7"
        self.assertFalse(self.config._is_due(self.olive, local(NOW)))
        self.olive.digest_send_hour = "4"
        self.assertTrue(self.config._is_due(self.olive, local(NOW)))

    def test_l_heure_zero_de_la_personne_est_une_heure_et_pas_un_vide(self):
        self.config.send_hour = 4
        self.olive.digest_send_hour = "0"
        self.assertFalse(self.config._is_due(self.olive, local(NOW)))
        minuit = local("2026-09-15 12:00:00")   # 2026-09-16 00:00 à Auckland
        self.assertEqual(minuit.hour, 0)
        self.assertTrue(self.config._is_due(self.olive, minuit))

    def test_jours_ouvrables_saute_la_fin_de_semaine(self):
        self.olive.digest_frequency = "weekdays"
        mercredi = local("2026-09-15 16:00:00")      # mercredi 16 à Auckland
        self.assertEqual(mercredi.weekday(), 2)
        self.assertTrue(self.config._is_due(self.olive, mercredi))
        samedi = local("2026-09-18 16:00:00")        # samedi 19 à Auckland
        self.assertEqual(samedi.weekday(), 5)
        self.assertFalse(self.config._is_due(self.olive, samedi))

    def test_les_jours_choisis_decident(self):
        self.olive.digest_frequency = "custom"
        mercredi = local(NOW)
        self.olive.digest_wednesday = False
        self.assertFalse(self.config._is_due(self.olive, mercredi))
        self.olive.digest_wednesday = True
        self.assertTrue(self.config._is_due(self.olive, mercredi))

    def test_un_hebdomadaire_est_un_seul_jour_coche(self):
        # ⚠️ Les sept jours s'écrivent ENSEMBLE : la contrainte refuse l'état
        # intermédiaire où « jours choisis » n'a plus aucun jour, et c'est bien
        # ce qu'on veut d'elle. La boîte de dialogue écrit tout d'un coup.
        self.olive.write({
            **{champ: False for champ in WEEKDAY_FIELDS},
            "digest_frequency": "custom", "digest_monday": True,
        })
        self.assertFalse(self.config._is_due(self.olive, local(NOW)))        # mercredi
        lundi = local("2026-09-13 16:00:00")                                  # lundi 14
        self.assertEqual(lundi.weekday(), 0)
        self.assertTrue(self.config._is_due(self.olive, lundi))

    def test_la_frequence_de_la_personne_bat_celle_du_gabarit(self):
        self.config.frequency = "weekdays"
        samedi = local("2026-09-18 16:00:00")
        self.assertFalse(self.config._is_due(self.olive, samedi))
        self.olive.digest_frequency = "daily"
        self.assertTrue(self.config._is_due(self.olive, samedi))

    # ------------------------------------------------- sections en trois états

    def test_une_section_vide_suit_le_gabarit(self):
        self.config.include_quote = True
        self.assertTrue(self.config._section_on(self.olive, "quote"))
        self.config.include_quote = False
        self.assertFalse(self.config._section_on(self.olive, "quote"))

    def test_la_personne_allume_une_section_que_le_gabarit_eteint(self):
        self.config.include_quote = False
        self.olive.digest_opt_quote = "yes"
        self.assertTrue(self.config._section_on(self.olive, "quote"))

    def test_la_personne_eteint_une_section_que_le_gabarit_allume(self):
        self.config.include_quote = True
        self.olive.digest_opt_quote = "no"
        self.assertFalse(self.config._section_on(self.olive, "quote"))

    def test_chaque_section_a_son_interrupteur_des_deux_cotes(self):
        from odoo.addons.daily_todo_digest.models.daily_digest import SECTIONS
        for name in SECTIONS:
            self.assertIn(f"include_{name}", self.config._fields, name)
            self.assertIn(f"digest_opt_{name}", self.olive._fields, name)

    # ------------------------------------------- la personne règle elle-même

    def test_la_personne_ecrit_ses_propres_preferences(self):
        from odoo.addons.daily_todo_digest.models.res_users import DIGEST_SELF_FIELDS
        for champ in DIGEST_SELF_FIELDS:
            self.assertIn(champ, self.olive.SELF_WRITEABLE_FIELDS, champ)
            self.assertIn(champ, self.olive.SELF_READABLE_FIELDS, champ)
        # Et pour de vrai, sous son identité, sans aucun droit d'administrateur.
        self.olive.with_user(self.olive).write({
            "digest_send_hour": "6", "digest_frequency": "weekdays",
            "digest_opt_quote": "no",
        })
        self.assertEqual(self.olive.digest_send_hour, "6")

    def test_la_personne_ne_touche_pas_au_gabarit(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.config.with_user(self.olive).write({"send_hour": 9})


@tagged("post_install", "-at_install")
class TestDigestRelectureDeux(TestDigestPreferences):
    """Les constats 5 à 11 d'une deuxième relecture adverse."""

    def test_aucun_destinataire_se_demande_par_false(self):
        """🔴 `= False` demande « aucun destinataire ». Passé tel quel, il
        devenait `("user_ids", "in", False)` — « sans destinataire NOMMÉ » — et
        un gabarit ne visant qu'un GROUPE se rendait comme n'en ayant aucun.
        """
        Config = self.env["daily.digest.config"]
        vide = Config.create({"name": "ZZ sans personne", "sequence": 90})
        nomme = Config.create({"name": "ZZ nommé", "sequence": 91,
                               "user_ids": [Command.set([self.olive.id])]})
        aucun = Config.search([("recipient_user_ids", "=", False)])
        self.assertIn(vide, aucun)
        self.assertNotIn(self.config, aucun, "le gabarit du groupe vise quelqu'un")
        self.assertNotIn(nomme, aucun)
        quelquun = Config.search([("recipient_user_ids", "!=", False)])
        self.assertIn(self.config, quelquun, "le gabarit du groupe est oublié")
        self.assertIn(nomme, quelquun)
        self.assertNotIn(vide, quelquun)

    def test_une_recherche_par_texte_passe_par_les_personnes(self):
        """⚠️ L'éditeur de filtres d'Odoo pose `ilike` : il levait."""
        Config = self.env["daily.digest.config"]
        trouves = Config.search([("recipient_user_ids", "ilike", "Olive Préférences")])
        self.assertIn(self.config, trouves)
        self.assertEqual(
            Config.search([("recipient_user_ids", "ilike", "zzz personne de ce nom")]),
            Config.browse())
        self.assertNotIn(
            self.config,
            Config.search([("recipient_user_ids", "not ilike", "Olive Préférences")]))
        # Et `in []` garde le sens d'Odoo : ne rien rendre.
        self.assertEqual(Config.search([("recipient_user_ids", "in", [])]),
                         Config.browse())

    def test_un_employe_peut_s_envoyer_un_essai(self):
        """🔴 La section RSS crée le journal d'envoi : sans `sudo`, « Envoyer un
        essai » levait `AccessError` pour qui n'est pas administrateur."""
        self.env["daily.digest.send.log"].search([
            ("config_id", "=", self.config.id), ("user_id", "=", self.olive.id),
        ]).unlink()
        with freeze_time(NOW), patch.object(
                type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_user(self.olive)._get_send_log(self.olive)
            self.config.with_user(self.olive).sudo(False).action_send_test()
        self.assertTrue(self.env["mail.mail"].search_count(
            [("email_to", "=", self.olive.email)]), "aucun essai n'est parti")

    def test_jours_choisis_sans_aucun_jour_se_refuse(self):
        """🔴 Décocher les sept, c'est ne plus jamais recevoir de briefing."""
        from odoo.exceptions import ValidationError
        self.olive.with_user(self.olive).write({"digest_frequency": "custom"})
        with self.assertRaises(ValidationError):
            self.olive.with_user(self.olive).write(
                {field: False for field in WEEKDAY_FIELDS})

    def test_l_heure_qui_n_existe_pas_est_reportee_a_la_suivante(self):
        """🔴 Au passage à l'heure avancée, 2 h n'existe pas : qui avait choisi
        cette heure-là ne recevait rien ce jour-là, en silence, une fois par
        année."""
        MONTREAL = pytz.timezone("America/Montreal")
        montrealais = self.env["res.users"].create({
            "name": "Montréalais Pref", "login": "mtl.pref@example.com",
            "email": "mtl.pref@example.com", "tz": "America/Montreal",
            "groups_id": [Command.set([self.interne.id, self.groupe.id])],
            "digest_send_hour": "2",
        })
        saut = MONTREAL.localize(datetime(2027, 3, 14, 3, 0))   # 2 h n'existe pas
        self.assertTrue(self.config._is_due(montrealais, saut),
                        "le briefing de 2 h doit partir à 3 h ce jour-là")
        ordinaire = MONTREAL.localize(datetime(2027, 3, 21, 3, 0))
        self.assertFalse(self.config._is_due(montrealais, ordinaire),
                         "un autre jour, 3 h n'est pas son heure")
        self.assertTrue(self.config._is_due(
            montrealais, MONTREAL.localize(datetime(2027, 3, 21, 2, 0))))

    def test_un_gabarit_borne_a_une_societe_ne_vise_pas_les_autres(self):
        """Viser un groupe large prenait les usagers de toutes les sociétés."""
        autre_societe = self.env["res.company"].create({"name": "ZZ Société des liens"})
        self.config.company_id = autre_societe
        self.assertEqual(self._mes_paires(), [],
                         "la personne n'est pas de cette société")
        self.olive.write({"company_ids": [Command.link(autre_societe.id)]})
        self.config.invalidate_recordset(["recipient_user_ids"])
        self.assertEqual(len(self._mes_paires()), 1)

    def test_le_de_suit_la_societe_du_destinataire(self):
        """L'en-tête, le logo et le pied prennent la société du destinataire :
        le `De:` la suivait pas."""
        societe = self.env["res.company"].create(
            {"name": "ZZ Société du De", "email": "zz.de@example.com"})
        self.olive.write({"company_ids": [Command.link(societe.id)],
                          "company_id": societe.id})
        with freeze_time(NOW), patch.object(
                type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=self.olive)
        mail = self.env["mail.mail"].search(
            [("email_to", "=", self.olive.email)], order="id desc", limit=1)
        # Odoo formate l'adresse avec le nom : « "Société" <adresse> ».
        self.assertIn("zz.de@example.com", mail.email_from)
        self.assertNotIn(self.env.company.email or "@@@", mail.email_from)


@tagged("post_install", "-at_install")
class TestDigestUnSeulBriefing(TestDigestPreferences):
    """Les constats 4 et 5 d'une relecture adverse."""

    def test_un_usager_archive_ne_recoit_rien(self):
        """Qui est parti ne doit pas recevoir les noms de tâches et de projets.

        ⚠️ Mesuré au banc : `group_ids.users` écarte DÉJÀ les archivés (le
        filtre des actifs s'applique à la lecture du x2many ; il faut
        `active_test=False` pour les voir). Le garde explicite de
        `_resolve_recipients` est donc une ceinture par-dessus les bretelles —
        et elle sert quand l'appelant porte `active_test=False` au contexte,
        ce que fait tout code qui parcourt des enregistrements archivés.
        """
        parti = self.env["res.users"].create({
            "name": "Parti Depuis", "login": "parti.depuis@example.com",
            "email": "parti.depuis@example.com",
            "groups_id": [Command.set([self.interne.id, self.groupe.id])],
        })
        self.assertTrue(self._mes_paires(parti), "il devrait être visé avant d'être archivé")
        parti.active = False
        self.assertEqual(self._mes_paires(parti), [])
        # Et même en forçant la lecture des archivés, le garde tient.
        # ⚠️ `recipient_user_ids` est calculé et NON stocké, sans
        # `depends_context` : sa valeur reste en cache d'une lecture à l'autre,
        # quel que soit le contexte. Il faut l'invalider pour que
        # `active_test=False` change quelque chose.
        self.config.invalidate_recordset(["recipient_user_ids"])
        force = self.config.with_context(active_test=False)
        self.assertIn(parti, force.recipient_user_ids,
                      "sans le filtre des actifs, l'archivé revient")
        self.assertEqual(
            [(u, c) for u, c in force._resolve_recipients() if u == parti], [],
            "le garde de _resolve_recipients doit le retenir quand même")

    def test_un_deuxieme_gabarit_n_envoie_pas_un_deuxieme_briefing_le_meme_jour(self):
        """🔴 La garde « déjà envoyé » était par (gabarit, personne) : changer
        la couverture en cours de journée donnait deux briefings le matin."""
        log = self.config._get_send_log(self.olive)
        log.last_sent = "2026-09-15 16:00:00"          # il est déjà parti à 4 h
        autre = self.env["daily.digest.config"].create({
            "name": "Gabarit de 5 h", "sequence": 1, "send_hour": 5,
            "group_ids": [Command.set([self.groupe.id])],
        })
        cinq_heures = local("2026-09-15 17:00:00")      # 5 h à Auckland
        self.assertEqual(cinq_heures.hour, 5)
        self.assertTrue(autre._is_due(self.olive, cinq_heures),
                        "l'heure du second gabarit est bien venue")
        self.assertTrue(
            autre._deja_envoye_aujourdhui(self.olive, cinq_heures, AUCKLAND),
            "son briefing du jour est déjà parti, par l'autre gabarit")
        # Et un envoi d'avant-hier ne retient rien.
        log.last_sent = "2026-09-13 16:00:00"
        self.assertFalse(autre._deja_envoye_aujourdhui(
            self.olive, cinq_heures, AUCKLAND))

    def test_le_cron_ne_renvoie_rien_quand_la_couverture_change_en_cours_de_jour(self):
        """🔴 La garde n'était éprouvée qu'en méthode : le cron pouvait cesser
        de la consulter sans qu'un essai bronche (mutation survivante du
        11-01). Ici, de bout en bout : 4 h par un gabarit, 5 h par un autre.
        """
        Mail = self.env["mail.mail"]

        def partis():
            return Mail.search_count([("email_to", "=", self.olive.email)])

        Config = self.env["daily.digest.config"]
        with patch.object(type(Mail), "send", lambda self, *a, **k: True):
            with freeze_time(NOW):                      # 4 h à Auckland
                Config._cron_send_daily_digests()
            self.assertEqual(partis(), 1, "le briefing de 4 h n'est pas parti")
            # La couverture change en cours de matinée : le gabarit de 4 h s'en
            # va, un autre prend la relève à 5 h. Son journal, lui, est vierge.
            self.config.active = False
            autre = Config.create({
                "name": "Gabarit de 5 h", "sequence": 1, "send_hour": 5,
                "group_ids": [Command.set([self.groupe.id])],
            })
            self.assertEqual(
                self.env["daily.digest.send.log"].search_count([
                    ("config_id", "=", autre.id), ("user_id", "=", self.olive.id),
                    ("last_sent", "!=", False)]),
                0, "le journal du second gabarit doit être vierge")
            with freeze_time("2026-09-15 17:00:00"):    # 5 h à Auckland
                Config._cron_send_daily_digests()
            self.assertEqual(partis(), 1,
                             "un deuxième briefing est parti le même matin")

    def test_un_journal_date_du_futur_ne_bloque_pas_la_journee(self):
        """⚠️ La question est « aujourd'hui », pas « aujourd'hui ou plus tard »."""
        log = self.config._get_send_log(self.olive)
        log.last_sent = "2026-09-18 16:00:00"          # trois jours plus tard
        self.assertFalse(self.config._deja_envoye_aujourdhui(
            self.olive, local(NOW), AUCKLAND))
        log.last_sent = NOW
        self.assertTrue(self.config._deja_envoye_aujourdhui(
            self.olive, local(NOW), AUCKLAND))

    def test_le_journal_d_une_autre_personne_ne_retient_personne(self):
        collegue = self.env["res.users"].create({
            "name": "Collègue Pref", "login": "collegue.pref@example.com",
            "email": "collegue.pref@example.com",
            "groups_id": [Command.set([self.interne.id, self.groupe.id])],
        })
        self.config._get_send_log(collegue).last_sent = "2026-09-15 16:00:00"
        self.assertFalse(self.config._deja_envoye_aujourdhui(
            self.olive, local(NOW), AUCKLAND))
