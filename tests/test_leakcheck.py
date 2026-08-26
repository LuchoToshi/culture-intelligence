from culture.utils.leakcheck import compile_patterns, find_leaks


def patterns(*names: str):
    return compile_patterns(list(names))


def test_plain_name_matches_case_insensitively():
    pats = patterns("Sabukaru")
    assert find_leaks("as seen in SABUKARU this week", pats) == ["Sabukaru"]


def test_short_names_are_no_longer_blind_spots():
    # The old len > 3 filter could never catch these two real sources.
    pats = patterns("NTS", "i-D")
    assert find_leaks("the NTS resident said", pats) == ["NTS"]
    assert find_leaks("an i-D cover story", pats) == ["i-D"]


def test_word_boundary_prevents_substring_hits():
    pats = patterns("NTS", "i-D")
    assert find_leaks("in quiet moments the scene shifts", pats) == []
    assert find_leaks("hi-definition footage of the show", pats) == []


def test_hyphen_adjacent_text_does_not_match():
    pats = patterns("NTS")
    assert find_leaks("the NTS-adjacent scene", pats) == []  # compound, not the name


def test_multiple_names_reported_once_each_sorted():
    pats = patterns("Dazed", "Hypebeast")
    text = "Hypebeast and Dazed and Hypebeast again"
    assert find_leaks(text, pats) == ["Dazed", "Hypebeast"]


def test_names_below_minimum_length_are_skipped():
    pats = patterns("iD", "x")
    assert pats == {}
