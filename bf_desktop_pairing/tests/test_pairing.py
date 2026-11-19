"""Le flux d'appairage, et surtout ce qu'il REFUSE.

⚠️ Taggé ``post_install`` : sur une grosse base, un essai ``at_install`` tourne
sur un registre PARTIEL, et ce module parle à ``res.users`` et aux règles
d'enregistrement de modules qui ne sont pas encore chargés.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from ..models.bf_desktop_device import PAIRING_TTL_SECONDS


@tagged("post_install", "-at_install", "bf_desktop_pairing")
class TestAppairage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Device = cls.env["bf.desktop.device"]
        cls.alice = cls.env["res.users"].create({
            "name": "Alice Interne", "login": "alice.bureau@example.invalid",
            "groups_id": [(4, cls.env.ref("base.group_user").id)],
        })
        cls.bob = cls.env["res.users"].create({
            "name": "Bob Interne", "login": "bob.bureau@example.invalid",
            "groups_id": [(4, cls.env.ref("base.group_user").id)],
        })

    # ── Le chemin normal ──────────────────────────────────────────────────

    def test_appairage_complet_rend_un_jeton_qui_resout(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        self.assertTrue(device)
        self.assertFalse(device.user_id, "une demande n'a pas de propriétaire")
        self.assertFalse(device.active, "une demande n'est pas un appareil")

        self.assertFalse(device._deliver(secret),
                         "rien n'est livré avant l'approbation")

        device._approve(self.alice.id)
        jeton = device._deliver(secret)
        self.assertTrue(jeton)
        self.assertTrue(device.active)
        self.assertEqual(device.user_id, self.alice)

        resolu = self.Device._resolve(jeton)
        self.assertEqual(resolu, device)

    def test_le_jeton_n_est_pas_gardable_en_clair(self):
        """Le contrôle de l'audit 2026-09-08 (S-M2), éprouvé pour de vrai.

        🔴 La première version de cet essai ne pouvait PAS échouer : elle
        affirmait qu'un champ `device_token` était vide, alors que ce module ne
        l'écrit jamais. Un essai qui nomme le contrôle central d'un audit sans
        l'éprouver est pire que pas d'essai. Ce qui se vérifie vraiment : la
        base ne porte AUCUNE colonne où un jeton pourrait être lu en clair, et
        l'empreinte stockée est bien celle du jeton rendu.
        """
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        jeton = device._deliver(secret)

        self.assertNotIn("device_token", self.Device._fields,
                         "aucun champ ne doit pouvoir porter le jeton en clair")
        self.env.cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'bf_desktop_device'")
        colonnes = {ligne[0] for ligne in self.env.cr.fetchall()}
        self.assertNotIn("device_token", colonnes,
                         "ni en base, même en colonne orpheline")

        # L'empreinte est bien celle du jeton rendu, et le jeton ne s'en
        # déduit pas.
        self.assertEqual(device.token_hash, self.Device._hash(jeton))
        self.assertNotEqual(device.token_hash, jeton)
        for colonne in colonnes:
            self.env.cr.execute(
                "SELECT count(*) FROM bf_desktop_device "
                "WHERE id = %%s AND %s::text = %%s" % colonne,
                (device.id, jeton))
            self.assertEqual(self.env.cr.fetchone()[0], 0,
                             "le jeton ne se lit dans aucune colonne (%s)"
                             % colonne)

    def test_le_code_et_le_secret_sont_effaces_apres_livraison(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        device._deliver(secret)
        self.assertFalse(device.pending_code)
        self.assertFalse(device.device_secret_hash)
        self.assertFalse(device.ip_hash, "l'empreinte du demandeur ne survit pas")

    # ── Ce qui est refusé ─────────────────────────────────────────────────

    def test_livraison_une_seule_fois(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        self.assertTrue(device._deliver(secret))
        self.assertIsNone(device._deliver(secret),
                          "un deuxième appel ne redonne pas de jeton")

    def test_mauvais_secret_detruit_la_demande(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        self.assertIsNone(device._deliver("pas-le-bon-secret"))
        self.assertFalse(device.exists(),
                         "une demande sollicitée avec le mauvais secret meurt")

    def test_secret_vide_refuse(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        self.assertIsNone(device._deliver(""))
        self.assertFalse(device.exists())

    def test_demande_expiree_est_detruite_et_introuvable(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device.sudo().write({
            "pending_expiry": device.pending_expiry
            - timedelta(seconds=PAIRING_TTL_SECONDS + 60),
        })
        self.assertFalse(self.Device._pending(code))
        self.assertFalse(device.exists(),
                         "une demande expirée ne se rejoue pas")

    def test_refus_detruit_la_demande(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._refuse()
        self.assertFalse(device.exists())
        self.assertFalse(self.Device._pending(code))

    def test_surface_inconnue_refusee(self):
        device, code, secret = self.Device._start_pairing("pas-une-surface")
        self.assertFalse(device)
        self.assertIsNone(code)

    def test_jeton_inconnu_ne_resout_rien(self):
        self.assertFalse(self.Device._resolve("jeton-invente"))
        self.assertFalse(self.Device._resolve(""))
        self.assertFalse(self.Device._resolve(None))

    def test_usager_archive_perd_sa_porte(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        jeton = device._deliver(secret)
        self.assertTrue(self.Device._resolve(jeton))
        self.alice.sudo().write({"active": False})
        self.assertFalse(self.Device._resolve(jeton),
                         "archiver le compte ferme la porte du poste")

    def test_usager_portail_refuse(self):
        portail = self.env["res.users"].create({
            "name": "Pierre Portail", "login": "pierre.portail@example.invalid",
            "groups_id": [(4, self.env.ref("base.group_portal").id)],
        })
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(portail.id)
        jeton = device._deliver(secret)
        self.assertFalse(self.Device._resolve(jeton),
                         "un compte de portail n'a pas de bureau")

    def test_appareil_revoque_ne_resout_plus(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        jeton = device._deliver(secret)
        device.with_user(self.alice).action_revoke()
        self.assertFalse(self.Device._resolve(jeton))

    # ── Droits ────────────────────────────────────────────────────────────

    def test_personne_ne_cree_un_appareil_a_la_main(self):
        with self.assertRaises(AccessError):
            self.Device.with_user(self.alice).create({
                "name": "Faux", "app": "stickynotes", "user_id": self.alice.id,
            })

    def test_personne_ne_reecrit_le_jeton_ni_le_proprietaire(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        device._deliver(secret)
        sien = device.with_user(self.alice)
        with self.assertRaises(AccessError):
            sien.write({"token_hash": "x" * 64})
        with self.assertRaises(AccessError):
            sien.write({"user_id": self.bob.id})
        # Ce qui reste permis : le renommer.
        sien.write({"name": "Mon poste"})
        self.assertEqual(device.name, "Mon poste")

    def test_je_ne_vois_pas_l_appareil_d_un_collegue(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        device._deliver(secret)
        vus = self.Device.with_user(self.bob).search([])
        self.assertNotIn(device, vus,
                         "les appareils d'autrui ne se lisent pas")

    def test_je_ne_revoque_pas_l_appareil_d_un_collegue(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        device._deliver(secret)
        # La règle de lecture le rend invisible : le geste ne trouve rien à
        # révoquer, et surtout ne touche pas l'appareil d'Alice.
        with self.assertRaises(AccessError):
            device.with_user(self.bob).action_revoke()
        self.assertTrue(device.active)

    def test_demande_en_vol_invisible_a_tous(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        for usager in (self.alice, self.bob):
            vus = self.Device.with_user(usager).with_context(
                active_test=False).search([])
            self.assertNotIn(device, vus,
                             "une demande non approuvée ne se lit pas")

    # ── Frein ─────────────────────────────────────────────────────────────

    def test_le_frein_refuse_apres_cinq_demandes_du_meme_appelant(self):
        empreinte = "a" * 64
        for _ in range(self.Device.MAX_PAIRINGS_PER_CALLER):
            self.assertTrue(self.Device._throttle_pairings(empreinte))
            self.Device._start_pairing("stickynotes", ip_hash=empreinte)
        self.assertFalse(self.Device._throttle_pairings(empreinte))
        # Un autre appelant n'est pas puni pour celui-là.
        self.assertTrue(self.Device._throttle_pairings("b" * 64))

    def test_un_appelant_ne_peut_pas_couper_l_appairage_de_tout_le_monde(self):
        """🔴 Le plafond global était un interrupteur d'appairage.

        Avec une borne commune basse (50), dix adresses sources — un seul /64
        IPv6 en fournit autant qu'on veut — suffisaient à ce que PERSONNE dans
        le locataire ne puisse plus apparier un widget, et le message rendu
        accusait son propre widget. La borne reste, très haut, pour que la
        table ne grossisse pas sans fin ; c'est le frein PAR APPELANT qui
        protège.
        """
        self.assertGreater(self.Device.MAX_PAIRINGS_ABSOLUTE, 1000)
        # Dix appelants saturent leur propre quota...
        for i in range(10):
            empreinte = ("%02d" % i) * 32
            for _ in range(self.Device.MAX_PAIRINGS_PER_CALLER):
                self.Device._start_pairing("stickynotes", ip_hash=empreinte)
            self.assertFalse(self.Device._throttle_pairings(empreinte))
        # ... et un appelant honnête passe toujours.
        self.assertTrue(self.Device._throttle_pairings("ff" * 32))

    def test_l_empreinte_de_l_appelant_est_a_cle(self):
        """Un SHA-256 nu d'une adresse IPv4, c'est l'adresse : 2^32 candidats."""
        empreinte = self.Device._fingerprint("198.51.100.7")
        self.assertNotEqual(empreinte, self.Device._hash("198.51.100.7"),
                            "l'empreinte doit dépendre du secret de l'instance")
        self.assertEqual(empreinte, self.Device._fingerprint("198.51.100.7"),
                         "et rester stable, sinon elle ne compte rien")

    def test_une_demande_sans_echeance_ne_bloque_pas_le_frein_pour_toujours(self):
        """Une ligne à échéance NULLE comptait sans jamais être ramassée."""
        device, code, secret = self.Device._start_pairing("stickynotes")
        device.sudo().write({"pending_expiry": False})
        self.assertEqual(self.Device._gc_pairings(), 1)
        self.assertFalse(device.exists())

    # ── La concordance de nombre ──────────────────────────────────────────

    # ── La version annoncée par le widget ─────────────────────────────────

    def _appareil(self, **extra):
        valeurs = {"name": "Banc", "user_id": self.env.user.id,
                   "app": "stickynotes"}
        valeurs.update(extra)
        return self.env["bf.desktop.device"].sudo().create(valeurs)

    def test_une_version_neuve_est_a_ranger(self):
        """🔴 `app_version` n'était écrit qu'à la CRÉATION : « Mes appareils »
        affichait pour toujours la version qui avait apparié le poste, et ce
        champ a servi de preuve à ce qu'il ne pouvait pas établir.
        """
        d = self._appareil(app_version="1.0.5")
        self.assertEqual(d._version_a_ranger("1.5.0"), "1.5.0")

    def test_la_meme_version_n_est_PAS_a_ranger(self):
        """⚠️ L'écriture n'a lieu que sur un CHANGEMENT. Écrire à chaque appel
        ferait tomber deux appels simultanés du même poste sur « could not
        serialize access due to concurrent update » — un incident connu, que
        le bureau rendrait quotidien : sa sonde, son delta et son ménage
        tombent dans la même seconde, toutes les trois secondes.
        """
        d = self._appareil(app_version="1.5.0")
        self.assertEqual(d._version_a_ranger("1.5.0"), "")
        self.assertEqual(d._version_a_ranger("  1.5.0  "), "",
                         "les espaces ne font pas un changement")

    def test_une_version_absente_ou_vide_ne_touche_a_rien(self):
        """Un widget plus ancien n'envoie pas l'en-tête : son absence ne doit pas
        effacer ce qu'on sait de lui."""
        d = self._appareil(app_version="1.0.5")
        for rien in (None, "", "   "):
            self.assertEqual(d._version_a_ranger(rien), "",
                             "%r ne doit rien changer" % (rien,))

    def test_une_version_absurde_est_tronquee_pas_refusee(self):
        """Le champ fait 40 caractères. Tronquer vaut mieux que lever : la
        version est un renseignement, pas une garde de sécurité."""
        d = self._appareil()
        rendu = d._version_a_ranger("9." * 60)
        self.assertEqual(len(rendu), 40)

    def test_le_contrat_de_l_en_tete_tient_des_DEUX_cotes(self):
        """🔴 La faute qui s'est reposée six fois dans ce lot : une clé posée
        d'un seul côté. L'en-tête que le widget envoie et celui que le serveur
        lit doivent porter le même nom — sinon la version ne remonte jamais, en
        silence.
        """
        import os
        from odoo.addons.bf_bloc_notes.controllers import mobile_api
        serveur = mobile_api.__file__
        if serveur.endswith(".pyc"):
            serveur = serveur[:-1]
        with open(serveur, encoding="utf-8") as f:
            code_serveur = f.read()
        self.assertIn("X-Symbifox-App-Version", code_serveur,
                      "le serveur doit lire l'en-tête")
        self.assertIn("_note_app_version", code_serveur,
                      "et le ranger par la méthode du registre")

        # Et le widget, dont le source vit hors du module — et hors de ce
        # dépôt. Son chemin se donne par l'environnement : sans lui, l'essai
        # saute plutôt que d'affirmer quelque chose qu'il ne peut pas lire.
        widget = os.path.expanduser(
            os.environ.get("SYMBIFOX_STICKYNOTES_API_JS", ""))
        if not widget or not os.path.isfile(widget):
            self.skipTest("source du widget absent de cette machine")
        with open(widget, encoding="utf-8") as f:
            code_widget = f.read()
        self.assertIn("X-Symbifox-App-Version", code_widget,
                      "le widget doit envoyer l'en-tête que le serveur lit")
        # ⚠️ Avec le jeton seulement : sans lui, le serveur ne sait pas de quel
        # appareil il s'agit, et l'en-tête ne sert qu'à se décrire à un inconnu.
        bloc = code_widget[code_widget.index("if (opts.token) {"):]
        self.assertIn("X-Symbifox-App-Version", bloc[:600],
                      "l'en-tête doit être posé dans la branche du jeton")

    def test_la_page_porte_l_avertissement_qui_est_la_vraie_garde(self):
        """🔴 Relevé en relecture.

        La concordance de nombre ne résiste pas à un attaquant préparé :
        ``/auth/device/start`` est public et lui rend ``match_number``, donc il
        connaît le bon nombre et peut l'écrire dans son leurre. Ce qui reste
        alors, c'est la PHRASE de la page. Elle est donc gardée ici : si elle
        disparaissait, il ne resterait rien du tout.
        """
        from odoo.addons.bf_desktop_pairing.controllers import pairing
        source = pairing.__file__
        if source.endswith(".pyc"):
            source = source[:-1]
        with open(source, encoding="utf-8") as f:
            code = f.read()
        for phrase in ("not just start this pairing", "close this page",
                       "I did not start this"):
            self.assertIn(
                phrase, code,
                "la page de consentement doit porter %r : c'est la seule garde "
                "qui tienne contre un appairage qu'on n'a pas lancé" % phrase)

    def test_le_nombre_de_concordance_est_parmi_les_trois_choix(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        choix = self.Device._match_choices(code)
        self.assertEqual(len(choix), 3)
        self.assertEqual(len(set(choix)), 3, "trois nombres distincts")
        self.assertIn(self.Device._match_number(code), choix)

    def test_les_choix_ne_changent_pas_d_un_affichage_a_l_autre(self):
        """Sinon une personne honnête douterait de ce qu'elle a vu."""
        device, code, secret = self.Device._start_pairing("stickynotes")
        self.assertEqual(self.Device._match_choices(code),
                         self.Device._match_choices(code))

    def test_deux_demandes_n_ont_pas_le_meme_nombre(self):
        nombres = set()
        for _ in range(12):
            device, code, secret = self.Device._start_pairing("stickynotes")
            nombres.add(self.Device._match_number(code))
        self.assertGreater(len(nombres), 1,
                           "le nombre doit dépendre de la demande")

    # ── La péremption ─────────────────────────────────────────────────────

    def test_un_jeton_perime_ne_se_ressuscite_pas(self):
        """🔴 `active` était écrivable : le propriétaire rouvrait en RPC une
        porte que la péremption des 90 jours venait de fermer."""
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        jeton = device._deliver(secret)
        device.sudo().write({
            "last_seen": fields.Datetime.now() - timedelta(days=200)})
        self.assertFalse(self.Device._resolve(jeton), "la porte est fermée")

        self.assertFalse(device.active, "l'appareil est désactivé")
        self.assertFalse(device.token_hash,
                         "et l'empreinte est partie, comme à la révocation")

        # Et la porte reste fermée même si l'écriture avait été perdue : le
        # refus est recalculé à chaque appel.
        self.assertFalse(self.Device._resolve(jeton))

        with self.assertRaises(AccessError):
            device.with_user(self.alice).write({"active": True})

    def test_le_menage_retire_les_demandes_mortes(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device.sudo().write({
            "pending_expiry": device.pending_expiry - timedelta(hours=1),
        })
        self.assertEqual(self.Device._gc_pairings(), 1)
        self.assertFalse(device.exists())

    def test_le_menage_epargne_les_appareils_apparies(self):
        device, code, secret = self.Device._start_pairing("stickynotes")
        device._approve(self.alice.id)
        device._deliver(secret)
        self.Device._gc_pairings()
        self.assertTrue(device.exists(),
                        "un appareil livré n'est pas une demande à purger")
