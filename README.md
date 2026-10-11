# stock_auto_left_trading

1 分钟级别的日内"左侧"交易：股票超卖后，等 MACD 金叉确认反弹再买入，涨 0.83% 就卖。
在 Robinhood 的 Agentic 账户里运行，下单由 Claude 通过 Robinhood connector 执行。

每个交易日 CT 8:20 由 Routine 新开一个会话：后台脚本用 Alpaca 免费数据每分钟算指标，只有接近买点时才叫醒 Claude，
Claude 再用 Robinhood 数据按严格规则确认后下单；CT 14:50 平仓，CT 14:53 另一个 Routine 兜底。详见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## 当前规则（2026-10-10 定）

| 项目 | 设定 |
|---|---|
| 股票 | AAOI、SNDK、ASTS |
| 买入 | MACD(12,26,9) 金叉 + RSI(14) < 35 + Stochastic(14,3,3) %K、%D 都 < 20，同一根 1 分钟 K 线 |
| 下单 | 下一分钟按卖价挂限价单，2 分钟没成交就撤单 |
| 仓位 | 每单约 $1,500（SNDK 一股就超过 $1,500，至少买 1 股），同时最多 2 个仓位 |
| 止盈 | **固定 +0.83%**，成交后马上挂限价卖单 |
| 止损 | **不设**；买入价 −3% 设手机价格提醒 |
| 收盘 | ET 15:50（CT 14:50）还没止盈的全部卖出 |
| 风控 | 当天亏 $100 停止开新仓；财报当天不交易该股票；ET 15:45 之后不开新仓 |

所有数值都在 [`config.toml`](config.toml) 里。

## 回测（固定止盈、不设止损，每单约 $1,500）

| 数据 | 交易 | 胜率 | 总盈亏 | 最大回撤 |
|---|---|---|---|---|
| 1 分钟，2026-08-17 至 09-28（30 天） | 19 | 74% | +$112 | −$21 |
| 5 分钟样本外，2026-06-01 至 08-14（53 天） | 17 | 88% | +$146 | −$21 |

Robinhood 的真实 1 分钟数据只保留约 6 周，所以样本外用的是 5 分钟数据。回测假设信号出现后下一分钟就按开盘价成交，实盘会差一些。

## 结构

```
config.toml            所有设置（账号不放这里，放 config.local.toml，已被 git 忽略）
trader/
  indicators.py        RSI、MACD、Stochastic（和 Robinhood 图表核对过）
  signals.py           买入规则（strict 在用；macd_kd、macd_rsi 只做对比）
  exits.py             止盈价、出场模拟（固定止盈；两种动态止盈只做影子记录）
  backtest.py          回测
  live/decide.py       一次决策：行情和持仓进 → 要执行的动作出
  live/alpaca.py       Alpaca 1 分钟 K 线（IEX，只做预筛）
  live/watcher.py      盯盘脚本：接近买点时输出 CANDIDATE 事件叫醒 Claude；收盘输出 EOD
scripts/               启动 watcher（start_watcher.sh），等下一个事件（next_event.sh）
  journal.py           交易日志和每日对比报告
docs/RUNBOOK.md        Claude 每分钟怎么执行
tests/                 单元测试（只用标准库）
```

## 使用

需要 Python 3.11 以上，不依赖第三方库。

```
python -m unittest discover -s tests -t .                       # 测试
python -m trader.backtest data/*.json --compare                  # 回测：固定止盈 vs 动态止盈
python -m trader.live.decide state.json                          # 一次决策
python -m trader.live.watcher --backfill 2026-10-09 2026-10-09   # 回放某几天会产生哪些 CANDIDATE（需要 Alpaca key）
python -m trader.journal report journal/DAY.jsonl data/*.json --day DAY
```

`data/*.json` 是 Robinhood `get_equity_historicals`（interval=minute）的原始返回，不提交到仓库。

## 预留但还没启用的

| 设置 | 现在 | 以后 |
|---|---|---|
| `exit.stop_loss.enabled` | `false` | 代码已支持，打开就按收盘价检查 |
| `exit.dynamic` | 只做影子记录 | 实盘攒够 20–30 笔后，再决定是否改用动态止盈（利润回落一半或 K、D > 80 时卖） |
| `entry.macd_min_cross_margin` | `0.0` | 设大于 0，过滤 MACD 刚擦过信号线的金叉 |
| 观察名单 | 3 只 | 扫描后扩充到 4–6 只、来自不同板块 |
