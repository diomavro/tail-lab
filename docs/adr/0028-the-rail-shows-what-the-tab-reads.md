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
* It said **Regime** is market-wide. Its per-regime residuals come from
  `/accuracy`, whose reference program (`PPUT` or `PPUT3M`) is chosen by the
  nearest strike and tenor. The view hard-coded "PPUT" in its copy regardless.

## Decision

**`ParamRail` renders only the sections the open tab reads, and a tab that
reads none gets no rail.** The map lives in `frontend/src/components/putlab/
rail.ts` (`RAIL`), with the reason for each row beside it:

| Tab | Rail sections |
|---|---|
| Workspace | universe, premium, strike, tenor, years |
| Recommendations | strike, tenor, years |
| Bake-off | strike, tenor, years |
| Surface | universe, strike (the anchor), tenor (picks the expiry) |
| Portfolio, Book, Regime, Glossary | none |

There is still **one** `PutLabControls` state and one rail component. Nothing
is duplicated into a view, so a Recommendations click still opens the Workspace
at that name's strike and tenor, and a strike set on one tab is the strike on
the next.

**Regime names the program it measured.** Without a rail it states which
reference the residuals replicate (from the payload's `model.reference`) and
that it is the program nearest the strike and tenor set on the Workspace,
instead of claiming PPUT.

**Inputs that no other tab reads stay in their page.** Portfolio's basket and
the Book's plan inputs are not shared controls; moving them into the rail would
make the rail carry state that means nothing on any other tab. This is the line
between "a second control surface" (forbidden by 0017) and a page's own
question.

**Below 760px of container width** the rail is a full-width block collapsed
behind a toggle that summarises only that tab's sections, and the tab row
scrolls sideways instead of wrapping. The width is measured on the page's own
box, not the window.

## Consequences

* The rail's name-specific provenance (bars, flagged, cadence) is shown only
  where the name picker is, since elsewhere it would describe a name the tab
  does not show.
* Adding a tab means adding its row to `RAIL`. The type is
  `Record<TabId, ...>`, so a tab without a row does not compile.
* A view that starts reading a shared control must add that section to its
  row in the same change, or the reader cannot see the state that drives it.
  That is the Regime trap above, and the reason each row carries its audit note.
