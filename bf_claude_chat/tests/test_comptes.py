"""Les comptes Claude et leurs fenêtres.

Ce que ces tests protègent n'est pas l'arithmétique des pourcentages, que le
serveur rend tout faits, mais les quatre façons dont ce modèle pourrait mentir
sans jamais lever d'erreur :

* une fenêtre non mesurée qui se mettrait à valoir zéro, donc à se lire
  « rien consommé » ;
* un relevé fusionné au lieu d'être remplacé, donc un jugement porté sur du
  périmé ;
* un `Float` qui ne sait pas valoir « inconnu », donc une bascule absente qui
  passe sous n'importe quel préavis ;
* un filtre posé sur un champ calculé non stocké, qui rendrait TOUTE la table
  en ayant l'air de filtrer.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


def _releve(five=None, seven=None, opus=None, bascule=None, forfait="max"):
    """Fabrique une charge utile de /api/oauth/usage."""
    charge = {"subscription_type": forfait}
    if five is not None:
        charge["five_hour"] = {"utilization": five,
                               "resets_at": "2026-09-11T13:00:00Z"}
    if seven is not None:
        charge["seven_day"] = {"utilization": seven, "resets_at": bascule}
    if opus is not None:
        charge["seven_day_opus"] = {"utilization": opus, "resets_at": bascule}
    return charge


@tagged("post_install", "-at_install")
class TestComptesClaude(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Compte = self.env["claude.account"]
        self.Fenetre = self.env["claude.account.window"]

    def _dans(self, heures):
        from datetime import timedelta

        from odoo import fields
        return (fields.Datetime.now() + timedelta(hours=heures)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    # ── L'identité ────────────────────────────────────────────────────
    def test_le_repertoire_est_la_cle_pas_le_nom(self):
        premier = self.Compte.compte_par_repertoire("/home/x/.claude", nom="Un")
        second = self.Compte.compte_par_repertoire("/home/x/.claude", nom="Deux")
        self.assertEqual(premier, second)
        self.assertEqual(premier.name, "Un", "le nom du premier doit tenir")

    def test_deux_repertoires_sont_deux_comptes(self):
        a = self.Compte.compte_par_repertoire("/home/x/.claude")
        b = self.Compte.compte_par_repertoire("/home/x/.claude-bsi")
        self.assertNotEqual(a, b)
        self.assertEqual(b.name, ".claude-bsi", "le nom se déduit du chemin")

    # ── Ce qui se mesure, et ce qui ne se mesure pas ──────────────────
    def test_une_fenetre_non_rendue_n_a_pas_de_ligne(self):
        """Le piège central : zéro se lit « gratuit », l'absence se lit « inconnu »."""
        cid = self.Compte.enregistrer_releve(
            "/home/x/.claude", charge=_releve(five=22.0, seven=6.0,
                                              bascule=self._dans(160)))
        compte = self.Compte.browse(cid)
        fenetres = compte.window_ids.mapped("fenetre")
        self.assertEqual(sorted(fenetres), ["five_hour", "seven_day"])
        self.assertNotIn("seven_day_opus", fenetres)

    def test_une_fenetre_rendue_sans_valeur_n_a_pas_de_ligne_non_plus(self):
        charge = _releve(five=22.0, bascule=self._dans(160))
        charge["seven_day"] = {"utilization": None,
                               "resets_at": self._dans(160)}
        cid = self.Compte.enregistrer_releve("/home/x/.claude", charge=charge)
        compte = self.Compte.browse(cid)
        self.assertEqual(compte.window_ids.mapped("fenetre"), ["five_hour"])

    def test_un_releve_remplace_les_fenetres_il_ne_les_fusionne_pas(self):
        self.Compte.enregistrer_releve(
            "/home/x/.claude", charge=_releve(five=22.0, seven=6.0, opus=3.0,
                                              bascule=self._dans(160)))
        cid = self.Compte.enregistrer_releve(
            "/home/x/.claude", charge=_releve(five=30.0,
                                              bascule=self._dans(160)))
        compte = self.Compte.browse(cid)
        self.assertEqual(compte.window_ids.mapped("fenetre"), ["five_hour"],
                         "une fenêtre que le serveur ne rend plus doit partir")
        self.assertEqual(compte.window_ids.utilization, 30.0)

    def test_l_horodatage_arrive_naif_en_utc(self):
        cid = self.Compte.enregistrer_releve(
            "/home/x/.claude",
            charge=_releve(seven=6.0, bascule="2026-09-18T07:00:00Z"))
        fenetre = self.Compte.browse(cid).window_ids
        self.assertIsNone(fenetre.resets_at.tzinfo,
                          "un datetime portant son fuseau décalerait la base")
        self.assertEqual(fenetre.resets_at.hour, 7)

    # ── Le jugement ───────────────────────────────────────────────────
    def _compte_avec(self, **kw):
        cid = self.Compte.enregistrer_releve("/home/x/.claude",
                                             charge=_releve(**kw))
        return self.Compte.browse(cid)

    def test_dans_les_clous(self):
        compte = self._compte_avec(five=22.0, seven=6.0,
                                   bascule=self._dans(160))
        self.assertEqual(compte.etat, "ok")

    def test_plafond_hebdomadaire(self):
        compte = self._compte_avec(seven=84.0, bascule=self._dans(50))
        self.assertEqual(compte.etat, "plafond")

    def test_plafond_de_session(self):
        compte = self._compte_avec(five=93.0, seven=6.0,
                                   bascule=self._dans(160))
        self.assertEqual(compte.etat, "plafond")

    def test_solde_dormant(self):
        compte = self._compte_avec(seven=12.0, bascule=self._dans(18))
        self.assertEqual(compte.etat, "dormant")

    def test_solde_bas_mais_bascule_encore_loin(self):
        compte = self._compte_avec(seven=12.0, bascule=self._dans(70))
        self.assertEqual(compte.etat, "ok")

    def test_une_bascule_inconnue_ne_fait_pas_crier_au_solde_dormant(self):
        """🔴 Le `Float` qui ne sait pas valoir « inconnu ».

        `heures_restantes` rend 0.0 quand la remise à zéro manque, et 0.0 passe
        sous n'importe quel préavis. La garde doit donc porter sur `resets_at`.
        """
        compte = self._compte_avec(seven=12.0, bascule=None)
        self.assertFalse(compte.window_ids.resets_at)
        self.assertEqual(compte.etat, "ok")

    def test_un_compte_sans_releve_utilisable_ne_juge_rien(self):
        cid = self.Compte.enregistrer_releve(
            "/home/x/.claude-bsi",
            erreur="session éteinte : jeton d'accès blanchi")
        compte = self.Compte.browse(cid)
        self.assertEqual(compte.etat_sonde, "session_morte")
        self.assertEqual(compte.etat, "muet")

    def test_les_seuils_du_compte_priment(self):
        compte = self._compte_avec(seven=50.0, bascule=self._dans(160))
        self.assertEqual(compte.etat, "ok")
        compte.seuil_haut = 45.0
        self.assertEqual(compte.etat, "plafond",
                         "le seuil vit au compte, pas dans la sonde")

    # ── Le filtre ─────────────────────────────────────────────────────
    def test_filtrer_sur_l_etat_ne_rend_pas_toute_la_table(self):
        """🔴 Un calculé non stocké sans `search=` rend TOUT, sans le dire."""
        dormant = self._compte_avec(seven=12.0, bascule=self._dans(18))
        tranquille_id = self.Compte.enregistrer_releve(
            "/home/y/.claude",
            charge=_releve(seven=10.0, bascule=self._dans(160)))
        tranquille = self.Compte.browse(tranquille_id)
        trouves = self.Compte.search([("etat", "=", "dormant")])
        self.assertIn(dormant, trouves)
        self.assertNotIn(tranquille, trouves)
        self.assertIn(tranquille, self.Compte.search([("etat", "!=", "dormant")]))

    # ── Ce qui ne doit jamais casser l'appelant ───────────────────────
    def test_une_charge_utile_difforme_ne_leve_rien(self):
        for charge in ({}, {"seven_day": "pas un dictionnaire"},
                       {"seven_day": {"utilization": "beaucoup"}}):
            self.assertTrue(
                self.Compte.enregistrer_releve("/home/z/.claude", charge=charge)
                is not None)

    def test_un_releve_en_echec_garde_les_fenetres_d_avant(self):
        """Les effacer ferait disparaître la seule chose qui reste à regarder."""
        self.Compte.enregistrer_releve(
            "/home/x/.claude", charge=_releve(seven=6.0,
                                              bascule=self._dans(160)))
        cid = self.Compte.enregistrer_releve("/home/x/.claude",
                                             erreur="HTTP 401 : jeton périmé")
        compte = self.Compte.browse(cid)
        self.assertEqual(compte.etat_sonde, "jeton_perime")
        self.assertTrue(compte.window_ids, "le dernier relevé connu doit tenir")

    # ── Les seuils ────────────────────────────────────────────────────
    def test_un_seuil_bas_au_dessus_du_haut_est_refuse(self):
        compte = self.Compte.compte_par_repertoire("/home/x/.claude")
        with self.assertRaises(ValidationError):
            compte.seuil_bas = 90.0

    def test_un_seuil_hors_pourcentage_est_refuse(self):
        compte = self.Compte.compte_par_repertoire("/home/x/.claude")
        with self.assertRaises(ValidationError):
            compte.seuil_haut = 140.0

    # ── Le lien avec le registre ──────────────────────────────────────
    def test_une_passe_peut_nommer_le_compte_qui_a_paye(self):
        ligne_id = self.env["claude.chat.message"].journaliser_passe(
            "refine_meeting", {"output_tokens": 10},
            res_model="meeting.record", res_id=777,
            compte="/home/x/.claude")
        ligne = self.env["claude.chat.message"].browse(ligne_id)
        self.assertEqual(ligne.session_id.account_id.config_dir,
                         "/home/x/.claude")
        self.assertEqual(ligne.account_id, ligne.session_id.account_id,
                         "la ligne recopie le compte, comme elle recopie l'usager")

    def test_une_passe_sans_compte_reste_visiblement_non_attribuee(self):
        """Un seau caché se lit comme zéro : il doit rester cherchable."""
        ligne_id = self.env["claude.chat.message"].journaliser_passe(
            "title", {"output_tokens": 5})
        ligne = self.env["claude.chat.message"].browse(ligne_id)
        self.assertFalse(ligne.account_id)
        self.assertIn(ligne, self.env["claude.chat.message"].search(
            [("account_id", "=", False), ("id", "=", ligne.id)]))


# Relevé réel de /api/oauth/usage, capturé le 2026-09-11 sur le compte Blue Fox.
# Gardé VERBATIM, y compris ce qu'on n'exploite pas : c'est la seule façon de
# voir arriver un changement de forme du serveur autrement que par une panne en
# production. Aucun secret là-dedans, que des pourcentages et des dates.
CHARGE_REELLE = {
    "five_hour": {
        "utilization": 25.0,
        "resets_at": "2026-09-11T13:00:00.203537+00:00",
        "limit_dollars": None, "used_dollars": None,
        "remaining_dollars": None, "locked_reason": None,
    },
    "seven_day": {
        "utilization": 7.0,
        "resets_at": "2026-09-18T07:00:00.203561+00:00",
        "limit_dollars": None, "used_dollars": None,
        "remaining_dollars": None, "locked_reason": None,
    },
    # ⚠️ Les fenêtres non mesurées arrivent à None, elles ne sont PAS absentes.
    "seven_day_oauth_apps": None,
    "seven_day_opus": None,
    "seven_day_sonnet": None,
    "seven_day_cowork": None,
    # Une fenêtre qu'on ne connaît pas, et qui porte un vrai zéro.
    "nimbus_quill": {"utilization": 0.0, "resets_at": None},
    "juniper_tide": {"eligible": False, "in_experiment": False},
    "extra_usage": None,
    "limits": [
        {"kind": "session", "group": "session", "percent": 25,
         "severity": "normal",
         "resets_at": "2026-09-11T13:00:00.203537+00:00", "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 7,
         "severity": "normal",
         "resets_at": "2026-09-18T07:00:00.203561+00:00", "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 1,
         "severity": "normal",
         "resets_at": "2026-09-18T07:00:00.203795+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}},
         "is_active": False},
    ],
    "seven_day_breakdown": {
        "as_of": "2026-09-11T11:28:50.222708+00:00",
        "window_started_at": "2026-09-11T07:00:00.203561+00:00",
        "rows": [
            {"key": "claude_code", "display_name": "Claude Code", "percent": 100},
            {"key": "chat", "display_name": "Chats", "percent": 0},
        ],
    },
}


@tagged("post_install", "-at_install")
class TestChargeUtileReelle(TransactionCase):
    """Le modèle contre le vrai relevé, pas contre une maquette.

    Les maquettes des tests d'au-dessus sont propres ; le serveur, lui, rend une
    douzaine de clés, des fenêtres à None, des noms qu'on ne connaît pas, et des
    horodatages à la microseconde. C'est là que les hypothèses se cassent.
    """

    def setUp(self):
        super().setUp()
        self.Compte = self.env["claude.account"]

    def test_le_vrai_releve_ne_produit_que_les_fenetres_mesurees(self):
        cid = self.Compte.enregistrer_releve("/home/livv/.claude",
                                             nom="Blue Fox",
                                             charge=CHARGE_REELLE)
        compte = self.Compte.browse(cid)
        self.assertEqual(sorted(compte.window_ids.mapped("fenetre")),
                         ["five_hour", "seven_day"])

    def test_une_fenetre_a_none_ne_devient_pas_un_zero(self):
        """`seven_day_opus` arrive à None, pas absente. Zéro se lirait « gratuit »."""
        cid = self.Compte.enregistrer_releve("/home/livv/.claude",
                                             charge=CHARGE_REELLE)
        compte = self.Compte.browse(cid)
        self.assertNotIn("seven_day_opus", compte.window_ids.mapped("fenetre"))

    def test_une_fenetre_inconnue_est_ignoree(self):
        """`nimbus_quill` porte un vrai 0.0 et n'a rien à faire dans le compte."""
        cid = self.Compte.enregistrer_releve("/home/livv/.claude",
                                             charge=CHARGE_REELLE)
        compte = self.Compte.browse(cid)
        self.assertEqual(len(compte.window_ids), 2)

    def test_l_horodatage_a_la_microseconde_se_lit(self):
        cid = self.Compte.enregistrer_releve("/home/livv/.claude",
                                             charge=CHARGE_REELLE)
        semaine = self.Compte.browse(cid).window_ids.filtered(
            lambda w: w.fenetre == "seven_day")
        self.assertTrue(semaine.resets_at)
        self.assertEqual(semaine.resets_at.hour, 7,
                         "07:00 UTC, soit 03:00 à Montréal : la bascule du jeudi")
        self.assertIsNone(semaine.resets_at.tzinfo)

    def test_la_microseconde_bouge_d_un_releve_a_l_autre(self):
        """🔴 Ce qui condamne toute mémoire indexée sur l'horodatage BRUT.

        Le serveur recalcule la borne à chaque appel : même bascule, microsecondes
        différentes. C'est pourquoi la sonde arrondit sa clé d'avis à l'heure.
        """
        import copy
        deuxieme = copy.deepcopy(CHARGE_REELLE)
        deuxieme["seven_day"]["resets_at"] = "2026-09-18T07:00:00.998877+00:00"
        premier_id = self.Compte.enregistrer_releve("/home/a/.claude",
                                                    charge=CHARGE_REELLE)
        second_id = self.Compte.enregistrer_releve("/home/b/.claude",
                                                   charge=deuxieme)
        bornes = [self.Compte.browse(i).window_ids.filtered(
            lambda w: w.fenetre == "seven_day").resets_at
            for i in (premier_id, second_id)]
        self.assertNotEqual(bornes[0], bornes[1], "les bruts diffèrent")
        self.assertEqual(bornes[0].replace(microsecond=0),
                         bornes[1].replace(microsecond=0),
                         "à la seconde près, c'est la même bascule")

    def test_un_releve_sans_forfait_ni_credits_ne_casse_rien(self):
        """Le vrai relevé ne porte ni `subscription_type` ni `extra_usage`."""
        cid = self.Compte.enregistrer_releve("/home/livv/.claude",
                                             charge=CHARGE_REELLE)
        compte = self.Compte.browse(cid)
        self.assertFalse(compte.subscription_type)
        self.assertFalse(compte.credits_actifs)
        self.assertEqual(compte.etat_sonde, "ok")
        self.assertEqual(compte.etat, "ok")


@tagged("post_install", "-at_install")
class TestComptesAdminsSeulement(TransactionCase):
    """« Admins seulement », posé à la demande et pas par prudence de principe.

    Ce que ces écrans montrent, c'est ce qu'un abonnement a encore sous le pied.
    Ça ne regarde pas les usagers, et sur un locataire client ça regarde encore
    moins le client.
    """

    def setUp(self):
        super().setUp()
        self.usager = self.env["res.users"].create({
            "name": "Usager ordinaire",
            "login": "usager_jetons_25577",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })

    def test_un_usager_ordinaire_ne_lit_pas_les_comptes(self):
        from odoo.exceptions import AccessError
        self.env["claude.account"].create({
            "name": "Blue Fox", "config_dir": "/home/x/.claude"})
        with self.assertRaises(AccessError):
            self.env["claude.account"].with_user(self.usager).search([])

    def test_un_usager_ordinaire_ne_lit_pas_les_fenetres(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env["claude.account.window"].with_user(self.usager).search([])

    def test_un_admin_les_lit(self):
        admin = self.env.ref("base.user_admin")
        compte = self.env["claude.account"].with_user(admin).create({
            "name": "Blue Fox", "config_dir": "/home/x/.claude"})
        self.assertTrue(compte.with_user(admin).read(["name"]))

    def test_le_registre_reste_lisible_par_l_usager(self):
        """⚠️ Fermer le compte ne doit pas fermer le clavardage.

        `claude.chat.message` porte un `account_id` related : si sa lecture
        exigeait un droit sur `claude.account`, le panneau Gen tomberait pour
        tout le monde sauf les admins.
        """
        session = self.env["claude.chat.session"].with_user(self.usager).create(
            {"name": "Essai"})
        ligne = self.env["claude.chat.message"].with_user(self.usager).create({
            "session_id": session.id, "role": "user", "content": "bonjour"})
        self.assertFalse(ligne.account_id)
        self.assertTrue(ligne.with_user(self.usager).read(
            ["content", "account_id"]))


@tagged("post_install", "-at_install")
class TestSeuilsRendusALaSonde(TransactionCase):
    """La sonde n'est pas admin, et doit quand même connaître les seuils.

    Sans ça, ils vivraient en double (ligne de cron et fiche) et dériveraient.
    """

    def setUp(self):
        super().setUp()
        self.Compte = self.env["claude.account"]

    def test_les_seuils_reviennent(self):
        compte = self.Compte.compte_par_repertoire("/home/x/.claude")
        compte.write({"seuil_haut": 70.0, "seuil_bas": 25.0, "preavis_h": 30.0})
        seuils = self.Compte.seuils_du_compte("/home/x/.claude")
        self.assertEqual(seuils["seuil_haut"], 70.0)
        self.assertEqual(seuils["seuil_bas"], 25.0)
        self.assertEqual(seuils["preavis_h"], 30.0)
        self.assertEqual(seuils["seuil_session"], 90.0, "le défaut tient")

    def test_un_compte_inconnu_rend_un_dictionnaire_vide(self):
        """Vide et pas une erreur : la sonde retombe sur ses défauts."""
        self.assertEqual(self.Compte.seuils_du_compte("/jamais/vu"), {})

    def test_un_repertoire_vide_ne_leve_rien(self):
        self.assertEqual(self.Compte.seuils_du_compte(False), {})

    def test_un_compte_archive_rend_quand_meme_ses_seuils(self):
        """Archivé veut dire « plus à l'écran », pas « plus mesuré »."""
        compte = self.Compte.compte_par_repertoire("/home/x/.claude")
        compte.active = False
        self.assertTrue(self.Compte.seuils_du_compte("/home/x/.claude"))


@tagged("post_install", "-at_install")
class TestBasculeEnHeureDeMontreal(TransactionCase):
    """La bascule doit se lire pareil quel que soit le fuseau du lecteur.

    🔴 Relevé le 2026-09-11 : avec une fiche d'usager réglée à
    Pacific/Auckland, la bascule du **jeudi
    03:00 heure de Montréal** s'affichait « 2026-09-18 19:00 », un vendredi
    soir. La date en base était juste ; c'est la lecture qui disait le contraire
    de ce que la mesure veut dire.
    """

    def setUp(self):
        super().setUp()
        self.Compte = self.env["claude.account"]

    def _fenetre(self):
        cid = self.Compte.enregistrer_releve(
            "/home/x/.claude",
            charge={"seven_day": {"utilization": 7.0,
                                  "resets_at": "2026-09-18T07:00:00.660597Z"}})
        return self.Compte.browse(cid).window_ids

    def test_la_bascule_se_dit_en_heure_de_montreal(self):
        self.assertEqual(self._fenetre().bascule_montreal,
                         "vendredi 18 septembre à 03:00")

    def test_le_fuseau_du_lecteur_ne_change_rien(self):
        for fuseau in ("Pacific/Auckland", "America/Toronto", "UTC", False):
            self.env.user.tz = fuseau
            fenetre = self.env["claude.account.window"].browse(
                self._fenetre().id)
            fenetre.invalidate_recordset(["bascule_montreal"])
            self.assertEqual(fenetre.bascule_montreal,
                             "vendredi 18 septembre à 03:00",
                             f"lu sous {fuseau}")

    def test_sans_bascule_connue_rien_n_est_invente(self):
        cid = self.Compte.enregistrer_releve(
            "/home/y/.claude",
            charge={"seven_day": {"utilization": 7.0, "resets_at": None}})
        self.assertFalse(self.Compte.browse(cid).window_ids.bascule_montreal)

    def test_l_heure_avancee_est_respectee(self):
        """En janvier Montréal est à -5, en septembre à -4 : pas de -4 en dur."""
        cid = self.Compte.enregistrer_releve(
            "/home/z/.claude",
            charge={"seven_day": {"utilization": 7.0,
                                  "resets_at": "2027-01-14T08:00:00Z"}})
        self.assertEqual(self.Compte.browse(cid).window_ids.bascule_montreal,
                         "jeudi 14 janvier à 03:00")
