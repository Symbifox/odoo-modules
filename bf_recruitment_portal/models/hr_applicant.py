# Part of bf_recruitment_portal. Voir LICENSE.
"""Portaliser la candidature, et décider de ce que la personne voit.

Deux responsabilités, et une seule des deux est technique :

* rendre `hr.applicant` adressable par un lien signé (`portal.mixin`) ;
* trancher **ce qui est visible et à partir de quand**, puis n'exposer que ça
  sous forme de dictionnaires. Le contrôleur et les gabarits ne voient jamais
  autre chose.
"""

import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import hmac as odoo_hmac

# Ce que la personne évaluée peut voir de sa propre séance, une fois la
# décision rendue. Tout ce qui n'est pas ici ne sort pas.
_ETATS_LISIBLES = ("tenue",)


class HrApplicant(models.Model):
    _name = "hr.applicant"
    _inherit = ["hr.applicant", "portal.mixin"]

    def _compute_access_url(self):
        super()._compute_access_url()
        for applicant in self:
            applicant.access_url = "/my/candidature/%s" % applicant.id

    # ------------------------------------------------------------------
    # La porte : quand le cahier devient lisible
    # ------------------------------------------------------------------

    def _portal_decision_taken(self):
        """La décision est-elle rendue ?

        🔴 C'est le garde du module, et il n'est pas cosmétique. Avant la
        décision, montrer les appréciations empoisonnerait le processus (la
        personne se prépare contre ce qu'elle lit) et exposerait l'opinion de
        tiers avant que quiconque ait tranché.

        On lit trois signaux plutôt qu'un : `decision_date`, que le cahier
        d'entrevues pose lui-même à la clôture, le motif de refus, et l'embauche.
        Un seul suffit ; aucun ne peut être vrai « par accident ».
        """
        self.ensure_one()
        return bool(self.decision_date or self.refuse_reason_id or self.employee_id)

    def _portal_state(self):
        """L'état du dossier, en trois valeurs, pour l'affichage.

        🔴 **Cette méthode doit lire les MÊMES signaux que
        `_portal_decision_taken()`.** Elle n'en lisait que deux sur trois, et
        l'écart n'était pas cosmétique : une personne EMBAUCHÉE dont le dossier
        d'employé n'a pas encore été créé passait le garde de la décision, donc
        voyait ses séances et téléchargeait tout son cahier, pendant que la page
        lui annonçait « À l'étude ». Les deux fonctions se contredisaient, et
        celle qui commande l'accès était la plus généreuse des deux.

        ⚠️ `date_closed` est la date d'EMBAUCHE dans le coeur d'Odoo 18, pas une
        date de clôture. On l'a déjà payé une fois sur le calendrier de
        conservation de `bf_recruitment_privacy` ; c'est donc bien un signal d'embauche.
        """
        self.ensure_one()
        if self.refuse_reason_id:
            return "non_retenue"
        if self.employee_id or self.date_closed:
            return "retenue"
        if self._portal_decision_taken():
            # ⚠️ Une décision consignée sans refus ni embauche : le dossier est
            # clos sans avoir abouti (désistement, poste annulé). Le portail
            # n'avait pas d'état pour ça, et le test des huit combinaisons l'a
            # fait sortir. L'inventer ici vaut mieux que de faire mentir l'un
            # des deux libellés qui existaient.
            return "close"
        return "en_cours"


    # ------------------------------------------------------------------
    # Les deux interrupteurs du locataire
    # ------------------------------------------------------------------

    def _portal_book_enabled(self):
        """Le cahier est-il téléchargeable chez ce locataire ?

        ⚠️ Le décrocher ne retire PAS le droit d'accès de la personne : il
        retire le libre-service. La décision et le motif écrit restent servis,
        et la demande se fait alors autrement. C'est un réglage d'exploitation,
        pas une restriction de droit.
        """
        self.ensure_one()
        return self.company_id.recruitment_portal_book_enabled

    def _portal_otp_required(self):
        """Faut-il un code à usage unique en plus du lien signé ?

        ⚠️ **Ce que ce code protège, et ce qu'il ne protège pas.** Le lien
        arrive lui aussi par courriel : contre une boîte compromise, un code
        envoyé à la même boîte n'ajoute rien, et prétendre le contraire serait
        malhonnête. Il protège d'un lien qui a FUITÉ sans la boîte : transféré,
        laissé dans un historique de navigation, dans un journal de mandataire,
        ou lu par-dessus une épaule. C'est un vrai risque pour un dossier qui
        porte des appréciations écrites, et c'est pour ça que le réglage
        existe. Il est décoché par défaut : un candidat refusé qui n'arrive pas
        à lire son propre dossier est un coût réel, à mettre en face.
        """
        self.ensure_one()
        return self.company_id.recruitment_portal_otp_required

    # ------------------------------------------------------------------
    # Le code à usage unique, sur le patron de bf_securetransfer
    # ------------------------------------------------------------------

    @api.model
    def _portal_otp_hash(self, code):
        """Empreinte CLÉE d'un code à usage unique.

        🔴 Surtout pas un `sha256` nu : le code fait six chiffres et le préfixe
        serait une constante, pas un sel. Tout l'espace de 10^6 se précalcule en
        moins d'une seconde, et l'empreinte ne protégerait rien de qui lit une
        sauvegarde. `odoo.tools.hmac` clé le condensé sur le secret de la base.
        Leçon déjà payée par `bf_securetransfer`, on ne la repaie pas.
        """
        return odoo_hmac(self.env(su=True), "bf_recruitment_portal_otp", code or "")

    def _portal_otp_indice(self):
        """« l...e@exemple.ca » : de quoi se reconnaître, pas de quoi apprendre.

        ⚠️ Publier l'adresse entière sur la page du code la donnerait à qui
        détient un lien qui a fuité, c'est-à-dire exactement la personne contre
        qui le code existe.
        """
        self.ensure_one()
        adresse = self.email_from or self.partner_id.email or ""
        if "@" not in adresse:
            return ""
        local, domaine = adresse.rsplit("@", 1)
        if len(local) <= 2:
            masque = local[:1] + "..."
        else:
            masque = "%s...%s" % (local[0], local[-1])
        return "%s@%s" % (masque, domaine)

    def _portal_otp_send(self):
        """Poser un code neuf et l'envoyer à l'adresse DU DOSSIER.

        Rend `(empreinte, expiration)` pour que le contrôleur les range dans la
        session. ⚠️ Le code lui-même ne sort jamais d'ici, et l'adresse ne se
        choisit pas : c'est celle que le dossier porte, jamais une adresse
        fournie par le visiteur. Sinon le code serait un service d'envoi à
        l'adresse de son choix.

        ⚠️ Privée à dessein : elle rend une empreinte et déclenche un envoi. Un
        accès RPC en lecture pourrait autrement récupérer le condensé et
        arroser la personne de courriels.
        """
        self.ensure_one()
        adresse = self.email_from or self.partner_id.email
        if not adresse:
            return None, None
        code = "%06d" % secrets.randbelow(1_000_000)
        expiration = fields.Datetime.now() + timedelta(minutes=15)
        self.sudo()._portal_otp_email(adresse, code)
        return self._portal_otp_hash(code), expiration

    def _portal_otp_email(self, adresse, code):
        """Le message qui porte le code. Court, et il dit à quoi il sert."""
        self.ensure_one()
        societe = self.company_id or self.env.company
        corps = _(
            "<p>Votre code pour ouvrir votre dossier de candidature au poste "
            "« %(poste)s » est :</p>"
            "<p style=\"font-size:28px;font-weight:700;letter-spacing:4px;"
            "margin:16px 0\">%(code)s</p>"
            "<p>Il vaut quinze minutes. Si vous n'avez pas demandé à ouvrir "
            "votre dossier, ignorez ce message : personne n'y accède sans ce "
            "code.</p>",
            poste=self.job_id.name or _("à pourvoir"), code=code,
        )
        self.env["mail.mail"].sudo().create({
            "subject": _("Votre code d'accès : %s", code),
            "body_html": corps,
            "email_to": adresse,
            "email_from": societe.email or self.env.company.email,
            "auto_delete": True,
        }).send()

    # ------------------------------------------------------------------
    # Listes blanches
    # ------------------------------------------------------------------

    def _portal_summary(self):
        """Le résumé d'une candidature. Sans décision, il ne dit que l'état."""
        self.ensure_one()
        record = self.sudo()
        etat = record._portal_state()
        return {
            "id": record.id,
            "job": record.job_id.name or _("Poste retiré"),
            "company": record.company_id.name or "",
            "applied_on": fields.Date.to_date(record.create_date),
            "closed_on": fields.Date.to_date(record.date_closed) if record.date_closed else False,
            "state": etat,
            "state_label": {
                "en_cours": _("À l'étude"),
                "non_retenue": _("Non retenue"),
                "retenue": _("Retenue"),
                "close": _("Dossier clos"),
            }[etat],
            "held_interviews": record.held_interview_count,
            "decision_taken": record._portal_decision_taken(),
            # ⚠️ Le motif n'est servi qu'APRÈS la décision. Il est rédigé pour
            # être lu par la personne évaluée, mais le lui montrer avant que
            # quoi que ce soit soit tranché n'aurait aucun sens.
            "decision_note": (record.decision_note or "") if record._portal_decision_taken() else "",
            # ⚠️ Le gabarit lit CE drapeau pour montrer ou non le bouton. Mais
            # cacher un bouton n'est pas un contrôle d'accès : la route du
            # cahier refuse elle aussi, et un test le prouve en l'appelant
            # directement.
            "book_enabled": record._portal_book_enabled(),
            "url": record.access_url,
        }

    def _portal_interviews(self):
        """Les séances, telles que la personne évaluée peut les voir.

        ⚠️ **Aucun nom d'évaluateur, aucune note individuelle.** Ce sont des
        renseignements qui portent sur des tiers. Le détail complet est dans le
        cahier PDF, qui est déjà écrit pour ça
        (`report_interview_book_candidate` de `bf_recruitment`).

        Rien ne sort tant que la décision n'est pas rendue.
        """
        self.ensure_one()
        if not self._portal_decision_taken():
            return []
        record = self.sudo()
        sorties = []
        for interview in record.interview_ids.filtered(
                lambda i: i.state in _ETATS_LISIBLES):
            sorties.append({
                "round": interview.round_number,
                "date": fields.Date.to_date(interview.date_start) if interview.date_start else False,
                "guide": interview.guide_id.name or "",
                "panel_size": len(interview.interviewer_ids),
                "criteria": len(interview.guide_id.criterion_ids),
            })
        return sorties

    # ------------------------------------------------------------------
    # La deuxième porte : se créer un compte
    # ------------------------------------------------------------------

    def _portal_signup_url(self):
        """Une invitation d'inscription pour le partenaire du candidat.

        ⚠️ On passe par `signup_prepare()` plutôt que par `/web/signup` nu :
        l'instance refuse par défaut les inscriptions libres
        (`auth_signup.allow_uninvited = False`, portée `b2b`). Une invitation
        fonctionne quand même, et c'est le bon régime : on n'ouvre pas
        l'inscription à tout le monde parce qu'un candidat veut un compte.
        """
        self.ensure_one()
        partner = self.sudo().partner_id
        if not partner:
            return False
        if partner.user_ids:
            # Elle a déjà un compte : la bonne porte est la connexion.
            return False
        # ⚠️ En Odoo 18, `signup_token` n'est plus un champ stocké sur
        # `res.partner` : `_get_signup_url_for_action()` fabrique le jeton
        # lui-même, par `_generate_signup_token()`. Vérifier un champ
        # `signup_token` après coup lève un `AttributeError`. C'est l'URL qui
        # porte l'invitation.
        #
        # 🔴 `signup_valid` est OBLIGATOIRE, et une version antérieure de ce
        # module l'omettait en concluant que « appeler `signup_prepare()` avant
        # n'ajoute rien ». C'est faux, et le défaut ne se voyait qu'au PREMIER
        # clic. Dans le coeur, la route vaut `login` par défaut et ne devient
        # `signup` que si le partenaire porte un `signup_type`, que seul
        # `signup_prepare()` pose, et que le coeur n'appelle QUE si `signup_valid`
        # est au contexte. Sans lui, la personne qui clique « Créer mon compte »
        # arrive sur la page de CONNEXION, où elle n'a rien à faire puisqu'elle
        # n'a pas de compte.
        #
        # ⚠️ Et le défaut se cache tout seul : `signup_prepare()` écrit
        # `signup_type` de façon persistante. Le deuxième appel rend donc la
        # bonne URL même sans le contexte, et une vérification faite après un
        # premier essai ne voit plus rien.
        return partner.with_context(signup_valid=True)._get_signup_url_for_action().get(partner.id)
