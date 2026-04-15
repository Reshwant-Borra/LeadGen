# Pain-point quality rubric (calibration sample)

Use this on **~20 random rows** from `out.csv` before you trust the pipeline at scale. Score each row **pass** or **fail** (or 0/1) per criterion; aim for **≥90%** passes on criteria 1–3 combined after you tune prompts or gating.

## Criteria

1. **Evidence** — Every claim in `Problem` / `Impact` / `Angle` is supported by the `Signals` JSON, the `Evidence` column (signal ids + quote), or plain reading of the homepage excerpt you would paste from the fetch. No invented tools, vendors, or internal facts.

2. **Fit** — The pain is a **conversion / intake / booking** style issue (what the six signals measure), not a generic industry complaint unless the site text clearly supports it.

3. **Actionability** — `Impact` states a plausible business consequence (lost leads, slower bookings, friction) without fake statistics.

4. **Outreach (if present)** — `Message` references something specific from the site or signals; obeys tone rules in `prompts.py` (no banned buzzwords).

## Gating for “auto-safe” sends

- Prefer rows with `Confidence` = **high** and non-empty `Evidence` where `signal_ids_used` matches **true** keys inside the `Signals` JSON.
- Rows with `needs_manual`, `INSUFFICIENT_DATA`, `skipped_llm2`, or empty `Problem`: **do not send** until reviewed.

## After scoring

If pass rate is low: widen homepage text (self-hosted Firecrawl), try a stronger free-tier model, or tighten niche/city so OSM returns sites with real `website` pages you can fetch.
