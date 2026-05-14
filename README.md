# AlgoEngine

An open-source algorithmic trading platform supporting multi-asset CFD trading including forex, indices, stocks, crypto, and commodities.

## Features

- **Multi-Asset Support**: Trade forex, indices, stocks, crypto, and commodities via CFDs
- **Backtesting Engine**: Historical data backtesting with performance optimization
- **Live Trading**: Real-time trading with multiple brokerage adapters
- **Risk Management**: Comprehensive risk controls and monitoring
- **Event-Driven Architecture**: Asynchronous, event-based processing
- **Modular Design**: Plugin system for custom strategies and indicators
- **Multi-Language Runtime**:
  - **Rust** — High-performance market data processing (OHLCV aggregation, statistical analysis)
  - **Go** — Real-time system monitoring and latency tracking
  - **TypeScript** — Web-based trading dashboard
  - **C++** — Technical indicator library (optional)

## Quick Start

### Local Development

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt

# Install package in editable mode
pip install -e .

# Run tests
pytest

# Start the engine (paper mode)
python -m cli.main
```

### One-Command Setup

```bash
# Full environment setup via Makefile
make setup
# or
./scripts/setup_env.sh --full
```

### Docker Deployment

```bash
# Build and start all services
make docker-up
# or
docker compose up -d

# Follow logs
make docker-logs

# Stop services
make docker-down
```

## Project Structure

```
algoengine/
├── src/                  # Python — Core trading engine
│   ├── engine/           #   Core engine & event bus
│   ├── data/             #   Data processing & storage
│   ├── trading/          #   Trade execution & live engine
│   ├── portfolio/        #   Portfolio management
│   ├── risk/             #   Risk management
│   ├── algorithms/       #   Algorithm framework
│   ├── adapters/         #   Broker adapters (OANDA, etc.)
│   ├── monitoring/       #   Latency & health monitoring
│   └── utils/            #   Logging, config, helpers
├── rust/                 # Rust — High-perf data processor
│   ├── Cargo.toml
│   └── src/main.rs       #   OHLCV aggregator, stats calculator
├── monitor/              # Go — System monitoring agent
│   ├── go.mod
│   ├── main.go           #   Health check, latency metrics
│   └── Dockerfile
├── dashboard/            # TypeScript — Web dashboard
│   ├── src/
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
├── scripts/              # Shell — Deployment & management
│   ├── setup_env.sh      #   Environment setup
│   ├── start_live.sh     #   Start live trading
│   ├── stop_live.sh      #   Stop live trading
│   └── deploy.sh         #   Docker deployment
├── cli/                  # Python — CLI interface
├── tests/                # Python — Test suite
├── docs/                 # Documentation
├── examples/             # Example strategies
├── config/               # YAML configuration files
├── Dockerfile            # Python engine container
├── docker-compose.yml    # Multi-service orchestration
├── Makefile              # Build automation
└── data/                 # Data storage directory
```

## Build Targets

The project includes a comprehensive `Makefile` with the following commands:

| Command           | Description                          |
|-------------------|--------------------------------------|
| `make setup`      | Full environment setup               |
| `make build`      | Build all components                 |
| `make test`       | Run all tests                        |
| `make lint`       | Run all linters                      |
| `make dev`        | Start development environment        |
| `make start`      | Start live trading (paper mode)      |
| `make stop`       | Stop live trading                    |
| `make docker-up`  | Start all Docker services            |
| `make docker-down`| Stop all Docker services             |
| `make clean`      | Remove build artifacts               |
| `make dist`       | Create distribution package          |

## Components

### Python Engine (Core)

The main trading engine with backtesting, live trading, and risk management.

### Rust Data Processor

High-performance market data processing:
- Real-time OHLCV bar aggregation from tick data
- Statistical analysis (mean, variance, skewness, kurtosis)
- Financial metrics (Sharpe ratio, Sortino ratio, max drawdown)
- IPC-ready serialization (JSON via serde)

### Go Monitoring Agent

Lightweight HTTP service for system health:
- `/health` — Service health check
- `/metrics` — System metrics (CPU, memory, goroutines)
- `/latency` — Real-time latency tracking

### TypeScript Dashboard

Web-based trading dashboard (built with React + TypeScript):
- Real-time charting
- Position monitoring
- Order management

## API Documentation

See [API.md](API.md) for complete API reference.

## License

MIT License — see [LICENSE](LICENSE) file for details.