from odoo import models

# Les deux champs qui décident de qui la page nomme et de l'adresse où part le code.
CHAMPS_DU_LECTEUR = {"bf_beneficiary_id", "partner_id"}


class ProjectProject(models.Model):
    _inherit = "project.project"

    def _bf_lecteur_du_code(self):
        """Qui lit la page et reçoit le code : (partie nommée, contact qui reçoit).

        La partie nommée est le partenaire commercial. Le contact ne compte que
        si cette société n'a pas d'adresse : c'est alors lui qui reçoit le code
        (le contact bénéficiaire par le pont, le contact donneur d'ordre par le
        module porteur), et changer de contact change de lecteur.
        """
        self.ensure_one()
        nommee = self.bf_beneficiary_effective_id
        if nommee.email:
            return nommee, False
        beneficiaire = self.bf_beneficiary_id
        if beneficiaire.active and beneficiaire.email:
            return nommee, beneficiaire
        return nommee, self.partner_id

    def write(self, vals):
        """Changer de lecteur oublie les codes et les appareils du mandat.

        Un code en vol et un appareil reconnu (90 jours) sont liés au MANDAT, pas
        à la personne qui lit. Sans cette purge, le payeur — ou le détonateur de
        liens d'un antivirus de courriel — qui avait ouvert la page avant qu'on
        nomme le bénéficiaire restait reconnu sur une page qui en nomme un autre,
        et le vrai lecteur lisait « code envoyé à p***@ » jusqu'à l'expiration.

        Le déclencheur porte sur le bénéficiaire ET le donneur d'ordre : sans
        bénéficiaire, c'est le donneur d'ordre que la page nomme. On compare le
        lecteur (`_bf_lecteur_du_code`) avant et après : passer d'un contact à un
        autre d'une société qui a son adresse ne déconnecte personne ; sous une
        société sans adresse, c'est le contact qui reçoit, et le changer purge.

        ⚠️ Rattacher un contact à une autre société (`parent_id` sur la fiche du
        partenaire) change aussi la partie nommée, sans passer par ici : comme
        pour le module porteur, le bouton « Oublier les appareils » s'en charge.

        La purge est celle de ce bouton, sans son contrôle de droit : seul un
        administrateur de projets écrit `project.project` en Odoo 18, le même
        groupe que le bouton. Elle exige un nouveau code ; elle remet aussi à
        zéro les plafonds d'envoi du mandat, comme le bouton.
        """
        if not CHAMPS_DU_LECTEUR & vals.keys():
            return super().write(vals)
        avant = {p.id: p._bf_lecteur_du_code() for p in self.sudo()}
        resultat = super().write(vals)
        changes = self.sudo().filtered(lambda p: p._bf_lecteur_du_code() != avant[p.id])
        if changes:
            domaine = [("project_id", "in", changes.ids)]
            self.env["bf.progression.device"].sudo().search(domaine).unlink()
            self.env["bf.progression.otp"].sudo().search(domaine).unlink()
            changes._progression_otp_remettre_la_suite()
        return resultat
