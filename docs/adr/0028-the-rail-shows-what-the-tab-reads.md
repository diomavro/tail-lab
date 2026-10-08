# 28. The rail shows only what the open tab reads

Date: 2026-10-07

## Status

**Accepted.** Dio commissioned the Put Lab redesign handoff
(`design_handoff_putlab_redesign`, 2026-10-07) and instructed that it be applied
and merged. Amends `docs/adr/0017`'s "`ParamRail` ... present on every tab";
everything else in 0017 stands.

## Context

`docs/adr/0017` made `ParamRail` the one control surface and put it on every
tab. Eight tabs later, most of them read none of it:

* **Portfolio** builds its own basket and window (`Portfolio.tsx` only seeds
  its years from the rail, once, at mount).
* **Book** reads nothing from `PutLabControls`; its plan inputs (program, hedge
  ratio, contributions, horizon) are its own.
* **Glossary** is reference text.

On those tabs the rail offered a name picker, a premium, a strike and a tenor
that changed nothing on screen. A control that moves nothing invites the reader
to believe it moved something, and it costs ~300px of width on every view,
including the Book, whose charts want it.

The handoff proposed a table of what each tab reads. It was audited against the
fetch keys in `PutLab.tsx` and the views, and two of its rows were wrong:

* It said the **Surface** "reads the asset only" and that `moneyness_pct` and
  `tenor_weeks` could be dropped from `fetchSurface`. They cannot:
  `read_surface` uses `moneyness_pct` to pick the anchor strike and
  `tenor_days` to pick the expiry. The anchor *is* the strike control
  (`docs/adr/0026`), so dropping it would have pinned the Surface to 5% / 30
  days with no way to move it.
* It said **Regime** is market-wide. Its timeline is, but its per-regime
  residuals come from `/accuracy`, whose reference program (`PPUT` or
  `PPUT3M`) is chosen by the nearest strike and tenor
  (`accuracy.select_reference`): the 10, 15 and 20% strikes pick PPUT3M at
  every tenor. The view hard-coded "PPUT" in its copy regardless, and said the
  residual "flips sign in a crisis" -- true of PPUT, false of PPUT3M, whose
  crisis residual is +2.89%/yr (`docs/MODEL_RESIDUAL.md`).

## Decision

**`ParamRail` renders only the sections the open tab reads, and a tab that
reads none gets no rail.** The map lives in `frontend/src/components/putlab/
rail.ts` (`RAIL`), with the reason for each row beside it:

| Tab | Rail sections |
|---|---|
| Workspace | universe, premium, strike, tenor, years |
| Recommendations | tenor, years |
| Bake-off | strike, tenor, years |
| Surface | universe, strike (the anchor), tenor (picks the expiry) |
| Regime | strike, tenor (pick the program the residuals replicate) |
| Portfolio, Book, Glossary | none |

There is still **one** `PutLabControls` state and one rail component. Nothing
is duplicated into a view, so a Recommendations click still opens the Workspace
at that name's strike and tenor, and a strike set on one tab is the strike on
the next.

**Regime names the program it measured and reads the sign off the numbers.**
It states which reference the residuals replicate (the payload's
`model.reference`) and says the residual flips sign only when the calm and
crisis residuals on screen actually do.

**The dateline follows the same rule.** A single name's identity and its
data-quality tag head the dateline only where the rail carries the name; the
backtest's spot, strike and rate only on the Workspace, whose read is gated off
every other tab (off-tab they would be the last Workspace run's, possibly for
another name). The Surface states its own chain session's spot, anchor and
expiry; the Book its underlying.

**Inputs that no other tab reads stay in their page.** Portfolio's basket and
the Book's plan inputs are not shared controls; moving them into the rail would
make the rail carry state that means nothing on any other tab. This is the line
between "a second control surface" (forbidden by 0017) and a page's own
question.

**When the page cannot hold the rail beside the result** (a content box under
820px: the rail's 260px, the 40px gap and main's 520px) the rail is a
full-width block above it, collapsed behind a toggle that summarises only that
tab's sections, and the tab row scrolls sideways instead of wrapping. The
handoff put this switch at 760px; between there and ~880px of window the row
had already wrapped, and a wrapped rail that was still sticky scrolled the
result underneath itself. The width is measured on the page's own box, not the
window, to the fraction of a pixel, and the return to side by side waits for
24px of spare room: a classic scrollbar appearing on the taller wide page
would otherwise toggle the layout every frame.

## Consequences

* The rail's name-specific provenance (bars, flagged, cadence) is shown only
  where the name picker is, since elsewhere it would describe a name the tab
  does not show.
* Adding a tab means adding its row to `RAIL`. The type is
  `Record<TabId, ...>`, so a tab without a row does not compile.
* A view that starts reading a shared control must add that section to its
  row in the same change, or the reader cannot see the state that drives it.
  That is the Regime trap above, and the reason each row carries its audit note.
* Recommendations carries no strike. Each row reports that name's own best
  cell over a fixed strike x tenor grid (`ranking.run_sweep`), and the
  screening roll's cycle dates depend on the tenor alone, so the strike moved
  nothing on screen while re-running the most expensive read in the app. The
  tenor stays: it decides, at the 90% coverage edge, which names cover the
  window. The handoff listed strike for this tab.
