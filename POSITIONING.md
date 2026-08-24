# Product Positioning

The following statement is the foundation for all product, architecture, data model,
and analysis decisions in this repository. It is recorded verbatim and must not be
rewritten, paraphrased, or shortened.

---

Build a fashion-first urban taste intelligence system. The system continuously collects fashion, culture and lifestyle signals from the internet and transforms them into structured intelligence about emerging scenes, products, brands, creators, cities and cultural movements. The goal is not simply to summarize fashion news or detect trends. The goal is to understand how fashion signals form, who adopts them, where they emerge, what cultural systems support them, how they spread between cities, and when they become mainstream or saturated.


The key strategic decision:

Do not build a fashion trend dashboard.

Build the intelligence layer between fashion scenes and fashion markets.

That is where the gap is.

---

## Derived priorities (working interpretation — may evolve; the statement above may not)

1. **Persistent signals over weekly snapshots.** A signal is a first-class, longitudinal
   object accumulating evidence across sources and weeks — formation, adopters, geography,
   spread, saturation. Weekly reports read from the signal layer; they are not the layer.
2. **Scene-side sources are the moat; market-side sources are context.** The current
   active universe (publications, YouTube) is the media middle. Scene-proximate
   collection and source discovery outrank adding more publications.
3. **Fashion is the anchor.** Music, food, fitness, nightlife and art signals matter
   through their relationship to fashion adoption, not as co-equal verticals.
4. **Geography is data.** Cities and inter-city spread are core analytical dimensions,
   not tags.
5. **No dashboard until the intelligence layer proves itself.** Unchanged from V0.
