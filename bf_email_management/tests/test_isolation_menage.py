"""Isolation par personne des courriels.

A et B : internes, non administrateurs (ni `base.group_system`, ni
`group_email_admin`). Les règles de lecture tiennent déjà (voir
`test_isolation_boites.py`). Ce qui est éprouvé ici, ce sont les chemins qui
les contournent :

1. Odoo ne rejoue PAS les règles d'enregistrement après un `write`. B pouvait
   donc donner SA règle de tri à A (qui détournait ensuite le courrier de A
   vers B), son message d'absence (la boîte de A répondait le texte de B), son
   compte IMAP ou ses sourdines.
2. Un calcul stocké se fait en superutilisateur : en posant sur SA ligne le
   `mail_message_id` d'un message qu'il ne peut pas lire, B en relisait le
   corps (`body_html`).
3. Le semis des identités d'expédition « vérifiées » prenait pour preuve le
   login d'un compte IMAP jamais connecté, ou l'adresse de sa fiche, que chacun
   modifie : B s'attribuait l'adresse de A et écrivait en son nom.
4. Les accusés de rappel du calendrier n'avaient aucune règle : B lisait le
   nom et l'heure des rencontres de A.
"""
import json

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


def _refuse(case, fn):
    try:
        with case.env.cr.savepoint():
            fn()
    except (AccessError, UserError, ValidationError):
        return True
    return False


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageCourriel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="menage_mel_a", groups="base.group_user",
                              email="a.menage@maison.invalid", name="Personne A")
        cls.b = new_test_user(cls.env, login="menage_mel_b", groups="base.group_user",
                              email="b.menage@maison.invalid", name="Personne B")
        for u in (cls.a, cls.b):
            assert not u.has_group("base.group_system")
            assert not u.has_group("bf_email_management.group_email_admin")
        cls.compte_a = cls.env["bf.email.account"].create({
            "name": "Boîte de A (fictive)", "user_id": cls.a.id, "host": "imap.maison.invalid",
            "port": 993, "login": "a.menage@maison.invalid", "password": "x", "state": "connected"})

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _courriel_de_a(self, subject="Résultat fictif"):
        return self.env["bf.email"].create({
            "subject": subject, "email_from": "clinique@exemple.invalid",
            "email_to": "a.menage@maison.invalid", "direction": "in", "status": "new",
            "user_id": self.a.id, "account_id": self.compte_a.id, "date": "2026-09-01 12:00:00",
            "message_id_header": f"<menage-{subject}@exemple.invalid>"})

    # ------------------------------------------------------------------
    # 1. Rien ne change de main par un write
    # ------------------------------------------------------------------
    def test_b_ne_donne_pas_sa_regle_de_tri_a_a(self):
        regle = self._en(self.b, "bf.email.rule").create({
            "name": "Détourner (fictif)", "scope": "user", "route_user_id": self.b.id,
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                      "operator": "contains", "value": "fictif"})]})
        refuse = _refuse(self, lambda: self._en(self.b, "bf.email.rule").browse(regle.id).write(
            {"user_id": self.a.id}))
        self.env.invalidate_all()
        self.assertEqual(regle.sudo().user_id, self.b, "B a donné sa règle de tri à A")
        self.assertTrue(refuse)
        # L'effet : le prochain courriel de A reste chez A.
        mel = self._courriel_de_a()
        self.env.invalidate_all()
        self.assertEqual(mel.user_id, self.a, "le courriel de A est parti dans la boîte de B")

    def test_b_ne_donne_pas_ses_fiches_personnelles_a_a(self):
        cas = {
            "bf.email.absence": {"name": "Absent (fictif)"},
            "bf.email.thread.mute": {"thread_root_id": "<racine-fictive@exemple.invalid>"},
            "bf.email.account": {"name": "Boîte piège", "host": "imap.piege.invalid", "port": 993,
                                 "login": "piege@piege.invalid", "password": "x"},
            "bf.recipient.group": {"name": "Groupe fictif"},
        }
        for model, vals in cas.items():
            with self.subTest(model=model):
                rec = self._en(self.b, model).create(dict(vals, user_id=self.b.id))
                refuse = _refuse(self, lambda m=model, r=rec: self._en(self.b, m).browse(r.id).write(
                    {"user_id": self.a.id}))
                self.env.invalidate_all()
                self.assertEqual(rec.sudo().user_id, self.b, f"{model} : B l'a donné à A")
                self.assertTrue(refuse)

    def test_a_garde_ses_gestes(self):
        """Contre-épreuve : poser son propre user_id, ou « Confier à » un
        courriel (voulu : c'est un geste de délégation), passe toujours."""
        regle = self._en(self.a, "bf.email.rule").create({"name": "Mienne", "scope": "user"})
        self._en(self.a, "bf.email.rule").browse(regle.id).write({"user_id": self.a.id, "name": "Encore mienne"})
        mel = self._courriel_de_a("Délégué fictif")
        self._en(self.a, "bf.email").browse(mel.id).write({"user_id": self.b.id})
        self.assertEqual(mel.sudo().user_id, self.b)

    # ------------------------------------------------------------------
    # 3. Identités d'expédition
    # ------------------------------------------------------------------
    def test_b_ne_se_fait_pas_verifier_l_adresse_de_a(self):
        # B déclare un compte au login de A, sans aucune connexion réelle.
        self._en(self.b, "bf.email.account").create({
            "name": "Faux compte", "host": "imap.piege.invalid", "port": 993,
            "login": "a.menage@maison.invalid", "password": "x"})
        # … et pose l'adresse de A sur sa propre fiche.
        self._en(self.b, "res.users").browse(self.b.id).write({"email": "a.menage@maison.invalid"})
        self.env["bf.email.identity"]._sync_from_accounts(self.b)
        self.env.invalidate_all()
        usables = self.env["bf.email.identity"]._usable_for(self.b)
        self.assertNotIn("a.menage@maison.invalid", usables.mapped("email_normalized"),
                         "B a obtenu une identité VÉRIFIÉE à l'adresse de A")

    def test_semis_de_sa_propre_adresse_reste_verifie(self):
        """Contre-épreuve : l'adresse qui n'est qu'à soi naît vérifiée.

        La preuve est le LOGIN. Le
        courriel de la fiche (ici différent du login) ne vérifie plus rien."""
        self.env["bf.email.identity"]._sync_from_accounts(self.a)
        self.assertNotIn("a.menage@maison.invalid",
                         self.env["bf.email.identity"]._usable_for(self.a).mapped("email_normalized"))
        c = new_test_user(self.env, login="c.menage@maison.invalid", groups="base.group_user",
                          email="c.menage@maison.invalid")
        self.env["bf.email.identity"]._sync_from_accounts(c)
        self.assertIn("c.menage@maison.invalid",
                      self.env["bf.email.identity"]._usable_for(c).mapped("email_normalized"))

    # ------------------------------------------------------------------
    # 4. Accusés de rappel du calendrier
    # ------------------------------------------------------------------
    def test_accuses_de_rappel_de_a_fermes_a_b(self):
        ack = self.env["bf.calendar.reminder.ack"].create({
            "partner_id": self.a.partner_id.id, "reminder_key": "odoo:menage-1",
            "event_name": "Rendez-vous médical fictif", "occurrence_start": "2026-09-30 14:00:00"})
        self.env.invalidate_all()
        self.assertFalse(self._en(self.b, "bf.calendar.reminder.ack").search([("id", "=", ack.id)]),
                         "B lit le nom des rencontres de A")
        self.assertTrue(_refuse(self, lambda: self._en(self.b, "bf.calendar.reminder.ack").browse(ack.id).write(
            {"dismissed_at": "2026-09-30 13:00:00"})))
        self.assertTrue(_refuse(self, lambda: self._en(self.b, "bf.calendar.reminder.ack").create({
            "partner_id": self.a.partner_id.id, "reminder_key": "odoo:menage-2"})))
        self.assertEqual(self._en(self.a, "bf.calendar.reminder.ack").search([("id", "=", ack.id)]), ack,
                         "contre-épreuve : A lit le sien")


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageCourrielRpc(HttpCase):
    """Le chemin réel d'un tiers : /web/dataset/call_kw, avec la session de B."""

    def test_b_ne_lit_pas_un_message_par_un_mail_message_id_force(self):
        mdp_a, mdp_b = "menage_rpc_a_pw1", "menage_rpc_b_pw1"
        a = new_test_user(self.env, login="menage_rpc_a", password=mdp_a, groups="base.group_user")
        b = new_test_user(self.env, login="menage_rpc_b", password=mdp_b, groups="base.group_user")
        # Un message que B ne peut pas lire : une note sur la fiche courriel de A.
        ligne_a = self.env["bf.email"].create({
            "subject": "Fiche de A", "direction": "in", "status": "new", "user_id": a.id,
            "date": "2026-09-01 12:00:00", "message_id_header": "<rpc-a@exemple.invalid>"})
        secret = ligne_a.with_user(a).message_post(body="SECRET-FICTIF-DE-A", message_type="comment",
                                                   subtype_xmlid="mail.mt_note")
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(b).browse(secret.id).read(["body"])
        self.authenticate("menage_rpc_b", mdp_b)

        def call(method, args, kwargs=None):
            return self.url_open("/web/dataset/call_kw", data=json.dumps({
                "jsonrpc": "2.0", "method": "call", "params": {
                    "model": "bf.email", "method": method, "args": args, "kwargs": kwargs or {}}}),
                headers={"Content-Type": "application/json"}).json()

        rep = call("create", [{"subject": "Leurre", "direction": "in", "status": "new",
                               "date": "2026-09-01 12:00:00", "mail_message_id": secret.id}])
        lu = ""
        if "result" in rep:
            rid = rep["result"][0] if isinstance(rep["result"], list) else rep["result"]
            lu = json.dumps(call("read", [[rid], ["body_html", "body_text"]]))
        self.assertNotIn("SECRET-FICTIF-DE-A", lu, "B relit par sa propre ligne le corps d'un message de A")
        self.assertIn("error", rep, "la création pointant un message illisible n'a pas été refusée")
        # Même chose par un write sur une ligne à lui.
        ligne_b = self.env["bf.email"].create({
            "subject": "Ligne de B", "direction": "in", "status": "new", "user_id": b.id,
            "date": "2026-09-01 12:00:00", "message_id_header": "<rpc-b@exemple.invalid>"})
        rep = call("write", [[ligne_b.id], {"mail_message_id": secret.id}])
        self.assertIn("error", rep, "le write pointant un message illisible n'a pas été refusé")
        self.env.invalidate_all()
        self.assertNotIn("SECRET-FICTIF-DE-A", ligne_b.body_html or "")


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageCourrielRegles(TransactionCase):
    """Une règle d'enregistrement par modèle, et un essai qui tombe si on la
    retire (mutations ). Les fiches sont semées en sudo au nom de A,
    puis relues à froid par A et par B."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="menage_mel_ra", groups="base.group_user")
        cls.b = new_test_user(cls.env, login="menage_mel_rb", groups="base.group_user")

    def _voit(self, user, rec):
        self.env.invalidate_all()
        return bool(self.env[rec._name].with_user(user).search([("id", "=", rec.id)]))

    def _seul_a(self, rec):
        self.assertTrue(self._voit(self.a, rec), f"{rec._name} : A ne voit plus le sien")
        self.assertFalse(self._voit(self.b, rec), f"{rec._name} : B voit celui de A")

    def test_fiches_personnelles_fermees(self):
        absence = self.env["bf.email.absence"].create({"name": "Absence fictive", "user_id": self.a.id})
        recs = [
            absence,
            self.env["bf.email.absence.reply"].create({
                "absence_id": absence.id, "name": "Réponse fictive", "body_html": "<p>x</p>"}),
            self.env["bf.dnd.held"].create({"user_id": self.a.id, "kind": "mail"}),
            self.env["bf.email.auto.log"].create({
                "kind": "forward", "user_id": self.a.id, "recipient": "x@exemple.invalid", "state": "sent"}),
            self.env["bf.email.mobile.device"].create({"user_id": self.a.id, "name": "Téléphone fictif"}),
            self.env["bf.email.account"].create({
                "name": "Boîte fictive", "user_id": self.a.id, "host": "imap.x.invalid", "port": 993,
                "login": "ra@x.invalid", "password": "x"}),
        ]
        for rec in recs:
            with self.subTest(model=rec._name):
                self._seul_a(rec)

    def test_regles_d_organisation_lisibles_par_tous(self):
        """Voulu : une règle sans propriétaire gouverne toutes les boîtes, chacun
        doit pouvoir la lire avec ses conditions."""
        regle = self.env["bf.email.rule"].create({
            "name": "Règle d'organisation fictive", "scope": "company", "user_id": False,
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                      "operator": "contains", "value": "fictif"})]})
        for rec in (regle, regle.condition_ids):
            with self.subTest(model=rec._name):
                self.assertTrue(self._voit(self.b, rec), f"{rec._name} : la règle d'organisation est cachée")
