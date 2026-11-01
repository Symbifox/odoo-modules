{
    "name": "BF Calendar Work Blocks - Meetings",
    "summary": "Work blocks (focus, production, meeting slot, catch-up) need "
               "neither an agenda nor minutes, and stay off the meetings "
               "dashboard.",
    # 18.0.1.0.0 : une plage de travail ne demande ni ordre du jour ni compte
    #   rendu (en pratique, beaucoup étaient dispensées à la main). Les dispenses vivent sur l'étiquette (bf_meeting, recopiées sur
    #   la rencontre) : ce pont les coche sur les quatre types de
    #   bf_calendar_block et aligne les plages À VENIR. Un pont, parce que
    #   pendant la montée de bf_calendar_block les champs de bf_meeting
    #   n'existent pas encore.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["bf_calendar_block", "bf_meeting"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    # Pas d'installation automatique : poser_apres_arret.sh refuse un module
    # auto_install en -i (il s'installerait pendant le -u d'un autre) ; un pont
    # s'installe à la main là où ses deux moitiés sont voulues ensemble.
    "auto_install": False,
}
