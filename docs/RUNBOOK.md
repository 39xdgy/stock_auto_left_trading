# 运行手册

代码只负责"决定"：输入行情和持仓，输出要做的动作。和 Robinhood 打交道的部分（拉数据、下单、撤单）由 Claude 通过 Robinhood connector 完成。

- 只用 **Agentic 账户**（尾号 3271）。账号不写进仓库（这是公开仓库）。
- 时间：代码里一律用美东时间（ET）；跟用户沟通时用美中时间（CT = ET − 1 小时）。
- `config.toml` 里 `run.mode = "observe"` 时只记录、不下单。

## 架构（2026-10-11 起）

为了省用量，Claude 不再每分钟自己看盘：

```
CT 8:20  Routine「每日交易」新开一个会话 ──> 开盘前检查 ──> 启动 watcher（后台进程）
                                                          │ 每分钟用 Alpaca IEX 数据算指标（不花 Claude 用量）
                                                          ▼
                       CANDIDATE（宽松条件命中） ──> 唤醒 Claude ──> 用 Robinhood 数据跑 decide（严格规则）
                                                                     ├─ 不成立：记一笔，继续等
                                                                     └─ 成立：限价买入 → 成交后挂 +0.83% 止盈
                       EOD（ET 15:50） ──> 唤醒 Claude ──> 撤单、市价卖出剩余仓位、日报
CT 14:53 Routine「收盘保护」另开一个会话：如果还有仓位或卖单，撤单并市价卖出（兜底）
```

Alpaca 的免费 IEX 数据只占市场成交的一小部分，和 Robinhood 的数据不完全一样，所以只用来"叫醒"，买不买永远以 Robinhood 数据上的严格规则为准。

## 每日会话

### 1. 准备（CT 8:20 左右）

1. `git clone https://github.com/39xdgy/stock_auto_left_trading && cd stock_auto_left_trading`，然后 `python3 -m unittest discover -s tests -t .` 必须全部通过。
2. 确认环境变量 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 存在（只检查是否存在，**不要打印值**）。
3. `get_accounts`：找到尾号 3271、`agentic_allowed=true` 的账户。之后所有下单、查询都带这个账户。
4. `get_earnings_calendar`（观察名单）：当天有财报的股票记为 `earnings_today`。
5. `get_portfolio`：记下 `buying_power`。
6. `get_equity_positions`、`get_equity_orders`：开盘前应该没有仓位和挂单；如果有，先报告给用户，不要自己处理隔夜仓位以外的东西（隔夜仓位按 decide 的规则挂止盈）。
7. `python3 -m trader.live.watcher --once`：确认 Alpaca 能取到数据（退出码 0）。
8. `scripts/start_watcher.sh`（开盘前启动也可以：ET 9:31 之前不会发出 CANDIDATE）。

### 2. 等事件

用 Bash（`run_in_background: true`，`timeout` = 7200000）运行 `scripts/next_event.sh N`。`N` 是 `journal/events.jsonl` 里已经处理过的行数（开始时是 0）。脚本在有新事件时打印事件并退出，退出会唤醒 Claude；处理完把 `N` 更新为最后处理的 `seq`，再运行一次。

- `IDLE`（90 分钟没事件）：看一眼里面的 `status.updated` 在 3 分钟以内，然后直接再运行一次。只做这些，不要多说话。

### 3. CANDIDATE

大多数 CANDIDATE 不会交易，所以先用最省的方式看一眼：

1. `get_equity_historicals`：观察名单，`interval = "minute"`，`start_time` = 前一个交易日 ET 9:30，`bounds = "regular"`。结果很大时会存成文件：不要把内容读进对话，用 `--historicals 文件路径` 交给 decide（顺便复制一份到 `data/YYYY-MM-DD/` 留给日报）。
2. 先只用行情跑一次 decide：`state.json` 里只放 `handled_signals`（取自 `journal/handled.json`）、`realized_pnl_today`、`earnings_today`，运行 `python3 -m trader.live.decide state.json --historicals 文件路径 --signals-only`（`now` 默认是当前时间；这一步不输出任何动作）。
   - `signals` 为空：看 `forming`（见第 7 步），否则回去等事件。不要多说话。
3. 有信号时才补齐：`get_equity_quotes`、`get_equity_positions`、`get_equity_orders`（未成交的）、`get_advanced_orders`（未完成的 OCO，写成 `oco_orders`，包括每个 OCO 两条腿的订单号 `leg_ids`）、`buying_power`，写进 `state.json`，再跑一次 decide（去掉 `--signals-only`）。如果今天已经确定 OCO 用不了，加上 `"oco_unavailable": true`。
4. 把输出里的每个 `signals` 记进 journal，并把 `"SYMBOL@bar"` 加进 `journal/handled.json`（不管买没买，同一个信号只处理一次）。
5. 立刻执行 `actions`（见下表），不要再去拉别的数据：信号是按分钟算的，晚一分钟就可能过了入场时间或价格。没有动作就回去等事件。
6. 下了买单之后：`place_equity_order` 返回的状态是 `unconfirmed`、`queued` 或 `confirmed` 都是正常的过渡状态（订单已提交，交易所还没回报），**不是**要用户确认，不要停下来问。Agentic 账户的人工审批是关闭的（可以用 `get_trade_approval_setting` 核对）。接着用 Bash `run_in_background` 跑 `sleep 60`，醒来后重复第 1–5 步（这时直接做完整的第 3 步）（decide 会在成交后给出止盈单和价格提醒，超过 2 分钟没成交会给出撤单）。直到没有未成交的买单、每个仓位都有止盈单，再回去等事件。
7. 没有买入、但 decide 输出的 `forming` 不为空（超卖、MACD 正在靠近信号线）：等到下一分钟的第 20 秒左右（Bash `run_in_background` 跑 `sleep`），再做一次第 1–5 步。每个 CANDIDATE 最多再查 2 次（IEX 的金叉常常比 Robinhood 早或晚一分钟）。之后回去等事件。

| 动作 | Robinhood 工具 | 参数 |
|---|---|---|
| `buy_limit` | 先 `review_equity_order`，再 `place_equity_order` | side=buy, type=limit, limit_price, quantity, time_in_force=gfd, market_hours=regular_hours, 新的 ref_id |
| `place_oco` | 先 `review_advanced_order`，再 `place_advanced_order` | side=sell, quantity, take_profit_limit_price, stop_loss_stop_price, time_in_force=gfd, market_hours=regular_hours, 新的 ref_id。止盈和止损是同一个 OCO 单：一边成交，Robinhood 自动撤掉另一边 |
| `cancel_oco` | `cancel_advanced_order` | order_id（OCO 的 id，两条腿一起撤） |
| `place_take_profit` | `place_equity_order` | side=sell, type=limit, limit_price, quantity, time_in_force=gfd |
| `set_watch_stop` | 写文件 | 把 `{symbol: stop_price}` 合并进 `journal/stops.json`，watcher 每分钟检查，跌破就发 `STOP` 事件 |
| `create_price_alert` | `create_alert` | condition_type=price_below, threshold |
| `cancel_order` | `cancel_equity_order` | order_id |
| `sell_market` | `place_equity_order` | side=sell, type=market, quantity |

**OCO 用不了时（备用方案）**：`review_advanced_order` 返回 `customer_approval_required: true`（需要你在 App 里批准，而没人会批准），或者 OCO 被拒绝（比如价格已经离止盈或止损不到 0.25%），就**不要**下这个 OCO。当天之后的 state 都加上 `"oco_unavailable": true` 再跑 decide：它会改为挂普通止盈限价单，加上 `set_watch_stop`；watcher 发 `STOP` 事件时，重建 state 跑 decide，它会撤掉止盈单并市价卖出。写进日报。

**`STOP` 事件**：和 CANDIDATE 一样补齐完整 state（第 3 步），跑 decide，立刻执行。

### 4. EOD（ET 15:50 / CT 14:50）

1. 重建 state（包括 `get_advanced_orders`），跑 decide（这时它只会输出撤单、撤 OCO 和市价卖出），执行。
2. 再查一次 `get_equity_positions`，确认 Agentic 账户里观察名单的股票都是 0 股。
3. 日报（写在最后的回复里，不推到仓库：这是公开仓库，交易记录不放上去）：当天的信号（包括没交易的和原因）、成交（用 `get_equity_orders` 核对价格和时间）、已实现盈亏、CANDIDATE 次数、遇到的问题。不需要跑 `trader.journal report`：Robinhood 保留约 6 周的 1 分钟数据，动态止盈的对比之后统一从 Robinhood 重新拉数据计算。

### 5. 出问题

- `ERROR`：Alpaca 连续失败。等 `RECOVERED`；期间仓位有止盈单保护，只是可能漏掉买点。
- `FATAL` 或 `WATCHER_EXITED`（不是在 EOD / NO_DATA 之后）：看 `journal/watcher.err`，重新 `scripts/start_watcher.sh` 一次。再失败就停止开新仓，告诉用户，并且仍然要在 ET 15:50 执行 EOD（用 Bash `run_in_background` 跑一个睡到 15:50 的命令来叫醒自己）。
- `NO_DATA`：今天休市，结束。
- 会话中断：CT 14:53 的「收盘保护」会兜底平仓。

## 安全规则

- **在 EOD 处理完之前，绝不能结束回合、停下来等用户回复。** 这个会话没有人在看，结束回合就等于停止交易：止盈单挂不上，15:50 也不会平仓（2026-10-14 就发生过）。任何时候结束一步，都必须已经有一个 Bash `run_in_background` 在跑（`next_event.sh` 或 `sleep`）来叫醒自己。不确定的事情记下来写进日报，继续按手册走；需要停止开新仓时也照样要等到 EOD 平仓。
- 只有两种情况可以提前结束：`NO_DATA`（休市），或者没有 Robinhood 工具。
- 只在 Agentic 账户下单，不碰主账户和 Roth IRA；不使用需要账号密码的非官方库。
- 不打印、不提交任何密钥；账号不进仓库。
- `decide` 的输出如果看起来不对（比如一次要买超过 `max_positions` 只、价格离报价很远），不执行，告诉用户。
- 会话重新开始时：先用 `get_equity_positions` 和 `get_equity_orders` 重建状态，不能只信自己的记录。
