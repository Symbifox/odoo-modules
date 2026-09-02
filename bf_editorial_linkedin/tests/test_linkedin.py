# -*- coding: utf-8 -*-
"""Le connecteur LinkedIn, éprouvé sans application LinkedIn.

Tout ce que le connecteur envoie et lit passe par ``_appel``. Le remplacer
suffit à jouer les réponses du réseau, y compris celles qu'on ne veut jamais
voir en production : le jeton expiré, la portée manquante, et surtout la
réussite muette qui ne rend pas d'identifiant.
"""

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


class Reponse:
    """Juste assez de ``requests.Response`` pour ce que le connecteur en lit."""

    def __init__(self, status_code=200, corps=None, headers=None, texte=""):
        self.status_code = status_code
        self._corps = corps if corps is not None else {}
        self.headers = headers or {}
        self.text = texte

    def json(self):
        return self._corps


@tagged("post_install", "-at_install")
class TestConnecteurLinkedIn(TransactionCase):

    def setUp(self):
        super().setUp()
        self.connecteur = self.env["bf.social.connector.linkedin"]
        self.canal = self.env["bf.social.channel"].create({
            "name": "LinkedIn d'essai",
            "network": "linkedin",
            "handle": "blue-fox-essai",
            "lang_id": self.env["res.lang"].search(
                [("active", "=", True)], limit=1).id,
        })
        # ⚠️ On ne POSE pas de secret : l'écrire exige la clé Fernet du
        # locataire, que le banc n'a pas. Un test qui dépend de la
        # configuration de chiffrement d'une instance ne mesure plus le
        # connecteur, il mesure l'instance. On remplace la lecture du secret
        # là où elle sert, et nulle part ailleurs.

        blogue = self.env["blog.blog"].create({"name": "Banc LinkedIn"})
        billet = self.env["blog.post"].create({
            "name": "Un billet à diffuser", "blog_id": blogue.id,
        })
        calendrier = self.env["bf.editorial.calendar"].create({
            "name": "LinkedIn", "require_all_langs": "no",
        })
        self.entree = self.env["bf.editorial.entry"].create({
            "name": "Entrée LinkedIn", "calendar_id": calendrier.id,
            "post_id": billet.id,
        })
        self.billet_social = self.env["bf.social.post"].create({
            "entry_id": self.entree.id,
            "channel_id": self.canal.id,
            "body": "Un texte court.",
            "link_url": "https://exemple.test/blog/un-billet-1",
        })

    def _avec(self, reponse):
        return patch.object(
            type(self.connecteur), "_appel",
            lambda self_, methode, chemin, channel, **kw: reponse,
        )

    # --- identifiants -----------------------------------------------------
    def test_un_jeton_valide_resout_l_urn(self):
        with self._avec(Reponse(200, {"sub": "abc123", "name": "Blue Fox"})):
            ok, message = self.connecteur._validate_credentials(self.canal)
        self.assertTrue(ok)
        self.assertEqual(self.canal.linkedin_member_urn, "urn:li:person:abc123")
        self.assertIn("Blue Fox", message)

    def test_un_401_dit_que_le_jeton_a_expire(self):
        with self._avec(Reponse(401, texte="unauthorized")):
            ok, message = self.connecteur._validate_credentials(self.canal)
        self.assertFalse(ok)
        self.assertIn("60 jours", message)

    def test_un_403_dit_quelle_portee_manque(self):
        with self._avec(Reponse(403)):
            ok, message = self.connecteur._validate_credentials(self.canal)
        self.assertFalse(ok)
        self.assertIn("openid", message)

    def test_une_reponse_sans_sujet_est_un_echec(self):
        with self._avec(Reponse(200, {"name": "Sans sub"})):
            ok, message = self.connecteur._validate_credentials(self.canal)
        self.assertFalse(ok)
        self.assertFalse(self.canal.linkedin_member_urn)

    # --- diffusion --------------------------------------------------------
    def test_l_identifiant_se_lit_dans_l_entete(self):
        """⚠️ Le corps d'une création réussie est VIDE. Un connecteur qui lit
        le corps conclurait à un échec et republierait au passage suivant."""
        self.canal.sudo().write({"linkedin_member_urn": "urn:li:person:abc"})
        urn = "urn:li:share:7654321"
        with self._avec(Reponse(201, {}, {"x-restli-id": urn})):
            resultat = self.connecteur._publish(self.billet_social)
        self.assertEqual(resultat["remote_id"], urn)
        self.assertIn(urn, resultat["url"])

    def test_une_reussite_sans_entete_ne_se_lit_pas_comme_un_echec_muet(self):
        """Le pire cas : LinkedIn accepte, ne rend pas l'identifiant, et une
        relance publierait deux fois. L'erreur doit le DIRE."""
        self.canal.sudo().write({"linkedin_member_urn": "urn:li:person:abc"})
        with self._avec(Reponse(201, {}, {})):
            with self.assertRaises(UserError) as pris:
                self.connecteur._publish(self.billet_social)
        self.assertIn("deux fois", str(pris.exception))

    def test_un_refus_leve_une_erreur_explicite(self):
        self.canal.sudo().write({"linkedin_member_urn": "urn:li:person:abc"})
        with self._avec(Reponse(422, texte='{"message":"duplicate post"}')):
            with self.assertRaises(UserError) as pris:
                self.connecteur._publish(self.billet_social)
        self.assertIn("422", str(pris.exception))
        self.assertIn("duplicate", str(pris.exception))

    def test_l_article_part_en_carte_et_non_dans_le_texte(self):
        self.canal.sudo().write({"linkedin_member_urn": "urn:li:person:abc"})
        corps = self.connecteur._corps_publication(self.billet_social)
        self.assertEqual(corps["commentary"], "Un texte court.")
        self.assertNotIn("exemple.test", corps["commentary"])
        self.assertEqual(
            corps["content"]["article"]["source"],
            "https://exemple.test/blog/un-billet-1",
        )
        self.assertFalse(self.connecteur._link_in_body())

    def test_un_texte_trop_long_est_refuse_avant_l_appel(self):
        self.canal.sudo().write({"linkedin_member_urn": "urn:li:person:abc"})
        self.billet_social.body = "a" * 3001
        with self.assertRaises(UserError) as pris:
            self.connecteur._publish(self.billet_social)
        self.assertIn("3000", str(pris.exception))

    # --- mesures ----------------------------------------------------------
    def test_aucune_mesure_ne_se_confond_avec_zero(self):
        """Rendre zéro ferait croire à une publication que personne n'a vue.
        Un dictionnaire vide dit « ce réseau ne les donne pas »."""
        self.assertEqual(self.connecteur._fetch_metrics(self.billet_social), {})

    # --- version d'API ----------------------------------------------------
    def test_la_version_d_api_se_regle_sans_redeployer(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_editorial_linkedin.api_version", "202512")
        self.assertEqual(self.connecteur._version(), "202512")
        with patch.object(
            type(self.canal), "_decrypt_secret", lambda self_: "un-jeton"
        ):
            entetes = self.connecteur._entetes(self.canal)
        self.assertEqual(entetes["LinkedIn-Version"], "202512")
        self.assertEqual(entetes["Authorization"], "Bearer un-jeton")
        self.assertEqual(entetes["X-Restli-Protocol-Version"], "2.0.0")

    def test_sans_jeton_l_erreur_dit_quoi_faire(self):
        with patch.object(
            type(self.canal), "_decrypt_secret", lambda self_: ""
        ):
            with self.assertRaises(UserError) as pris:
                self.connecteur._entetes(self.canal)
        self.assertIn("jeton", str(pris.exception))

    # --- expiration du jeton ---------------------------------------------
    def test_le_preavis_part_avant_l_echeance(self):
        self.canal.linkedin_token_expiry = fields.Date.add(
            fields.Date.context_today(self.canal), days=3)
        avant = len(self.canal.message_ids)
        self.env["bf.social.channel"]._cron_warn_linkedin_expiry()
        corps = " ".join(c or "" for c in self.canal.message_ids.mapped("body"))
        self.assertGreater(len(self.canal.message_ids), avant)
        self.assertIn("expire dans 3", corps)

    def test_un_jeton_encore_loin_ne_derange_personne(self):
        self.canal.linkedin_token_expiry = fields.Date.add(
            fields.Date.context_today(self.canal), days=45)
        avant = len(self.canal.message_ids)
        self.env["bf.social.channel"]._cron_warn_linkedin_expiry()
        self.assertEqual(len(self.canal.message_ids), avant)

    def test_un_jeton_deja_expire_le_dit(self):
        self.canal.linkedin_token_expiry = fields.Date.subtract(
            fields.Date.context_today(self.canal), days=2)
        self.env["bf.social.channel"]._cron_warn_linkedin_expiry()
        corps = " ".join(c or "" for c in self.canal.message_ids.mapped("body"))
        self.assertIn("expiré depuis 2", corps)

    def test_sans_date_notee_le_cron_ne_dit_rien(self):
        avant = len(self.canal.message_ids)
        self.env["bf.social.channel"]._cron_warn_linkedin_expiry()
        self.assertEqual(len(self.canal.message_ids), avant)
