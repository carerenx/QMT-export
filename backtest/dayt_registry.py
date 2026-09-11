"""Single local strategy registration seam; golden artifacts are never edited."""
STRATEGIES = {
    'v39': 'DayTradeing_v39_stragety_miniqmt.py',
    'v51': 'DayT_v51_IntradayStrength.py',
    'v52': 'DayT_v52_DirectionalOvernight.py',
}


def register_with_legacy_loader():
    from analysis import compare_v51_v39_minute as loader
    loader.FILES.update(STRATEGIES)
    return loader
