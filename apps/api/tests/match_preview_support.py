"""The hand-computed expectation the match-preview odds tests need.

Everything here is invented: the prices the tests store, the bookmakers and the
market are made up, nothing is downloaded or ingested, and no source is
contacted. The module only covers what the preview tests need on top of the
rows :mod:`tests.factories`, :mod:`tests.live_support` and
:mod:`tests.live_metrics_support` already build, so those helpers are reused
rather than duplicated.

The one expectation that cannot be read off a stored column is the
margin-removed probability of a price. It is spelled out here with plain
arithmetic instead of calling :func:`lmi_core.modeling.market.remove_margin`,
because that function is what the endpoint calls: comparing the payload against
it would prove the endpoint agrees with itself. Comparing it against the
definition of the proportional method proves the payload is right.
"""


def implied_probabilities(home: float, draw: float, away: float) -> dict[str, float]:
    """Return the proportional margin-removed probabilities of three decimal prices.

    The inverse of each price is divided by the sum of the three inverses, so the
    returned values sum to one. The arithmetic is the test's own and is
    deliberately not factored out of the endpoint's helper: a decimal price of
    ``2.00`` implies ``0.5`` before the margin is removed, which is the number a
    reader can check with a pencil.

    Args:
        home: the decimal price of a home win.
        draw: the decimal price of a draw.
        away: the decimal price of an away win.

    Returns:
        The three probabilities keyed the way the payload keys them, so a test
        compares one dictionary against the served object.
    """
    inverses = [1.0 / home, 1.0 / draw, 1.0 / away]
    total = sum(inverses)
    return {
        "home": inverses[0] / total,
        "draw": inverses[1] / total,
        "away": inverses[2] / total,
    }
