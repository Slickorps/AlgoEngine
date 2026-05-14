// =============================================================================
// AlgoEngine - High-Performance Data Processing Module (Rust)
// =============================================================================
//
// This module handles computationally intensive tasks such as:
//   - Real-time market data normalization
//   - OHLCV aggregation from tick data
//   - Statistical calculations (variance, correlation, etc.)
//   - Data serialization/deserialization for IPC
//
// Built for maximum performance: no GC, zero-cost abstractions.
// =============================================================================

use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

// ── Constants ─────────────────────────────────────────────────────────

const MAX_OHLCV_BARS: usize = 10_000;
const MAX_TICK_BUFFER: usize = 1_000_000;

// ── Data Structures ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct Tick {
    pub symbol: String,
    pub timestamp: u64,
    pub price: f64,
    pub volume: f64,
    pub bid: f64,
    pub ask: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct OhlcvBar {
    pub symbol: String,
    pub timestamp: u64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub vwap: f64,
    pub trade_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MarketStats {
    pub symbol: String,
    pub timestamp: u64,
    pub last_price: f64,
    pub price_change_24h: f64,
    pub price_change_pct_24h: f64,
    pub volume_24h: f64,
    pub high_24h: f64,
    pub low_24h: f64,
    pub vwap_24h: f64,
    pub bid_ask_spread: f64,
    pub trade_count_24h: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct AggregatedMetrics {
    pub symbol: String,
    pub mean_price: f64,
    pub variance: f64,
    pub std_dev: f64,
    pub skewness: f64,
    pub kurtosis: f64,
    pub sharpe_ratio: f64,
    pub sortino_ratio: f64,
    pub max_drawdown: f64,
    pub total_return: f64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DataRequest {
    pub symbols: Vec<String>,
    pub start_timestamp: Option<u64>,
    pub end_timestamp: Option<u64>,
    pub aggregation: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DataResponse {
    pub status: String,
    pub data: serde_json::Value,
    pub processed_at: u64,
    pub processing_time_ms: f64,
    pub record_count: usize,
}

// ── OHLCV Aggregator ─────────────────────────────────────────────────

pub struct OhlcvAggregator {
    bars: BTreeMap<(String, u64), OhlcvBar>,
    current_bar: Option<OhlcvBar>,
    interval_seconds: u64,
}

impl OhlcvAggregator {
    pub fn new(interval_seconds: u64) -> Self {
        Self {
            bars: BTreeMap::new(),
            current_bar: None,
            interval_seconds,
        }
    }

    pub fn add_tick(&mut self, tick: &Tick) {
        let bar_time = (tick.timestamp / self.interval_seconds) * self.interval_seconds;

        match &mut self.current_bar {
            Some(bar) if bar.timestamp == bar_time && bar.symbol == tick.symbol => {
                // Update existing bar
                bar.high = bar.high.max(tick.price);
                bar.low = bar.low.min(tick.price);
                bar.close = tick.price;
                bar.volume += tick.volume;
                bar.trade_count += 1;
                // VWAP = cumulative(price * volume) / cumulative volume
                bar.vwap = ((bar.vwap * (bar.volume - tick.volume))
                    + (tick.price * tick.volume))
                    / bar.volume;
            }
            _ => {
                // Finalize previous bar
                if let Some(bar) = self.current_bar.take() {
                    self.bars.insert((bar.symbol.clone(), bar.timestamp), bar);
                    self.trim_old_bars();
                }

                // Start new bar
                self.current_bar = Some(OhlcvBar {
                    symbol: tick.symbol.clone(),
                    timestamp: bar_time,
                    open: tick.price,
                    high: tick.price,
                    low: tick.price,
                    close: tick.price,
                    volume: tick.volume,
                    vwap: tick.price,
                    trade_count: 1,
                });
            }
        }
    }

    fn trim_old_bars(&mut self) {
        while self.bars.len() > MAX_OHLCV_BARS {
            if let Some(key) = self.bars.keys().next().cloned() {
                self.bars.remove(&key);
            }
        }
    }

    pub fn get_bars(&self, symbol: &str, count: usize) -> Vec<OhlcvBar> {
        let mut result: Vec<OhlcvBar> = self
            .bars
            .iter()
            .filter(|((sym, _), _)| sym == symbol)
            .map(|(_, bar)| bar.clone())
            .collect();

        result.sort_by(|a, b| a.timestamp.cmp(&b.timestamp));
        result.truncate(count);
        result
    }

    pub fn latest_price(&self, symbol: &str) -> Option<f64> {
        self.bars
            .iter()
            .rev()
            .find(|((sym, _), _)| sym == symbol)
            .map(|(_, bar)| bar.close)
    }
}

// ── Statistical Calculator ────────────────────────────────────────────

pub struct StatsCalculator {
    price_history: BTreeMap<String, VecDeque<f64>>,
    max_history: usize,
}

impl StatsCalculator {
    pub fn new(max_history: usize) -> Self {
        Self {
            price_history: BTreeMap::new(),
            max_history,
        }
    }

    pub fn add_price(&mut self, symbol: &str, price: f64) {
        let history = self
            .price_history
            .entry(symbol.to_string())
            .or_insert_with(|| VecDeque::with_capacity(self.max_history));

        history.push_back(price);
        if history.len() > self.max_history {
            history.pop_front();
        }
    }

    pub fn calculate_metrics(&self, symbol: &str) -> Option<AggregatedMetrics> {
        let history = self.price_history.get(symbol)?;
        let n = history.len() as f64;

        if n < 2.0 {
            return None;
        }

        // Mean
        let sum: f64 = history.iter().sum();
        let mean = sum / n;

        // Variance and standard deviation
        let variance: f64 = history.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1.0);
        let std_dev = variance.sqrt();

        // Skewness
        let skewness = if std_dev > 0.0 {
            let m3: f64 = history.iter().map(|x| (x - mean).powi(3)).sum::<f64>() / n;
            m3 / std_dev.powi(3)
        } else {
            0.0
        };

        // Kurtosis
        let kurtosis = if std_dev > 0.0 {
            let m4: f64 = history.iter().map(|x| (x - mean).powi(4)).sum::<f64>() / n;
            m4 / std_dev.powi(4) - 3.0
        } else {
            0.0
        };

        // Returns for Sharpe/Sortino
        let returns: Vec<f64> = history
            .iter()
            .zip(history.iter().skip(1))
            .map(|(prev, curr)| (curr - prev) / prev)
            .collect();

        let total_return = if n > 1.0 {
            (history.back().unwrap() - history.front().unwrap()) / history.front().unwrap()
        } else {
            0.0
        };

        let mean_return = if !returns.is_empty() {
            returns.iter().sum::<f64>() / returns.len() as f64
        } else {
            0.0
        };

        let return_std = if returns.len() > 1 {
            let var: f64 = returns
                .iter()
                .map(|r| (r - mean_return).powi(2))
                .sum::<f64>()
                / (returns.len() - 1) as f64;
            var.sqrt()
        } else {
            0.0
        };

        let sharpe_ratio = if return_std > 0.0 {
            mean_return / return_std * (252.0_f64).sqrt()
        } else {
            0.0
        };

        // Sortino (downside deviation only)
        let downside_returns: Vec<&f64> = returns.iter().filter(|r| **r < 0.0).collect();
        let downside_std = if !downside_returns.is_empty() {
            let mean_down: f64 = downside_returns.iter().copied().sum::<f64>()
                / downside_returns.len() as f64;
            let var: f64 = downside_returns
                .iter()
                .map(|r| (r - mean_down).powi(2))
                .sum::<f64>()
                / downside_returns.len() as f64;
            var.sqrt()
        } else {
            0.0
        };

        let sortino_ratio = if downside_std > 0.0 {
            mean_return / downside_std * (252.0_f64).sqrt()
        } else {
            0.0
        };

        // Max drawdown
        let max_drawdown = Self::calculate_max_drawdown(history);

        Some(AggregatedMetrics {
            symbol: symbol.to_string(),
            mean_price: mean,
            variance,
            std_dev,
            skewness,
            kurtosis,
            sharpe_ratio,
            sortino_ratio,
            max_drawdown,
            total_return,
        })
    }

    fn calculate_max_drawdown(prices: &VecDeque<f64>) -> f64 {
        let mut peak = f64::MIN;
        let mut max_drawdown = 0.0;

        for &price in prices.iter() {
            if price > peak {
                peak = price;
            }
            let drawdown = (peak - price) / peak;
            if drawdown > max_drawdown {
                max_drawdown = drawdown;
            }
        }

        max_drawdown
    }
}

// ── Market Data Pipeline ─────────────────────────────────────────────

pub struct DataPipeline {
    aggregator: OhlcvAggregator,
    stats_calculator: StatsCalculator,
    tick_buffer: VecDeque<Tick>,
}

impl DataPipeline {
    pub fn new() -> Self {
        Self {
            aggregator: OhlcvAggregator::new(60), // 1-minute bars
            stats_calculator: StatsCalculator::new(100_000),
            tick_buffer: VecDeque::with_capacity(MAX_TICK_BUFFER),
        }
    }

    pub fn process_tick(&mut self, tick: Tick) {
        self.tick_buffer.push_back(tick.clone());
        if self.tick_buffer.len() > MAX_TICK_BUFFER {
            self.tick_buffer.pop_front();
        }

        self.stats_calculator.add_price(&tick.symbol, tick.price);
        self.aggregator.add_tick(&tick);
    }

    pub fn get_ohlcv_bars(&self, symbol: &str, count: usize) -> Vec<OhlcvBar> {
        self.aggregator.get_bars(symbol, count)
    }

    pub fn get_market_stats(&self, symbol: &str) -> Option<MarketStats> {
        let latest = self.aggregator.latest_price(symbol)?;

        // Get price 24h ago
        let history = self.stats_calculator.price_history.get(symbol)?;
        let price_24h_ago = if history.len() > 1 {
            history[0.max(history.len().saturating_sub(1440))]
        } else {
            latest
        };

        let price_change_24h = latest - price_24h_ago;
        let price_change_pct = if price_24h_ago != 0.0 {
            (price_change_24h / price_24h_ago) * 100.0
        } else {
            0.0
        };

        let volume_24h: f64 = self
            .aggregator
            .get_bars(symbol, 1440)
            .iter()
            .map(|b| b.volume)
            .sum();

        let bars = self.aggregator.get_bars(symbol, 1440);
        let high_24h = bars.iter().map(|b| b.high).fold(f64::MIN, f64::max);
        let low_24h = bars.iter().map(|b| b.low).fold(f64::MAX, f64::min);
        let vwap_24h = if volume_24h > 0.0 {
            bars.iter().map(|b| b.vwap * b.volume).sum::<f64>() / volume_24h
        } else {
            latest
        };

        let trade_count_24h: u64 = bars.iter().map(|b| b.trade_count).sum();
        let bid_ask_spread = latest * 0.0001; // Simulated spread

        Some(MarketStats {
            symbol: symbol.to_string(),
            timestamp: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs(),
            last_price: latest,
            price_change_24h,
            price_change_pct_24h: price_change_pct,
            volume_24h,
            high_24h,
            low_24h,
            vwap_24h,
            bid_ask_spread,
            trade_count_24h,
        })
    }

    pub fn process_request(&self, request: &DataRequest) -> DataResponse {
        let start = std::time::Instant::now();

        let mut result = serde_json::Map::new();

        for symbol in &request.symbols {
            let ohlcv = self.get_ohlcv_bars(symbol, 200);
            let stats = self.get_market_stats(symbol);
            let metrics = self.stats_calculator.calculate_metrics(symbol);

            let mut entry = serde_json::Map::new();
            if let Ok(val) = serde_json::to_value(&ohlcv) {
                entry.insert("ohlcv".to_string(), val);
            }
            if let Some(stats) = stats {
                if let Ok(val) = serde_json::to_value(&stats) {
                    entry.insert("stats".to_string(), val);
                }
            }
            if let Some(metrics) = metrics {
                if let Ok(val) = serde_json::to_value(&metrics) {
                    entry.insert("metrics".to_string(), val);
                }
            }
            result.insert(symbol.clone(), serde_json::Value::Object(entry));
        }

        let elapsed = start.elapsed();
        let record_count: usize = request.symbols.len();

        DataResponse {
            status: "success".to_string(),
            data: serde_json::Value::Object(result),
            processed_at: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs(),
            processing_time_ms: elapsed.as_secs_f64() * 1000.0,
            record_count,
        }
    }
}

// ── Main ──────────────────────────────────────────────────────────────

fn main() {
    println!("AlgoEngine Data Processor v{}", env!("CARGO_PKG_VERSION"));

    // Initialize pipeline
    let mut pipeline = DataPipeline::new();

    // Simulate some market data for demonstration
    let symbols = ["BTC/USD", "ETH/USD", "AAPL", "GOOGL"];
    let mut price_map: std::collections::HashMap<&str, f64> = [
        ("BTC/USD", 65000.0),
        ("ETH/USD", 3500.0),
        ("AAPL", 185.0),
        ("GOOGL", 140.0),
    ]
    .iter()
    .cloned()
    .collect();

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();

    // Generate 1000 sample ticks
    for i in 0..1000 {
        for symbol in &symbols {
            let price = price_map.get_mut(symbol).unwrap();
            // Random walk
            let change = (*price * 0.001) * (if i % 2 == 0 { 1.0 } else { -1.0 });
            *price += change;

            let tick = Tick {
                symbol: symbol.to_string(),
                timestamp: now + i as u64,
                price: *price,
                volume: (100.0 + (i as f64 * 0.5)) % 1000.0,
                bid: *price * 0.9999,
                ask: *price * 1.0001,
            };
            pipeline.process_tick(tick);
        }
    }

    // Query data
    let request = DataRequest {
        symbols: vec!["BTC/USD".to_string(), "ETH/USD".to_string()],
        start_timestamp: None,
        end_timestamp: None,
        aggregation: Some("1m".to_string()),
    };

    let response = pipeline.process_request(&request);
    println!(
        "Processed {} symbols in {:.2}ms",
        response.record_count, response.processing_time_ms
    );
    println!("Response: {}", serde_json::to_string_pretty(&response).unwrap());
}