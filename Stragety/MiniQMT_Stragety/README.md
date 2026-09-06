# MiniQMT strategy conventions

## Layout and launch paths

| Directory | Contents |
|---|---|
| `DayT/` | Intraday DayTrading versions and their `infra/` connector package. |
| `MA5MA20/` | CSI 500 MA5/MA20 screeners, overnight strategies, and their tests. |
| `RSIDayTrade/` | RSI intraday strategies. |
| `core/` | Shared configuration and signal calculations for MiniQMT strategies. |

Run a DayTrading strategy directly from the repository root with its category
directory in the path:

```powershell
python Stragety\MiniQMT_Stragety\DayT\DayTradeing_v41_stragety_miniqmt.py --mode signal
```

For the Big QMT bridge, the default is v41 in `DayT/`; another version can be
selected explicitly:

```powershell
python run_bigqmt.py --strategy Stragety\MiniQMT_Stragety\DayT\DayTradeing_v40_stragety_miniqmt.py --mode signal
```

## Encoding

- New or substantially rebuilt MiniQMT strategy files must be UTF-8 without a BOM and declare `# -*- coding: utf-8 -*-` on the first line.
- Existing GBK strategies remain GBK until they are materially changed; do not perform repository-wide encoding-only conversions.
- All tools, tests, and editors must read a strategy according to its declared encoding. UTF-8 is the default for new work because it is reliably supported by Python 3 and code-review tooling.

## Strategy independence

- A new version is a complete strategy file. It may import shared `core`, `infra`, or utility modules only.
- Do not import, subclass, or use wildcard imports from an earlier versioned strategy file.
- A parameter-only or mechanism-only optimization may be made in its existing strategy file.
