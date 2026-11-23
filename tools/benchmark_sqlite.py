"""Benchmark YRadar inventory reads and VIN updates on disposable synthetic data."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import platform
import random
import shutil
import sqlite3
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database


READ = 'SELECT * FROM vehicles WHERE is_active = 1 ORDER BY price'
INDEX = 'CREATE INDEX active_prices ON vehicles(price) WHERE is_active = 1'
UPDATE = "UPDATE vehicles SET price = price - 100, last_seen = '2030-01-02' WHERE vin = ?"


def seed(path, size, active_percent):
    with patch.object(database, 'DB_PATH', str(path)):
        database.init_db()
    rng = random.Random(42)
    prices = list(range(size))
    rng.shuffle(prices)
    with closing(sqlite3.connect(path)) as db, db:
        db.executemany(
            """INSERT INTO vehicles
               (vin, model, year, trim, price, mileage, source, is_active, last_seen, hw4_reason)
               VALUES (?, ?, 2025, 'Long Range', ?, ?, 'Synthetic', ?, '2030-01-01', ?)""",
            ((f'DEMO-{i}', 'Model Y' if i % 2 else 'Model 3',
              20000 + prices[i] / 100, rng.randrange(90000), int(i % 100 < active_percent),
              'Synthetic equipment description for the benchmark') for i in range(size)))


def read_sample(path):
    with closing(sqlite3.connect(path)) as db:
        plan = [r[3] for r in db.execute('EXPLAIN QUERY PLAN ' + READ)]
        db.execute(READ).fetchall()  # Prime the cache before each timed read.
        start = perf_counter()
        rows = db.execute(READ).fetchall()
        ms = (perf_counter() - start) * 1000
    return ms, plan, rows


def write_sample(path, keys, batch):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA synchronous=FULL')
        start = perf_counter()
        for offset in range(0, len(keys), batch):
            with db:
                for vin in keys[offset:offset + batch]:
                    db.execute(UPDATE, (vin,))
        ms = (perf_counter() - start) * 1000
        changed = db.execute(
            "SELECT vin, price FROM vehicles WHERE last_seen='2030-01-02' ORDER BY vin").fetchall()
    return ms, changed


def measure(folder, size, writes, repeats, active_percent):
    base = folder / 'base.db'
    indexed = folder / 'indexed.db'
    trial = folder / 'writes.db'
    seed(base, size, active_percent)
    expected_rows = (size // 100) * active_percent + min(size % 100, active_percent)
    shutil.copyfile(base, indexed)
    with closing(sqlite3.connect(indexed)) as db:
        db.execute(INDEX)
    keys = [f'DEMO-{i}' for i in random.Random(17).sample(range(size), writes)]
    with closing(sqlite3.connect(base)) as db:
        expected = sorted((key, db.execute('SELECT price - 100 FROM vehicles WHERE vin=?',
                                           (key,)).fetchone()[0]) for key in keys)
        journal = db.execute('PRAGMA journal_mode').fetchone()[0]
    times = {k: [] for k in ('stock_read', 'indexed_read', 'single_commit', 'batch_commit')}
    plans = {}
    for rep in range(repeats):
        reads = [('stock_read', base), ('indexed_read', indexed)]
        updates = [('single_commit', 1), ('batch_commit', writes)]
        prior = None
        for name, path in (reads if rep % 2 == 0 else reads[::-1]):
            ms, plans[name], rows = read_sample(path)
            if len(rows) != expected_rows or (prior is not None and prior != rows):
                raise RuntimeError('Read variants returned different inventory')
            prior = rows
            times[name].append(ms)
        for name, batch in (updates if rep % 2 == 0 else updates[::-1]):
            shutil.copyfile(base, trial)
            ms, changed = write_sample(trial, keys, batch)
            if changed != expected:
                raise RuntimeError('Updates did not produce the expected inventory')
            times[name].append(ms)
    return dict(rows=size, active_percent=active_percent, active_rows=len(rows), updated_rows=writes, journal=journal,
                read_plans=plans, median_ms={k: round(median(v), 3) for k, v in times.items()},
                samples_ms={k: [round(t, 3) for t in v] for k, v in times.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[10000, 100000])
    parser.add_argument('--active-percent', type=int, default=1,
                        help='Active rows in each group of 100 synthetic listings (default: 1)')
    parser.add_argument('--updates', type=int, default=100)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.active_percent <= 100:
        parser.error('--active-percent must be between 1 and 100')
    if min(args.sizes) < args.updates or min(args.updates, args.repeats) < 1:
        parser.error('Require positive sizes, updates and repeats, with updates <= each size')
    results = []
    for size in args.sizes:
        with TemporaryDirectory(dir=ROOT, prefix='.sqlite-bench-') as folder:
            results.append(measure(Path(folder), size, args.updates, args.repeats, args.active_percent))
    print(json.dumps(dict(platform=platform.system(), python=platform.python_version(),
                          sqlite=sqlite3.sqlite_version, results=results), indent=2))


if __name__ == '__main__':
    main()
