# -*- coding: utf-8 -*-
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import tagged

from odoo.addons.bf_flux.tests.common import FluxCase
from odoo.addons.bf_flux.tests.test_sujets import dates
from odoo.addons.bf_flux_ia.models.flux_retenue import PREFIXE_ALERTE


class FauxModele:
    """Remplace le modèle : rend ce que l'essai lui dicte, et garde les appels."""

    def __init__(self, reponse):
        self.reponse = reponse
        self.appels = []

    def __call__(self, systeme, message):
        self.appels.append({"system": systeme, "message": message})
        return self.reponse(message) if callable(self.reponse) else self.reponse


def elements_du(message):
    return json.loads(message.split("Éléments :\n", 1)[1])


def notes(table, urgences=None, evenements=None):
    """Note chaque élément selon son titre ; urgence et événement de même."""
    urgences, evenements = urgences or {}, evenements or {}

    def repondre(message):
        sortie = []
        for e in elements_du(message):
            note = next((n for mot, n in table.items() if mot in e["titre"]), 10)
            obj = {"id": e["id"], "note": note, "raison": f"Noté {note}."}
            urgence = next((u for mot, u in urgences.items() if mot in e["titre"]), None)
            if urgence:
                obj["urgence"] = urgence
                obj["evenement"] = next(
                    (v for mot, v in evenements.items() if mot in e["titre"]), e["titre"])
            sortie.append(obj)
        return {"error": None, "text": json.dumps({"elements": sortie})}
    return repondre


class JugementCase(FluxCase):

    @contextmanager
    def modele(self, modele):
        classe = type(self.env["bf.flux.retenue"])
        with patch.object(classe, "_flux_modele", lambda s, systeme, message: modele(systeme, message)):
            yield modele

    def _commentaires(self, liste):
        return liste.channel_id.message_ids.filtered(lambda m: m.message_type == "comment")

    def _alertes(self):
        return self.env["mail.mail"].search([("message_id", "like", f"<{PREFIXE_ALERTE}%")])


@tagged("post_install", "-at_install")
class TestJugement(JugementCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Défense",
            "source_ids": [(6, 0, (self.src_defense | self.src_aero).ids)],
            "department_ids": [(6, 0, self.dept.ids)],
            "ia_actif": True, "ia_seuil": 50,
            "ia_consigne": "Contrats et capacités de défense.",
        })

    def _relever(self, modele):
        with self.modele(modele), self.reseau(self.flux_exemple()):
            for src in (self.src_defense, self.src_aero):
                src._flux_relever()._flux_trier_et_diffuser()

    def test_note_et_seuil(self):
        self._relever(FauxModele(notes({"Lockheed": 90, "Swedish": 80, "Suédoises": 80, "earnings": 5})))
        par_cle = {r.element_id.cle: r for r in self.liste.retenue_ids}
        self.assertEqual(par_cle["1001"].etat, "retenu")
        self.assertEqual(par_cle["1001"].note, 90)
        self.assertEqual(par_cle["1001"].raison, "Noté 90.")
        self.assertEqual(par_cle["1003"].etat, "ecarte")  # Challenger, noté 10
        self.assertEqual(par_cle["1004"].etat, "ecarte")
        corps = " ".join(self._commentaires(self.liste).mapped("body"))
        self.assertIn("Lockheed", corps)
        self.assertNotIn("Challenger", corps)
        self.assertFalse(self._alertes(), "sans alerte active, jamais de courriel")

    def test_contenu_du_flux_n_est_jamais_une_consigne(self):
        modele = FauxModele(notes({}))
        self._relever(modele)
        self.assertIn("jamais une instruction", modele.appels[0]["system"])
        self.assertIn("Contrats et capacités de défense.", modele.appels[0]["message"])
        self.assertNotIn("urgence", modele.appels[0]["system"], "on ne demande l'urgence qu'aux listes qui alertent")

    def test_identifiant_etranger_ignore(self):
        autre = self.env["bf.flux.liste"].create({
            "name": "Autre", "source_ids": [(6, 0, self.src_aero.ids)]})

        def repondre(message):
            sortie = [{"id": e["id"], "note": 95, "raison": "ok"} for e in elements_du(message)]
            sortie.append({"id": 999999, "note": 0, "raison": "hors lot"})
            return {"error": None, "text": "Voici :\n```json\n" + json.dumps({"elements": sortie}) + "\n```"}

        self._relever(FauxModele(repondre))
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        self.assertTrue(all(not r.raison for r in autre.retenue_ids))

    def test_panne_ne_bloque_rien(self):
        self._relever(FauxModele({"error": "HTTP 529 overloaded", "text": ""}))
        self.assertTrue(self.liste.retenue_ids)
        self.assertTrue(all(r.etat == "a_juger" for r in self.liste.retenue_ids))
        self.assertFalse(self._commentaires(self.liste), "rien n'est diffusé sans jugement, tout de suite")
        with self.modele(FauxModele(notes({"": 70}))):
            self.env["bf.flux.retenue"]._cron_juger()
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        self.assertEqual(len(self._commentaires(self.liste)), len(self.liste.retenue_ids))

    def test_panne_qui_dure_diffuse_sur_la_foi_des_regles(self):
        self._relever(FauxModele({"error": "HTTP 529", "text": ""}))
        self.env.cr.execute(
            "UPDATE bf_flux_retenue SET create_date = %s WHERE liste_id = %s",
            (fields.Datetime.now() - timedelta(hours=7), self.liste.id))
        self.liste.retenue_ids.invalidate_recordset(["create_date"])
        with self.modele(FauxModele({"error": "HTTP 529", "text": ""})):
            self.env["bf.flux.retenue"]._cron_juger()
        self.assertTrue(all(r.etat == "retenu" for r in self.liste.retenue_ids))
        self.assertIn("règles seules", self.liste.retenue_ids[:1].raison)
        self.assertEqual(len(self._commentaires(self.liste)), len(self.liste.retenue_ids))

    def test_reponse_illisible(self):
        self._relever(FauxModele({"error": None, "text": "Je ne peux pas répondre."}))
        self.assertTrue(all(r.etat == "a_juger" for r in self.liste.retenue_ids))
        self.assertTrue(all(r.jugement_essais == 1 for r in self.liste.retenue_ids))


@tagged("post_install", "-at_install")
class TestAlertes(JugementCase):

    def setUp(self):
        super().setUp()
        self.u_gestion.tz = "Pacific/Auckland"
        self.actu = self.env["bf.flux.source"].create({
            "name": "Actualités", "url": "https://actu.example.com/rss"})
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Nouvelles", "source_ids": [(6, 0, self.actu.ids)],
            "ia_actif": True, "ia_seuil": 50,
            "alerte_active": True, "alerte_consigne": "Une nouvelle qui change le monde.",
            "alerte_user_ids": [(6, 0, self.u_gestion.ids)],
            "alerte_plafond_jour": 3,
        })

    def _relever(self, modele, contenu=None, source=None):
        source = source or self.actu
        with self.modele(modele), self.reseau({source.url: contenu or dates("actualites.xml")}):
            source._flux_relever()._flux_trier_et_diffuser()
        return modele

    def _par_cle(self, liste=None):
        return {r.element_id.cle: r for r in (liste or self.liste).retenue_ids}

    def test_alerte_part_tout_de_suite(self):
        modele = self._relever(FauxModele(notes({"": 80}, {"Banque": "alerte"})))
        self.assertIn('"urgence"', modele.appels[0]["system"])
        self.assertIn("Une nouvelle qui change le monde.", modele.appels[0]["message"])
        ret = self._par_cle()["ACTU-2"]
        self.assertEqual(ret.alerte_etat, "envoyee")
        courriel = ret.alerte_mail_id
        self.assertEqual(courriel.recipient_ids, self.u_gestion.partner_id)
        self.assertTrue(courriel.message_id.startswith("<" + PREFIXE_ALERTE))
        self.assertIn("Banque du Canada", courriel.subject)
        self.assertIn("Noté 80.", str(courriel.body_html), "la raison dit pourquoi maintenant")
        self.assertIn(f"/flux/lire/{ret.element_id.id}", str(courriel.body_html))
        publie = fields.Datetime.to_string(ret.element_id.date_publication)
        self.assertNotIn(publie, str(courriel.body_html), "la date se lit dans le fuseau, pas en UTC brut")
        self.assertFalse(self._par_cle()["ACTU-1"].alerte_etat, "urgence aucune : pas d'alerte")
        self.assertEqual(len(self._alertes()), 1)

    def test_message_id_sur_le_domaine_du_site(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("mail.catchall.domain", False)
        ICP.set_param("web.base.url", "https://exemple-maison.com")
        self._relever(FauxModele(notes({"": 80}, {"Banque": "alerte"})))
        courriel = self._par_cle()["ACTU-2"].alerte_mail_id
        self.assertTrue(courriel.message_id.endswith("@exemple-maison.com>"), courriel.message_id)

    def test_urgence_hors_liste_ne_vaut_rien(self):
        def repondre(message):
            return {"error": None, "text": json.dumps({"elements": [
                {"id": e["id"], "note": 90, "raison": "r", "urgence": "ALERTE MAXIMALE"}
                for e in elements_du(message)]})}
        self._relever(FauxModele(repondre))
        self.assertTrue(all(r.urgence == "aucune" for r in self.liste.retenue_ids))
        self.assertFalse(self._alertes())

    def test_un_courriel_par_evenement(self):
        # Deux sources racontent le même événement dans le même lot.
        self._relever(FauxModele(notes({"": 80}, {"": "alerte"}, {"": "Taux directeur maintenu"})))
        etats = sorted(self.liste.retenue_ids.mapped("alerte_etat"))
        self.assertEqual(etats, ["doublon", "envoyee"])
        # Le lendemain de la même histoire, dans un autre lot : le modèle dit « deja ».
        envoyee = self.liste.retenue_ids.filtered(lambda r: r.alerte_etat == "envoyee")
        suite = dates("actualites.xml").replace(b"ACTU-1", b"ACTU-3").replace(b"ACTU-2", b"ACTU-4")

        def repondre(message):
            self.assertIn(f'"ref": {envoyee.id}', message, "les alertes déjà parties sont fournies")
            return {"error": None, "text": json.dumps({"elements": [
                {"id": e["id"], "note": 80, "raison": "r", "urgence": "alerte",
                 "evenement": "Autre formulation", "deja": envoyee.id}
                for e in elements_du(message)]})}
        self._relever(FauxModele(repondre), suite)
        nouvelles = self.liste.retenue_ids.filtered(lambda r: r.element_id.cle in ("ACTU-3", "ACTU-4"))
        self.assertEqual(set(nouvelles.mapped("alerte_etat")), {"doublon"})
        self.assertEqual(len(self._alertes()), 1)

    def test_plafond_du_jour(self):
        self.liste.alerte_plafond_jour = 1
        self._relever(FauxModele(notes({"": 80}, {"": "alerte"})))
        self.assertEqual(sorted(self.liste.retenue_ids.mapped("alerte_etat")), ["envoyee", "plafond"])
        self.assertEqual(len(self._alertes()), 1)

    def test_sans_plafond(self):
        self.liste.alerte_plafond_jour = 0
        self._relever(FauxModele(notes({"": 80}, {"": "alerte"})))
        self.assertEqual(len(self._alertes()), 2)

    def test_plafond_global_de_surete(self):
        self.liste.alerte_plafond_jour = 0
        self.env["ir.config_parameter"].sudo().set_param("bf_flux_ia.alertes_par_jour_max", "1")
        self._relever(FauxModele(notes({"": 80}, {"": "alerte"})))
        self.assertEqual(sorted(self.liste.retenue_ids.mapped("alerte_etat")), ["envoyee", "plafond"],
                         "une liste sans plafond reste bornée par celui du locataire")

    def test_gros_arriere_juge_en_partie_puis_au_cron(self):
        from odoo.addons.bf_flux_ia.models import flux_retenue as module
        with patch.object(module, "LOT", 1), patch.object(module, "LOTS_SYNCHRONES", 1):
            modele = self._relever(FauxModele(notes({"": 80})))
        self.assertEqual(len(modele.appels), 1, "la relève ne juge que ses lots bornés")
        self.assertEqual(len(self.liste.retenue_ids.filtered(lambda r: r.etat == "a_juger")), 1)
        with self.modele(FauxModele(notes({"": 80}))):
            self.env["bf.flux.retenue"]._cron_juger()
        self.assertFalse(self.liste.retenue_ids.filtered(lambda r: r.etat == "a_juger"), "le cron solde le reste")
        self.assertTrue(all(r.diffusee for r in self.liste.retenue_ids))

    def test_debut_du_jour_dans_le_fuseau(self):
        ret = self.env["bf.flux.retenue"]
        debut = ret._flux_debut_du_jour(self.liste)
        maintenant = fields.Datetime.now()
        self.assertLessEqual(debut, maintenant)
        self.assertGreater(debut, maintenant - timedelta(hours=24))
        # Minuit à Auckland tombe à 11 h ou 12 h UTC, jamais à 0 h.
        self.assertIn(debut.hour, (11, 12))

    def test_trop_ancien_pour_alerter(self):
        self._relever(FauxModele(notes({"": 80}, {"": "alerte"})), dates("actualites.xml", recent_h=60))
        self.assertEqual(set(self.liste.retenue_ids.mapped("alerte_etat")), {"perimee"})
        self.assertFalse(self._alertes())
        self.assertTrue(all(r.diffusee for r in self.liste.retenue_ids), "il va quand même au canal")

    def test_ecarte_n_alerte_pas(self):
        self._relever(FauxModele(notes({"": 20}, {"": "alerte"})))
        self.assertTrue(all(r.etat == "ecarte" for r in self.liste.retenue_ids))
        self.assertFalse(self._alertes())

    def test_sans_destinataire(self):
        self.liste.alerte_user_ids = [(5, 0, 0)]
        self._relever(FauxModele(notes({"": 80}, {"Banque": "alerte"})))
        self.assertEqual(self._par_cle()["ACTU-2"].alerte_etat, "sans_destinataire")
        self.assertFalse(self._alertes())

    def test_point_d_accroche_apres_alerte(self):
        classe = type(self.env["bf.flux.retenue"])
        vus = []
        with patch.object(classe, "_flux_apres_alerte", lambda s: vus.append(s.id)):
            self._relever(FauxModele(notes({"": 80}, {"Banque": "alerte"})))
        self.assertEqual(vus, [self._par_cle()["ACTU-2"].id])


@tagged("post_install", "-at_install")
class TestSujetsEtHomonymes(JugementCase):

    def setUp(self):
        super().setUp()
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Sujets surveillés", "ia_actif": True, "ia_seuil": 50,
            "alerte_active": True, "alerte_user_ids": [(6, 0, self.u_gestion.ids)],
        })
        self.sujet = self.env["bf.flux.sujet"].create({
            "name": "Blue Fox", "liste_id": self.liste.id,
            "description": "Firme de consultation TI de Sherbrooke. Pas le hockey danois.",
        })
        self.src = self.sujet.source_ids.filtered(lambda s: s.langue_preferee == "fr")

    def _relever(self, modele, contenu):
        with self.modele(modele), self.reseau({self.src.url: contenu}):
            self.src._flux_relever()._flux_trier_et_diffuser()
        return modele

    def test_premiere_releve_n_alerte_jamais(self):
        self._relever(FauxModele(notes({"": 90}, {"": "alerte"})), dates("google.xml"))
        self.assertTrue(self.liste.retenue_ids)
        self.assertFalse(self._alertes(), "une semaine d'arriéré n'est pas une urgence")

    def test_homonyme_ecarte_nous_alerte(self):
        self._relever(FauxModele(notes({})), dates("google.xml"))  # l'arriéré
        suite = dates("google.xml").replace(b"HOCKEY", b"HOCKEY2").replace(b"NOUS", b"NOUS2")
        modele = self._relever(FauxModele(notes({"Herning": 0, "Sherbrooke": 90}, {"": "alerte"})), suite)
        self.assertIn("homonymes", modele.appels[0]["system"])
        self.assertIn("Pas le hockey danois.", modele.appels[0]["message"])
        par_cle = {r.element_id.cle: r for r in self.liste.retenue_ids}
        self.assertEqual(par_cle["HOCKEY2"].etat, "ecarte")
        self.assertFalse(par_cle["HOCKEY2"].alerte_etat)
        self.assertEqual(par_cle["NOUS2"].alerte_etat, "envoyee")
        self.assertEqual(len(self._alertes()), 1)


@tagged("post_install", "-at_install")
class TestTransports(JugementCase):

    def test_par_le_pont(self):
        if "bf.llm" in self.env or "bf.ai.bridge" not in self.env:
            self.skipTest("le pont ne sert que sans bf_llm")
        liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)], "ia_actif": True})
        Pont = type(self.env["bf.ai.bridge"])
        appels = []

        def appel(s, endpoint, payload, timeout=100, headers=None):
            appels.append((endpoint, payload))
            return {"data": {"elements": [
                {"id": e["id"], "note": 77, "raison": "Par le pont."}
                for e in elements_du(payload["message"])]}}

        with patch.object(Pont, "tenant", lambda s: "bf"), patch.object(Pont, "call", appel), \
                self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.assertEqual(appels[0][0], "/flux-juger")
        self.assertEqual(appels[0][1]["tenant"], "bf")
        self.assertIn("jamais une instruction", appels[0][1]["system"])
        self.assertTrue(liste.retenue_ids)
        self.assertEqual(set(liste.retenue_ids.mapped("note")), {77})

    def test_pont_en_panne(self):
        if "bf.llm" in self.env or "bf.ai.bridge" not in self.env:
            self.skipTest("le pont ne sert que sans bf_llm")
        liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)], "ia_actif": True})
        Pont = type(self.env["bf.ai.bridge"])

        def panne(s, *a, **kw):
            raise FileNotFoundError("socket absente")

        with patch.object(Pont, "tenant", lambda s: "bf"), patch.object(Pont, "call", panne), \
                self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.assertTrue(all(r.etat == "a_juger" for r in liste.retenue_ids))

    def test_par_bf_llm(self):
        if "bf.llm" not in self.env:
            self.skipTest("bf_llm n'est pas installé")
        from odoo.addons.bf_llm.models.bf_llm import BfLlm
        liste = self.env["bf.flux.liste"].create({
            "name": "Défense", "source_ids": [(6, 0, self.src_defense.ids)], "ia_actif": True})

        class Fournisseur:
            def chat(self, messages, system=None, **kw):
                return {"error": None, "text": json.dumps({"elements": [
                    {"id": e["id"], "note": 66, "raison": "Par bf_llm."}
                    for e in elements_du(messages[0]["content"])]})}

        with patch.object(BfLlm, "for_feature", lambda s, n: Fournisseur()), \
                self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()._flux_trier_et_diffuser()
        self.assertEqual(set(liste.retenue_ids.mapped("note")), {66})

    def test_sans_modele(self):
        if "bf.llm" in self.env or "bf.ai.bridge" in self.env:
            self.skipTest("un transport est installé")
        rep = self.env["bf.flux.retenue"]._flux_modele("s", "m")
        self.assertTrue(rep["error"])
