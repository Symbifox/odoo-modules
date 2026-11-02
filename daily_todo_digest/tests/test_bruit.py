# -*- coding: utf-8 -*-
"""Moins de bruit dans le briefing.

Mesuré sur un briefing réel : la moitié des activités du jour étaient des
rappels d'entretien aux titres identiques répétés, et plusieurs tâches du jour
attendaient quelqu'un d'autre.

Gelé au 2026-09-15 16:00 UTC = mardi 16 septembre 04:00 à Auckland (NZST,
UTC+12), l'heure d'envoi. La journée d'Auckland court du 2026-09-15 12:00 UTC
au 2026-09-16 12:00 UTC, et c'est là que se joue le défaut du fuseau : la même
échéance se lit « 15/09 » à Montréal et « 16/09 » à Auckland.
"""

import re
from datetime import date, datetime, time
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged

NOW = "2026-09-15 16:00:00"


@tagged("post_install", "-at_install")
class TestDigestBruit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True))
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["daily_todo_digest"], ["fr_CA"], overwrite=True)
        cls.olive = cls.env["res.users"].create({
            "name": "Olive Bruit", "login": "olive.bruit@example.com",
            "email": "olive.bruit@example.com", "lang": "fr_CA",
            "tz": "Pacific/Auckland",
        })
        cls.project = cls.env["project.project"].create({"name": "Projet du bruit"})
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Briefing du bruit",
            "user_ids": [Command.set([cls.olive.id])],
            "include_weather": False, "include_quote": False,
            "include_meetings": False, "include_calendar_events": False,
            "include_week_preview": False, "include_flux_subjects": False,
        })
        cls.partner = cls.env["res.partner"].create({"name": "Cible des rappels"})
        cls.partner_model = cls.env["ir.model"]._get("res.partner")

    # ------------------------------------------------------------- outillage

    def _task(self, name, deadline, **vals):
        valeurs = {
            "name": name, "project_id": self.project.id,
            "user_ids": [Command.set([self.olive.id])],
            "date_deadline": deadline, **vals,
        }
        # 🔴 `bf_time_of_day` réécrit l'HEURE de l'échéance d'après la plage
        # horaire par défaut (la plus proche de maintenant). Sans ce champ à
        # vide, une échéance posée à 02:00 UTC ressortait à 19:00 UTC, hors de
        # la journée que l'essai vise. La clé n'est passée que là où le module
        # est installé.
        if "time_of_day_id" in self.env["project.task"]._fields:
            valeurs["time_of_day_id"] = False
        return self.env["project.task"].create(valeurs)

    def _activity(self, summary, deadline, res_model="res.partner", res_id=None):
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id(res_model),
            "res_id": res_id or self.partner.id,
            "user_id": self.olive.id,
            "summary": summary,
            "date_deadline": deadline,
        })

    def _action_du_lien(self, url):
        """L'action derrière un lien de liste, et l'`active_id` que l'URL porte.

        🔴 Mesuré au navigateur : Odoo 18 ne lit aucun domaine passé dans
        l'URL. Un lien de liste ne peut donc être qu'un `/odoo/<active_id>
        /action-<id>` dont le domaine vit côté serveur.
        """
        trouve = re.search(r"/odoo(?:/(\d+))?/action-(\d+)$", url)
        self.assertTrue(trouve, f"lien hors du patron /odoo/.../action-<id> : {url}")
        action = self.env["ir.actions.act_window"].browse(int(trouve.group(2)))
        self.assertTrue(action.exists(), f"le lien pointe une action absente : {url}")
        return action, int(trouve.group(1)) if trouve.group(1) else None

    def _domaine_evalue(self, action, user, active_id=None, aujourdhui=None):
        """Le domaine comme le client web l'évalue.

        Le client évalue le domaine d'une action avec le contexte de la personne
        qui clique : `uid` est le sien, `active_id` vient de l'URL, et
        `context_today()` est sa journée. ⚠️ Côté navigateur cette journée est
        celle de SON fuseau — ici on passe celle du destinataire, qui est la
        même personne.
        """
        from odoo.tools.safe_eval import safe_eval
        contexte = {
            "uid": user.id,
            "context_today": lambda: aujourdhui or date(2026, 9, 16),
            "datetime": datetime, "time": time, "relativedelta": relativedelta,
        }
        if active_id:
            contexte["active_id"] = active_id
        return safe_eval(action.domain, contexte)

    def _corps(self, user=None):
        user = user or self.olive
        # Gardé en file : un courriel envoyé s'auto-supprime, il n'y aurait
        # rien à relire.
        with freeze_time(NOW), patch.object(
                type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=user)
        mail = self.env["mail.mail"].search(
            [("email_to", "=", user.email)], order="id desc", limit=1)
        self.assertTrue(mail, "aucun courriel produit")
        return mail.body_html

    # -------------------------------------------- repli des rappels récurrents

    def test_les_rappels_recurrents_tiennent_une_seule_ligne(self):
        for _ in range(6):
            self._activity("Backup verification", "2026-09-16")
        self.config.activity_fold_model_ids = [Command.set([self.partner_model.id])]
        corps = self._corps()
        self.assertIn("6 rappel", corps)
        # Le titre répété ne paraît plus six fois : une seule ligne le porte.
        self.assertEqual(corps.count("Backup verification"), 0)
        # 🔴 L'ancienne mesure cherchait « mail.activity » dans l'URL du lien :
        # le modèle n'y vit plus, il vit dans l'action côté serveur.
        action, active_id = self._action_du_lien(
            re.search(r'href="([^"]*action-\d+)"', corps).group(1))
        self.assertEqual(action.res_model, "mail.activity")
        self.assertEqual(active_id, self.partner_model.id)

    def test_sans_repli_les_rappels_paraissent_un_par_un(self):
        for _ in range(6):
            self._activity("Backup verification", "2026-09-16")
        self.config.activity_fold_model_ids = [Command.clear()]
        corps = self._corps()
        self.assertEqual(corps.count("Backup verification"), 6)
        self.assertNotIn("6 rappel", corps)

    def test_le_repli_separe_le_retard_du_jour(self):
        self._activity("Backup verification", "2026-09-16")
        self._activity("Backup verification", "2026-09-10")
        self.config.activity_fold_model_ids = [Command.set([self.partner_model.id])]
        corps = self._corps()
        self.assertIn("1 rappel(s) en retard", corps)
        # Et la ligne du jour, qui ne dit pas « en retard ».
        self.assertEqual(corps.count("1 rappel(s)"), 2)

    def test_une_activite_qui_se_nomme_comme_son_enregistrement_ne_se_dit_pas_deux_fois(self):
        jumeau = self.env["res.partner"].create({"name": "Backup verification"})
        self._activity("Backup verification", "2026-09-16", res_id=jumeau.id)
        self.config.activity_fold_model_ids = [Command.clear()]
        corps = self._corps()
        self.assertEqual(corps.count("Backup verification"), 1)

    # ------------------------------------------------- tâches en attente

    def test_une_tache_en_attente_quitte_le_jour_et_se_compte(self):
        self._task("Attend le client", "2026-09-16 02:00:00", state="04_waiting_normal")
        self._task("À faire aujourd'hui", "2026-09-16 02:00:00")
        corps = self._corps()
        self.assertNotIn("Attend le client", corps)
        self.assertIn("À faire aujourd&#39;hui", corps)
        self.assertIn("1 tâche", corps)
        self.assertIn("attente", corps)

    def test_la_case_ramene_les_taches_en_attente_dans_la_liste(self):
        self._task("Attend le client", "2026-09-16 02:00:00", state="04_waiting_normal")
        self.config.include_waiting_tasks = True
        corps = self._corps()
        self.assertIn("Attend le client", corps)

    def test_les_etats_d_attente_sont_ceux_que_la_base_declare(self):
        declares = dict(
            self.env["project.task"].fields_get(["state"])["state"]["selection"])
        for state in self.config._waiting_states():
            self.assertIn(state, declares)
        self.assertIn("04_waiting_normal", self.config._waiting_states())

    def test_une_tache_fermee_ne_parait_jamais(self):
        self._task("Déjà faite", "2026-09-16 02:00:00", state="1_done")
        self._task("Annulée", "2026-09-16 02:00:00", state="1_canceled")
        # Un briefing qui n'a rien à dire ne part pas : il faut une ligne.
        self._task("Bien vivante", "2026-09-16 02:00:00")
        corps = self._corps()
        self.assertNotIn("Déjà faite", corps)
        self.assertNotIn("Annulée", corps)

    # -------------------------------------- fuseau des échéances affichées

    def test_l_echeance_affichee_est_dans_le_fuseau_du_destinataire(self):
        # 2026-09-16 02:00 UTC : le 16 à Auckland (14:00), le 15 à Montréal (22:00).
        self._task("Tâche à la bascule", "2026-09-16 02:00:00")
        corps = self._corps()
        self.assertIn("16/09", corps)
        self.assertNotIn("15/09", corps)

    # ------------------------------------------------------- échappement

    def test_le_texte_des_taches_et_des_activites_est_echappe(self):
        self._task("<b>Boum</b>", "2026-09-16 02:00:00")
        self._activity("<i>Rappel</i>", "2026-09-16")
        self.config.activity_fold_model_ids = [Command.clear()]
        corps = self._corps()
        self.assertNotIn("<b>Boum</b>", corps)
        self.assertIn("&lt;b&gt;Boum&lt;/b&gt;", corps)
        self.assertNotIn("<i>Rappel</i>", corps)
        self.assertIn("&lt;i&gt;Rappel&lt;/i&gt;", corps)

    # --------------------------------------------- la section morte vit

    def test_les_taches_a_venir_se_rendent_quand_on_les_demande(self):
        self._task("Demain", "2026-09-17 02:00:00")
        self.config.include_upcoming_tasks = True
        self.config.upcoming_days = 3
        corps = self._corps()
        self.assertIn("Demain", corps)

    def test_les_taches_a_venir_se_taisent_quand_la_case_est_fermee(self):
        self._task("Demain", "2026-09-17 02:00:00")
        self._task("Aujourd'hui", "2026-09-16 02:00:00")
        self.config.include_upcoming_tasks = False
        corps = self._corps()
        self.assertNotIn("Demain", corps)

    def test_la_grille_de_semaine_suit_sa_case(self):
        self._task("Dans trois jours", "2026-09-19 02:00:00")
        # De la matière pour aujourd'hui : un briefing vide ne part pas.
        self._task("Aujourd'hui", "2026-09-16 02:00:00")
        self.config.include_week_preview = True
        self.assertIn("7 prochains jours", self._corps())
        self.config.include_week_preview = False
        self.assertNotIn("7 prochains jours", self._corps())


@tagged("post_install", "-at_install")
class TestDigestRelectureAdverse(TestDigestBruit):
    """Les constats d'une relecture adverse, un essai chacun.

    Chaque essai ici est né d'un défaut réel trouvé après que les 54 premiers
    essais étaient verts. Ils gardent la frontière que ces 54 ne gardaient
    pas : ce que le courriel CONTIENT, et pas seulement qu'il existe.
    """

    # ----------------------------------------------- constat 1 (BLOQUANT)

    def test_le_lien_de_la_ligne_repliee_ouvre_exactement_ce_qu_elle_compte(self):
        """🔴 L'ancien essai décodait un `&domain=` d'URL qu'Odoo 18 ne lit
        JAMAIS : vert, et gardant une couche morte. Celui-ci évalue le domaine
        de l'action, côté serveur, et compte ce que la liste rendrait.
        """
        for _ in range(6):
            self._activity("Backup verification", "2026-09-16")
        self._activity("Backup verification", "2026-09-10")
        self.config.activity_fold_model_ids = [Command.set([self.partner_model.id])]
        with freeze_time(NOW):
            data = self.config._gather_digest_data(
                self.olive, tz_name="Pacific/Auckland")
        # Un rappel du même modèle pour quelqu'un d'autre, et un de MOI sur un
        # autre modèle : ni l'un ni l'autre n'a d'affaire dans la liste.
        autre = self.env["res.users"].create({
            "name": "Autre Repli", "login": "autre.repli@example.com",
            "email": "autre.repli@example.com"})
        self.env["mail.activity"].create({
            "res_model_id": self.partner_model.id, "res_id": self.partner.id,
            "user_id": autre.id, "summary": "Pas la mienne",
            "date_deadline": "2026-09-16"})
        self._activity("Sur un autre modèle", "2026-09-16", res_model="project.project",
                       res_id=self.project.id)
        for seau, compte in (("today_activities", 6), ("overdue_activities", 1)):
            replies = data[seau]["folded"]
            self.assertEqual(len(replies), 1, f"{seau} : une ligne repliée attendue")
            self.assertEqual(replies[0]["count"], compte)
            action, active_id = self._action_du_lien(replies[0]["link"])
            self.assertEqual(action.res_model, "mail.activity")
            self.assertEqual(active_id, self.partner_model.id,
                             f"{seau} : le modèle replié n'est pas visé")
            # ⚠️ Sans borne de date, à dessein : le navigateur évaluerait
            # `context_today()` dans SON fuseau. La liste porte donc les sept
            # rappels repliés de la personne sur ce modèle — les siens, et ce
            # modèle-là seulement.
            vus = self.env["mail.activity"].search(
                self._domaine_evalue(action, self.olive, active_id))
            self.assertEqual(len(vus), 7, f"{seau} : la liste ne porte pas ses rappels")
            self.assertEqual(set(vus.mapped("user_id")), {self.olive})
            self.assertEqual(set(vus.mapped("res_model")), {"res.partner"})

    def test_le_lien_des_taches_en_attente_ouvre_celles_du_destinataire(self):
        """🔴 Le domaine vit côté serveur et filtre sur `uid` : la personne qui
        clique voit les SIENNES, et l'usager du cron ne s'en mêle pas."""
        mienne = self._task("Attend le client", "2026-09-16 02:00:00",
                            state="04_waiting_normal")
        autre = self.env["res.users"].create({
            "name": "Autre Attente", "login": "autre.attente@example.com",
            "email": "autre.attente@example.com"})
        sienne = self._task("Attend pour quelqu'un d'autre", "2026-09-16 02:00:00",
                            state="04_waiting_normal",
                            user_ids=[Command.set([autre.id])])
        action = self.env.ref("daily_todo_digest.action_digest_waiting_tasks")
        self.assertIn(f"/action-{action.id}", self._corps())
        Task = self.env["project.task"]
        vues = Task.search(self._domaine_evalue(action, self.olive))
        self.assertIn(mienne, vues)
        self.assertNotIn(sienne, vues, "la liste montre la tâche de quelqu'un d'autre")
        self.assertIn(sienne, Task.search(self._domaine_evalue(action, autre)))

    def test_aucun_lien_du_briefing_ne_porte_un_domaine_d_url(self):
        """🔴 `&domain=` n'est lu par aucune couche d'Odoo 18, et `model=` sans
        `action=` retombe sur l'accueil. Un seul lien de cette forme et la
        personne atterrit ailleurs, en silence. Toutes les actions visées
        doivent exister."""
        self._activity("Backup verification", "2026-09-16")
        self.config.activity_fold_model_ids = [Command.set([self.partner_model.id])]
        self.config.include_week_preview = True
        self._task("Cachée", "2026-09-16 02:00:00", display_in_project=False)
        self._task("Attend", "2026-09-16 02:00:00", state="04_waiting_normal")
        corps = self._corps()
        self.assertNotIn("domain=", corps, "un lien porte encore un domaine d'URL")
        self.assertNotIn("/web#model=", corps, "un lien ouvrirait l'écran d'accueil")
        actions = set(re.findall(r"/action-(\d+)", corps))
        self.assertTrue(actions, "aucun lien de liste dans le briefing")
        for ident in actions:
            self.assertTrue(
                self.env["ir.actions.act_window"].browse(int(ident)).exists(),
                f"le briefing pointe l'action {ident}, qui n'existe pas")

    # ----------------------------------------------- constat 2 (BLOQUANT)

    def _avec_meteo(self):
        """La météo sans sortir sur le réseau."""
        return patch.object(
            type(self.config), "_get_weather",
            lambda self, user=None: {
                "city": "Wellington", "current_temp": 14, "high": 21, "low": 14,
                "precipitation": 0, "precipitation_prob": 0,
                "emoji": "☁️", "description": "Couvert",
            })

    def test_la_personne_eteint_la_meteo_que_le_gabarit_allume(self):
        self._task("Quelque chose", "2026-09-16 02:00:00")
        self.config.include_weather = True
        with self._avec_meteo():
            self.assertIn("Wellington", self._corps())
            self.olive.digest_opt_weather = "no"
            self.assertNotIn("Wellington", self._corps())

    def test_la_personne_allume_la_meteo_que_le_gabarit_eteint(self):
        self._task("Quelque chose", "2026-09-16 02:00:00")
        self.config.include_weather = False
        with self._avec_meteo():
            self.assertNotIn("Wellington", self._corps())
            self.olive.digest_opt_weather = "yes"
            self.assertIn("Wellington", self._corps())

    def test_la_personne_eteint_la_citation_que_le_gabarit_allume(self):
        self._task("Quelque chose", "2026-09-16 02:00:00")
        self.config.include_quote = True
        self.assertIn("font-style:italic", self._corps())
        self.olive.digest_opt_quote = "no"
        self.assertNotIn("font-style:italic", self._corps())

    def test_le_rendu_tient_la_citation_eteinte_meme_avec_la_matiere(self):
        """La cueillette décide déjà de la citation (`on("quote")`), donc la
        ceinture du RENDU ne change rien en production — et pouvait tomber sans
        qu'un essai bronche (mutation survivante). Éprouvée ici sur le
        chemin du rendu seul, matière en main."""
        self.config.include_quote = True
        gabarit = self.config.with_context(
            lang=self.olive.lang or "en_US", tz=self.olive.tz)
        with freeze_time(NOW):
            data = gabarit._gather_digest_data(self.olive, tz_name="Pacific/Auckland")
            self.assertTrue(data.get("quote"), "le gabarit allumé donne une citation")
            self.assertIn("font-style:italic", gabarit._generate_html(data, self.olive))
            self.olive.digest_opt_quote = "no"
            self.assertNotIn("font-style:italic", gabarit._generate_html(data, self.olive))

    def test_la_ville_de_la_meteo_est_echappee(self):
        """Elle est écrite par la personne elle-même (SELF_WRITEABLE_FIELDS)."""
        self.config.include_weather = True
        with patch.object(
                type(self.config), "_get_weather",
                lambda s, user=None: {
                    "city": "<b>Ici</b>", "current_temp": 1, "high": 2, "low": 0,
                    "precipitation": 0, "precipitation_prob": 0,
                    "emoji": "☁️", "description": "Couvert"}):
            corps = self._corps()
        self.assertNotIn("<b>Ici</b>", corps)
        self.assertIn("&lt;b&gt;Ici&lt;/b&gt;", corps)

    # ----------------------------------------------- constat 6

    def test_un_briefing_sans_matiere_part_quand_meme(self):
        """🔴 Les onze satellites greffent leur section pendant le RENDU : se
        taire avant `_generate_html` avalerait leurs avis."""
        self.config.include_weather = False
        self.config.include_quote = False
        self.assertIn("en retard ou prévue aujourd", self._corps())

    def test_un_briefing_dont_tout_est_eteint_ne_part_pas(self):
        """⚠️ « Tout » comprend les sections des satellites : chacun déclare son
        `include_<nom>` sur le gabarit, et une seule allumée fait partir le
        briefing. Éteindre les onze du cœur ne suffit plus — c'est voulu."""
        from odoo.addons.daily_todo_digest.models.daily_digest import (
            NON_SECTION_INCLUDES, SECTIONS)
        self._task("Quelque chose", "2026-09-16 02:00:00")
        for nom in SECTIONS:
            self.olive[f"digest_opt_{nom}"] = "no"
        self.config.write({
            nom: False for nom in self.config._fields
            if nom.startswith("include_")
            and nom[len("include_"):] not in NON_SECTION_INCLUDES
        })
        avant = self.env["mail.mail"].search_count([("email_to", "=", self.olive.email)])
        with freeze_time(NOW), patch.object(
                type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=self.olive)
        self.assertEqual(
            self.env["mail.mail"].search_count([("email_to", "=", self.olive.email)]),
            avant, "un briefing est parti alors que tout est éteint")

    # ----------------------------------------------- invariants

    def test_la_section_d_un_satellite_fait_partir_le_briefing(self):
        """🔴 Les onze satellites greffent leur section pendant le RENDU, après
        la décision de se taire. Qui éteint les onze sections du cœur pour ne
        garder QUE ses avis devops ou ses paliers de banques d'heures — un geste
        d'un clic — ne recevait plus rien du tout.
        """
        from odoo.addons.daily_todo_digest.models.daily_digest import (
            NON_SECTION_INCLUDES, SECTIONS)
        Config = self.env["daily.digest.config"]
        satellites = [
            nom for nom in Config._fields
            if nom.startswith("include_")
            and nom[len("include_"):] not in SECTIONS
            and nom[len("include_"):] not in NON_SECTION_INCLUDES
        ]
        if not satellites:
            self.skipTest("aucun module satellite installé sur ce banc")
        for nom in SECTIONS:
            self.olive[f"digest_opt_{nom}"] = "no"
        self.config.write({nom: False for nom in satellites})
        self.assertFalse(self.config._any_section_on(self.olive))
        self.config[satellites[0]] = True
        self.assertTrue(
            self.config._any_section_on(self.olive),
            f"{satellites[0]} est allumé : le briefing doit partir")
        avant = self.env["mail.mail"].search_count(
            [("email_to", "=", self.olive.email)])
        with freeze_time(NOW), patch.object(
                type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=self.olive)
        self.assertEqual(
            self.env["mail.mail"].search_count([("email_to", "=", self.olive.email)]),
            avant + 1, "la section du satellite n'a pas fait partir le briefing")

    def test_une_passe_ne_cueille_que_pour_une_personne(self):
        """⚠️ `assertRaises(ValueError)` seul passait pour la mauvaise raison :
        c'est le « Expected singleton » d'Odoo qui levait, et la garde pouvait
        disparaître sans qu'un essai bronche (mutation survivante)."""
        autre = self.env["res.users"].create({
            "name": "Autre Bruit", "login": "autre.bruit@example.com",
            "email": "autre.bruit@example.com"})
        with self.assertRaisesRegex(ValueError, "exactly one recipient"):
            self.config._gather_digest_data(self.olive | autre, tz_name="UTC")
        with self.assertRaisesRegex(ValueError, "exactly one recipient"):
            self.config._gather_digest_data(self.env["res.users"], tz_name="UTC")
        with self.assertRaisesRegex(ValueError, "exactly one recipient"):
            self.config._gather_digest_data(tz_name="UTC")

    def test_un_nom_fait_d_espaces_ne_fait_pas_perdre_le_briefing(self):
        self.olive.name = "   "
        self._task("Quelque chose", "2026-09-16 02:00:00")
        self.assertIn("Bonjour", self._corps())
        self.olive.name = "Olive Bruit"
