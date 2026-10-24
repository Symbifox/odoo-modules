"""Signaler une réponse de Gen, exigé par Google Play.

Ce qui doit rester vrai : on ne signale que SES réponses de Gen, une fois ;
le modèle est fermé à tout usager ordinaire, portail compris ; la route
mobile refuse ce qui n'a pas la bonne forme ; et `/messages` dit ce qui est
déjà signalé.
"""

import json
from unittest.mock import patch

import psycopg2.errors

from odoo import fields
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from ..models.claude_chat_flag import (
    CONSERVATION_PRECISIONS_MOIS, PLAFOND_PAR_HEURE, nettoyer_precisions)


def _donnees(env, login):
    user = new_test_user(env, login=login, groups="base.group_user")
    session = env["claude.chat.session"].create({"name": "Conversation", "user_id": user.id})
    Message = env["claude.chat.message"]
    question = Message.create({"session_id": session.id, "role": "user", "content": "Une question"})
    reponse = Message.create({"session_id": session.id, "role": "assistant",
                              "content": "Une réponse de Gen"})
    return user, session, question, reponse


def _aveugle(Flag):
    """Le second de deux envois simultanés : son instantané (REPEATABLE READ)
    ne voit pas le premier, ses recherches de signalement rendent vide et il
    tente l'INSERT, qui bute sur la contrainte unique."""
    vraie = type(Flag).search

    def search(modele, domain, *args, **kwargs):
        if modele._name == "claude.chat.flag" and any(
                isinstance(t, (list, tuple)) and t[0] == "message_id" for t in domain):
            return modele.browse()
        return vraie(modele, domain, *args, **kwargs)

    return patch.object(type(Flag), "search", search)


def _supprimee_avant_l_insert(Flag):
    """La réponse supprimée entre le contrôle de la route et l'INSERT : la
    clé étrangère refuse le signalement."""
    vraie = type(Flag).create

    def create(modele, vals):
        modele.env.cr.execute("DELETE FROM claude_chat_message WHERE id = %s",
                              [vals["message_id"]])
        return vraie(modele, vals)

    return patch.object(type(Flag), "create", create)


@tagged("post_install", "-at_install")
class TestSignalementModele(TransactionCase):

    def setUp(self):
        super().setUp()
        self.user, self.session, self.question, self.reponse = _donnees(self.env, "banc_signal")
        self.Flag = self.env["claude.chat.flag"]

    def test_un_seul_signalement_par_reponse_et_par_personne(self):
        premier, deja = self.Flag._signaler(self.reponse, self.user, "offensive", "  grossier  ")
        self.assertFalse(deja)
        self.assertEqual(premier.comment, "grossier")
        self.assertEqual(premier.state, "new")
        second, deja = self.Flag._signaler(self.reponse, self.user, "other", "autre chose")
        self.assertTrue(deja)
        self.assertEqual(second, premier)
        self.assertEqual(premier.reason, "offensive", "le premier reste tel quel")
        self.assertEqual(self.Flag.sudo().search_count([("message_id", "=", self.reponse.id)]), 1)

    def test_deux_envois_simultanes_le_second_dit_deja_sans_doublon(self):
        premier, _deja = self.Flag._signaler(self.reponse, self.user, "offensive", "précision confidentielle")
        # L'INSERT refusé ne doit rien écrire au journal : « bad query » y
        # mettrait les précisions de la personne.
        with _aveugle(self.Flag), self.assertNoLogs("odoo.sql_db", level="ERROR"):
            second, deja = self.Flag._signaler(self.reponse, self.user, "offensive",
                                               "précision confidentielle")
        self.assertTrue(deja)
        self.assertFalse(second, "le premier n'est pas lisible depuis le second : rien à rendre")
        self.assertTrue(premier.exists())
        self.assertEqual(self.Flag.sudo().search_count([("message_id", "=", self.reponse.id)]), 1)

    def test_une_reponse_disparue_n_est_pas_un_deja(self):
        """Seule la contrainte unique veut dire « déjà » : une clé étrangère
        refusée remonte (la route la rend en 404), sans rien au journal."""
        self.env.cr.execute("DELETE FROM claude_chat_message WHERE id = %s", [self.reponse.id])
        with self.assertRaises(psycopg2.errors.ForeignKeyViolation), \
                self.assertNoLogs("odoo.sql_db", level="ERROR"):
            self.Flag._signaler(self.reponse, self.user, "offensive", "précision confidentielle")
        self.env.cr.execute("SELECT count(*) FROM claude_chat_flag WHERE user_id = %s", [self.user.id])
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_nettoyer_precisions(self):
        self.assertEqual(nettoyer_precisions("a\x00b\ud800c\udfffd 😀 é"), "abcd 😀 é")
        self.assertEqual(nettoyer_precisions("rien à retirer"), "rien à retirer")

    def test_le_nom_affiche_dit_le_motif_et_la_date(self):
        flag, _deja = self.Flag._signaler(self.reponse, self.user, "dangerous")
        libelle = dict(self.Flag._fields["reason"]._description_selection(self.env))["dangerous"]
        self.assertNotEqual(flag.display_name, "dangerous")
        self.assertTrue(flag.display_name.startswith(libelle + " · "), flag.display_name)
        self.assertIn(str(fields.Date.context_today(flag).year), flag.display_name)
        if self.env["res.lang"]._lang_get("fr_CA"):
            self.assertTrue(flag.with_context(lang="fr_CA").display_name.startswith("Dangereuse"),
                            flag.with_context(lang="fr_CA").display_name)

    def test_des_precisions_vides_ne_sont_pas_gardees(self):
        flag, _deja = self.Flag._signaler(self.reponse, self.user, "false", "   ")
        self.assertFalse(flag.comment)

    def test_le_plafond_par_heure(self):
        Message = self.env["claude.chat.message"]
        for i in range(PLAFOND_PAR_HEURE):
            m = Message.create({"session_id": self.session.id, "role": "assistant", "content": str(i)})
            flag, _deja = self.Flag._signaler(m, self.user, "other")
            self.assertTrue(flag)
        flag, deja = self.Flag._signaler(self.reponse, self.user, "other")
        self.assertFalse(flag)
        self.assertFalse(deja)

    def test_la_conversation_supprimee_le_signalement_reste_sans_le_texte(self):
        flag, _deja = self.Flag._signaler(self.reponse, self.user, "dangerous", "")
        self.session.unlink()
        self.assertTrue(flag.exists())
        self.assertFalse(flag.message_id)
        self.assertFalse(flag.answer)
        self.assertEqual(flag.reason, "dangerous")

    def test_un_usager_ordinaire_n_a_aucun_acces(self):
        self.Flag._signaler(self.reponse, self.user, "offensive")
        for op in ("read", "write", "create", "unlink"):
            with self.assertRaises(AccessError, msg=op):
                self.Flag.with_user(self.user).check_access(op)

    def test_un_portail_ne_peut_ni_lire_ni_changer_l_etat(self):
        flag, _deja = self.Flag._signaler(self.reponse, self.user, "offensive")
        portail = new_test_user(self.env, login="banc_signal_portail", groups="base.group_portal")
        with self.assertRaises(AccessError):
            flag.with_user(portail).read(["reason"])
        with self.assertRaises(AccessError):
            flag.with_user(portail).action_mark_reviewed()
        self.assertEqual(flag.state, "new")

    def test_signaler_n_est_pas_appelable_par_rpc(self):
        """`_signaler` écrit en sudo : jamais une porte RPC, portail compris."""
        with self.assertRaises(AccessError):
            get_public_method(self.Flag.with_user(self.user), "_signaler")

    def test_un_administrateur_lit_et_traite(self):
        flag, _deja = self.Flag._signaler(self.reponse, self.user, "offensive", "x")
        admin = new_test_user(self.env, login="banc_signal_admin",
                              groups="base.group_user,base.group_system")
        vu = flag.with_user(admin)
        self.assertEqual(vu.answer, "Une réponse de Gen")
        vu.action_mark_reviewed()
        self.assertEqual(flag.state, "reviewed")
        vu.action_mark_new()
        self.assertEqual(flag.state, "new")
        self.assertTrue(self.env.ref("bf_claude_chat.action_claude_flag"))
        self.assertIn(self.env.ref("base.group_system"),
                      self.env.ref("bf_claude_chat.menu_claude_flag_admin").groups_id)


@tagged("post_install", "-at_install")
class TestSignalementRoutes(HttpCase):

    def setUp(self):
        super().setUp()
        Device = next((self.env[m] for m in ("bf.email.mobile.device", "sms.archive.mobile.device")
                       if m in self.env), None)
        if Device is None:
            self.skipTest("aucun modèle d'appareil mobile sur cette base")
        self.user, self.session, self.question, self.reponse = _donnees(self.env, "banc_signal_route")
        self.autre, _s, _q, self.sienne = _donnees(self.env, "banc_signal_autre")
        self.jeton = Device._issue(self.user.id, name="Essai signalement").device_token
        Message = self.env["claude.chat.message"]
        self.interne = Message.create({"session_id": self.session.id, "role": "assistant",
                                       "content": "consigne", "internal": True})
        self.Flag = self.env["claude.chat.flag"].sudo()

    def _appel(self, route, body=None, jeton=True):
        entetes = {"Authorization": "Bearer %s" % self.jeton} if jeton else {}
        url = "/bf_claude_chat/mobile/v1" + route
        if body is None:
            return self.url_open(url, headers=entetes, timeout=30)
        entetes["Content-Type"] = "application/json"
        return self.url_open(url, data=json.dumps(body), headers=entetes, timeout=30)

    def _signaler(self, message_id, reason="offensive", **extra):
        return self._appel("/report", dict(message_id=message_id, reason=reason, **extra))

    def test_api_10(self):
        self.assertGreaterEqual(self._appel("/ping").json()["api"], 10)

    def test_signaler_sa_reponse_puis_le_rejeu_ne_double_pas(self):
        r = self._signaler(self.reponse.id, "false", comment="Le chiffre est inventé")
        self.assertEqual(r.status_code, 200)
        corps = r.json()
        self.assertTrue(corps["ok"])
        self.assertFalse(corps["already"])
        flag = self.Flag.browse(corps["report_id"])
        self.assertEqual((flag.message_id, flag.user_id, flag.reason, flag.comment),
                         (self.reponse, self.user, "false", "Le chiffre est inventé"))
        r2 = self._signaler(self.reponse.id, "other")
        self.assertEqual(r2.json(), {"ok": True, "report_id": flag.id, "already": True})
        self.assertEqual(self.Flag.search_count([("message_id", "=", self.reponse.id)]), 1)

    def test_ce_qui_ne_se_signale_pas_rend_404_et_ne_cree_rien(self):
        for mid in (self.sienne.id, self.question.id, self.interne.id, 0, 99999999, -3,
                    2 ** 31, 10 ** 30, str(self.sienne.id)):
            self.assertEqual(self._signaler(mid).status_code, 404, mid)
        self.assertFalse(self.Flag.search([("user_id", "in", (self.user | self.autre).ids)]))

    def test_un_corps_mal_type_rend_400_jamais_500(self):
        mid = self.reponse.id
        for corps in (
            ["liste"], "texte", 3,
            {"message_id": "abc", "reason": "other"},
            {"message_id": [mid], "reason": "other"},
            {"message_id": {"id": mid}, "reason": "other"},
            {"message_id": True, "reason": "other"},
            {"message_id": float(mid), "reason": "other"},
            {"message_id": "9" * 5000, "reason": "other"},
            {"message_id": mid, "reason": ["offensive"]},
            {"message_id": mid, "reason": {"offensive": 1}},
            {"message_id": mid, "reason": 1},
        ):
            r = self._appel("/report", corps)
            self.assertEqual(r.status_code, 400, corps)
            self.assertIn("error", r.json(), corps)
        self.assertFalse(self.Flag.search([("message_id", "=", mid)]))
        # Un identifiant fait de chiffres, en texte, se lit.
        self.assertEqual(self._appel("/report", {"message_id": str(mid), "reason": "other"}).status_code, 200)

    def test_deux_envois_simultanes_le_second_rend_deja(self):
        premier = self._signaler(self.reponse.id, comment="précision confidentielle").json()
        self.assertFalse(premier["already"])
        with _aveugle(self.Flag):
            r = self._signaler(self.reponse.id, comment="précision confidentielle")
        self.assertEqual(r.status_code, 200, "ni 429 ni 500")
        self.assertEqual(r.json(), {"ok": True, "report_id": 0, "already": True})
        self.assertIs(type(r.json()["report_id"]), int, "0, pas false : l'app attend un entier")
        self.assertEqual(self.Flag.search_count([("message_id", "=", self.reponse.id)]), 1)

    def test_precisions_avec_nul_ou_demi_paire_se_signalent_nettoyees(self):
        """`\\u0000` ou une demi-paire isolée faisaient lever psycopg2 (500),
        et l'op en file était refusée pour toujours."""
        r = self._signaler(self.reponse.id, comment="a\u0000b\ud83dc\ude00d 😀")
        self.assertEqual(r.status_code, 200, r.text)
        flag = self.Flag.browse(r.json()["report_id"])
        self.assertEqual(flag.comment, "abcd 😀")
        # 1 000 caractères utiles plus des nuls : le nettoyage passe avant la borne.
        autre = self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "Autre"})
        self.assertEqual(self._signaler(autre.id, comment="x" * 1000 + "\u0000" * 5).status_code, 200)

    def test_une_reponse_supprimee_avant_l_ecriture_rend_404(self):
        with _supprimee_avant_l_insert(self.Flag), self.assertNoLogs("odoo.sql_db", level="ERROR"):
            r = self._signaler(self.reponse.id, comment="précision confidentielle")
        self.assertEqual((r.status_code, r.json()), (404, {"error": "réponse introuvable"}))
        self.assertFalse(self.Flag.search([("user_id", "=", self.user.id)]))

    def test_une_reponse_en_cours_rend_409(self):
        en_cours = self.env["claude.chat.message"].sudo().create({
            "session_id": self.session.id, "role": "assistant", "content": "…", "state": "pending"})
        self.assertEqual(self._signaler(en_cours.id).status_code, 409)

    def test_une_reponse_en_erreur_se_signale(self):
        erreur = self.env["claude.chat.message"].sudo().create({
            "session_id": self.session.id, "role": "assistant", "content": "texte partiel",
            "state": "error"})
        self.assertEqual(self._signaler(erreur.id).status_code, 200)

    def test_motif_et_precisions_mal_formes_rendent_400(self):
        self.assertEqual(self._signaler(self.reponse.id, "spam").status_code, 400)
        self.assertEqual(self._appel("/report", {"message_id": self.reponse.id}).status_code, 400)
        # La route refuse elle-même, dans sa forme : pas la contrainte du modèle.
        trop = self._signaler(self.reponse.id, comment="x" * 1001)
        self.assertEqual((trop.status_code, trop.json()), (400, {"error": "précisions trop longues"}))
        self.assertEqual(self._signaler(self.reponse.id, comment=["liste"]).status_code, 400)
        self.assertFalse(self.Flag.search([("message_id", "=", self.reponse.id)]))
        self.assertEqual(self._signaler(self.reponse.id, comment="x" * 1000).status_code, 200)

    def test_sans_jeton_401(self):
        self.assertEqual(self._appel("/report", {"message_id": self.reponse.id, "reason": "other"},
                                     jeton=False).status_code, 401)

    def test_au_dela_du_plafond_429(self):
        Message = self.env["claude.chat.message"]
        for i in range(PLAFOND_PAR_HEURE):
            m = Message.create({"session_id": self.session.id, "role": "assistant", "content": str(i)})
            self.Flag._signaler(m, self.user, "other")
        self.assertEqual(self._signaler(self.reponse.id).status_code, 429)

    def test_messages_dit_ce_qui_est_deja_signale(self):
        self._signaler(self.reponse.id)
        # Une seconde réponse de la MÊME conversation, signalée par un AUTRE
        # (`_signaler` en sudo : la route le lui interdirait). Ce n'est pas le
        # signalement de l'appelant : `reported` reste faux pour lui.
        seconde = self.env["claude.chat.message"].create({
            "session_id": self.session.id, "role": "assistant", "content": "Une autre réponse"})
        self.Flag._signaler(seconde, self.autre, "other")
        rangs = {m["id"]: m for m in self._appel(
            "/messages?session_id=%d" % self.session.id).json()["messages"]}
        self.assertTrue(rangs[self.reponse.id]["reported"])
        self.assertFalse(rangs[seconde.id]["reported"])
        self.assertFalse(rangs[self.question.id]["reported"])


@tagged("post_install", "-at_install")
class TestSignalementConservation(TransactionCase):
    """Les précisions ne vivent que CONSERVATION_PRECISIONS_MOIS mois."""

    def setUp(self):
        super().setUp()
        self.user, self.session, _q, self.reponse = _donnees(self.env, "banc_signal_purge")
        Message = self.env["claude.chat.message"]
        self.autre = Message.create({"session_id": self.session.id, "role": "assistant",
                                     "content": "Une autre réponse"})
        self.Flag = self.env["claude.chat.flag"]
        self.vieux, _d = self.Flag._signaler(self.reponse, self.user, "dangerous", "vieux texte")
        self.recent, _d = self.Flag._signaler(self.autre, self.user, "false", "texte récent")
        self.vieux.sudo().action_mark_reviewed()
        maintenant = fields.Datetime.now()
        self._dater(self.vieux, fields.Datetime.subtract(
            maintenant, months=CONSERVATION_PRECISIONS_MOIS, days=1))
        self._dater(self.recent, fields.Datetime.subtract(
            maintenant, months=CONSERVATION_PRECISIONS_MOIS, days=-1))

    def _dater(self, flag, quand):
        self.env.cr.execute("UPDATE claude_chat_flag SET create_date = %s WHERE id = %s",
                            (quand, flag.id))
        flag.invalidate_recordset()

    def test_douze_mois(self):
        self.assertEqual(CONSERVATION_PRECISIONS_MOIS, 12)

    def test_les_vieilles_precisions_sont_videes_le_reste_demeure(self):
        date_avant = self.vieux.create_date
        self.assertEqual(self.Flag._cron_purger_precisions(), 1)
        self.vieux.invalidate_recordset()
        self.recent.invalidate_recordset()
        self.assertFalse(self.vieux.comment)
        self.assertEqual((self.vieux.reason, self.vieux.state, self.vieux.create_date,
                          self.vieux.user_id, self.vieux.message_id),
                         ("dangerous", "reviewed", date_avant, self.user, self.reponse))
        self.assertEqual(self.recent.comment, "texte récent")
        self.assertEqual((self.recent.reason, self.recent.state), ("false", "new"))
        # Un second passage ne touche plus rien.
        self.assertEqual(self.Flag._cron_purger_precisions(), 0)

    def test_le_cron_existe_et_tourne_chaque_jour(self):
        cron = self.env.ref("bf_claude_chat.ir_cron_purge_flag_details")
        # `active` ne se vérifie pas ici : un environnement d'essai peut éteindre les crons.
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "days"))
        self.assertEqual(cron.model_id.model, "claude.chat.flag")
        self.assertIn("_cron_purger_precisions()", cron.code)
        cron.method_direct_trigger()
        self.vieux.invalidate_recordset()
        self.assertFalse(self.vieux.comment)

    def test_la_purge_n_est_pas_une_porte_rpc(self):
        with self.assertRaises(AccessError):
            get_public_method(self.Flag.with_user(self.user), "_cron_purger_precisions")
