"""Run an existing MiniQMT strategy through this project's Big QMT shim."""

import argparse
import importlib.util
import os
from pathlib import Path
import runpy
import sys


DEFAULT_DAYTRADING_V40 = (Path(__file__).resolve().parents[4] / "Stragety" / "MiniQMT_Stragety"
    / "DayTradeing_v40_stragety_miniqmt.py")


class ExternalStrategyLaunchError(RuntimeError):
    pass


def _load_strategy_account(strategy_file):
    config_file = strategy_file.parent / "core" / "config.py"
    if not config_file.is_file():
        raise ExternalStrategyLaunchError(
            "strategy dependency is missing: %s" % config_file
        )
    spec = importlib.util.spec_from_file_location(
        "_bigqmt_external_strategy_config", str(config_file)
    )
    if spec is None or spec.loader is None:
        raise ExternalStrategyLaunchError("cannot load strategy config: %s" % config_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    account_id = str(getattr(module, "ACCOUNT", "") or "").strip()
    if not account_id:
        raise ExternalStrategyLaunchError("core.config.ACCOUNT is empty")
    return account_id


def _assert_project_xtquant_shim():
    import xtquant

    actual = Path(xtquant.__file__).resolve()
    expected_dir = Path(__file__).resolve().parents[1] / "xtquant"
    if actual.parent != expected_dir.resolve():
        raise ExternalStrategyLaunchError(
            "real MiniQMT xtquant was imported instead of this project's shim: %s" % actual
        )


def launch_strategy(strategy, strategy_args=None):
    """Launch ``strategy`` with its account routed to the Big QMT bridge."""
    strategy_file = Path(strategy).expanduser().resolve()
    if not strategy_file.is_file():
        raise ExternalStrategyLaunchError("strategy file does not exist: %s" % strategy_file)

    strategy_root = str(strategy_file.parent)
    account_id = _load_strategy_account(strategy_file)
    if strategy_root not in sys.path:
        sys.path.insert(0, strategy_root)

    # Reconfigure after reading the strategy account. This intentionally wins
    # over a stale account id in the client config while retaining its transport
    # and credentials.
    from .xtquant_compat import configure

    trader, _xtdata = configure(account_id=account_id)
    if str(trader.client.account_id or "") != account_id:
        raise ExternalStrategyLaunchError(
            "bridge account %s does not match strategy account %s"
            % (trader.client.account_id, account_id)
        )
    _assert_project_xtquant_shim()

    old_argv = sys.argv[:]
    try:
        sys.argv = [str(strategy_file)] + list(strategy_args or [])
        try:
            return runpy.run_path(str(strategy_file), run_name="__main__")
        except ModuleNotFoundError as exc:
            raise ExternalStrategyLaunchError(
                "strategy dependency %r is not installed in this Python environment"
                % (exc.name or str(exc))
            ) from exc
    finally:
        sys.argv = old_argv


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a MiniQMT strategy against the Big QMT RPC bridge."
    )
    parser.add_argument(
        "--strategy",
        default=os.environ.get("BIGQMT_EXTERNAL_STRATEGY", str(DEFAULT_DAYTRADING_V40)),
        help="path to the MiniQMT strategy; remaining arguments are forwarded",
    )
    args, forwarded = parser.parse_known_args(argv)
    try:
        launch_strategy(args.strategy, forwarded)
    except ExternalStrategyLaunchError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
