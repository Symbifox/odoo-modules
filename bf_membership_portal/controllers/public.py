from odoo import SUPERUSER_ID, _, http
from odoo.http import request
from odoo.tools import email_normalize

#: Le champ piège : caché aux personnes, rempli par les robots.
HONEYPOT_FIELD = "website_url"
#: La clé de session qui porte la demande payable jusqu'au clic « Payer en ligne ».
#: Seule la session qui a envoyé la demande peut la payer en ligne.
SESSION_PAY_MEMBERSHIP = "bf_membership_pay_membership"
#: Longueur maximale d'un champ texte du formulaire.
MAX_FIELD_LENGTH = 200


class MembershipPublic(http.Controller):
    """Le formulaire public d'adhésion et le répertoire des membres."""

    # ------------------------------------------------------------------
    # Le formulaire d'adhésion
    # ------------------------------------------------------------------

    def _signup_types(self):
        return request.env["bf.membership.type"].sudo().search([
            ("public_signup", "=", True),
            ("company_id", "=", request.env.company.id),
        ])

    def _render_signup(self, types, values=None, error=None):
        return request.render("bf_membership_portal.membership_signup", {
            "types": types,
            "company": request.env.company.sudo(),
            "values": values or {},
            "error": error,
        })

    @http.route("/membres/adhesion", type="http", auth="public", methods=["GET"], website=True, sitemap=False)
    def membership_signup(self, **kw):
        types = self._signup_types()
        if not types:
            raise request.not_found()
        return self._render_signup(types)

    def _signup_errors(self, types, post):
        """Le motif de refus, le même pour tout le monde.

        🔴 Aucun motif ne dépend de ce que le registre contient : « ce courriel
        est déjà membre » dirait à un inconnu qui est au registre.
        """
        required = ("type_id", "name", "email")
        if any(not (post.get(f) or "").strip() for f in required):
            return _("Remplissez les champs obligatoires.")
        if any(len(post.get(f) or "") > MAX_FIELD_LENGTH
               for f in ("name", "email", "phone", "street", "city", "zip")):
            return _("Un des champs est trop long.")
        if not email_normalize(post["email"]):
            return _("L'adresse courriel n'est pas valide.")
        try:
            type_id = int(post["type_id"])
        except ValueError:
            return _("Choisissez une catégorie.")
        if type_id not in types.ids:
            return _("Choisissez une catégorie.")
        return None

    @http.route("/membres/adhesion", type="http", auth="public", methods=["POST"], website=True,
                sitemap=False)
    def membership_signup_submit(self, **post):
        """Recevoir une demande d'adhésion.

        Le jeton CSRF est vérifié par Odoo avant d'arriver ici (route `http`,
        `csrf` actif d'office). Le champ piège et le plafond par adresse IP
        arrêtent les envois automatiques. Quoi qu'il arrive ensuite, la
        personne voit la même page de remerciement.
        """
        types = self._signup_types()
        if not types:
            raise request.not_found()
        if post.get(HONEYPOT_FIELD):
            return request.redirect("/membres/adhesion/merci")
        error = self._signup_errors(types, post)
        ip = request.httprequest.remote_addr
        env = request.env(user=SUPERUSER_ID, su=True)
        Membership = env["bf.membership"]
        if not error and Membership._public_requests_exceeded(ip):
            error = _("Trop de demandes sont parties d'ici dans la dernière heure. Réessayez plus tard, "
                      "ou communiquez avec l'organisme.")
        if error:
            return self._render_signup(types, values=post, error=error)

        lang = request.env.lang
        values = {
            "name": post["name"].strip(),
            "email": post["email"].strip(),
            "phone": (post.get("phone") or "").strip(),
            "street": (post.get("street") or "").strip(),
            "city": (post.get("city") or "").strip(),
            "zip": (post.get("zip") or "").strip(),
            "is_company": bool(post.get("is_company")),
            "directory_consent": bool(post.get("directory_consent")),
            "notice_email_consent": bool(post.get("notice_email_consent")),
            "lang": lang if lang and env["res.lang"]._lang_get(lang) else False,
        }
        mtype = env["bf.membership.type"].browse(int(post["type_id"]))
        membership = Membership._create_public_request(mtype, values, ip=ip)
        if membership._public_payable():
            request.session[SESSION_PAY_MEMBERSHIP] = membership.id
        else:
            request.session.pop(SESSION_PAY_MEMBERSHIP, None)
        return request.redirect("/membres/adhesion/merci")

    def _session_payable(self):
        """La demande payable de CETTE session, ou rien."""
        membership_id = request.session.get(SESSION_PAY_MEMBERSHIP)
        if not membership_id:
            return None
        membership = request.env["bf.membership"].sudo().browse(int(membership_id)).exists()
        return membership if membership and membership._public_payable() else None

    @http.route("/membres/adhesion/merci", type="http", auth="public", website=True, sitemap=False)
    def membership_signup_done(self, message=None, **kw):
        """La même page pour toutes les demandes. Le bouton « Payer en ligne »
        ne paraît que pour la session qui a envoyé une demande payable : rien
        dans l'adresse de la page ne le fait apparaître. Le message vient d'une
        clé, jamais d'un texte de l'adresse."""
        return request.render("bf_membership_portal.membership_signup_done", {
            "company": request.env.company.sudo(),
            "can_pay": bool(self._session_payable()),
            "office_payment": message == "office",
        })

    @http.route("/membres/adhesion/payer", type="http", auth="public", methods=["POST"], website=True,
                sitemap=False)
    def membership_signup_pay(self, **post):
        """Créer la facture au clic, puis mener à son paiement en ligne.

        Le jeton CSRF est vérifié par Odoo avant d'arriver ici. Sans demande
        payable dans la session, retour à la page de remerciement, sans rien
        dire de plus.
        """
        membership = self._session_payable()
        if not membership:
            return request.redirect("/membres/adhesion/merci")
        if not (membership.invoice_id.state == "posted" or membership._automatic_invoice_allowed()):
            # La garde du bouton « Facturer » : une cotisation changée par
            # l'équipe ne se valide pas au clic d'une personne anonyme.
            return request.redirect("/membres/adhesion/merci?message=office")
        invoice = membership._public_pay_invoice()
        return request.redirect(invoice.get_portal_url())

    # ------------------------------------------------------------------
    # Le répertoire
    # ------------------------------------------------------------------

    @staticmethod
    def _listed_status(partner, company):
        """Le statut que lit le répertoire de `company` : celui des seules
        adhésions de CETTE société.

        🔴 Jamais `member_status`, qui confond les sociétés : dans une base qui
        en tient plusieurs, une ancienne membre d'une société paraîtrait au
        répertoire de cette société parce qu'elle est en règle dans une autre.
        """
        return partner.sudo()._member_status_in(company)

    def _directory_allowed(self, company):
        """Qui voit le répertoire de `company` en mode « membres connectés » : un
        employé, ou une personne en règle (ou en grâce) dans CETTE société,
        elle-même ou par l'organisation qu'elle représente."""
        user = request.env.user
        if user._is_internal():
            return True
        partner = user.partner_id
        candidates = partner | partner._membership_represented()
        return any(self._listed_status(p, company) in ("member", "grace") for p in candidates.sudo())

    @http.route("/membres/repertoire", type="http", auth="public", website=True, sitemap=False)
    def membership_directory(self, **kw):
        """Le répertoire des membres.

        🔴 Fermé d'office (404, comme s'il n'existait pas). Ouvert, il ne montre
        que les membres en règle ou en grâce de CETTE société qui y ont
        consenti, et seulement leur nom et leur ville : les enregistrements ne
        vont jamais au gabarit, seulement ces deux valeurs.
        """
        company = request.env.company.sudo()
        mode = company.membership_directory
        if mode == "closed":
            raise request.not_found()
        if mode == "members":
            if request.env.user._is_public():
                return request.redirect("/web/login?redirect=/membres/repertoire")
            if not self._directory_allowed(company):
                raise request.not_found()
        partners = request.env["res.partner"].sudo().search([
            ("directory_consent", "=", True),
            ("membership_ids", "any", [("company_id", "=", company.id), ("state", "in", ("active", "expired"))]),
        ], order="name")
        partners = partners.filtered(lambda p: self._listed_status(p, company) in ("member", "grace"))
        entries = [{"name": p.name, "city": p.city or ""} for p in partners]
        return request.render("bf_membership_portal.membership_directory", {
            "company": company,
            "entries": entries,
        })
