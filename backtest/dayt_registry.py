"""Single local strategy registration seam; golden artifacts are never edited."""
# Different runtime interface: do not inject Policy objects into the legacy runner.
RESEARCH_STRATEGIES = {
    'long_hold_factorial': {
        'entry': 'Stragety/MyPy-Q/LongHoldRotation_v4_FactorialResearch.py',
        'runner': 'backtest/long_hold_ablation.py',
        'interface': 'AblationPolicy',
        'legacy_daily_reset_compatible': False,
    },
    'profit_priority_fusion': {
        'entry': 'Stragety/MyPy-Q/LongHoldSwing_v1_ProfitPriority.py',
        'runner': 'backtest/profit_priority_research.py',
        'interface': 'Policy',
        'legacy_daily_reset_compatible': False,
    },
}

STRATEGIES = {
    'v39': 'DayTradeing_v39_stragety_miniqmt.py',
    'v51': 'DayT_v51_IntradayStrength.py',
    'v52': 'DayT_v52_DirectionalOvernight.py',
    'v39_nomom': 'DayTradeing_v39_nomom.py',
    'v39_v40_nomom': 'DayTradeing_v40_nomom_BaseRecovery.py',
    'v39_nomom_continuous_carry': 'DayTradeing_v39_nomom_ContinuousCarry.py',
    'v51_nomom': 'DayT_v51_nomom.py',
    'v52_nomom': 'DayT_v52_nomom.py',
    'v53_nomom': 'DayT_v53_nomom_CycleRiskExit.py',
    'v54_nomom': 'DayT_v54_nomom_TrendGuard.py',
    'v55_nomom': 'DayT_v55_nomom_NoOvernightMomentumGuard.py',
    'v56_nomom': 'DayT_v56_nomom_ConfirmedReversalRiskBudget.py',
}


def register_with_legacy_loader():
    from analysis import compare_v51_v39_minute as loader
    loader.FILES.update(STRATEGIES)
    return loader
