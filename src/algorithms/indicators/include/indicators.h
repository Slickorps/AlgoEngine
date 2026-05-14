/**
 * AlgoEngine High-Performance Technical Indicators (C++)
 *
 * This module provides optimized C++ implementations of common
 * technical analysis indicators used in algorithmic trading.
 */

#ifndef ALGOENGINE_INDICATORS_H
#define ALGOENGINE_INDICATORS_H

#include <vector>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <numeric>
#include <algorithm>

namespace algoengine {
namespace indicators {

// ------------------------------------------------------------------
// Moving Averages
// ------------------------------------------------------------------

/**
 * Simple Moving Average (SMA)
 *
 * @param data Input price data series
 * @param period Lookback period
 * @return Vector of SMA values (first valid at index period-1)
 */
inline std::vector<double> sma(const std::vector<double>& data, size_t period) {
    if (data.size() < period || period == 0) {
        return {};
    }

    std::vector<double> result(data.size() - period + 1, 0.0);
    double sum = std::accumulate(data.begin(), data.begin() + period, 0.0);
    result[0] = sum / static_cast<double>(period);

    for (size_t i = period; i < data.size(); ++i) {
        sum += data[i] - data[i - period];
        result[i - period + 1] = sum / static_cast<double>(period);
    }

    return result;
}

/**
 * Exponential Moving Average (EMA)
 *
 * @param data Input price data series
 * @param period Lookback period
 * @return Vector of EMA values (first valid at index period-1)
 */
inline std::vector<double> ema(const std::vector<double>& data, size_t period) {
    if (data.size() < period || period == 0) {
        return {};
    }

    std::vector<double> result(data.size(), 0.0);
    double multiplier = 2.0 / static_cast<double>(period + 1);

    // Start with SMA for the first period
    double sum = std::accumulate(data.begin(), data.begin() + period, 0.0);
    result[period - 1] = sum / static_cast<double>(period);

    for (size_t i = period; i < data.size(); ++i) {
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1];
    }

    return result;
}

// ------------------------------------------------------------------
// Oscillators
// ------------------------------------------------------------------

/**
 * Relative Strength Index (RSI)
 *
 * @param data Input price data series
 * @param period Lookback period (typically 14)
 * @return Vector of RSI values (0-100 scale, first valid at index period)
 */
inline std::vector<double> rsi(const std::vector<double>& data, size_t period = 14) {
    if (data.size() < period + 1 || period == 0) {
        return {};
    }

    std::vector<double> result(data.size() - period, 0.0);
    double gain = 0.0, loss = 0.0;

    // Initial average gain/loss
    for (size_t i = 1; i <= period; ++i) {
        double diff = data[i] - data[i - 1];
        if (diff > 0) gain += diff;
        else loss -= diff;
    }

    gain /= static_cast<double>(period);
    loss /= static_cast<double>(period);

    double rs = (loss == 0.0) ? 100.0 : gain / loss;
    result[0] = 100.0 - (100.0 / (1.0 + rs));

    for (size_t i = period + 1; i < data.size(); ++i) {
        double diff = data[i] - data[i - 1];
        double current_gain = (diff > 0) ? diff : 0.0;
        double current_loss = (diff < 0) ? -diff : 0.0;

        gain = (gain * (period - 1) + current_gain) / static_cast<double>(period);
        loss = (loss * (period - 1) + current_loss) / static_cast<double>(period);

        rs = (loss == 0.0) ? 100.0 : gain / loss;
        result[i - period] = 100.0 - (100.0 / (1.0 + rs));
    }

    return result;
}

// ------------------------------------------------------------------
// Volatility Indicators
// ------------------------------------------------------------------

/**
 * Bollinger Bands
 *
 * @param data Input price data series
 * @param period Lookback period (typically 20)
 * @param num_std Number of standard deviations (typically 2)
 * @return Tuple of (middle_band, upper_band, lower_band) vectors
 */
inline std::tuple<std::vector<double>, std::vector<double>, std::vector<double>>
bollinger_bands(const std::vector<double>& data, size_t period = 20, double num_std = 2.0) {
    if (data.size() < period || period == 0) {
        return {};
    }

    auto middle = sma(data, period);
    size_t result_size = middle.size();

    std::vector<double> upper(result_size);
    std::vector<double> lower(result_size);

    for (size_t i = 0; i < result_size; ++i) {
        size_t start_idx = i;
        size_t end_idx = i + period;

        // Calculate standard deviation
        double mean = middle[i];
        double sq_sum = 0.0;

        for (size_t j = start_idx; j < end_idx; ++j) {
            double dev = data[j] - mean;
            sq_sum += dev * dev;
        }

        double std_dev = std::sqrt(sq_sum / static_cast<double>(period));
        upper[i] = mean + num_std * std_dev;
        lower[i] = mean - num_std * std_dev;
    }

    return {middle, upper, lower};
}

// ------------------------------------------------------------------
// Momentum Indicators
// ------------------------------------------------------------------

/**
 * Moving Average Convergence Divergence (MACD)
 *
 * @param data Input price data series
 * @param fast_period Fast EMA period (typically 12)
 * @param slow_period Slow EMA period (typically 26)
 * @param signal_period Signal line period (typically 9)
 * @return Tuple of (macd_line, signal_line, histogram) vectors
 */
inline std::tuple<std::vector<double>, std::vector<double>, std::vector<double>>
macd(const std::vector<double>& data,
     size_t fast_period = 12,
     size_t slow_period = 26,
     size_t signal_period = 9) {
    if (data.size() < slow_period || fast_period >= slow_period) {
        return {};
    }

    auto fast_ema = ema(data, fast_period);
    auto slow_ema = ema(data, slow_period);

    // MACD line: fast_ema - slow_ema (aligned from slow_period index)
    size_t start_offset = slow_period - 1;
    size_t macd_size = data.size() - start_offset;

    std::vector<double> macd_line(macd_size);
    for (size_t i = 0; i < macd_size; ++i) {
        macd_line[i] = fast_ema[start_offset + i] - slow_ema[start_offset + i];
    }

    // Signal line: EMA of MACD line
    auto signal = ema(macd_line, signal_period);

    // Histogram: MACD line - Signal line
    size_t hist_start = signal_period - 1;
    size_t hist_size = macd_size - hist_start;
    std::vector<double> histogram(hist_size);
    for (size_t i = 0; i < hist_size; ++i) {
        histogram[i] = macd_line[hist_start + i] - signal[hist_start + i];
    }

    return {macd_line, signal, histogram};
}

/**
 * Rate of Change (ROC)
 *
 * @param data Input price data series
 * @param period Lookback period
 * @return Vector of ROC values (percentage change)
 */
inline std::vector<double> roc(const std::vector<double>& data, size_t period = 12) {
    if (data.size() <= period || period == 0) {
        return {};
    }

    std::vector<double> result(data.size() - period, 0.0);
    for (size_t i = period; i < data.size(); ++i) {
        result[i - period] = ((data[i] - data[i - period]) / data[i - period]) * 100.0;
    }

    return result;
}

/**
 * Average True Range (ATR)
 *
 * @param high High price series
 * @param low Low price series
 * @param close Close price series
 * @param period Lookback period (typically 14)
 * @return Vector of ATR values
 */
inline std::vector<double> atr(const std::vector<double>& high,
                                const std::vector<double>& low,
                                const std::vector<double>& close,
                                size_t period = 14) {
    size_t n = high.size();
    if (n < period + 1 || n != low.size() || n != close.size() || period == 0) {
        return {};
    }

    // Calculate True Range for each bar
    std::vector<double> tr(n - 1);
    for (size_t i = 1; i < n; ++i) {
        double hl = high[i] - low[i];
        double hc = std::abs(high[i] - close[i - 1]);
        double lc = std::abs(low[i] - close[i - 1]);
        tr[i - 1] = std::max({hl, hc, lc});
    }

    // Initial ATR is SMA of first 'period' TR values
    size_t atr_n = tr.size() - period + 1;
    std::vector<double> result(atr_n, 0.0);

    double sum = std::accumulate(tr.begin(), tr.begin() + period, 0.0);
    result[0] = sum / static_cast<double>(period);

    for (size_t i = period; i < tr.size(); ++i) {
        result[i - period + 1] = (result[i - period] * (period - 1) + tr[i]) / static_cast<double>(period);
    }

    return result;
}

// ------------------------------------------------------------------
// Statistical Analysis
// ------------------------------------------------------------------

/**
 * Linear Regression Slope
 *
 * @param data Input data series
 * @param period Lookback period
 * @return Vector of slope values
 */
inline std::vector<double> linear_regression_slope(const std::vector<double>& data, size_t period) {
    if (data.size() < period || period < 2) {
        return {};
    }

    std::vector<double> result(data.size() - period + 1, 0.0);
    double x_mean = static_cast<double>(period - 1) / 2.0;
    double x_var = 0.0;

    // Pre-calculate x variance
    for (size_t i = 0; i < period; ++i) {
        double dev = static_cast<double>(i) - x_mean;
        x_var += dev * dev;
    }

    for (size_t i = 0; i < result.size(); ++i) {
        double y_mean = 0.0;
        for (size_t j = 0; j < period; ++j) {
            y_mean += data[i + j];
        }
        y_mean /= static_cast<double>(period);

        double covariance = 0.0;
        for (size_t j = 0; j < period; ++j) {
            covariance += (static_cast<double>(j) - x_mean) * (data[i + j] - y_mean);
        }

        result[i] = covariance / x_var;
    }

    return result;
}

// ------------------------------------------------------------------
// Pattern Recognition
// ------------------------------------------------------------------

/**
 * Detect cross of two data series (e.g., price crossing SMA)
 *
 * @param series1 First data series
 * @param series2 Second data series
 * @return Vector of cross signals: 1=up cross, -1=down cross, 0=no cross
 */
inline std::vector<int8_t> cross_detection(const std::vector<double>& series1,
                                            const std::vector<double>& series2) {
    if (series1.size() != series2.size() || series1.size() < 2) {
        return {};
    }

    std::vector<int8_t> result(series1.size(), 0);

    for (size_t i = 1; i < series1.size(); ++i) {
        if (series1[i - 1] <= series2[i - 1] && series1[i] > series2[i]) {
            result[i] = 1;  // Up cross (golden cross)
        } else if (series1[i - 1] >= series2[i - 1] && series1[i] < series2[i]) {
            result[i] = -1; // Down cross (death cross)
        }
    }

    return result;
}

/**
 * Find local minima and maxima
 *
 * @param data Input data series
 * @param order Number of bars on each side to confirm pivot
 * @return Vector of pivot signals: 1=peak, -1=valley, 0=no pivot
 */
inline std::vector<int8_t> pivot_detection(const std::vector<double>& data, size_t order = 2) {
    if (data.size() < 2 * order + 1) {
        return {};
    }

    std::vector<int8_t> result(data.size(), 0);

    for (size_t i = order; i < data.size() - order; ++i) {
        bool is_peak = true;
        bool is_valley = true;

        for (size_t j = 1; j <= order; ++j) {
            if (data[i] <= data[i - j] || data[i] <= data[i + j]) {
                is_peak = false;
            }
            if (data[i] >= data[i - j] || data[i] >= data[i + j]) {
                is_valley = false;
            }
        }

        if (is_peak) result[i] = 1;
        else if (is_valley) result[i] = -1;
    }

    return result;
}

// ------------------------------------------------------------------
// Statistical Moments
// ------------------------------------------------------------------

/**
 * Standard deviation of a data series
 */
inline std::vector<double> stddev(const std::vector<double>& data, size_t period) {
    if (data.size() < period || period < 2) {
        return {};
    }

    std::vector<double> result(data.size() - period + 1, 0.0);

    for (size_t i = 0; i < result.size(); ++i) {
        double mean = 0.0;
        for (size_t j = 0; j < period; ++j) {
            mean += data[i + j];
        }
        mean /= static_cast<double>(period);

        double variance = 0.0;
        for (size_t j = 0; j < period; ++j) {
            double dev = data[i + j] - mean;
            variance += dev * dev;
        }
        variance /= static_cast<double>(period);

        result[i] = std::sqrt(variance);
    }

    return result;
}

} // namespace indicators
} // namespace algoengine

#endif // ALGOENGINE_INDICATORS_H