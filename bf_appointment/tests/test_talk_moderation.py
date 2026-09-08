"""La salle Talk d'un rendez-vous, et qui la modère.

Défaut trouvé à l'heure même d'un rendez-vous : la salle avait été créée
par le robot de service et n'avait que lui pour participant. L'organisateur entrait par le lien public comme un invité —
aucun droit de modération, aucune notification, et la salle n'apparaissait
nulle part dans sa liste Talk. La création réussissait pourtant, et les
courriels partaient avec la bonne adresse : rien ne signalait quoi que ce
soit.

Ce que les tests ci-dessous verrouillent, dans l'ordre où ça compte :

* la salle fraîchement créée reçoit une PROMOTION en modération, pas juste un
  ajout de participant (l'ajout seul laisse l'hôte simple utilisateur) ;
* la correspondance `login_odoo=compte_nc` n'installe PAS un hôte comme
  modérateur des rendez-vous d'un autre — le cas d'un locataire à deux
  hôtes, qui reçoivent chacun leurs propres réservations ;
* un Nextcloud qui hoquette ne fait pas tomber le rendez-vous : la salle est
  rendue quand même.

Le premier test échoue sur 18.0.2.54.2 : l'ancien code n'appelle jamais
`/moderators`. C'est ce qui le rend utile.
"""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


class FausseReponse:
    """Le minimum de `requests.Response` dont le code appelé se sert."""

    def __init__(self, charge=None, status_code=200):
        self._charge = charge if charge is not None else {"ocs": {"data": {}}}
        self.status_code = status_code
        self.text = "%s" % (charge or "")

    def json(self):
        return self._charge

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("HTTP %s" % self.status_code)


@tagged("bf_appointment", "bf_appointment_video", "bf_talk_moderation")
class TestTalkModeration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_appointment.nc_talk_base_url", "https://nc.test.invalid")
        ICP.set_param("bf_appointment.nc_talk_user", "robot-service")
        ICP.set_param("bf_appointment.nc_talk_password_encrypted", "peu-importe")
        cls.hote = cls.env["res.users"].create({
            "name": "Hôte du rendez-vous",
            "login": "hote@test.invalid",
        })
        cls.autre_hote = cls.env["res.users"].create({
            "name": "Autre hôte",
            "login": "autre@test.invalid",
        })

    def _booking(self, hote=None):
        """Une réservation nue : ces tests n'appellent que la couche Talk."""
        return self.env["resource.booking"].new({
            "user_id": (hote or self.hote).id,
        })

    def _appels(self, reponse_participants=None, reponse_moderators=None):
        """Joue `_generate_nc_talk_url` avec un Nextcloud simulé.

        Rend (url_rendue, liste des (méthode, url, données)).
        """
        traces = []

        def faux_post(url, **kw):
            traces.append(("POST", url, kw.get("data")))
            if url.endswith("/moderators"):
                return reponse_moderators or FausseReponse()
            if url.endswith("/participants"):
                return reponse_participants or FausseReponse()
            return FausseReponse({"ocs": {"data": {"token": "salle123"}}})

        def faux_get(url, **kw):
            traces.append(("GET", url, kw.get("params")))
            return FausseReponse({"ocs": {"data": [
                {"actorType": "users", "actorId": "robot-service", "attendeeId": 1},
                {"actorType": "guests", "actorId": "xyz", "attendeeId": 2},
                {"actorType": "users", "actorId": "Compte NC", "attendeeId": 42},
            ]}})

        booking = self._booking()
        with patch.object(
            type(booking), "_decrypt_nc_talk_password", return_value="mdp"
        ), patch("requests.post", side_effect=faux_post), \
                patch("requests.get", side_effect=faux_get):
            url = booking._generate_nc_talk_url()
        return url, traces

    # -- comportement -------------------------------------------------------

    def test_la_salle_creee_recoit_un_moderateur_humain(self):
        """Le test qui échoue sur 2.54.2 : l'ancien code s'arrêtait à la salle."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "Compte NC")
        url, traces = self._appels()

        self.assertEqual(url, "https://nc.test.invalid/index.php/call/salle123")
        ajouts = [t for t in traces if t[0] == "POST" and t[1].endswith("/participants")]
        promotions = [t for t in traces if t[1].endswith("/moderators")]
        self.assertEqual(len(ajouts), 1, "le compte doit être ajouté à la salle")
        self.assertEqual(
            ajouts[0][2], {"newParticipant": "Compte NC", "source": "users"})
        self.assertEqual(
            len(promotions), 1,
            "ajouter ne suffit pas : sans promotion l'hôte reste simple utilisateur")
        self.assertEqual(
            promotions[0][2], {"attendeeId": 42},
            "la promotion prend l'attendeeId, pas le nom d'utilisateur")

    def test_sans_configuration_rien_ne_change(self):
        """Un locataire qui n'a pas posé le paramètre garde l'ancien comportement."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "")
        url, traces = self._appels()
        self.assertEqual(url, "https://nc.test.invalid/index.php/call/salle123")
        self.assertEqual(
            [t for t in traces if "/participants" in t[1] or "/moderators" in t[1]], [],
            "aucun appel de modération ne doit partir sans configuration")

    def test_un_nextcloud_qui_refuse_ne_fait_pas_tomber_le_rendez_vous(self):
        """Une salle sans modérateur reste une salle joignable."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "Compte NC")
        url, traces = self._appels(
            reponse_participants=FausseReponse({"error": "new-participant"}, 404))
        self.assertEqual(
            url, "https://nc.test.invalid/index.php/call/salle123",
            "le refus de Nextcloud ne doit pas coûter la salle")
        self.assertEqual([t for t in traces if t[1].endswith("/moderators")], [])

    def test_une_promotion_refusee_ne_fait_pas_tomber_le_rendez_vous(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "Compte NC")
        url, _traces = self._appels(
            reponse_moderators=FausseReponse({"error": "moderator"}, 400))
        self.assertEqual(url, "https://nc.test.invalid/index.php/call/salle123")

    # -- la correspondance par organisateur ---------------------------------

    def test_entree_nue_vaut_pour_tous_les_organisateurs(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "Jean Tremblay")
        self.assertEqual(
            self._booking()._nc_talk_moderator_logins(), ["Jean Tremblay"],
            "un identifiant Nextcloud peut contenir une espace")
        self.assertEqual(
            self._booking(self.autre_hote)._nc_talk_moderator_logins(),
            ["Jean Tremblay"])

    def test_entree_appariee_ne_vaut_que_pour_son_organisateur(self):
        """Deux hôtes, deux comptes Nextcloud, aucun mélange."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators",
            "hote@test.invalid=Compte A, autre@test.invalid=Compte B")
        self.assertEqual(self._booking()._nc_talk_moderator_logins(), ["Compte A"])
        self.assertEqual(
            self._booking(self.autre_hote)._nc_talk_moderator_logins(), ["Compte B"])

    def test_les_deux_formes_se_cumulent_et_dedoublonnent(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators",
            "  Support , hote@test.invalid=Compte A ,\nSupport, "
            "autre@test.invalid=Compte B ,, ")
        self.assertEqual(
            self._booking()._nc_talk_moderator_logins(), ["Support", "Compte A"])

    def test_la_casse_du_login_odoo_ne_compte_pas(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators", "HOTE@Test.Invalid=Compte A")
        self.assertEqual(self._booking()._nc_talk_moderator_logins(), ["Compte A"])
