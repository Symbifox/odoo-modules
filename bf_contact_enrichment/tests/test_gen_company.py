"""Sociétés complétées par une recherche en ligne de Gen.

Le pont est simulé : ce qui s'éprouve ici est ce que le module fait de sa
réponse. Qui met en file (un membre du groupe, à la main ou à la création ; ni
un programme, ni une personne), ce que la préférence du demandeur décide
(proposer, remplir, ne rien lancer), et la discipline d'écriture : rien sans
confirmation par défaut, jamais un champ déjà renseigné écrasé, la provenance
au fil.
"""
import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

BRIDGE = "odoo.addons.bf_contact_enrichment.tools.bridge.call_bridge"
GATEWAY = "odoo.addons.bf_ai_bridge.tools.transport.post"
_ENDPOINT, _CHARGE = 1, 2  # transport.post(socket, endpoint, payload, …)

TROUVE = {
    "company_name": "Fromagerie du Moulin",
    "website": "fromageriedumoulin.example.test",
    "email": "info@fromageriedumoulin.example.test",
    "phone": "450-555-0147",
    "street": "12 rue du Moulin",
    "city": "Racine",
    "zip": "J0E 1Y0",
    "state": "Québec",
    "country": "Canada",
    "sources": ["https://fromageriedumoulin.example.test/contact/",
                "javascript:alert(1)"],
    "confidence": 88,
    "source": "websearch",
}


@tagged("post_install", "-at_install")
class TestGenCompany(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les messages que la tâche écrit se lisent dans la langue du demandeur :
        # ces essais les attendent en anglais, sur une base fr_CA seule comme sur
        # une base neuve (voir test_langue pour le français).
        cls.env["res.lang"]._activate_lang("en_US")
        groupe = cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich")
        cls.membre = cls.env["res.users"].create({
            "lang": "en_US",
            "name": "Membre Enrichissement",
            "login": "membre_gen_26837",
            "email": "membre_gen_26837@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_partner_manager").id,
                                  groupe.id])],
        })
        cls.autre = cls.env["res.users"].create({
            "lang": "en_US",
            "name": "Hors du groupe",
            "login": "autre_gen_26837",
            "email": "autre_gen_26837@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_partner_manager").id])],
        })
        cls.canada = cls.env.ref("base.ca")
        cls.quebec = cls.env["res.country.state"].search(
            [("country_id", "=", cls.canada.id), ("code", "=", "QC")], limit=1)

    def _creer(self, user=None, **vals):
        user = user or self.membre
        return self.env["res.partner"].with_user(user).create(dict(
            {"name": "Fromagerie du Moulin", "is_company": True,
             "city": "Racine"}, **vals))

    def _traiter(self, partner, reponse):
        with patch(BRIDGE, return_value=reponse) as appel:
            self.env["res.partner"]._cron_bf_gen_companies(chunk=5)
        partner.invalidate_recordset()
        return appel

    # ── Mise en file ────────────────────────────────────────────────

    def test_creation_incomplete_met_en_file(self):
        p = self._creer()
        self.assertEqual(p.x_bf_gen_state, "queued")
        self.assertEqual(p.x_bf_gen_requested_by, self.membre)

    def test_creation_complete_reste_hors_file(self):
        p = self._creer(website="example.com", phone="819 555 0100",
                        street="1 rue Principale")
        self.assertFalse(p.x_bf_gen_state)

    def test_personne_jamais_en_file(self):
        p = self._creer(is_company=False, name="Maude Fortier")
        self.assertFalse(p.x_bf_gen_state)

    def test_hors_groupe_jamais_en_file(self):
        self.assertFalse(self._creer(user=self.autre).x_bf_gen_state)

    def test_programme_jamais_en_file(self):
        """sudo, import et installation : personne n'a demandé, personne ne paie."""
        Partner = self.env["res.partner"].with_user(self.membre)
        for ctx in ({"import_file": True}, {"install_mode": True},
                    {"bf_gen_no_queue": True}):
            p = Partner.with_context(**ctx).create(
                {"name": "Importée", "is_company": True})
            self.assertFalse(p.x_bf_gen_state, ctx)
        p = Partner.sudo().create({"name": "Par sudo", "is_company": True})
        self.assertFalse(p.x_bf_gen_state)

    def test_mode_off_garde_le_bouton(self):
        self.membre.bf_gen_company_mode = "off"
        p = self._creer()
        self.assertFalse(p.x_bf_gen_state)
        p.with_user(self.membre).action_bf_gen_complete()
        self.assertEqual(p.x_bf_gen_state, "queued")

    def test_bouton_refuse_hors_groupe_et_personne(self):
        p = self._creer(user=self.autre)
        with self.assertRaises(AccessError):
            p.with_user(self.autre).action_bf_gen_complete()
        personne = self._creer(is_company=False, name="Maude Fortier")
        with self.assertRaises(UserError):
            personne.with_user(self.membre).action_bf_gen_complete()

    def test_l_etat_ne_s_ecrit_pas_par_rpc(self):
        """🔴 Sans la garde, mettre en file par RPC contournait le groupe."""
        p = self._creer(user=self.autre)
        for user in (self.autre, self.membre):
            p.with_user(user).write({"x_bf_gen_state": "queued",
                                     "x_bf_gen_requested_by": self.membre.id,
                                     "comment": "écrit"})
            self.assertFalse(p.x_bf_gen_state)
            self.assertFalse(p.x_bf_gen_requested_by)
            self.assertIn("écrit", p.comment)  # le reste passe
        forcee = self.env["res.partner"].with_user(self.autre).create(
            {"name": "Forcée", "is_company": True, "x_bf_gen_state": "queued"})
        self.assertFalse(forcee.x_bf_gen_state)

    def test_l_usager_regle_sa_preference(self):
        self.membre.with_user(self.membre).write({"bf_gen_company_mode": "fill"})
        self.assertEqual(self.membre.bf_gen_company_mode, "fill")

    # ── Appel du pont ───────────────────────────────────────────────

    def test_la_charge_part_sur_le_locataire_de_cette_base(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_ai_bridge.tenant", "banc")
        p = self._creer(country_id=self.canada.id)
        with patch(GATEWAY, return_value={"data": TROUVE}) as envoi:
            self.env["res.partner"]._cron_bf_gen_companies()
        self.assertEqual(envoi.call_args[0][_ENDPOINT], "/enrich/company-search")
        charge = envoi.call_args[0][_CHARGE]
        self.assertEqual(charge["org"], "banc")
        self.assertEqual(charge["name"], "Fromagerie du Moulin")
        self.assertEqual(charge["city"], "Racine")
        self.assertEqual(charge["country"], self.canada.name)
        self.assertEqual(p.x_bf_gen_state, "proposed")

    # ── Proposer (défaut) ───────────────────────────────────────────

    def test_proposer_n_ecrit_rien_avant_confirmation(self):
        p = self._creer()
        self._traiter(p, {"data": TROUVE})
        self.assertEqual(p.x_bf_gen_state, "proposed")
        self.assertFalse(p.phone)
        self.assertFalse(p.website)
        self.assertEqual(json.loads(p.sudo().x_bf_gen_proposal)["phone"],
                         "450-555-0147")

    def test_revoir_puis_appliquer_remplit_les_vides(self):
        p = self._creer(phone="819 000 0000")  # déjà renseigné : ne bouge pas
        self._traiter(p, {"data": TROUVE})
        action = p.with_user(self.membre).action_bf_gen_review()
        wizard = self.env["bf.contact.enrich.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.source, "gen")
        self.assertEqual(wizard.state, "review")
        self.assertEqual(wizard.proposed_state, "Québec")
        wizard.with_user(self.membre).action_apply()
        self.assertEqual(p.phone, "819 000 0000")
        self.assertEqual(p.website, "http://fromageriedumoulin.example.test")
        self.assertEqual(p.street, "12 rue du Moulin")
        self.assertEqual(p.zip, "J0E 1Y0")
        self.assertEqual(p.state_id, self.quebec)
        self.assertEqual(p.x_bf_gen_state, "done")
        self.assertFalse(p.sudo().x_bf_gen_proposal)
        fil = " ".join(p.message_ids.mapped("body"))
        self.assertIn("fromageriedumoulin.example.test/contact/", fil)
        self.assertIn("Membre Enrichissement", fil)
        self.assertNotIn("javascript:", fil)

    def test_ecarter_n_ecrit_rien(self):
        p = self._creer()
        self._traiter(p, {"data": TROUVE})
        action = p.with_user(self.membre).action_bf_gen_review()
        wizard = self.env["bf.contact.enrich.wizard"].browse(action["res_id"])
        wizard.with_user(self.membre).action_dismiss_gen()
        self.assertEqual(p.x_bf_gen_state, "dismissed")
        self.assertFalse(p.phone)
        self.assertFalse(p.sudo().x_bf_gen_proposal)

    def test_revoir_sans_proposition_refuse(self):
        p = self._creer()
        with self.assertRaises(UserError):
            p.with_user(self.membre).action_bf_gen_review()

    def test_la_proposition_reste_au_groupe(self):
        p = self._creer()
        self._traiter(p, {"data": TROUVE})
        with self.assertRaises(AccessError):
            p.with_user(self.autre).read(["x_bf_gen_proposal"])

    # ── Remplir ─────────────────────────────────────────────────────

    def test_remplir_ecrit_directement_au_dessus_du_seuil(self):
        self.membre.bf_gen_company_mode = "fill"
        p = self._creer()
        self._traiter(p, {"data": TROUVE})
        self.assertEqual(p.x_bf_gen_state, "done")
        self.assertEqual(p.phone, "450-555-0147")
        self.assertEqual(p.country_id, self.canada)
        self.assertEqual(p.state_id, self.quebec)
        self.assertIn("online search (Gen)",
                      " ".join(p.message_ids.mapped("body")))

    def test_la_province_se_trouve_sans_accent_ni_casse(self):
        """Gen rend « Québec » ; une base en anglais l'appelle « Quebec »."""
        Partner = self.env["res.partner"]
        for texte in ("Québec", "quebec", "QUÉBEC", " Qc ", "QC"):
            self.assertEqual(
                Partner._bf_gen_state_id(texte, self.canada.id),
                self.quebec.id, texte)
        self.assertFalse(Partner._bf_gen_state_id("Vermont", self.canada.id))
        self.assertFalse(Partner._bf_gen_state_id("", self.canada.id))
        self.assertFalse(Partner._bf_gen_state_id("Québec", False))

    def test_aucun_courriel_ne_part(self):
        """Gen répond par la fiche, une note et une notification sur le bus : ni
        proposition, ni écriture, ni erreur n'envoie de courriel."""
        Mail = self.env["mail.mail"].sudo()
        avant = Mail.search_count([])
        for mode, reponse in (
                ("propose", {"data": TROUVE}),
                ("fill", {"data": TROUVE}),
                ("fill", {"data": {"confidence": 10}}),
                ("propose", {"error": "Company search timed out (120s)"})):
            self.membre.bf_gen_company_mode = mode
            p = self._creer()
            self._traiter(p, reponse)
        with patch(BRIDGE, side_effect=FileNotFoundError("socket")):
            self._creer()
            self.env["res.partner"]._cron_bf_gen_companies(chunk=5)
        self.assertEqual(Mail.search_count([]), avant, "aucun mail.mail né du module")

    def test_remplir_sous_le_seuil_propose(self):
        self.membre.bf_gen_company_mode = "fill"
        p = self._creer()
        self._traiter(p, {"data": dict(TROUVE, confidence=45)})
        self.assertEqual(p.x_bf_gen_state, "proposed")
        self.assertFalse(p.phone)

    def test_remplir_suit_le_demandeur_pas_le_createur(self):
        """La préférence lue est celle de qui a demandé la dernière fois."""
        p = self._creer(user=self.autre)
        self.membre.bf_gen_company_mode = "fill"
        p.with_user(self.membre).action_bf_gen_complete()
        self._traiter(p, {"data": TROUVE})
        self.assertEqual(p.x_bf_gen_state, "done")

    # ── Rien, erreurs ───────────────────────────────────────────────

    def test_confiance_basse_rien_de_fiable(self):
        p = self._creer()
        self._traiter(p, {"data": dict(TROUVE, confidence=20)})
        self.assertEqual(p.x_bf_gen_state, "nothing")
        self.assertFalse(p.sudo().x_bf_gen_proposal)

    def test_confiance_sans_rien_derriere(self):
        p = self._creer()
        self._traiter(p, {"data": {"confidence": 95, "sources": []}})
        self.assertEqual(p.x_bf_gen_state, "nothing")

    def test_confiance_en_texte(self):
        p = self._creer()
        self._traiter(p, {"data": dict(TROUVE, confidence="88")})
        self.assertEqual(p.x_bf_gen_state, "proposed")

    def test_pont_absent(self):
        p = self._creer()
        with patch(BRIDGE, side_effect=FileNotFoundError("socket")):
            self.env["res.partner"]._cron_bf_gen_companies()
        self.assertEqual(p.x_bf_gen_state, "error")
        self.assertEqual(p.x_bf_gen_message, "Gen service unavailable.")

    def test_erreur_du_pont(self):
        p = self._creer()
        self._traiter(p, {"error": "Company search timed out (120s)"})
        self.assertEqual(p.x_bf_gen_state, "error")
        self.assertIn("timed out", p.x_bf_gen_message)

    def test_une_fiche_fautive_ne_bloque_pas_la_suivante(self):
        a = self._creer(name="Première")
        b = self._creer(name="Seconde")
        reponses = iter([RuntimeError("boum"), {"data": TROUVE}])

        def pont(*_args, **_kw):
            r = next(reponses)
            if isinstance(r, Exception):
                raise r
            return r
        with patch(BRIDGE, side_effect=pont):
            self.env["res.partner"]._cron_bf_gen_companies(chunk=5)
        self.assertEqual(a.x_bf_gen_state, "error")
        self.assertEqual(b.x_bf_gen_state, "proposed")

    def test_le_demandeur_est_averti(self):
        p = self._creer()
        Partner = type(self.env["res.partner"])
        with patch.object(Partner, "_bus_send", autospec=True) as envoi:
            self._traiter(p, {"data": TROUVE})
        self.assertTrue(envoi.called)
        destinataire, genre, message = envoi.call_args[0]
        self.assertEqual(destinataire, self.membre.partner_id)
        self.assertEqual(genre, "simple_notification")
        self.assertEqual(message["type"], "info")

    # ── Relecture adverse, tour 1 ───────────────────────────────────

    def test_les_defauts_du_contexte_ne_mettent_pas_en_file(self):
        """🔴 M1 : les `default_x_bf_gen_*` s'ajoutaient après la garde des vals."""
        Partner = self.env["res.partner"]
        p = Partner.with_user(self.autre).with_context(
            default_x_bf_gen_state="queued").create(
            {"name": "Par défaut", "is_company": True})
        self.assertFalse(p.x_bf_gen_state)
        # Remplir au nom d'un autre : ni file, ni écriture sans confirmation.
        self.membre.bf_gen_company_mode = "fill"
        q = Partner.with_user(self.autre).with_context(
            default_x_bf_gen_state="queued",
            default_x_bf_gen_requested_by=self.membre.id).create(
            {"name": "Au nom d'un autre", "is_company": True})
        self._traiter(q, {"data": TROUVE})
        self.assertFalse(q.x_bf_gen_state)
        self.assertFalse(q.x_bf_gen_requested_by)
        self.assertFalse(q.phone)
        # Un membre : la mise en file à la création tient, à son nom à lui.
        r = Partner.with_user(self.membre).with_context(
            default_x_bf_gen_state="done",
            default_x_bf_gen_requested_by=self.autre.id).create(
            {"name": "Membre", "is_company": True})
        self.assertEqual(r.x_bf_gen_state, "queued")
        self.assertEqual(r.x_bf_gen_requested_by, self.membre)

    def test_aucune_transaction_tenue_pendant_l_appel(self):
        """🔴 M2 : la transaction est validée avant l'appel au pont, et après."""
        p = self._creer()
        ordre = []
        Partner = type(self.env["res.partner"])

        def pont(*_a, **_k):
            ordre.append("pont")
            return {"data": TROUVE}
        Env = type(self.env)
        invalider = Env.invalidate_all

        def relire(env, *a, **k):
            ordre.append("relire")
            return invalider(env, *a, **k)
        with patch.object(Partner, "_bf_gen_valider", autospec=True,
                          side_effect=lambda _self: ordre.append("valider")), \
                patch.object(Env, "invalidate_all", relire), \
                patch(BRIDGE, side_effect=pont):
            self.env["res.partner"]._cron_bf_gen_companies()
        # 🔴 N1 : la transaction se termine APRÈS l'appel et AVANT la relecture.
        # Les lectures de paramètres du pont (cache froid) ouvrent un instantané
        # pendant l'appel ; sans cette fin, l'écriture concurrente n'était pas
        # vue et l'écriture du résultat tombait en SerializationFailure.
        self.assertEqual(ordre[:4], ["valider", "pont", "valider", "relire"])
        self.assertEqual(ordre[-1], "valider")
        p.invalidate_recordset()
        self.assertEqual(p.x_bf_gen_state, "proposed")

    def test_fiche_changee_pendant_l_appel_rien_n_est_ecrit(self):
        """🔴 M2 : la fiche est relue après l'appel, pas reprise de l'instantané."""
        cr = self.env.cr
        changements = {
            "écartée": lambda p: cr.execute(
                "UPDATE res_partner SET x_bf_gen_state = 'dismissed' "
                "WHERE id = %s", (p.id,)),
            "archivée": lambda p: cr.execute(
                "UPDATE res_partner SET active = false WHERE id = %s", (p.id,)),
            "supprimée": lambda p: p.sudo().unlink(),
        }
        Partner = type(self.env["res.partner"])
        for cas, changer in changements.items():
            with self.subTest(cas):
                p = self._creer(name="Changée %s" % cas)
                p.flush_recordset()
                avant = p.x_bf_gen_state  # en cache, comme dans la tâche

                def pont(*_a, _p=p, _changer=changer, **_k):
                    _changer(_p)
                    return {"data": TROUVE}
                with patch(BRIDGE, side_effect=pont), \
                        patch.object(Partner, "_bus_send", autospec=True) as avis:
                    self.env["res.partner"]._cron_bf_gen_companies()
                self.assertEqual(avant, "queued")
                self.assertFalse(avis.called, cas)
                if cas == "supprimée":
                    self.assertFalse(p.exists())
                    continue
                p.invalidate_recordset()
                self.assertNotEqual(p.x_bf_gen_state, "proposed", cas)
                self.assertFalse(p.sudo().x_bf_gen_proposal, cas)

    def test_une_ecriture_en_echec_sort_la_fiche_de_la_file(self):
        """🔴 M2 : même l'écriture de l'erreur en échec ne laisse pas la fiche en
        file ; sans cela, la recherche se repayait à chaque passage."""
        p = self._creer()
        Partner = type(self.env["res.partner"])
        ecrire = Partner.write

        def write(rec, vals):
            if vals.get("x_bf_gen_state") in ("proposed", "error"):
                raise RuntimeError("écriture refusée")
            return ecrire(rec, vals)
        with patch(BRIDGE, return_value={"data": TROUVE}) as appel, \
                patch.object(Partner, "write", autospec=True, side_effect=write):
            self.env["res.partner"]._cron_bf_gen_companies()
            self.env["res.partner"]._cron_bf_gen_companies()
        p.invalidate_recordset()
        self.assertEqual(p.x_bf_gen_state, "error")
        self.assertEqual(p.x_bf_gen_message, "Unexpected error.")
        self.assertEqual(appel.call_count, 1)

    def test_province_dans_le_pays_qui_sera_sur_la_fiche(self):
        """m3 : Canada sur la fiche, Vermont rendu : la province est omise."""
        self.membre.bf_gen_company_mode = "fill"
        usa = self.env.ref("base.us")
        vermont = self.env["res.country.state"].search(
            [("country_id", "=", usa.id), ("code", "=", "VT")], limit=1)
        data = dict(TROUVE, country="United States", state="Vermont")
        p = self._creer(country_id=self.canada.id)
        self._traiter(p, {"data": data})
        self.assertEqual(p.x_bf_gen_state, "done")
        self.assertEqual(p.country_id, self.canada)
        self.assertFalse(p.state_id)
        # Sans pays sur la fiche, celui rendu fait foi.
        q = self._creer(name="Sans pays")
        self._traiter(q, {"data": data})
        self.assertEqual(q.country_id, usa)
        self.assertEqual(q.state_id, vermont)
        # En écrasant, le pays rendu remplace celui de la fiche.
        vals = p._bf_gen_vals(data, overwrite=True)
        self.assertEqual(vals["country_id"], usa.id)
        self.assertEqual(vals["state_id"], vermont.id)

    def test_l_etat_s_ecrit_sous_un_usager_ordinaire(self):
        """f6 : l'usager de la tâche planifiée peut ne pas être sudo ; la garde
        écartait alors l'état en silence, et la fiche restait en file."""
        self.membre.bf_gen_company_mode = "fill"
        cas = [
            ({"error": "boum"}, "error"),
            ({"data": dict(TROUVE, confidence=10)}, "nothing"),
            ({"data": TROUVE}, "done"),
            ({"data": dict(TROUVE, confidence=45)}, "proposed"),
        ]
        Cron = self.env["res.partner"].with_user(self.membre)
        self.assertFalse(Cron.env.su)
        for reponse, attendu in cas:
            with self.subTest(attendu):
                p = self._creer(name="Ordinaire %s" % attendu)
                with patch(BRIDGE, return_value=reponse):
                    Cron._cron_bf_gen_companies(chunk=5)
                p.invalidate_recordset()
                self.assertEqual(p.x_bf_gen_state, attendu)
        p = self._creer(name="Ordinaire pont absent")
        with patch(BRIDGE, side_effect=FileNotFoundError("socket")):
            Cron._cron_bf_gen_companies(chunk=5)
        p.invalidate_recordset()
        self.assertEqual(p.x_bf_gen_state, "error")

    # ── Relecture adverse, tour 2 ───────────────────────────────────

    def _lecteur(self, creer=False):
        """Un membre du groupe qui lit les contacts sans pouvoir les modifier
        (et, au besoin, peut en créer)."""
        groupes = [self.env.ref("base.group_user").id,
                   self.env.ref("bf_contact_enrichment.group_bf_contact_enrich").id]
        if creer:
            createur = self.env["res.groups"].create({"name": "Créer seulement 26837"})
            self.env["ir.model.access"].create({
                "name": "créer seulement 26837",
                "model_id": self.env.ref("base.model_res_partner").id,
                "group_id": createur.id,
                "perm_read": True, "perm_create": True,
                "perm_write": False, "perm_unlink": False,
            })
            groupes.append(createur.id)
        user = self.env["res.users"].create({
            "name": "Lecteur 26837", "login": "lecteur_gen_26837%s" % creer,
            "email": "lecteur_gen_26837@example.com",
            "groups_id": [(6, 0, groupes)],
        })
        self.assertTrue(self._creer().with_user(user).has_access("read"))
        self.assertFalse(self._creer().with_user(user).has_access("write"))
        return user

    def test_sans_droit_d_ecriture_ni_bouton_ni_file(self):
        """N2 : qui ne peut pas modifier la fiche ne fait pas payer de recherche."""
        lecteur = self._lecteur()
        p = self._creer(user=self.autre)
        with self.assertRaises(AccessError):
            p.with_user(lecteur).action_bf_gen_complete()
        self.assertFalse(p.x_bf_gen_state)
        createur = self._lecteur(creer=True)
        q = self.env["res.partner"].with_user(createur).create(
            {"name": "Créée sans droit d'écriture", "is_company": True})
        self.assertFalse(q.x_bf_gen_state)

    def test_remplir_sans_droit_d_ecriture_propose(self):
        """N2 : le demandeur qui a perdu le droit d'écriture reçoit une
        proposition ; rien n'est écrit en sudo à sa place."""
        self.membre.bf_gen_company_mode = "fill"
        p = self._creer()
        self.assertEqual(p.x_bf_gen_state, "queued")
        self.membre.groups_id = [
            (3, self.env.ref("base.group_partner_manager").id)]
        self.assertFalse(p.with_user(self.membre).has_access("write"))
        self._traiter(p, {"data": TROUVE})
        self.assertEqual(p.x_bf_gen_state, "proposed")
        self.assertFalse(p.phone)
        self.assertFalse(p.street)
        # Avec le droit, l'écriture se fait sous son nom à lui.
        self.membre.groups_id = [
            (4, self.env.ref("base.group_partner_manager").id)]
        q = self._creer(name="Avec le droit")
        self._traiter(q, {"data": TROUVE})
        self.assertEqual(q.x_bf_gen_state, "done")
        self.assertIn(self.membre.partner_id,
                      q.message_ids.filtered(
                          lambda m: "Online search by Gen" in (m.body or "")
                      ).author_id)

    def test_plus_une_societe_sort_de_la_file(self):
        """N4 : une fiche passée en personne n'est ni cherchée ni écrite."""
        self.membre.bf_gen_company_mode = "fill"
        p = self._creer()
        p.sudo().write({"is_company": False})
        self.assertEqual(p.x_bf_gen_state, "queued")
        appel = self._traiter(p, {"data": TROUVE})
        self.assertFalse(appel.called)
        self.assertEqual(p.x_bf_gen_state, "nothing")
        self.assertEqual(p.x_bf_gen_message, "This contact is no longer a company.")
        self.assertFalse(p.phone)
        # Passée en personne PENDANT l'appel : relue, puis sortie de la file.
        q = self._creer(name="Changée pendant l'appel")
        q.flush_recordset()

        def pont(*_a, **_k):
            self.env.cr.execute(
                "UPDATE res_partner SET is_company = false WHERE id = %s", (q.id,))
            return {"data": TROUVE}
        with patch(BRIDGE, side_effect=pont):
            self.env["res.partner"]._cron_bf_gen_companies()
        q.invalidate_recordset()
        self.assertEqual(q.x_bf_gen_state, "nothing")
        self.assertFalse(q.phone)
