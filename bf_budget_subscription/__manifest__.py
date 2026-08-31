{
    "name": "Budgets opérationnels — engagements récurrents",
    "version": "18.0.1.0.1",
    "category": "Accounting/Accounting",
    "summary": "Les abonnements deviennent un calendrier d'engagements datés, et le théorique s'y appuie",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget", "bf_subscription"],
    "description": """
Budgets opérationnels — engagements récurrents
==============================================

Rattache le registre des abonnements aux postes budgétaires, et s'en sert pour
deux choses que le socle ne peut pas faire seul.

**L'engagé cesse d'attendre la facture**
  Un renouvellement à venir dans la période budgétaire est déjà engagé : il est
  connu, daté et contractuel. Le socle ne comptait que le comptabilisé.

**Le théorique suit le calendrier, pas le chronomètre**
  Une dépense annuelle ne se lisse pas sur douze mois. Quand les engagements d'un
  poste sont datés, le théorique devient « ce qui était dû à ce jour » au lieu
  d'une fraction du temps écoulé. La part du plan qui n'est adossée à aucun
  engagement daté reste au prorata, et la ligne affiche laquelle des deux bases
  elle a utilisée.

⚠️ **Un abonnement à la demande n'a pas de calendrier**
  Il dépense pourtant. Le module ne le compte pas comme un engagement nul en
  silence : il l'affiche, parce qu'un calendrier partiel pris pour complet
  sous-estime le théorique et fabrique de fausses alertes.
""",
    "data": [
        "security/ir.model.access.csv",
        "wizard/bf_budget_subscription_link_wizard_views.xml",
        "views/subscription_subscription_views.xml",
        "views/bf_budget_line_views.xml",
    ],
}
