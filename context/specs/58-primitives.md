# Unit 58: Primitive components

## Goal

`Chip`, `KPITile`, `Meter` and `ScoreBreakdown` ported from the design
exports with every state `ui-context.md` names; vitest plus axe (PRD
§Capability 6; TRD §5.2; ui-context.md · Component Library, Colors,
Accessibility).

## Design

Terminal only. Four client components under `console/components/`, each
with a CSS Module that reads only `var(--*)` tokens. `Chip` has one anatomy
for every enumerated value: a glyph, the PRD value in mono, a 9% tinted
ground and a 1 px border of the same semantic role, `role="img"` with
"Task state: blocked"-style labels; its tables map status, risk, gate,
verdict, severity, surface and label values to roles and glyphs exactly as
the UI context's colour and glyph tables say (`Blocker` solid, `skipped`
and `Unverifiable` dashed, risk as `▮▯▯ ▮▮▯ ▮▮▮`, label chips `⌗` with a
45% border, disabled at 45% opacity; an unknown value is neutral without a
glyph). `Meter` is `role="meter"` with "$46.10 of $54.00, 85 percent" as
its value text; tone and glyph move together (`●` under 80%, `▲` 80 to
99%, `■` at the cap), a 10 px bar with the 80% tick, loading and error
states that read "… / …" and "— / —". `KPITile` is a framed `role="group"`
with a full label: mono-caps label, 40 px value with unit (`—` when not
readable), seven trend bars (last accent, others 22% ink), a status line
(`on`, `off`, `nodata`, `loading`, `error`) and the target text. `ScoreBreakdown`
is a framed card with the instrument version, base × factor rows with their
rule text, the equation in a mono box, the outcome `Chip` and conclusion,
the override login and reason, and the tier U reading "Scope names no
paths. Score 0." The tests render every state, assert the spoken labels
and run axe (colour contrast disabled under jsdom, which computes no
colours) with zero violations.

## Implementation

- `console/components/{Chip,KPITile,Meter,ScoreBreakdown}.tsx` with their
  `.module.css`; `console/components/primitives.test.tsx`.

## Dependencies

- Unit 56. `axe-core` and `@testing-library/react` (already dev dependencies).

## Verify when done

- [ ] Each component renders every state named in `ui-context.md`
- [ ] axe reports 0 violations; chips expose "Task state: blocked"; meters
      speak their value; tiles are groups with a full label
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass
