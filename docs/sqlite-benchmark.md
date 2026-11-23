# SQLite performance as inventory history grows

YRadar keeps vehicles after they leave Tesla's inventory so it can recognize price changes and relistings. The desktop needs a different view of the same data: currently active listings in price order. This benchmark asks when a price index helps that read, and how much transaction boundaries cost when a scan records price changes.

[中文摘要](#中文摘要)

## Does the active-list index help?

We ran the desktop's `SELECT * FROM vehicles WHERE is_active = 1 ORDER BY price` on 100,000 synthetic rows. We changed the share of listings still active and compared the stock schema with a copy containing a partial index on `price` for active rows. Each cell is the median of five warm reads, including fetching every matching row into Python tuples.

| Share active | Rows returned | Stock schema | With partial index |
|---|---:|---:|---:|
| 1% | 1,000 | 17.0 ms | 7.9 ms |
| 50% | 50,000 | 244.7 ms | 408.3 ms |

The index helped when history dominated. At 50% active, it made the same query slower. In **both** scenarios, `EXPLAIN QUERY PLAN` showed the same change:

```text
Stock:   SCAN vehicles
         USE TEMP B-TREE FOR ORDER BY
Indexed: SCAN vehicles USING INDEX active_prices
```

The index supplies the desired order, but the desktop requests every column. SQLite still needs the table rows for each indexed match. For maintainers considering this index, the useful comparison is the full result set at an active share like their own. Removing the temporary sort alone did not predict the faster plan. The timed operation ends before buyer-specific filtering or Tkinter rendering.

## What does committing one VIN at a time cost?

The monitor saves a vehicle change and any corresponding notification together so a restart can recover pending alerts. We isolated one part of that work: update the price and last-seen date of the same 100 VINs by primary key, using one open connection. On a fresh copy of the 100,000-row stock database with 1% active, **100 individual transactions took 786.4 ms** and **one transaction containing all 100 updates took 25.5 ms** (five-trial medians).

This gives a reason to investigate batching if local writes start delaying scans. A larger transaction also defers visibility until commit and rolls back more work on interruption. A production change would need to keep each vehicle change and its queued alert atomic, then measure the delay until a notification becomes available. This experiment times database updates only. Both candidate changes are confined to the benchmark.

## Reproduce on your machine

From the repository root, run each scenario separately:

```sh
python -B tools/benchmark_sqlite.py --sizes 100000 --active-percent 1
python -B tools/benchmark_sqlite.py --sizes 100000 --active-percent 50
```

Set `--updates` to the number of VINs in a typical scan and `--repeats` to the desired trial count. The script creates disposable file-backed databases with the application's schema and fixed-seed synthetic data. It checks that the reads return identical ordered rows and that both write variants produce the expected VINs and prices. JSON output includes individual timings, medians, query plans, and Python and SQLite versions.

**Measurement conditions.** Windows, Python 3.13.12, SQLite 3.50.4. Stock schema and indexed reads alternate order between trials. Write variants alternate too, each starting from a fresh unindexed copy with DELETE journaling and FULL synchronous commits. Timings exclude setup, index creation and maintenance, network access, and alert delivery. The active shares are test inputs. They can be adjusted to explore different inventory histories.

An earlier five-trial run used the same 1% active workload at two history sizes. It illustrates how absolute timings can vary even on one machine:

| Inventory | Stock read | Indexed read | 100 individual commits | 100 updates in one transaction |
|---:|---:|---:|---:|---:|
| 10,000 | 0.9 ms | 0.3 ms | 780.3 ms | 12.9 ms |
| 100,000 | 38.1 ms | 13.0 ms | 850.9 ms | 29.5 ms |

## 中文摘要

买家打开桌面列表时，只需要看当前在售的车。YRadar 同时保留已下架车源，以便识别重新上架和价格变化。我们用十万条合成记录测试桌面版实际执行的查询。在售占 1% 时，为在售车辆建立价格索引后，五轮查询的中位耗时从 17.0 毫秒降至 7.9 毫秒。在售占 50% 时，同一个索引却让耗时从 244.7 毫秒升至 408.3 毫秒。两个场景都省去了临时排序，但查询仍需取回每辆车的完整记录。可以用上面的命令调整在售比例，在自己的机器上比较。

另一个实验模拟扫描时按 VIN 保存价格和最后发现时间。在十万条记录、1% 在售的数据库上，保存相同的 100 次更新，逐辆提交耗时 786.4 毫秒，整批提交耗时 25.5 毫秒。较大的事务会推迟其他连接看到更新的时间。若把它用于实时提醒，还需要验证车辆变化与待发通知能一起提交，并测量提醒实际等待了多久。以上数字只衡量本地数据库操作，实验均使用临时数据库。
