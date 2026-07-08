"""Unit tests for the BrandNameFilter that renders logger names as lineproof.*"""

import logging

from netmon.utils import BrandNameFilter


def record(name):
    return logging.LogRecord(name, logging.INFO, __file__, 1, "msg", None, None)


def test_package_loggers_rebranded():
    r = record("netmon.pinger")
    BrandNameFilter().filter(r)
    assert r.name == "lineproof.pinger"


def test_bare_package_and_main_rebranded():
    r = record("netmon")
    BrandNameFilter().filter(r)
    assert r.name == "lineproof"

    r = record("__main__")
    BrandNameFilter().filter(r)
    assert r.name == "lineproof.main"


def test_third_party_loggers_untouched():
    r = record("apscheduler.scheduler")
    BrandNameFilter().filter(r)
    assert r.name == "apscheduler.scheduler"
    # No false prefix match on similarly named loggers either.
    r = record("netmonitor.other")
    BrandNameFilter().filter(r)
    assert r.name == "netmonitor.other"


def test_idempotent_across_both_handlers():
    r = record("netmon.pinger")
    f = BrandNameFilter()
    f.filter(r)
    f.filter(r)  # second handler sees the already-rewritten record
    assert r.name == "lineproof.pinger"


def test_never_drops_records():
    assert BrandNameFilter().filter(record("netmon.pinger")) is True
    assert BrandNameFilter().filter(record("anything.else")) is True
