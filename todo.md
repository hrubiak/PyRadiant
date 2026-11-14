# To do

## Seed fits from the previous good fit

Today every Planck fit starts from fixed values: `curve_fit(..., p0=[2500, 1e-11])`
in `fit_black_body_function` (`pyradiant/model/TemperatureModelConfiguration.py`).
The fixed scaling guess can be orders of magnitude off when exposure time or gain
changes, which may cause failed fits on spectra that look fittable.

Proposal:

- Keep, per side, the last *good* fit's temperature and its scaling / (exposure × gain).
- Seed the next fit with that temperature and that rate × the new frame's exposure × gain.
- Fall back to the fixed `p0` when there's no previous good fit (e.g. after a folder change).
- A toggle, off by default, so existing results don't change unless opted in. Batches
  record whether seeding was used.
- Wien fits are linear (no starting values) and are unaffected.

Caveat: seeding makes a fit depend on what was fitted before it. A batch runs frames in
order, but the live session follows browsing order, so marginal spectra could fit
differently live vs in a batch. Converged fits land on the same answer either way.

Before building it: run a batch on a real folder and check whether failed fits are on
spectra that look fittable — that tells whether seeding is worth it.
