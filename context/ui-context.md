# UI Context

Source of truth: the console's Claude Design package (Console Journeys,
Console Styleguide, Factory Console, the four component exports, and the
bound Industry design system), which is not part of this repository. This
file is the short form. The agent never makes a visual decision; it reads this file. A value
that is not here is an open question for `progress-tracker.md`, not a guess.

## Theme

An operations console people watch for hours: dense, legible, evidence first.
Built on the **Industry** design system (a wireframe, blueprint idiom: steel
accent on a light technical ground, square corners, hairline borders, "+"
registration marks at the corners of instrument frames) with a console-owned
dark theme derived from it. Light and dark both ship; the theme follows
`prefers-color-scheme`, with a best-effort override stored per browser. No
brand tokens exist yet; a brand would replace the chrome tokens in one sheet.

Four principles from the styleguide:

- **Evidence first.** Every status has its evidence one click away: a chip
  opens the gate, the gate opens the command and its output.
- **Dense, legible.** 1280 to 1920 px, 1440 px reference. Tables over cards.
  36 px rows at 1.5 line height. No scrolling inside cells.
- **Agents are steps.** Planner, builder and reviewer appear as phases with a
  harness and a model. No avatars, no first person, no chat.
- **The PRD's names.** States, labels, roles and instruments are spelled as
  in the PRD, in monospace whenever a person might copy them.

## Colors

Chrome tokens come from the Industry stylesheet (`industry.css`) and are
overridden for dark in `console-tokens.css`. All values as CSS custom
properties; no hex in component code.

| Role | CSS variable | Light | Dark |
| --- | --- | --- | --- |
| Page background | `--color-bg` | `#f2f2f3` | `#141618` |
| Surface (inputs, dialog) | `--color-surface` | `#e9e9ea` | `#1b1e21` |
| Text | `--color-text` | `#1d1f20` | `#e4e6e8` |
| Muted text (derived) | `color-mix(in srgb, var(--color-text) 62%, transparent)` | about `#6a6c6d` | about `#9a9da1` |
| Divider, hairline border | `--color-divider` | `color-mix(in srgb, #1d1f20 16%, transparent)` | `rgba(228,230,232,.16)` |
| Accent (steel): primary actions, links, focus, running state | `--color-accent` | `#5980a6` | `#7ea4c9` |
| Accent hover | `--color-accent-600` | `#597ea3` | `#94bce3` |
| Accent pressed | `--color-accent-700` | `#416180` | `#b5d9fd` |
| Accent deep (text-size accent on light ground) | `--color-accent-700` / `--color-accent-900` | `#416180` / `#1d2d3d` | `#b5d9fd` / `#d6ebff` |

Tonal ramps `--color-neutral-100..900` and `--color-accent-100..900` exist in
`industry.css` (OKLCH, shared lightness scale). Use 100 to 300 for tinted
fills and hovers, 500 as base, 700 to 900 for text on tinted fills. Prefer a
ramp step over an ad-hoc `color-mix()`. `--color-accent-2-*` duplicates the
accent (mono scheme); never use it as a second colour.

Semantic palette, console-owned, defined in `console-tokens.css`. Five roles
used **only** for status, risk, gate, verdict, severity and meters. Each value
pairs the colour with a glyph and the text label, so no meaning depends on
colour alone. Never use these for decoration; never use the accent for a
state.

| Role | CSS variable | Light | Dark | Values that use it | Glyph cue |
| --- | --- | --- | --- | --- | --- |
| Pass | `--sem-pass` | `#3d7a55` | `#82c29a` | `merged`, gate `pass`, `accepted`, KPI on target | `✓` |
| Fail | `--sem-fail` | `#b0433a` | `#ec8f84` | gate `fail`, `risk:governed`, `human_decision_needed`, `Blocker` (solid fill), meter at cap, KPI off target, error | `×`, `!`, `▮▮▮`, `■` at cap |
| Warn | `--sem-warn` | `#8f630e` | `#dcae55` | `blocked`, `risk:high`, `revision_needed`, `Major`, meter 80 to 99% | `■`, `↻`, `▮▮▯`, `▲` |
| Info | `--sem-info` | `#416180` | `#9ec3e6` | `running`, `pr_open`, `Minor`, gate `running`, meter under 80% | `●`, `◆` |
| Neutral | `--sem-neutral` | `#5d5d60` | `#a8a8ac` | `queued`, `cancelled`, `risk:low`, gate `skipped` and `pending`, `Nit`, `Unverifiable`, no data | `○`, `×`, `▮▯▯`, `–`, dashed border |

Tinted ground for a chip: `color-mix(in srgb, var(--sem-x) 9%, transparent)`;
its border: `color-mix(in srgb, var(--sem-x) 38%, transparent)` (45% for a
GitHub label chip). Selected row or live-tail tint:
`color-mix(in srgb, var(--color-accent) 8%, transparent)`.

## Typography

| Role | Font | Variable | Weight | Size / line height | Use |
| --- | --- | --- | --- | --- | --- |
| Display | Barlow Condensed | `--font-heading` | 600 | 40 / 1.0 | KPI values only |
| Title | Barlow Condensed | `--font-heading` | 600 | 28 / 1.15 | Screen title, one per screen |
| Section | Barlow Condensed | `--font-heading` | 600 | 20 / 1.2 | Section headings |
| Card | Barlow Condensed | `--font-heading` | 600 | 17 / 1.2 | Card and drawer titles |
| Body | Barlow | `--font-body` | 400 | 13 to 14 / 1.5 | Body, table cells |
| Meta | Barlow | `--font-body` | 400 | 12 / 1.45 | Secondary lines, helper text |
| Mono | JetBrains Mono | `--font-mono` | 400, 500 | 12.5 / 1.5 | Ids, paths, labels, SHAs, commands, envelope JSON |
| Mono small | JetBrains Mono | `--font-mono` | 400 | 11 / 1.4 | Table headers (uppercase, `letter-spacing .06em`), trace rows, chips (11.5) |

Fonts load from Google Fonts: Barlow 400/500/700, Barlow Condensed 400/600,
JetBrains Mono 400/500. All open-source. Headings use `letter-spacing
-0.015em`. Define `--font-mono: 'JetBrains Mono', ui-monospace, monospace;`
in `console-tokens.css`; the Industry sheet has no mono face.

## Spacing and Density

| Token | Value | Use |
| --- | --- | --- |
| `--space-1` | 3.4px | Glyph to label |
| `--space-2` | 6.8px | Vertical cell padding, chip gaps |
| `--space-3` | 10.2px | Horizontal cell padding, card inset |
| `--space-4` | 13.6px | Card padding |
| `--space-6` | 20.4px | Grid gutter between tiles |
| `--space-8` | 27.2px | Between sections in a screen |

Density rules:

- Table rows 36 px for task, approval and audit tables; 30 px for trace rows.
  Text 12.5 to 13 px at 1.5. Headers 11 px mono uppercase.
- Ids, numbers and times right-aligned in mono; text left-aligned. Truncate
  `args` and snippets with an ellipsis; full value on hover and in the row
  drawer.
- Timelines: 30 px per phase, one shared time axis per task, bars at least
  2 px so a 1 s code step stays visible.
- Chip: height 20 px, padding 0 7 px, gap 5 px, 1 px border.
- Meter: 10 px tall, 1 px border, a 1 px tick at 80%.
- Content max width 1920 px. At 1280 px the right rail drops under the main
  column.

## Border Radius and Frames

Industry sets square corners on components. The console keeps them.

| Context | Rule |
| --- | --- |
| Cards, tiles, drawers, dialogs, buttons, inputs, tags, segmented controls | `border-radius: 0` (set by `industry.css` on `.card, .btn, .input, .tag, .seg, .dialog`) |
| Instrument frames (cards, tiles, drawers, dialogs) | `.blueprint` class with four `<i class="corner tl|tr|bl|br">` children: 1 px hairline border plus 11 px "+" marks at the corners |
| Rows, table cells | Hairline bottom border only; no frame, no marks |
| Radio dot | Round (the only circle) |
| `--radius-sm/md/lg` (2 / 4 / 7 px) | Exist in the system; not used by console components in v1 |

Elevation: `--shadow-sm`, `--shadow-md`, `--shadow-lg` (ink-tinted). Cards and
dialogs have a transparent surface with a hairline border; the dialog sits on
`--color-surface` with `--shadow-lg` over a backdrop of
`color-mix(in srgb, var(--color-neutral-900) 50%, transparent)`. The primary
button is the one solid object: accent fill, square corners.

## Component Library

- Base: the Industry CSS classes (`.btn`, `.btn-primary`, `.btn-secondary`,
  `.btn-ghost`, `.btn-icon`, `.tag`, `.field`, `.input`, `.radio`, `.seg`,
  `.card`, `.nav`, `.table`, `.dialog`, `.blueprint`, `.text-muted`) from the
  vendored `industry.css`. Copy their markup; do not create parallel classes.
- Behaviour: Radix primitives, unstyled, for Dialog (focus trap), Tabs,
  Tooltip and DropdownMenu.
- Console components, named exactly as below, each with the states Default,
  Hover, Loading, Empty, Error and Disabled unless noted:

| Component | What it is | Key contract |
| --- | --- | --- |
| `Chip` | One anatomy for every enumerated value: glyph, PRD value in mono, 9% tinted ground, 1 px border of the same role | `kind` ∈ status, risk, gate, verdict, severity, surface, label; `role="img"`, `aria-label` "Task state: blocked"; `Blocker` is solid fill with `--color-bg` text; `Unverifiable` and `skipped` have a dashed border; disabled 45% opacity; risk shows a three-step bar `▮▯▯ ▮▮▯ ▮▮▮` |
| `KPITile` | Label (mono caps), 40 px value with unit, 7-point trend bars (last bar accent, others 22% ink), status line with glyph, target text, optional note | `status` ∈ on, off, nodata, loading, error; value renders `—` when not readable; `role="group"` with a full `aria-label` |
| `Meter` | Label, `used / cap` in mono, 10 px bar with an 80% tick, percent line | `role="meter"` with `aria-valuetext` "$46.10 of $54.00, 85 percent"; tone and glyph change together: `●` under 80%, `▲` 80 to 99%, `■` at cap; `state` ∈ default, loading, error |
| `GateLadder` | Ordered list of gates as run, first failure selected, drawer open | `role="list"` with position announced; gates after a failure show `–` skipped and are not clickable; `›` reveals "Open evidence"; running gate `●`, pending `○` |
| `EvidenceDrawer` | Command, exit code, attempt ("build 1 · repair 1 of 3"), `gate_results` id and time, the last 4,000 characters of output in mono | Opens from a gate row; `Esc` closes |
| `PhaseTimeline` | One row per phase on a shared axis with duration and cost | Bar fill encodes kind: solid accent = agent phase, hairline outline = code step, hatched 135° = waiting on a human; left glyph carries the phase's gate result; running phase extends to now; queued shows "Queued, 2 ahead" |
| `TraceTable` | Newest first, live tail on, new row tinted 2 s, columns `started_at agent tool args result_snippet ok duration_ms` | Header "live · newest row 2 s ago · 10,000 rows · cursor rowid > 48211 · Pause tail"; paused reads "Resume tail"; "Tail behind by 34 s" warning keeps rows; virtualised; `p` toggles |
| `EnvelopeViewer` | Tabs planner / builder (attempt n) / reviewer; JSON in mono; footer `output_type`, `envelope_id`, time, Copy JSON | `valid = false` highlights the schema error path and shows raw text; empty reads "No envelope; phase did not return." |
| `ScoreBreakdown` | Framed card: title and instrument version, rows base × factors with rule text, the equation in a mono box, outcome `Chip` plus conclusion | `instrument` ∈ risk, review; override shows login and reason under the outcome; tier U reads "Scope names no paths", score 0 |
| `FindingsList` | Header "Conformance 44 · accept ≥ 70 · round 1 of 2"; columns Severity, Req., Location, Problem, Adjustments, Score, Effect | Blocking first, advisory, then dismissed at 60% opacity with the quoted Non-goals line; location opens on GitHub; empty "No findings. Every AC met." |
| `ApprovalCard` | "Approve plan": writes label, on repo#issue, as @login through GitHub authorisation, bound to spec @ sha, holdout n tests all present; primary button "Write factory:plan-approved"; secondary "Open issue on GitHub ↗"; footer "The GitHub label is the approval." | Enter or Space activates; loading "Writing label…"; error "GitHub refused the write (403)" and nothing recorded; disabled states name the reason (no write access, not a CODEOWNER, spec sha changed); success line "factory:plan-approved by @login · 2 min ago · approvals row bound to sha" |
| `KillSwitchDialog` | Header pill `FACTORY_ENABLED=1 · 3 live processes`; button "Stop factory…" outlined in the fail role; dialog states the effect, requires a one-line Reason, "Recorded as an events row with your login @login" | Focus lands in Reason; `Esc` cancels; Enter confirms only when Reason is filled; loading "Stopping 3 processes · 41 s left" per pid; disabled when no write access or already 0 (shows who and when) |
| `ConfigChangeCard` | File, PR link, unified diff lines, eval result line "Overall 63 → 50 (−13 pt). bugs 67 → 44.", footer "Opened by @login · reason · not applied until merged" | Loading "Eval running n/24"; error when eval blocked or PR could not be opened |
| `EvalComparisonTable` | Columns Version, Trigger, Overall, bugs, tests, docs, small features, Δ overall, Result | Whole points against the merged baseline; Δ coloured by role; a drop over 10 points reads "blocked" |
| `RiskOverrideDialog`, `AuditDecisionRow`, `RoundSwitcher`, `ProposeChangeDialog`, `RunEvalButton`, `StuckFlag`, `NavBadge`, `Freshness`, `ChainFooter` | Supporting controls | Every confirming control needs a Reason; `ChainFooter` reads "chain verified · n records · head …" |

## Layout Patterns

- **Shell.** Top header: installation name, nav for the six screens with an
  Approvals count badge, the `FACTORY_ENABLED` flag pill, the login pill.
  Below it the screen title (one per screen) and a `Freshness` stamp. Width
  up to 1920 px, 16 px minimum side gutter at 1280.
- **Six routes, one spine.** `/` Overview; `/tasks/[taskId]` Task detail;
  `/tasks/[taskId]/review` Review findings; `/approvals?tab=plans|questions|
  audit`; `/eval`; `/config?tab=roster|instruments|budgets|paths|
  installation`. Approvals and Configuration are URL tabs, not separate
  screens. There is no seventh screen.
- **Overview.** Top row of five `KPITile`s on a `--space-6` gutter grid;
  "Needs a human" table; running and queued table; "Spend today" `Meter`
  beside the kill-switch; a collapsed Indicators panel (leading indicators
  with thresholds) at the bottom.
- **Task detail.** `TaskStrip` (status chip, risk chip, links to issue, spec,
  branch, PR); `PhaseTimeline`; `GateLadder` with `EvidenceDrawer` to the
  right (drops below at 1280); `RepairRounds`; task budget `Meter`;
  `ScoreBreakdown` (risk); `EnvelopeViewer`; `TraceTable`; `ChainFooter`.
- **Review findings.** `ReviewHeader`, `AcTable` (one `met` verdict per AC
  with evidence), `FindingsList`, `ScoreBreakdown` (review), `RoundSwitcher`.
- **Approvals.** Plan queue with `SpecView` (sections, AC→holdout table with
  existence checks) and `ApprovalCard`; `QuestionsTable` with the question in
  the issue's language (`lang="pt-BR"` or `"en"` on the cell); `AuditStrata`
  and `AuditTable` with `AuditDecisionRow`.
- **Eval and versions.** `EvalSetSummary`, `RunsByVersion`
  (`EvalComparisonTable`), `BlockedChange`, `InstallDryRun`, `RunEvalButton`.
- **Configuration.** `OpenChanges` (cards), `RosterTable`,
  `InstrumentTables`, `BudgetsTable`, `ProtectedPathsTable`,
  `InstallationPanel`, `ProposeChangeDialog`.
- **Drawers** open to the right of the list they belong to and close with
  `Esc`. **Dialogs** are centred, focus-trapped, with a required Reason field
  for any confirming action.
- **Under 1280 px** every route redirects to `/unsupported`: "Factory Console
  needs a window at least 1280 px wide".

Keyboard map (shown with `?`): `1` to `6` go to the six screens; `j` and `k`
move between rows; `Enter` opens the focused task, gate or approval; `a`
focuses the approval button on an open plan; `Esc` closes a drawer or dialog;
`p` pauses and resumes the tail; the kill-switch is reachable by Tab from the
Overview header.

## Icons and Glyphs

- Lucide icons at `strokeWidth={1.5}`, used sparingly for actions and
  navigation (open external `↗`, copy, pause, chevron). Inline 16 px, in
  buttons 18 px, on `currentColor`.
- State is carried by text glyphs, never by icons alone: `○` queued or
  pending, `●` running, `■` blocked or cap reached, `◆` pr_open, `✓` merged
  or pass or accepted, `×` cancelled or fail, `–` skipped, `↻`
  revision_needed, `!` human_decision_needed or error, `▲` meter warning,
  `▮▯▯ ▮▮▯ ▮▮▮` risk low, high, governed, `A B C D U` severity letters only
  inside the score-breakdown card, `GH` and `FC` surface chips, `⌗` GitHub
  label chip.

## Copy and Tone

- Sentence case for headings and buttons. No exclamation marks. No first
  person for agents.
- Numbers carry units: `$6.00`, `45 min`, `1,240 ms`, `$3.92 of $6.00 · 65%`.
- Relative times ("4 min ago") show the absolute timestamp with offset on
  hover and focus: `2026-10-23 14:28:07 -03`.
- Say what happened and what the system did, in the PRD's words. "Blocked at
  code gates after 3 repairs. typecheck failed." Not "Oops! The agent got
  stuck." "Write factory:plan-approved", not "Approve and continue!"
  "Reviewer (Codex) returned revision_needed." "Opens PR to
  .factory/risk.yaml. Not applied until merged."
- Error messages are declarative and name the fix: "GitHub refused the label
  write (403): your token lacks write access on acme/atlas-api."
- UI is English. A blocked task's question is rendered verbatim in the
  issue's language.

## Accessibility

- WCAG 2.2 AA. Body text at least 4.5:1; the accent is reserved for large
  text and chrome, deep ramp steps for text-size accent.
- Without colour: every chip, gate and severity pairs colour with a glyph and
  its text value; meters add `● ▲ ■`.
- Screen readers: chips expose "Task state: blocked"; gate ladder is a list
  with position; meters use `role="meter"` with value text; KPI tiles are
  groups with a full label.
- Keyboard: Tab to an approval card, Enter writes the label; kill-switch
  Enter opens the dialog, focus lands in Reason, Esc cancels, Enter confirms
  only with Reason filled. Every approval and the kill-switch are operable by
  keyboard alone.
- Focus: 2 px accent outline, 2 px offset, on every interactive element.
  Never removed.
- Motion: the live-tail tint and the running pulse are the only animations;
  both stop under `prefers-reduced-motion`.
- Targets at least 24 × 24 px.
