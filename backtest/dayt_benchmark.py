"""Versioned, offline DayT benchmark. Never overwrites an existing golden archive."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'backtest/dayt_golden_20260910'
EXPECTED = {'v51': {'excess': -14042., 'trades': 366},
            'v39': {'excess': -11531., 'trades': 392}}


def digest(data): return hashlib.sha256(data).hexdigest()


def freeze():
    if GOLDEN.exists():
        raise RuntimeError('Golden already exists; verify it, never overwrite it')
    previous = json.loads((ROOT/'analysis/v51_v39_minute/results.json').read_text(encoding='utf-8'))
    names = {'v39':'DayTradeing_v39_stragety_miniqmt.py', 'v51':'DayT_v51_IntradayStrength.py'}
    paths = [ROOT/'Stragety/MiniQMT_Stragety/DayT'/name for name in names.values()]
    for version, name in names.items():
        assert digest((ROOT/'Stragety/MiniQMT_Stragety/DayT'/name).read_bytes()) == previous['hashes'][version], version
    paths += list((ROOT/'Stragety/MiniQMT_Stragety/core').rglob('*.py'))
    paths += list((ROOT/'Stragety/MiniQMT_Stragety/DayT/infra').rglob('*.py'))
    paths += [ROOT/'analysis/compare_v51_v39_minute.py']
    paths += [ROOT/'analysis/v51_v39_minute'/name for name in ('1m.csv','1d.csv','results.json')]
    files = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in paths}
    manifest = dict(schema=1, expected=EXPECTED, sessions=99,
                    files={name:digest(content) for name,content in files.items()},
                    python=sys.version, parameters=dict(cash=100000,shares=200,slips=[0,.01],reset='daily'))
    GOLDEN.mkdir()
    with zipfile.ZipFile(GOLDEN/'snapshot.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items(): archive.writestr(name,content)
    (GOLDEN/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Frozen',len(files),'files; not yet replay-verified')


def verify(replay=False):
    manifest=json.loads((GOLDEN/'manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(GOLDEN/'snapshot.zip') as archive:
        assert set(archive.namelist()) == set(manifest['files']), 'archive file inventory mismatch'
        for name,expected in manifest['files'].items():
            assert digest(archive.read(name)) == expected, 'hash mismatch: '+name
        if not replay:
            print('GOLDEN HASH PASS'); return
        with tempfile.TemporaryDirectory(prefix='dayt_golden_') as temporary:
            target=Path(temporary)
            for name in archive.namelist():
                if not (target/name).resolve().is_relative_to(target.resolve()):
                    raise RuntimeError('unsafe archive path')
            archive.extractall(target)
            result=subprocess.run([sys.executable,str(target/'analysis/compare_v51_v39_minute.py')],
                                  cwd=target,check=True,capture_output=True,text=True,encoding='utf-8',errors='replace')
            payload=json.loads((target/'analysis/v51_v39_minute/results.json').read_text(encoding='utf-8'))
            rows=payload['results']
            assert len(rows)==396 and not any(r['failure'] for r in rows), result.stdout[-4000:]
            for version,expected in EXPECTED.items():
                group=[r for r in rows if r['version']==version and r['slip']==0]
                assert len(group)==99
                assert abs(sum(r['excess_gross'] for r in group)-expected['excess']) < .001, version+' gross mismatch'
                assert sum(len(r['trades']) for r in group)==expected['trades'], version+' trade mismatch'
            original=json.loads(archive.read('analysis/v51_v39_minute/results.json'))['results']
            for before,after in zip(original,rows):
                assert before['date']==after['date'] and before['version']==after['version']
                for field in ('trades','excess_gross','ledger_unclosed','cycle_gross'):
                    assert before[field]==after[field], (after['date'],after['version'],field)
            print('GOLDEN REPLAY PASS: 396 independent-day runs, exact trades and P&L')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','verify','replay'))
    args=parser.parse_args()
    if args.action=='freeze': freeze()
    else: verify(args.action=='replay')
