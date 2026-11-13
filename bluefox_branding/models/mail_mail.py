from odoo import api, models

from . import brand_color_mixin as bc


class MailMail(models.Model):
    _inherit = 'mail.mail'

    # -- Quelle société ce courriel porte-t-il ? ------------------------------
    #
    # ⚠️ **`env.company` est la mauvaise réponse, et elle a l'air de la
    # bonne.** C'est la société de la SESSION qui crée le courriel, et celle
    # de l'usager du cron de la file au moment de l'envoi — jamais celle du
    # dossier qui envoie. Un courriel d'une filiale sortait donc avec
    # l'accent de la société d'en-tête, et deux envois du même dossier
    # changeaient de couleur selon qui les avait déclenchés.
    #
    # La règle : la société du DOSSIER, à défaut celle de l'AUTEUR du
    # message, à défaut celle de la session. La mise en page des avis lit
    # déjà la société du dossier (`_notify_by_email_prepare_rendering_context`,
    # qui lit `company_id` sur l'enregistrement) : le corps et son habillage
    # disent enfin la même chose.
    #
    # L'auteur avant la session, parce que c'est le cas dominant : quand le
    # dossier n'a pas de société, le courriel part souvent par un cron ou un
    # robot d'intégration, dont la société n'a aucun rapport avec le contenu,
    # alors que l'auteur du message en a un.

    @staticmethod
    def _bf_entier(valeur):
        """L'identifiant, ou None (voir `bc.entier`, partagé avec le rendu)."""
        return bc.entier(valeur)

    @api.model
    def _bf_societe_de_marque(self, indices):
        """La société dont ce courriel porte la marque.

        :param dict indices: ce qu'on sait du courriel — `model`/`res_id`,
          `mail_message_id`, `author_id`. Ce sont les clés d'un `create`, ou
          les champs relus sur un courriel existant.
        :return: une société, jamais vide.

        🔴 **`mail.message.record_company_id` n'est PAS une source**, même si
        elle en a l'air : le cœur la remplit avec
        `_mail_get_companies(default=self.env.company)`, donc avec la société
        de la session gelée au moment de la publication dès que le dossier n'en
        a pas. S'y fier, c'est garder le défaut qu'on corrige sous forme de
        donnée — et court-circuiter l'auteur. On relit le dossier.
        """
        indices = dict(indices or {})

        # Un avis ne reçoit que l'identifiant de son message (la mise en page
        # a déjà été rendue) : le modèle, le dossier et l'auteur se lisent là.
        identifiant = self._bf_entier(indices.get('mail_message_id'))
        if identifiant:
            message = self.env['mail.message'].sudo().browse(identifiant).exists()
            if message:
                for cle, valeur in (('model', message.model),
                                    ('res_id', message.res_id),
                                    ('author_id', message.author_id.id)):
                    if not indices.get(cle):
                        indices[cle] = valeur

        # 1. le dossier qui envoie
        societe = self._bf_societe_du_dossier(indices.get('model'), indices.get('res_id'))
        if societe:
            return societe

        # 2. l'auteur du message
        societe = self._bf_societe_de_l_auteur(indices.get('author_id'))
        if societe:
            return societe

        # 3. la session
        return self.env.company

    @api.model
    def _bf_societe_du_dossier(self, model, res_id):
        """La société du dossier qui envoie, ou une société vide.

        Lue comme le cœur la lit (`_mail_get_companies`), donc par le champ
        Société du modèle quand il en a un. En sudo : afficher une couleur
        n'est pas un droit d'accès, et l'usager du cron de la file ne voit pas
        les sociétés des autres.
        """
        identifiant = self._bf_entier(res_id)
        # `model not in self.env` ne suffit pas : un modèle peut être dans le
        # registre et n'avoir aucune table (voir `bc.modele_sans_table`).
        if not model or not identifiant or model not in self.env \
                or bc.modele_sans_table(self.env[model]):
            return self.env['res.company']
        dossier = self.env[model].sudo().browse(identifiant).exists()
        if not dossier:
            return self.env['res.company']
        return dossier._mail_get_companies(default=False)[dossier.id] \
            or self.env['res.company']

    @api.model
    def _bf_societe_de_l_auteur(self, author_id):
        """La société de l'auteur du message : celle de son usager, à défaut
        celle de son contact.

        ⚠️ `user_ids` cache les usagers archivés, et un robot d'intégration
        en est souvent un : on lit donc sans le filtre d'activité, sinon
        l'auteur ne rend rien et la session reprend la main (28 % des
        courriels sur un dossier sans société ont un auteur archivé).

        ⚠️ Mais l'archivé ne passe pas DEVANT l'actif. Lu sans le filtre,
        `user_ids` rend l'ordre de la base — `res.users._order` vaut
        « name, login », et les usagers d'un même contact partagent son nom,
        donc c'est le login qui départage. Mesuré : un contact portant un
        usager actif et un archivé résolvait la société de l'ARCHIVÉ. On trie
        donc les actifs devant ; `sorted` étant stable, les actifs gardent
        entre eux l'ordre de la base. L'intention est « l'archivé compte
        quand il est seul ».
        """
        identifiant = self._bf_entier(author_id)
        if not identifiant:
            return self.env['res.company']
        auteur = self.env['res.partner'].sudo().browse(identifiant).exists()
        if not auteur:
            return self.env['res.company']
        usagers = auteur.with_context(active_test=False).user_ids
        for usager in usagers.sorted(key=lambda usager: not usager.active):
            if usager.company_id:
                return usager.company_id
        return auteur.company_id or self.env['res.company']

    def _bf_societe_de_marque_du_courriel(self):
        """La société de marque d'un courriel déjà en base."""
        self.ensure_one()
        courriel = self.sudo()
        return self._bf_societe_de_marque({
            'model': courriel.model,
            'res_id': courriel.res_id,
            'author_id': courriel.author_id.id,
        })

    # -- Remplacement --------------------------------------------------------

    def _replace_button_colors(self, html_content, company=None):
        """Replace old Odoo button colors with ``company``'s brand colors."""
        return bc.replace_button_colors(self.env, html_content, company=company)

    def _send(self, auto_commit=False, raise_exception=False, smtp_session=None, **kwargs):
        """Override to replace button colors in email body before sending."""
        for mail in self:
            if not bc.porte_une_vieille_couleur(mail.body_html):
                continue
            corps = self._replace_button_colors(
                mail.body_html, company=mail._bf_societe_de_marque_du_courriel())
            if corps != mail.body_html:
                mail.body_html = corps
        return super()._send(
            auto_commit=auto_commit,
            raise_exception=raise_exception,
            smtp_session=smtp_session,
            **kwargs
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Override to replace button colors when mail is created."""
        for vals in vals_list:
            cles = [cle for cle in ('body_html', 'body')
                    if bc.porte_une_vieille_couleur(vals.get(cle))]
            if not cles:
                continue
            societe = self._bf_societe_de_marque(vals)
            for cle in cles:
                vals[cle] = self._replace_button_colors(vals[cle], company=societe)
        return super().create(vals_list)

    def write(self, vals):
        """Override to replace button colors when mail is updated."""
        cles = [cle for cle in ('body_html', 'body')
                if bc.porte_une_vieille_couleur(vals.get(cle))]
        if not cles:
            return super().write(vals)
        # La couleur se décide par courriel : une écriture en lot peut porter
        # sur des dossiers de sociétés différentes.
        resultat = True
        for courriel in self:
            propres = dict(vals)
            societe = courriel._bf_societe_de_marque_du_courriel()
            for cle in cles:
                propres[cle] = courriel._replace_button_colors(vals[cle], company=societe)
            resultat = super(MailMail, courriel).write(propres) and resultat
        return resultat
