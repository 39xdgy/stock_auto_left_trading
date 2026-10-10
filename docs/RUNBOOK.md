# 运行手册（Claude 每分钟怎么做）

代码只负责"决定"：输入行情和持仓，输出这一分钟要做的动作。和 Robinhood 打交道的部分（拉数据、下单、撤单）由 Claude 通过 Robinhood connector 完成。

- 只用 **Agentic 账户**。账号只写在 `config.local.toml`（已被 git 忽略），**不提交到仓库**（这是公开仓库）。
- 时间：代码里一律用美东时间（ET）；跟你沟通时用美中时间（CT = ET − 1 小时）。
- `config.toml` 里 `run.mode = "observe"` 时只记录、不下单；改成 `"live"` 之前要先得到你的确认。

## 开盘前（ET 9:25 / CT 8:25）

1. `get_accounts`：确认 Agentic 账户可交易。
2. `get_earnings_calendar`（观察名单里的股票）：当天有财报的股票写进 `earnings_today`。
3. `get_portfolio`：记下 `buying_power`。
4. `get_equity_positions` 和 `get_equity_orders`：确认开盘前没有遗留仓位或挂单（正常情况下前一天已经全部平仓）。

## 交易时间每分钟（ET 9:31 – 15:50）

1. **拉数据**：`get_equity_historicals`，`symbols` = 观察名单（一次最多 10 只），`interval = "minute"`，`start_time` = 前一个交易日 ET 9:30（MACD 和 RSI 需要预热），`bounds = "regular"`。
2. **报价**：`get_equity_quotes`，取每只股票的 `ask_price`。
3. **持仓和挂单**：`get_equity_positions`（数量、`average_buy_price`）、`get_equity_orders`（只看还没成交的单子）。
4. **当天已实现盈亏**：从 `journal/` 里当天的成交记录算出来。
5. 把以上内容写成 `state.json`（格式见 `trader/live/decide.py` 顶部的说明），然后运行：
   ```
   python -m trader.live.decide state.json
   ```
6. **执行输出的动作**（observe 模式下只记录到 journal，不执行）：

| 动作 | Robinhood 工具 | 参数 |
|---|---|---|
| `buy_limit` | 先 `review_equity_order`，再 `place_equity_order` | side=buy, type=limit, limit_price, quantity, time_in_force=gfd, market_hours=regular_hours, 新的 ref_id |
| `place_take_profit` | `place_equity_order` | side=sell, type=limit, limit_price, quantity, time_in_force=gfd |
| `create_price_alert` | `create_alert` | condition_type=price_below, threshold |
| `cancel_order` | `cancel_equity_order` | order_id |
| `sell_market` | `place_equity_order` | side=sell, type=market, quantity |

7. **记录**：每个信号（`signals` 里的每一条）都追加一行 `{"kind": "signal", ...}` 到 `journal/YYYY-MM-DD.jsonl`；每次成交追加 `{"kind": "fill", ...}`；你实时报给我的买点追加 `{"kind": "user_call", ...}`。

## 收盘后

```
python -m trader.journal report journal/2026-10-12.jsonl data/2026-10-12/*.json --day 2026-10-12
```

报告列出每个规则信号和你的每个买点，在固定止盈（实际执行的）和两种动态止盈（只记录）下分别能赚多少，以及当天的实际成交。

## 安全规则

- 只在 Agentic 账户下单，不碰主账户和 Roth IRA；不使用需要账号密码的非官方库。
- `decide` 的输出如果看起来不对（比如一次要买超过 `max_positions` 只、价格离报价很远），先停下来问你。
- 会话中断后重新开始：先用 `get_equity_positions` 和 `get_equity_orders` 重建状态，不能只信自己的记录。
- ET 15:50 平仓这一步只能靠 Claude 执行。如果会话断了，没止盈的仓位会留到隔夜，需要你在 App 里手动处理。
