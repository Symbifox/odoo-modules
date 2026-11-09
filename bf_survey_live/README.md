# Live Session Screens (`bf_survey_live`)

Odoo already runs the Kahoot-style session: a code, a projected QR code, people
joining through `/s/<code>` with a nickname, a timer, a speed bonus, the rising
histogram and the leaderboard. This module adds the screens it does not have,
and the presentation frame around them.

## Why

A workshop is not a list of questions. It opens, it presents, it illustrates, it
concludes — and three of its moments have no screen in the core app: an open
text answer that nobody can read as a list, teams, and the final picture people
photograph.

## Features

- **Word cloud.** A long-text question shows **nothing** live in the core app:
  no histogram, no list. The cloud counts the room's words and writes them the
  larger the more often they come back. The counting happens on the server (240
  French and English stop words, elisions removed, case folded and displayed in
  the majority spelling, size as the square root of the count), and the question
  can discard words of its own: the question's own word usually crushes
  everything else. A question that collects an email or a nickname never
  projects.
- **Teams.** Two ways, never both at once: **at random** (a round-robin draw
  into two to six teams, equal to one member, with the system's randomness), or
  **by a question** (a single-choice question carries the *picks the team* flag,
  each answer becomes a team, and answering joins it). The team board is
  summoned over the current screen with the `T` key — without it nobody in the
  room knows which team they are in.
- **A podium.** The last screen of a session is the core's leaderboard: bars.
  The podium puts the first three on blocks in the company's own colour, ranks
  4 to 10 underneath, for people as well as for teams.
- **A presentation frame.** A survey **section** that receives a layout becomes
  a **slide**, projected in its place between the questions, and the host moves
  from one screen to the next with the same arrow. Six layouts: standard (the
  core's section, untouched), title, text, image, image and text, quote. A
  screen counter, bottom right, says where you are.
- **The participant's phone** carries the words of the slide (title, subtitle,
  text) and its image, in one column, plus **their team** on a chip in the
  team's colour. The waiting screen shows the teams and their headcounts,
  refreshed on their own.
- **The join code on every screen.** The core projects the QR code on the
  welcome screen and never again: from the first question on, whoever arrives
  late has nothing left to scan. A card bottom left carries the company logo,
  the QR code and the session code, big enough for a room, for the whole
  session. It is measured in viewport units, not in `rem`, because the core's
  chart takes 75% of the window: a card sized once for a 1500 px screen sat on
  the chart's axis labels on a 1024 px projector. And it leaves the screen while
  the leaderboard or the podium is up — that is the picture people photograph at
  the end of a workshop, and a code to join is no longer any use on it.
- **The answers the session did not show.** A long text without a word cloud and
  a numerical question left the screen empty while the room answered. Both now
  appear in the core's own frame, one answer per label.

- **The host's own test does not reach the wall.** Odoo marks a survey test
  opened during a session as a session answer, so without a guard a "Test" run
  by the host appeared among the room's answers, in the word cloud and on the
  board. Every screen drops it.

## Two rules that hold the module

**Team ranking is by average per member**, with the total shown beside it.
Ranking by total would make the biggest team win: with teams born from a
question, "which department?" would hand victory to the largest department,
which is not a game.

**Teams freeze when the session opens**, not at the first answer. Otherwise two
attendees answering at the same time would create the same team, and the
uniqueness constraint would drop one of the two answers. An answer added during
the session therefore leaves its author without a team, counted separately on
the board.

## How it grafts onto the core

Four stitches, and no more:

1. The session template receives four attributes and one frame for the cloud,
   **inside** the core's written-answers area. The core's classes are untouched.
2. `_setupTextAnswers` and `_setupLeaderboard` are overridden. The board honours
   the core's leaderboard contract (`showLeaderboard`, `hideLeaderboard`), so the
   session's screen machine does not change: it believes it is showing its own
   leaderboard.
3. The cloud and the board pull their data from **their own routes**, not from
   `/survey/session/results`. The progress bar stays the core's.
4. The frame does not rewrite the core's walk from one screen to the next. That
   walk looks for a QUESTION and skips a section carrying neither question nor
   description; we look at what it just skipped, and if a slide is left between
   the current screen and the one it offers, that slide comes first. Three
   overrides, all switched off outside a session, two of them also off when the
   survey carries no slide.

## The brand

The company's logo appears on the waiting screen and beside the session code,
and the podium blocks take its colour (`primary_color`, the field of its
document layout). Without a logo, nothing shows; without a colour, the podium
keeps the measured blue of `bf_live_base`.

⚠️ A colour too pale or too dark to carry text is pushed until one ink reaches
4.5:1 on it (`bf_live_base.brand_ramp`): the house hue stays its own, it only
darkens.

## Access

Attendees stay anonymous: the session is joined with a nickname, and nothing in
these screens prints an email address. A question that collects an email or a
nickname never projects, neither as a cloud nor as labels. The board and the
cloud are rendered for the host of the session, through routes that require a
logged-in user.

## Configuration

1. On the survey, *Options*: the session leaderboard mode (per person or per
   team), the podium, the screen counter, the join code card, and the number of
   teams to draw.
2. On a question, *Options*: *word cloud* as its live screen (long text or short
   text), the words to discard, and *this answer picks the team* on a
   single-choice question.
3. On a section, the *Slide* tab: its layout, subtitle and image.

## Known limits

- **A slide IS a section**: the questions that follow it belong to it, which
  shows in the results report. And a frame needs at least one question, because
  the core refuses to open a session on a survey that has none.
- **An arrow is not a screen**: on a scored question the core inserts its own
  screens (answers, then leaderboard) before moving on. The screen counter is
  the only reliable landmark.
- **A long answer is cut at 70 characters**: the core's frame writes each answer
  on one line, without wrapping, and computes its width from the text.
- A team's counters on the form cover **all** the sessions of the survey; the
  projected board only shows the current one. The two numbers differ, and that
  is intended: one is the history, the other the live.
- The participant's phone is not branded: the respondent form is the one of
  every survey, session or not, and putting the house logo on it would change
  the screen of everyone answering a questionnaire by email.
- No screen of a leaderboard ever appears, podium included, unless the survey
  has a question marked *save as nickname*: the core computes
  `session_show_leaderboard` from it.

## License

BUSL-1.1, Change Date **2030-11-08**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
