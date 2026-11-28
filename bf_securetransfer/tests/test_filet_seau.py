"""Le filet du seau suit la plus longue rétention, et ne descend jamais.

La règle `st-expire-orphans-<préfixe>` efface tout objet du préfixe après
`max(60, plus longue rétention + 15)` jours. Avant 18.0.1.26.0 elle gardait la
valeur du jour où « Configurer le bucket S3 » avait été jouée : relever la
rétention d'une marque (de 7 à 30 jours, par exemple) ne la
reposait pas.

Ce que la suite épingle :

* relever une rétention DEMANDE la tâche planifiée (déclencheur), sans parler
  au fournisseur dans la transaction de l'écran ;
* la tâche remonte la règle, consigne le délai posé, et la fiche d'un
  brouillon gardé suit sans geste manuel ;
* le filet ne descend jamais : ni par la tâche, ni par l'action manuelle ;
* filet inconnu, stockage non configuré, fournisseur qui refuse : la tâche ne
  pose rien, ne lève pas, et la valeur consignée reste celle du seau ;
* la tâche est livrée ACTIVE — lu dans le XML, pas sur la base : un environnement d'essai
  éteint toutes les tâches planifiées après chaque geste.

S3 est bouchonné : la suite tourne sans réseau.
"""
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

from lxml import etree

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer.models import s3

try:  # pragma: no cover — selon l'image
    from botocore.exceptions import ClientError as _ClientError
except ImportError:  # pragma: no cover
    class _ClientError(Exception):
        def __init__(self, response, operation_name):
            super().__init__(response["Error"]["Code"])
            self.response = response
            self.operation_name = operation_name

PREFIXE = "transfers-essai-filet"
CRON = "bf_securetransfer.cron_st_sync_lifecycle"


def cerr(code):
    return _ClientError({"Error": {"Code": code, "Message": code}}, "Op")


@tagged("post_install", "-at_install")
class TestFiletSeau(TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env["ir.config_parameter"].sudo()
        for cle, valeur in (
            ("s3_endpoint_url", "https://s3.example.test"),
            ("s3_bucket", "seau-essai"),
            ("s3_region", "ca-east-1"),
            ("s3_key_prefix", PREFIXE),
            ("default_free_max_retention_days", "7"),
            ("default_paid_max_retention_days", "30"),
        ):
            self.icp.set_param("bf_securetransfer." + cle, valeur)
        # Toutes les marques archivées : le calcul du filet devient le même sur
        # n'importe quelle base (les archivées comptent quand même, d'où 0).
        self.env["secure.transfer.brand"].sudo().with_context(
            active_test=False).search([]).write(
            {"is_default": False, "active": False, "max_retention_days": 0})
        self.client = MagicMock(name="s3_client")
        self._seau(None)
        for cible, valeur in (
            ("client", MagicMock(return_value=self.client)),
            ("_client_error", lambda: _ClientError),
            ("_http", MagicMock(side_effect=AssertionError("pas de réseau"))),
        ):
            p = patch.object(s3, cible, valeur)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {
            "BF_SECURETRANSFER_S3_ACCESS_KEY": "CLE",
            "BF_SECURETRANSFER_S3_SECRET_KEY": "SECRET",
        })
        p.start()
        self.addCleanup(p.stop)
        self.cron = self.env.ref(CRON)
        # ⚠ Odoo 18 n'écrit aucun déclencheur « maintenant » pour une tâche
        # éteinte (`_trigger_list`), et un environnement d'essai éteint toutes les tâches après
        # chaque geste : sans cette ligne, les essais du déclencheur ne
        # mesureraient que l'état de la base. L'état LIVRÉ se prouve au XML
        # (dernier essai).
        self.cron.sudo().write({"active": True})

    # ------------------------------------------------------------ outils
    def _seau(self, jours):
        """Ce que le seau simulé porte : la règle d'un voisin, plus la nôtre
        à `jours` (aucune quand `jours` est None)."""
        regles = [{"ID": "st-expire-orphans-transfers-voisin",
                   "Status": "Enabled", "Expiration": {"Days": 999}}]
        if jours is not None:
            regles.append({
                "ID": "st-expire-orphans-" + PREFIXE, "Status": "Enabled",
                "Filter": {"Prefix": PREFIXE + "/"},
                "Expiration": {"Days": jours},
            })
        self.client.get_bucket_lifecycle_configuration.return_value = {
            "Rules": regles}

    def _poser(self, jours):
        """Une règle posée à `jours`, sur le seau ET consignée."""
        self._seau(jours)
        s3.record_applied_lifecycle(self.env, PREFIXE, jours)

    def _marque(self, jours):
        return self.env["secure.transfer.brand"].sudo().create({
            "name": f"Marque filet {jours}",
            "domain": f"m{jours}.essai-filet.test",
            "max_retention_days": jours,
        })

    def _declencheurs(self):
        return self.env["ir.cron.trigger"].sudo().search_count(
            [("cron_id", "=", self.cron.id)])

    def _regle_posee(self):
        regles = (self.client.put_bucket_lifecycle_configuration.call_args
                  .kwargs["LifecycleConfiguration"]["Rules"])
        return {r["ID"]: r for r in regles}

    # ======================================================= le déclencheur
    def test_relever_une_marque_demande_la_tache(self):
        """Une rétention qui dépasse le filet posé demande la tâche planifiée…"""
        self._poser(60)
        avant = self._declencheurs()
        marque = self._marque(30)
        self.assertEqual(self._declencheurs(), avant,
                         "30 + 15 tient sous 60 : rien à reposer")
        marque.write({"max_retention_days": 90})
        self.assertEqual(self._declencheurs(), avant + 1)

    def test_le_declencheur_ne_parle_pas_au_fournisseur(self):
        """… mais rien ne part vers le seau dans la transaction de l'écran : un
        fournisseur lent ferait échouer l'enregistrement d'une marque."""
        self._poser(60)
        avant = self._declencheurs()
        self._marque(120)
        self.assertEqual(self._declencheurs(), avant + 1,
                         "sans déclencheur, cet essai ne prouverait rien")
        self.client.put_bucket_lifecycle_configuration.assert_not_called()
        self.client.get_bucket_lifecycle_configuration.assert_not_called()
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 60)

    def test_les_defauts_de_palier_demandent_aussi_la_tache(self):
        """Une marque à 0 hérite du défaut de son palier : relever ce défaut
        dans les paramètres allonge les envois au même titre.

        ⚠ Pas de `res.config.settings.execute()` ici : il joue le `set_values`
        de TOUS les modules installés, dont un qui appelle un service externe
        (refusé par l'harnais). On joue le seul maillon de ce module, en
        bouchonnant ce qui est au-dessus de lui dans la chaîne."""
        from odoo.addons.bf_securetransfer.models.res_config_settings import (
            ResConfigSettings as Maillon,
        )
        self._poser(60)
        avant = self._declencheurs()
        self.icp.set_param(
            "bf_securetransfer.default_paid_max_retention_days", "180")
        reglages = self.env["res.config.settings"].new({})
        mro = type(reglages).__mro__
        suivant = mro[mro.index(Maillon) + 1]
        # create=True : la classe d'un module n'hérite pas de `set_values` en
        # Python, Odoo ne compose la chaîne que dans la classe du registre.
        with patch.object(suivant, "set_values", lambda self: None,
                          create=True):
            Maillon.set_values(reglages)
        self.assertEqual(self._declencheurs(), avant + 1)

    def test_filet_inconnu_aucun_declencheur(self):
        """Sans règle posée sous ce préfixe, la tâche ne ferait rien : ne pas
        la réveiller pour rien."""
        self.icp.set_param(
            "bf_securetransfer." + s3.LIFECYCLE_APPLIED_PARAM, False)
        avant = self._declencheurs()
        self._marque(400)
        self.assertEqual(self._declencheurs(), avant)

    # ========================================================== la tâche
    def test_la_tache_remonte_la_regle_et_la_consigne(self):
        self._poser(60)
        self._marque(100)
        self.assertTrue(self.env["secure.transfer"]._cron_sync_lifecycle())
        regles = self._regle_posee()
        self.assertEqual(
            regles["st-expire-orphans-" + PREFIXE]["Expiration"]["Days"], 115)
        self.assertEqual(
            regles["st-expire-orphans-" + PREFIXE]["Filter"]["Prefix"],
            PREFIXE + "/")
        self.assertEqual(
            regles["st-expire-orphans-transfers-voisin"]["Expiration"]["Days"],
            999, "la règle d'un autre locataire du seau a été touchée")
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 115)

    def test_la_fiche_du_brouillon_suit_sans_geste_manuel(self):
        """Le critère d'acceptation : la date affichée sur un
        brouillon gardé reste vraie après qu'une marque a relevé sa rétention."""
        self._poser(60)
        marque = self._marque(30)
        t = self.env["secure.transfer"].sudo().create({
            "brand_id": marque.id,
            "sender_email": "expediteur@example.test",
            "recipient_emails": "destinataire@example.test",
            "retention_days": 7,
            "state": "draft",
        })
        base = t.create_date
        self.assertEqual(t.draft_storage_deadline, base + timedelta(days=60))
        marque.write({"max_retention_days": 120})
        self.env["secure.transfer"]._cron_sync_lifecycle()
        t.invalidate_recordset()
        self.assertEqual(t.draft_storage_deadline, base + timedelta(days=135))

    def test_le_repli_d_idrive_consigne_aussi(self):
        """IDrive E2 refuse la règle d'abandon des multipart : la règle
        d'expiration passe seule, et c'est elle qu'on consigne."""
        self._poser(60)
        self._marque(100)
        self.client.put_bucket_lifecycle_configuration.side_effect = [
            cerr("InvalidRequest"), None,
        ]
        self.env["secure.transfer"]._cron_sync_lifecycle()
        self.assertEqual(
            self.client.put_bucket_lifecycle_configuration.call_count, 2)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 115)

    def test_un_refus_du_fournisseur_ne_consigne_rien_et_ne_leve_pas(self):
        """Odoo 18 désactive une tâche qui échoue plusieurs fois de suite :
        lever, ce serait perdre le rattrapage. Et la fiche garde le délai que
        le seau porte vraiment."""
        self._poser(60)
        self._marque(100)
        self.client.put_bucket_lifecycle_configuration.side_effect = cerr(
            "AccessDenied")
        self.assertFalse(self.env["secure.transfer"]._cron_sync_lifecycle())
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 60)

    def test_stockage_non_configure_ne_leve_pas(self):
        self._poser(60)
        self._marque(100)
        self.icp.set_param("bf_securetransfer.s3_endpoint_url", False)
        self.assertFalse(self.env["secure.transfer"]._cron_sync_lifecycle())
        self.client.put_bucket_lifecycle_configuration.assert_not_called()
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 60)

    def test_filet_inconnu_la_tache_ne_pose_rien(self):
        """Poser une règle sans les sondes ni le CORS de l'action serait une
        demi-installation silencieuse (instances sans seau configuré)."""
        self.icp.set_param(
            "bf_securetransfer." + s3.LIFECYCLE_APPLIED_PARAM, False)
        self._marque(100)
        self.assertTrue(self.env["secure.transfer"]._cron_sync_lifecycle())
        self.client.put_bucket_lifecycle_configuration.assert_not_called()
        self.assertIsNone(s3.applied_orphan_expiry_days(self.env))

    def test_une_regle_sous_un_autre_prefixe_ne_se_remonte_pas(self):
        s3.record_applied_lifecycle(self.env, "transfers-ancien", 60)
        self._marque(100)
        self.env["secure.transfer"]._cron_sync_lifecycle()
        self.client.put_bucket_lifecycle_configuration.assert_not_called()

    # ========================================================== le cliquet
    def test_une_regle_ecrasee_par_un_voisin_est_remise(self):
        """🔴 La lecture-fusion-écriture du seau partagé n'est pas atomique : un
        voisin peut y remettre une ancienne copie de notre règle (60) après que
        nous avons posé et consigné 115. La fiche annoncerait J+115 pendant
        que le seau efface à J+60. La passe relit le seau et remet 115."""
        s3.record_applied_lifecycle(self.env, PREFIXE, 115)
        self._seau(60)
        self.env["secure.transfer"]._cron_sync_lifecycle()
        self.assertEqual(
            self._regle_posee()["st-expire-orphans-" + PREFIXE]
            ["Expiration"]["Days"], 115)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 115)

    def test_une_regle_disparue_est_remise(self):
        s3.record_applied_lifecycle(self.env, PREFIXE, 115)
        self._seau(None)
        self.env["secure.transfer"]._cron_sync_lifecycle()
        self.assertIn("st-expire-orphans-" + PREFIXE, self._regle_posee())
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 115)

    def test_rien_ne_part_quand_le_seau_porte_deja_le_bon_delai(self):
        self._poser(60)
        self._marque(30)
        self.assertTrue(self.env["secure.transfer"]._cron_sync_lifecycle())
        self.client.put_bucket_lifecycle_configuration.assert_not_called()

    def test_la_tache_ne_baisse_jamais_le_filet(self):
        """🔴 Une marque ramenée de 90 à 30 jours ne raccourcit pas les envois
        déjà accordés pour 90 : baisser le filet les effacerait vivants."""
        self._poser(200)
        self._marque(30)
        self.assertTrue(self.env["secure.transfer"]._cron_sync_lifecycle())
        self.client.put_bucket_lifecycle_configuration.assert_not_called()
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 200)

    def test_l_action_manuelle_ne_baisse_pas_non_plus(self):
        """« Configurer le bucket S3 » rejouée après une baisse : même cliquet."""
        self._poser(200)
        self._marque(30)
        rapport = s3.setup_bucket(self.env)
        self.assertTrue(rapport["lifecycle"]["ok"], rapport["lifecycle"]["detail"])
        self.assertEqual(
            self._regle_posee()["st-expire-orphans-" + PREFIXE]
            ["Expiration"]["Days"], 200)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 200)

    def test_l_action_manuelle_ne_baisse_pas_sur_un_parametre_perdu(self):
        """Paramètre consigné perdu (restauration, préfixe remis) : le seau,
        lui, porte encore 200. Le cliquet se prend sur la règle relue."""
        self.icp.set_param(
            "bf_securetransfer." + s3.LIFECYCLE_APPLIED_PARAM, False)
        self._seau(200)
        self._marque(30)
        s3.setup_bucket(self.env)
        self.assertEqual(
            self._regle_posee()["st-expire-orphans-" + PREFIXE]
            ["Expiration"]["Days"], 200)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 200)

    def test_l_action_manuelle_monte_quand_il_faut(self):
        self._poser(60)
        self._marque(100)
        s3.setup_bucket(self.env)
        self.assertEqual(s3.applied_orphan_expiry_days(self.env), 115)

    # ===================================================== l'état livré
    def test_la_tache_est_livree_active_et_quotidienne(self):
        """Lu dans le XML du module : un environnement d'essai éteint toutes les tâches après
        chaque geste, un essai sur la base serait vert une seule fois."""
        chemin = os.path.join(
            get_module_path("bf_securetransfer"), "data",
            "secure_transfer_cron.xml")
        arbre = etree.parse(chemin)
        rec = arbre.xpath("//record[@id='cron_st_sync_lifecycle']")
        self.assertEqual(len(rec), 1)
        champs = {f.get("name"): f for f in rec[0].findall("field")}
        self.assertEqual(champs["active"].get("eval"), "True")
        self.assertEqual(champs["interval_type"].text, "days")
        self.assertEqual(champs["code"].text, "model._cron_sync_lifecycle()")
