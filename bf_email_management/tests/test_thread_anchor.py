"""Le fil ne doit jamais s'ancrer sur la boîte plutôt que sur le dossier.

Le défaut vécu le 2026-08-31 tient en dix-sept secondes : le cron
IMAP crée la rangée à 19:18, l'usager répond à 19:19, la passerelle ne classe
le courriel qu'à 19:20. À 19:19 la rangée n'a pas de ``res_model``, donc le
composeur retombait sur ``("bf.email", id)`` et l'envoi partait avec un
Message-ID ``openerp-<id>-bf.email``. Le correspondant répond à cet en-tête, la
passerelle le lit, et la conversation entière quitte le dossier — pour de bon,
puisque chaque réponse suivante cite le même en-tête.

Deux défenses, éprouvées séparément :

* **avant l'envoi** — ``_composer_target`` retrouve le dossier du fil par les
  en-têtes plutôt que de se rabattre sur la boîte ;
* **à la réception** — une route qui vise une rangée ``bf.email`` est réécrite
  vers le dossier où cette rangée est classée, pour les fils déjà partis.

Un contrôle qui rendrait « vert » sans rien mesurer ne vaut rien : chaque cas
positif est doublé du cas où la résolution NE DOIT PAS avoir lieu (repli
préservé, lien explicite intact, route étrangère laissée telle quelle).
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class ThreadAnchorCase(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner.write({"groups_id": [
            (4, cls.env.ref("project.group_project_user").id),
        ]})
        cls.project = cls.env["project.project"].create({"name": "Dossier client"})
        cls.task = cls.env["project.task"].create({
            "name": "Réinitialisation du mot de passe",
            "project_id": cls.project.id,
        })
        cls.other_task = cls.env["project.task"].create({
            "name": "Autre dossier",
            "project_id": cls.project.id,
        })

    # -- fabriques ----------------------------------------------------
    def _message_on(self, record, message_id, subject="Re: mot de passe"):
        return self.env["mail.message"].create({
            "model": record._name,
            "res_id": record.id,
            "message_type": "comment",
            "subject": subject,
            "body": "<p>corps</p>",
            "message_id": message_id,
        })

    def _row(self, **overrides):
        vals = {
            "subject": "RE: mot de passe",
            "email_from": "client@acme.test",
            "email_to": "owner@test.invalid",
            "direction": "in",
            "status": "new",
            "source": "imap",
            "account_id": self.account.id,
            "user_id": self.owner.id,
            "imap_in_inbox": True,
            "imap_folder": "INBOX",
            "date": "2026-08-31 19:18:01",
            "message_id_header": "<arrivee@acme.test>",
        }
        vals.update(overrides)
        return self.env["bf.email"].with_user(self.owner).create(vals)


# ----------------------------------------------------------------------
# Avant l'envoi : la cible du composeur
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestComposerTarget(ThreadAnchorCase):

    def test_in_reply_to_ramene_au_dossier(self):
        """Le cas vécu : rangée pas encore classée, ancêtre dans une tâche."""
        self._message_on(self.task, "<ancetre@test.invalid>")
        row = self._row(in_reply_to="<ancetre@test.invalid>")
        self.assertFalse(row.res_model, "la rangée doit être non classée")
        self.assertEqual(
            row._composer_target(), ("project.task", self.task.id),
        )

    def test_sans_ancetre_le_repli_reste_la_boite(self):
        """Le contrôle discrimine : sans piste, on retombe bien sur la boîte."""
        row = self._row(in_reply_to=False, thread_root_id=False)
        self.assertEqual(row._composer_target(), ("bf.email", row.id))

    def test_ancetre_introuvable_ne_fabrique_pas_de_cible(self):
        """Un In-Reply-To qui ne correspond à rien ne doit rien inventer."""
        row = self._row(in_reply_to="<jamais-vu@test.invalid>")
        self.assertEqual(row._composer_target(), ("bf.email", row.id))

    def test_lien_explicite_intact(self):
        """Le classement est une décision : on ne la re-devine pas."""
        self._message_on(self.other_task, "<ancetre@test.invalid>")
        row = self._row(
            in_reply_to="<ancetre@test.invalid>",
            res_model="project.task", res_id=self.task.id,
        )
        self.assertEqual(
            row._composer_target(), ("project.task", self.task.id),
        )

    def test_ancrage_sur_une_rangee_remonte_au_dossier(self):
        """Une réponse ancrée sur une réponse : on remonte la chaîne."""
        anchored = self._row(
            message_id_header="<ancre@acme.test>",
            res_model="project.task", res_id=self.task.id,
        )
        row = self._row(
            message_id_header="<suite@acme.test>",
            res_model="bf.email", res_id=anchored.id,
        )
        self.assertEqual(
            row._composer_target(), ("project.task", self.task.id),
        )

    def test_soeur_du_meme_fil_deja_classee(self):
        """Sans en-tête exploitable, la sœur classée du même fil tranche."""
        self._row(
            message_id_header="<soeur@acme.test>",
            thread_root_id="<racine@acme.test>",
            res_model="project.task", res_id=self.task.id,
            date="2026-08-30 12:00:00",
        )
        row = self._row(
            in_reply_to=False, thread_root_id="<racine@acme.test>",
        )
        self.assertEqual(
            row._composer_target(), ("project.task", self.task.id),
        )

    def test_references_lue_a_rebours(self):
        """Le plus PROCHE ancêtre gagne, pas le premier de la chaîne."""
        self._message_on(self.other_task, "<vieux@test.invalid>")
        self._message_on(self.task, "<recent@test.invalid>")
        row = self._row(
            in_reply_to=False,
            raw_headers=(
                "Subject: RE: mot de passe\n"
                "References: <vieux@test.invalid>\n"
                " <recent@test.invalid>\n"
            ),
        )
        self.assertEqual(
            row._composer_target(), ("project.task", self.task.id),
        )

    def test_cible_morte_retombe_sur_la_boite(self):
        """Une piste vers une fiche disparue ne doit pas casser le composeur.

        Supprimer la tâche ne suffirait pas à monter le cas : la cascade
        emporte ses messages, donc la piste n'existerait plus non plus. On
        pose donc un message qui désigne un identifiant jamais créé — ce que
        laisse derrière lui un enregistrement supprimé hors cascade.
        """
        orphan = self._message_on(self.other_task, "<ancetre-mort@test.invalid>")
        # Le déplacement se fait en SQL : `create` comme `write` sur
        # mail.message résolvent la fiche visée (calcul du `reply_to`), donc
        # l'ORM refuse tout net de désigner un identifiant mort. Le cas
        # existe pourtant en base, et c'est `.exists()` qui le garde.
        self.env.cr.execute(
            "UPDATE mail_message SET res_id = %s WHERE id = %s",
            (2 ** 31 - 1, orphan.id),
        )
        orphan.invalidate_recordset(["res_id"])
        self.assertTrue(orphan.exists())
        row = self._row(in_reply_to="<ancetre-mort@test.invalid>")
        self.assertEqual(row._composer_target(), ("bf.email", row.id))

    def test_boite_jamais_rendue_comme_dossier(self):
        """Un ancêtre qui vit LUI AUSSI sur la boîte ne classe rien."""
        host = self._row(message_id_header="<hote@acme.test>")
        self._message_on(host, "<ancetre@test.invalid>")
        row = self._row(in_reply_to="<ancetre@test.invalid>")
        self.assertEqual(row._composer_target(), ("bf.email", row.id))


# ----------------------------------------------------------------------
# À la réception : la garde de routage
# ----------------------------------------------------------------------
@tagged("post_install", "-at_install")
class TestRouteRedirect(ThreadAnchorCase):

    def _redirect(self, route):
        return self.env["mail.thread"]._bf_email_redirect_route(route)

    def test_route_vers_la_boite_redirigee(self):
        row = self._row(res_model="project.task", res_id=self.task.id)
        route = ("bf.email", row.id, None, self.owner.id, False)
        self.assertEqual(
            self._redirect(route),
            ("project.task", self.task.id, None, self.owner.id, False),
        )

    def test_route_etrangere_intacte(self):
        """Le contrôle discrimine : rien d'autre n'est touché."""
        route = ("project.task", self.task.id, None, self.owner.id, False)
        self.assertEqual(self._redirect(route), route)

    def test_rangee_non_classee_laisse_la_route(self):
        """Sans dossier connu, mieux vaut la boîte que le hasard."""
        row = self._row()
        route = ("bf.email", row.id, None, self.owner.id, False)
        self.assertEqual(self._redirect(route), route)

    def test_rangee_disparue_laisse_la_route(self):
        row = self._row()
        row_id = row.id
        row.unlink()
        route = ("bf.email", row_id, None, self.owner.id, False)
        self.assertEqual(self._redirect(route), route)

    def test_passerelle_de_bout_en_bout(self):
        """Le vrai chemin : un courriel qui répond à un envoi ancré sur la boîte."""
        row = self._row(res_model="project.task", res_id=self.task.id)
        sent = self._message_on(row, "<envoi-ancre@test.invalid>")
        self.assertEqual(sent.model, "bf.email")
        avant = self.env["mail.message"].search_count([
            ("model", "=", "project.task"), ("res_id", "=", self.task.id),
        ])
        raw = (
            "Message-ID: <reponse-client@acme.test>\n"
            "In-Reply-To: <envoi-ancre@test.invalid>\n"
            "References: <envoi-ancre@test.invalid>\n"
            "From: Client Acme <client@acme.test>\n"
            "To: owner@test.invalid\n"
            "Subject: RE: mot de passe\n"
            "\n"
            "Merci, c'est réglé.\n"
        ).encode()
        self.env["mail.thread"].message_process(None, raw)
        arrivee = self.env["mail.message"].search([
            ("message_id", "=", "<reponse-client@acme.test>"),
        ])
        self.assertEqual(len(arrivee), 1)
        self.assertEqual(arrivee.model, "project.task")
        self.assertEqual(arrivee.res_id, self.task.id)
        self.assertEqual(
            self.env["mail.message"].search_count([
                ("model", "=", "project.task"), ("res_id", "=", self.task.id),
            ]),
            avant + 1,
        )
