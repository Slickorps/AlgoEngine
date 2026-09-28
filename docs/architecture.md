# AlgoEngine Architecture

## Overview

AlgoEngine is a modular, event-driven algorithmic trading platform designed for
multi-asset trading (equities, forex, futures, crypto, CFDs). It supports
backtesting, paper trading, and live broker connectivity.

## Core Components

### 1. Engine (`src/engine/`)

The central orchestrator that coordinates all components:

- **Engine**: Main class managing algorithm lifecycle (initialize → warmup → run → stop)
- **EventBus**: Central event distribution system (sync + async handlers)
- **TimeKeeper**: Time management for backtesting and live trading
- **Interfaces**: Abstract base classes for all components

### 2. Data (`src/data/`)

Market data acquisition, storage, and streaming:

- **DataFeed / StreamingDataFeed**: Base classes for data providers
- **WebSocket feed**: Real-time streaming with reconnect/backoff
- **Storage**: Historical data persistence (Parquet/CSV + SQLite metadata)
- **Aggregator / Cache**: Tick-to-bar aggregation and TTL caching

### 3. Trading (`src/trading/`)

Order lifecycle and execution:

- **OrderManager / PositionManager**: Order tracking and position accounting
- **ExecutionEngine**: Order routing and fill processing
- **LiveEngine**: Production engine with health checks, reconciliation, and circuit breaker

### 4. Portfolio (`src/portfolio/`)

Portfolio and performance analytics:

- **Portfolio**: Cash, positions, and snapshots
- **PerformanceCalculator**: Sharpe, Sortino, drawdown, VaR, trade metrics

### 5. Risk (`src/risk/`)

Risk management and pre-trade checks (position size, exposure, drawdown, concentration).

### 6. Algorithms (`src/algorithms/`)

Strategy framework, technical indicators, and a sample SMA crossover strategy.

### 7. Backtesting (`src/backtesting/`)

- **BacktestEngine**: Event-driven historical simulation
- **ParameterOptimizer**: Grid-search optimization with scoring and sensitivity analysis

### 8. Adapters (`src/adapters/`)

Broker and data-source adapters: OANDA, IG, Binance/CoinGecko (crypto),
SimulatedBroker (backtest), Yahoo Finance, Alpha Vantage.

### 9. Protocols (`src/protocols/`)

FIX 4.4 engine: message parsing/encoding, session state machine, and routing.

### 10. Plugins (`src/plugins/`)

Plugin discovery (entry points + directory scan), import sandbox, dependency
resolution, and lifecycle management.

### 11. Monitoring (`src/monitoring/`)

Latency tracking, Prometheus metrics, and alerting.

## Event Flow

```
DataFeed -> EventBus -> Algorithm
                           |
                           v
                    TradingLogic
                           |
                           v
            TransactionHandler -> Broker
                           |
                           v
                    PortfolioManager
                           |
                           v
                     RiskManager
```

## Key Interfaces (`src/engine/interfaces.py`)

### IAlgorithm
Base interface for all trading algorithms. Implementations must provide:
- `initialize()`: Setup logic
- `on_data(data)`: Handle market data
- `on_order_event(event)`: Handle order updates
- `on_position_changed(position)`: Handle position updates
- `on_warmup_finished()`: Warmup completion hook
- `on_end_of_day()`: End-of-day hook
- `terminate(message)`: Cleanup logic

### IDataFeed
Interface for market data providers:
- `connect()` / `disconnect()`: Connection lifecycle
- `subscribe(symbols)` / `unsubscribe(symbols)`: Symbol subscriptions
- `get_history(symbol, start, end, resolution)`: Historical data
- `is_connected()`: Connection status

### ITransactionHandler
Interface for order execution:
- `process_order(order)`: Submit orders
- `cancel_order(order_id)`: Cancel orders
- `update_order(order)`: Modify orders
- `get_open_orders(symbol)`: Query open orders
- `get_order_by_id(order_id)`: Query a single order

### IPortfolio
Interface for portfolio management:
- `get_cash(currency)`: Available cash
- `get_total_portfolio_value()`: Mark-to-market value
- `get_position(symbol)` / `get_all_positions()`: Current positions
- `get_unrealized_profit()` / `get_realized_profit()`: P&L
- `process_fill(fill)`: Update on fills
- `set_cash(cash, currency)`: Cash adjustment

### IResultHandler
Result and log sink: `log_message`, `debug_message`, `error_message`,
`runtime_statistic`, `order_event`, `save_results`, `set_algorithm`, `exit`.

### IExecutionModel / IRiskManager
Pluggable execution and risk models:
- `IExecutionModel.execute(portfolio, orders)`, `on_order_event(event)`
- `IRiskManager.manage_risk(portfolio, orders)`, `on_position_changed(position)`,
  `is_within_limits(portfolio)`

## Multi-Language Runtime

- **Rust** (`rust/`): High-performance market data processing
- **Go** (`monitor/`): Real-time system monitoring
- **TypeScript** (`dashboard/`): Web trading dashboard

## Configuration

Configuration is managed through YAML files and environment variables
(see `config/default.yaml` and `.env.example`):

```yaml
env: development
timezone: UTC

database:
  host: localhost
  port: 5432

logging:
  level: INFO
  dir: logs
```

Environment variables override config values:
- `ALGO_ENV`: Environment name
- `ALGO_DB_HOST`: Database host
- `ALGO_LOG_LEVEL`: Log level

## Testing

Run the test suite with pytest (slow/machine-dependent benchmarks are opt-in):

```bash
pytest tests/                 # default run (excludes slow benchmarks)
pytest tests/ -m slow         # run performance benchmarks explicitly
pytest tests/ --cov=src       # with coverage
```

## Project Status

All planned development phases are implemented:

1. **Core engine and utilities** — engine, event bus, timekeeper, config, logging
2. **Data layer** — feeds, WebSocket streaming, storage, aggregation, caching
3. **Trading and execution** — order/position management, execution engine, live engine
4. **Algorithms** — strategy framework, indicators, sample strategies
5. **Portfolio and risk** — performance analytics, risk rules
6. **Backtesting** — engine and parameter optimization
7. **Integration** — broker/data adapters, FIX protocol, plugin system
8. **Operations** — monitoring, dashboard, deployment (Docker/K8s), CI

## Next Steps

1. Increase test coverage toward the 90% target for remaining deep-link modules
   (FIX session network layer, live engine reconciliation paths).
2. Expand documentation with generated API references (Sphinx).
