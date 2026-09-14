"""Le suivi de démarchage fédéré : l'agence montre, le client écarte, et rien de plus."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation

CAMPAGNE = "bf.outreach.campaign"


@tagged("post_install", "-at_install", "federation", "federation_outreach")
class TestFederationOutreach(TestFederation):

    def _exiger_le_demarchage(self):
        if CAMPAGNE not in self.env:
            self.skipTest("bf_outreach n'est pas installé : l'agence ne peut rien tirer")

    def _campagne(self, nom="Campagne d'automne", cibles=None):
        self._exiger_le_demarchage()
        campagne = self.env[CAMPAGNE].create({
            "name": nom, "partner_id": self.peer_b.partner_id.id, "state": "running",
            "description": "<p>On parle de <b>conformité Loi 25</b>.</p>",
        })
        Target, Touch = self.env["bf.outreach.target"], self.env["bf.outreach.touch"]
        for nom_cible, contact, courriel in (cibles or [
                ("Boulangerie du Vieux-Port", "Sylvain Meunier", "s.meunier@exemple.test"),
                ("Transport Lachance", "Yvon Lachance", "y.lachance@exemple.test")]):
            cible = Target.create({"campaign_id": campagne.id, "name": nom_cible, "contact_name": contact,
                                   "function": "Directeur", "email": courriel, "phone": "555-555-0100"})
            Touch.create({"target_id": cible.id, "kind": "call", "direction": "out", "outcome": "voicemail",
                          "summary": "Laissé un message à %s, rappeler jeudi" % contact,
                          "note": "<p>Détail confidentiel de l'appel</p>"})
        return campagne

    def _suivi(self, campagne, partager=True, **vals):
        suivi = self.env["federation.outreach.report"].create(dict({
            "name": "Démarchage d'automne", "campaign_ref": "%s,%s" % (CAMPAGNE, campagne.id),
            "client_partner_id": self.peer_b.partner_id.id,
        }, **vals))
        suivi.action_refresh()
        if partager:
            suivi.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return suivi

    def _miroir(self, suivi):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(suivi.id)),
             ("res_model", "=", "federation.outreach.report")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % suivi.name)
        return link._record().with_context(active_test=False)

    def _gestionnaire(self, login="gestionnaire.demarchage"):
        return self.env["res.users"].create({
            "name": "Quelqu'un qui gère", "login": login,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project.group_project_manager").id])]})

    # --- Le genre ---------------------------------------------------------------------
    def test_o01_le_genre_et_son_retour_sont_annonces(self):
        self.peer_a.action_ping()
        self.peer_a.invalidate_recordset()
        annonces = (self.peer_a.accepted_kinds or "").split(",")
        for genre in ("outreach.share", "outreach.card", "outreach.exclude"):
            self.assertIn(genre, annonces)

    # --- Ce qui traverse --------------------------------------------------------------
    def test_o02_les_cibles_et_leurs_touches_traversent_sans_personne(self):
        miroir = self._miroir(self._suivi(self._campagne()))
        self.assertEqual(miroir.name, "Démarchage d'automne (Pair A)")
        self.assertEqual(miroir.line_ids.mapped("name"), ["Boulangerie du Vieux-Port", "Transport Lachance"])
        self.assertEqual(miroir.target_count, 2)
        self.assertEqual(miroir.contacted_count, 2)
        touche = miroir.line_ids[0].touch_ids
        self.assertEqual(len(touche), 1)
        self.assertEqual(touche.kind, "Appel", "le libellé traverse : le client n'a pas le démarchage")
        self.assertEqual(touche.outcome, "Boîte vocale")
        for ligne in miroir.line_ids:
            self.assertFalse(ligne.contact_name or ligne.function or ligne.email or ligne.phone,
                             "🔴 une donnée personnelle a traversé sans que l'agence le coche")
            self.assertFalse(any(ligne.touch_ids.mapped("summary")), "🔴 le résumé libre d'une touche a traversé")

    def test_o03_la_carte_par_defaut_ne_porte_aucune_personne(self):
        suivi = self._suivi(self._campagne(), partager=False)
        carte = repr(suivi._federation_card())
        for fuite in ("Sylvain Meunier", "s.meunier@", "555-555-0100", "Laissé un message", "Directeur",
                      "Détail confidentiel"):
            self.assertNotIn(fuite, carte, f"🔴 « {fuite} » est dans la carte par défaut")

    def test_o04_cocher_fait_traverser_les_personnes_mais_jamais_le_detail(self):
        suivi = self._suivi(self._campagne(), include_contacts=True)
        miroir = self._miroir(suivi)
        ligne = miroir.line_ids.filtered(lambda l: l.name == "Boulangerie du Vieux-Port")
        self.assertEqual(ligne.contact_name, "Sylvain Meunier")
        self.assertEqual(ligne.email, "s.meunier@exemple.test")
        self.assertIn("rappeler jeudi", ligne.touch_ids.summary)
        self.assertNotIn("Détail confidentiel", repr(suivi._federation_card()),
                         "🔴 le détail d'une touche ne traverse jamais, même coché")

    def test_o05_un_pair_ne_glisse_pas_de_coordonnees_sans_la_case(self):
        """Une carte qui porte des coordonnées sans annoncer `include_contacts` ne les pose pas."""
        carte = {"name": "Forgée", "include_contacts": False, "targets": [
            {"key": "9", "name": "Cible", "contact_name": "Quelqu'un", "email": "a@b.test", "phone": "1",
             "touches": [{"date": "2026-09-01 10:00:00", "kind": "Appel", "summary": "secret"}]}]}
        suivi = self.env["federation.outreach.report"].sudo()._federation_receive(self.peer_a, carte)
        ligne = suivi.line_ids
        self.assertFalse(ligne.contact_name or ligne.email or ligne.phone)
        self.assertFalse(ligne.touch_ids.summary)

    def test_o06_le_miroir_est_un_suivi_jamais_une_campagne(self):
        """🔴 Une campagne porte un cron qui crée des activités : un miroir de campagne en
        ferait naître chez le client, sur des gens qu'il ne démarche pas."""
        campagne = self._campagne()
        avant = self.env[CAMPAGNE].sudo().search_count([])
        self._suivi(campagne)
        self.assertEqual(self.env[CAMPAGNE].sudo().search_count([]), avant,
                         "🔴 la réception a créé une campagne de démarchage")

    # --- Le retour : écarter ----------------------------------------------------------
    def test_o07_le_client_ecarte_une_cible_et_l_agence_ne_la_contacte_plus(self):
        campagne = self._campagne()
        suivi = self._suivi(campagne)
        miroir = self._miroir(suivi)
        ligne = miroir.line_ids.filtered(lambda l: l.name == "Transport Lachance")
        ligne.action_exclude("Déjà notre client")
        self._flush()
        cible = campagne.target_ids.filtered(lambda c: c.name == "Transport Lachance")
        cible.invalidate_recordset()
        self.assertTrue(cible.do_not_contact, "la cible est passée à « ne pas contacter » chez l'agence")
        self.assertIn("Déjà notre client", cible.do_not_contact_reason or "")
        suivi.invalidate_recordset()
        chez_l_agence = suivi.line_ids.filtered(lambda l: l.name == "Transport Lachance")
        self.assertTrue(chez_l_agence.excluded_by_client)
        self.assertTrue(chez_l_agence.do_not_contact,
                        "🔴 la ligne du suivi se lit « écartée par le client » et encore « à contacter » "
                        "jusqu'au rafraîchissement du lendemain")
        autre = campagne.target_ids.filtered(lambda c: c.name == "Boulangerie du Vieux-Port")
        self.assertFalse(autre.do_not_contact, "écarter une cible ne touche pas les autres")
        self.assertFalse(suivi.line_ids.filtered(lambda l: l.name == "Boulangerie du Vieux-Port").do_not_contact)

    def test_o08_une_cle_hors_du_suivi_ne_touche_rien(self):
        """🔴 La clé arrive du réseau : elle ne sert jamais à retrouver une cible hors de
        la campagne du suivi. Sinon un pair écarterait n'importe quelle cible en devinant
        un identifiant."""
        campagne = self._campagne()
        suivi = self._suivi(campagne)
        etrangere = self._campagne(nom="Campagne d'un autre client", cibles=[("Cible d'un autre", "X", "x@y.test")])
        victime = etrangere.target_ids
        lien = suivi._federation_link()
        self.assertFalse(suivi._federation_apply_exclude(lien, {"key": str(victime.id), "reason": "forgé"}))
        victime.invalidate_recordset()
        self.assertFalse(victime.do_not_contact, "🔴 une cible d'une autre campagne a été écartée par une clé forgée")

    def test_o09_l_ecart_survit_aux_rafraichissements_des_deux_cotes(self):
        campagne = self._campagne()
        suivi = self._suivi(campagne)
        miroir = self._miroir(suivi)
        miroir.line_ids.filtered(lambda l: l.name == "Transport Lachance").action_exclude("Déjà client")
        self._flush()
        campagne.target_ids.filtered(lambda c: c.name == "Boulangerie du Vieux-Port").write({"function": "PDG"})
        suivi.action_refresh()
        self._flush()
        suivi.invalidate_recordset()
        miroir.invalidate_recordset()
        # 🔴 `do_not_contact` masquait la perte : la cible écartée repasse « ne pas contacter »
        # par sa campagne, donc un essai en OU restait vert alors que le rafraîchissement avait
        # effacé QUI l'a écartée et POURQUOI. L'essai exige la provenance, des deux côtés.
        for cote, rapport in (("agence", suivi), ("client", miroir)):
            ligne = rapport.line_ids.filtered(lambda l: l.name == "Transport Lachance")
            self.assertTrue(ligne.excluded_by_client, f"🔴 l'écart s'est perdu au rafraîchissement, côté {cote}")
            self.assertEqual(ligne.excluded_reason, "Déjà client", f"🔴 le motif s'est perdu, côté {cote}")
            self.assertTrue(ligne.do_not_contact or cote == "client" and ligne.excluded_by_client)

    def test_o10_on_n_ecarte_que_dans_un_suivi_recu(self):
        suivi = self._suivi(self._campagne())
        with self.assertRaises(UserError):
            suivi.line_ids[:1].action_exclude("chez l'agence elle-même")

    def test_o11_ecarter_demande_le_role(self):
        miroir = self._miroir(self._suivi(self._campagne()))
        usager = self.env["res.users"].create({
            "name": "Quelqu'un sans le rôle", "login": "sans.role.demarchage",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        with self.assertRaises(AccessError):
            miroir.line_ids[:1].with_user(usager).action_exclude("tentative")

    # --- Le miroir se lit -------------------------------------------------------------
    def test_o12_le_miroir_se_lit(self):
        # 🔴 `AccessError` HÉRITE de `UserError` : l'utilisateur a donc le droit d'écrire.
        miroir = self._miroir(self._suivi(self._campagne()))
        gestionnaire = self._gestionnaire()
        with self.assertRaises(UserError) as pris:
            miroir.with_user(gestionnaire).write({"name": "Je retouche"})
        self.assertNotIsInstance(pris.exception, AccessError)
        self.assertIn("se lit ici", str(pris.exception))
        with self.assertRaises(UserError) as pris:
            miroir.line_ids[:1].with_user(gestionnaire).write({"stage": "Gagnée"})
        self.assertNotIsInstance(pris.exception, AccessError)
        self.assertIn("se lisent ici", str(pris.exception))
        with self.assertRaises(UserError):
            miroir.with_user(gestionnaire).action_refresh()

    # --- La portée et la suite --------------------------------------------------------
    def test_o13_sans_client_mandant_aucun_pair(self):
        self._exiger_le_demarchage()
        suivi = self.env["federation.outreach.report"].create({"name": "Sans client"})
        self.assertFalse(suivi.federation_possible)
        with self.assertRaises(ValidationError):
            suivi.write({"federation_peer_id": self.peer_b.id})

    def test_o14_le_cron_rafraichit_et_renvoie_ce_qui_a_change(self):
        campagne = self._campagne()
        suivi = self._suivi(campagne)
        self.env["bf.outreach.target"].create({"campaign_id": campagne.id, "name": "Métallurgie Beauce-Sud"})
        self.env["federation.outreach.report"]._cron_refresh_shared()
        self._flush()
        self.assertIn("Métallurgie Beauce-Sud", self._miroir(suivi).line_ids.mapped("name"))

    def test_o15_un_suivi_casse_ne_prive_pas_les_autres_du_rafraichissement(self):
        campagne = self._campagne()
        bon = self._suivi(campagne)
        casse = self._suivi(self._campagne(nom="Campagne supprimée", cibles=[("Z", "Z", "z@z.test")]))
        casse.campaign_ref.sudo().unlink()
        self.env["bf.outreach.target"].create({"campaign_id": campagne.id, "name": "Nouvelle cible"})
        self.env["federation.outreach.report"]._cron_refresh_shared()
        self._flush()
        self.assertIn("Nouvelle cible", self._miroir(bon).line_ids.mapped("name"),
                      "🔴 un suivi dont la campagne a disparu a arrêté le rafraîchissement des autres")
