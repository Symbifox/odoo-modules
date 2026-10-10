# bf_cx_onboarding: getting-started panel

Auto-installs when both `bf_cx` and `bf_onboarding_base` are installed.
Adds the Customer Experience getting-started panel, in four steps: create
a program, configure the pacing, launch a first wave, configure the
Complaints team. Each step ticks itself once the matching action has been
taken (a program created, a wave sent, the pacing adjusted, a first
complaint recorded). Nothing is sent to the client.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. On upgrade, the onboarding panel and its steps switch to English where they still hold the delivered French.
