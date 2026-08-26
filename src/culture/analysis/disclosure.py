"""The disclosure rule every reader-facing generation prompt shares.

One constant, imported everywhere descriptive text is generated, so the rule
cannot drift between prompts. The Sabukaru leak (signal 2, Aug 2026) happened
because this rule lived only in the public-brief prompt while signal
descriptions — which render on the public homepage — were generated without
it.

The internal weekly report deliberately does NOT use this: operators need
source names for validation, and that report stays behind the login. The rule
applies to text that is, or may become, reader-facing: signal names and
descriptions, archetype observations, item interpretations, and public essays.
"""

DISCLOSURE_RULES = """\
Non-negotiable disclosure rules — the source network is proprietary:
- NEVER name a monitored source, publication, creator, account, podcast, or
  newsletter in any name, description, or narrative text you write. Use
  generic descriptors instead: "a Tokyo subculture publication", "a menswear
  creator with a large YouTube audience", "several independent street-style
  accounts".
- Names of brands, artists, designers, cities, venues, events, and products
  that the CULTURE is about are fine — it is the observers that stay
  anonymous, not the observed.
"""
