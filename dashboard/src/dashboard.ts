/**
 * AlgoEngine Real-time Trading Dashboard
 *
 * TypeScript-based monitoring dashboard that connects to the Python
 * trading engine via WebSocket and provides real-time visualization
 * of trading activity, positions, and performance metrics.
 */

import express, { Application, Request, Response } from 'express';
import http from 'http';
import { Server as SocketIOServer, Socket } from 'socket.io';
import WebSocket from 'ws';

// ------------------------------------------------------------------
// Types & Interfaces
// ------------------------------------------------------------------

interface EngineStatus {
    mode: string;
    health: string;
    running: boolean;
    connected: boolean;
    circuit_breaker_active: boolean;
    pending_orders: number;
    daily_trade_count: number;
    daily_trade_limit: number;
    daily_volume: number;
    stats: TradingStats;
    portfolio: PortfolioSummary;
    uptime: string;
}

interface TradingStats {
    session_start: string;
    total_orders_submitted: number;
    total_orders_filled: number;
    total_orders_rejected: number;
    total_orders_cancelled: number;
    total_trades: number;
    total_volume: number;
    total_commission: number;
    total_slippage: number;
    net_pnl: number;
    peak_portfolio_value: number;
    current_drawdown_pct: number;
    connection_drops: number;
    is_circuit_breaker_active: boolean;
    last_error: string | null;
}

interface PortfolioSummary {
    total_value: number;
    cash: number;
    positions_count: number;
    daily_pnl: number;
    total_pnl: number;
}

interface PositionInfo {
    symbol: string;
    quantity: number;
    avg_entry_price: number;
    current_price: number;
    unrealized_pnl: number;
    realized_pnl: number;
    pnl_percent: number;
}

interface OrderInfo {
    order_id: string;
    symbol: string;
    side: string;
    type: string;
    quantity: number;
    filled_quantity: number;
    limit_price: number | null;
    stop_price: number | null;
    status: string;
    created_at: string;
}

interface AlertMessage {
    type: 'info' | 'warning' | 'error' | 'critical';
    timestamp: string;
    message: string;
    source: string;
}

interface DashboardConfig {
    engineApiUrl: string;
    engineWsUrl: string;
    port: number;
    refreshInterval: number;
    maxAlerts: number;
}

// ------------------------------------------------------------------
// Dashboard Server
// ------------------------------------------------------------------

class DashboardServer {
    private app: Application;
    private server: http.Server;
    private io: SocketIOServer;
    private config: DashboardConfig;
    private engineWs: WebSocket | null = null;
    private engineHttpUrl: string;

    private status: EngineStatus | null = null;
    private positions: PositionInfo[] = [];
    private orders: OrderInfo[] = [];
    private alerts: AlertMessage[] = [];
    private historicalPnl: { timestamp: string; value: number }[] = [];

    private refreshTimer: NodeJS.Timeout | null = null;

    private static readonly DEFAULT_CONFIG: DashboardConfig = {
        engineApiUrl: 'http://127.0.0.1:8000',
        engineWsUrl: 'ws://127.0.0.1:8000/ws',
        port: 3000,
        refreshInterval: 2000,
        maxAlerts: 100
    };

    constructor(config?: Partial<DashboardConfig>) {
        this.config = { ...DashboardServer.DEFAULT_CONFIG, ...config };
        this.engineHttpUrl = this.config.engineApiUrl;

        this.app = express();
        this.server = http.createServer(this.app);
        this.io = new SocketIOServer(this.server, {
            cors: {
                origin: '*',
                methods: ['GET', 'POST']
            }
        });

        this.setupRoutes();
        this.setupSocketHandlers();
    }

    // ------------------------------------------------------------------
    // Setup
    // ------------------------------------------------------------------

    private setupRoutes(): void {
        this.app.use(express.json());
        this.app.use(express.static('public'));

        // API routes for dashboard data
        this.app.get('/api/status', (_req: Request, res: Response) => {
            res.json(this.status);
        });

        this.app.get('/api/positions', (_req: Request, res: Response) => {
            res.json(this.positions);
        });

        this.app.get('/api/orders', (_req: Request, res: Response) => {
            res.json(this.orders);
        });

        this.app.get('/api/alerts', (_req: Request, res: Response) => {
            res.json(this.alerts);
        });

        this.app.get('/api/pnl-history', (_req: Request, res: Response) => {
            res.json(this.historicalPnl);
        });

        this.app.get('/api/health', (_req: Request, res: Response) => {
            res.json({
                status: 'healthy',
                uptime: process.uptime(),
                connected: this.engineWs?.readyState === WebSocket.OPEN,
                lastUpdate: this.status?.stats.session_start || null
            });
        });

        // Dashboard HTML
        this.app.get('/', (_req: Request, res: Response) => {
            res.send(this.getDashboardHTML());
        });
    }

    private setupSocketHandlers(): void {
        this.io.on('connection', (socket: Socket) => {
            console.log(`[Dashboard] Client connected: ${socket.id}`);

            // Send current state on connection
            socket.emit('status', this.status);
            socket.emit('positions', this.positions);
            socket.emit('orders', this.orders);
            socket.emit('alerts', this.alerts.slice(-20));
            socket.emit('pnl-history', this.historicalPnl);

            socket.on('disconnect', () => {
                console.log(`[Dashboard] Client disconnected: ${socket.id}`);
            });

            socket.on('command', async (command: string) => {
                await this.handleCommand(command);
            });
        });
    }

    // ------------------------------------------------------------------
    // Engine Connection
    // ------------------------------------------------------------------

    async start(): Promise<void> {
        // Start HTTP server
        this.server.listen(this.config.port, () => {
            console.log(`[Dashboard] Server running on http://localhost:${this.config.port}`);
        });

        // Connect to engine's WebSocket
        this.connectEngineWs();

        // Start polling engine REST API
        this.startPolling();
    }

    private connectEngineWs(): void {
        try {
            this.engineWs = new WebSocket(this.config.engineWsUrl);

            this.engineWs.on('open', () => {
                console.log('[Dashboard] Connected to engine WebSocket');
                this.addAlert('info', 'Connected to trading engine', 'connection');
            });

            this.engineWs.on('message', (data: WebSocket.Data) => {
                try {
                    const message = JSON.parse(data.toString());
                    this.handleEngineMessage(message);
                } catch (err) {
                    console.error('[Dashboard] Failed to parse engine message:', err);
                }
            });

            this.engineWs.on('error', (err: Error) => {
                console.error('[Dashboard] Engine WebSocket error:', err.message);
                this.addAlert('error', `Engine connection error: ${err.message}`, 'connection');
            });

            this.engineWs.on('close', () => {
                console.log('[Dashboard] Engine WebSocket disconnected');
                this.addAlert('warning', 'Disconnected from engine - retrying...', 'connection');

                // Reconnect after delay
                setTimeout(() => this.connectEngineWs(), 5000);
            });
        } catch (err) {
            console.error('[Dashboard] Failed to create WebSocket:', err);
            setTimeout(() => this.connectEngineWs(), 5000);
        }
    }

    private startPolling(): void {
        this.refreshTimer = setInterval(async () => {
            await this.pollEngineStatus();
            this.broadcastUpdates();
        }, this.config.refreshInterval);
    }

    private async pollEngineStatus(): Promise<void> {
        try {
            const response = await fetch(`${this.engineHttpUrl}/api/v1/status`);
            if (response.ok) {
                this.status = await response.json() as EngineStatus;
            }
        } catch (err) {
            // Engine might not be running - that's ok
        }

        try {
            const posResponse = await fetch(`${this.engineHttpUrl}/api/v1/positions`);
            if (posResponse.ok) {
                this.positions = await posResponse.json() as PositionInfo[];
            }
        } catch (err) {
            // ignore
        }

        try {
            const ordResponse = await fetch(`${this.engineHttpUrl}/api/v1/orders`);
            if (ordResponse.ok) {
                this.orders = await ordResponse.json() as OrderInfo[];
            }
        } catch (err) {
            // ignore
        }

        // Track PnL history
        if (this.status) {
            this.historicalPnl.push({
                timestamp: new Date().toISOString(),
                value: this.status.stats.net_pnl
            });

            // Keep last 500 data points
            if (this.historicalPnl.length > 500) {
                this.historicalPnl = this.historicalPnl.slice(-500);
            }
        }
    }

    private handleEngineMessage(message: any): void {
        switch (message.type) {
            case 'fill':
                this.addAlert(
                    'info',
                    `Fill: ${message.side} ${message.quantity} ${message.symbol} @ ${message.fill_price}`,
                    'trading'
                );
                break;

            case 'order_submitted':
                this.addAlert(
                    'info',
                    `Order submitted: ${message.side} ${message.quantity} ${message.symbol}`,
                    'trading'
                );
                break;

            case 'order_rejected':
                this.addAlert(
                    'warning',
                    `Order rejected: ${message.symbol} - ${message.reason}`,
                    'trading'
                );
                break;

            case 'error':
                this.addAlert(
                    'error',
                    `Engine error: ${message.message}`,
                    'engine'
                );
                break;

            case 'circuit_breaker':
                this.addAlert(
                    'critical',
                    `Circuit breaker triggered: ${message.reason}`,
                    'risk'
                );
                break;

            case 'health_change':
                this.addAlert(
                    message.current === 'healthy' ? 'info' : 'warning',
                    `Engine health: ${message.previous} -> ${message.current}`,
                    'system'
                );
                break;
        }

        // Broadcast to all connected clients
        this.io.emit('alert', this.alerts[this.alerts.length - 1]);
    }

    private async handleCommand(command: string): Promise<void> {
        try {
            const response = await fetch(`${this.engineHttpUrl}/api/v1/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command })
            });

            if (response.ok) {
                this.addAlert('info', `Command executed: ${command}`, 'system');
            } else {
                this.addAlert('warning', `Command failed: ${command}`, 'system');
            }
        } catch (err) {
            this.addAlert('error', `Command error: ${command}`, 'system');
        }
    }

    // ------------------------------------------------------------------
    // Alert Management
    // ------------------------------------------------------------------

    private addAlert(type: AlertMessage['type'], message: string, source: string): void {
        this.alerts.push({
            type,
            timestamp: new Date().toISOString(),
            message,
            source
        });

        if (this.alerts.length > this.config.maxAlerts) {
            this.alerts = this.alerts.slice(-this.config.maxAlerts);
        }
    }

    private broadcastUpdates(): void {
        if (this.status) {
            this.io.emit('status', this.status);
        }
        this.io.emit('positions', this.positions);
        this.io.emit('orders', this.orders);
        this.io.emit('pnl-history', this.historicalPnl);
    }

    // ------------------------------------------------------------------
    // Dashboard HTML
    // ------------------------------------------------------------------

    private getDashboardHTML(): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AlgoEngine Dashboard</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e17;
            color: #e0e0e0;
            overflow-x: hidden;
        }
        .header {
            background: linear-gradient(135deg, #0d1a2d 0%, #1a2a4a 100%);
            padding: 16px 24px;
            border-bottom: 1px solid #1e3a5f;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 20px;
            font-weight: 600;
            color: #4fc3f7;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .header h1 span { color: #8899aa; font-weight: 400; }
        .status-badge {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }
        .status-healthy { background: #1b5e20; color: #81c784; }
        .status-degraded { background: #e65100; color: #ffb74d; }
        .status-critical { background: #b71c1c; color: #ef9a9a; }
        .status-offline { background: #424242; color: #9e9e9e; }
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            padding: 16px;
        }
        .card {
            background: linear-gradient(135deg, #111d2e 0%, #0f1928 100%);
            border: 1px solid #1a2d45;
            border-radius: 12px;
            padding: 16px;
        }
        .card h2 {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #5a7a9a;
            margin-bottom: 12px;
        }
        .metric-row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid #152238;
        }
        .metric-row:last-child { border-bottom: none; }
        .metric-label { color: #8899aa; font-size: 13px; }
        .metric-value { font-weight: 500; font-size: 14px; }
        .positive { color: #4caf50; }
        .negative { color: #f44336; }
        .neutral { color: #ff9800; }
        .order-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }
        .order-table th {
            text-align: left;
            padding: 8px 6px;
            color: #5a7a9a;
            border-bottom: 1px solid #1a2d45;
        }
        .order-table td {
            padding: 6px;
            border-bottom: 1px solid #0f1a2a;
        }
        .alert-list {
            max-height: 200px;
            overflow-y: auto;
        }
        .alert-item {
            padding: 6px 8px;
            margin: 4px 0;
            border-radius: 4px;
            font-size: 12px;
            display: flex;
            gap: 8px;
        }
        .alert-info { background: #0d2137; border-left: 3px solid #1976d2; }
        .alert-warning { background: #2a1f0d; border-left: 3px solid #f57c00; }
        .alert-error { background: #2a0d0d; border-left: 3px solid #d32f2f; }
        .alert-critical { background: #3a0d0d; border-left: 3px solid #b71c1c; }
        .alert-time { color: #5a7a9a; white-space: nowrap; }
        .chart-container {
            grid-column: 1 / -1;
            height: 250px;
        }
        @media (max-width: 768px) {
            .dashboard-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>AlgoEngine <span>Live Dashboard</span></h1>
        <div>
            <span id="statusBadge" class="status-badge status-offline">Disconnected</span>
            <span id="uptime" style="margin-left:12px;color:#5a7a9a;font-size:13px;"></span>
        </div>
    </div>

    <div class="dashboard-grid">
        <!-- Engine Status -->
        <div class="card">
            <h2>Engine Status</h2>
            <div id="engineStatus"></div>
        </div>

        <!-- Trading Stats -->
        <div class="card">
            <h2>Trading Activity</h2>
            <div id="tradingStats"></div>
        </div>

        <!-- Orders -->
        <div class="card" style="grid-column: span 2;">
            <h2>Recent Orders</h2>
            <div id="ordersList"></div>
        </div>

        <!-- Positions -->
        <div class="card">
            <h2>Open Positions</h2>
            <div id="positionsList"></div>
        </div>

        <!-- Alerts -->
        <div class="card">
            <h2>Recent Alerts</h2>
            <div id="alertsList" class="alert-list"></div>
        </div>

        <!-- P&L Chart -->
        <div class="card chart-container">
            <h2>PnL History</h2>
            <canvas id="pnlChart"></canvas>
        </div>
    </div>

    <script>
        const socket = io();
        let pnlChart = null;

        socket.on('status', (status) => {
            if (!status) return;

            // Update status badge
            const badge = document.getElementById('statusBadge');
            const healthClass = 'status-' + (status.health || 'offline');
            badge.className = 'status-badge ' + healthClass;
            badge.textContent = (status.mode || '?').toUpperCase() + ' - ' + (status.health || 'N/A');

            // Update uptime
            document.getElementById('uptime').textContent = status.uptime || '';

            // Build engine status
            const engineHtml = [
                ['Mode', status.mode || 'N/A'],
                ['Health', status.health || 'N/A'],
                ['Running', status.running ? 'Yes' : 'No'],
                ['Connected', status.connected ? 'Yes' : 'No'],
                ['Pending Orders', status.pending_orders || 0],
                ['Daily Trades', (status.daily_trade_count || 0) + ' / ' + (status.daily_trade_limit || 'N/A')],
                ['Daily Volume', (status.daily_volume || 0).toFixed(2)],
                ['Circuit Breaker', status.circuit_breaker_active ? 'ACTIVE' : 'Inactive']
            ].map(([label, value]) =>
                '<div class="metric-row"><span class="metric-label">' + label + '</span><span class="metric-value">' + value + '</span></div>'
            ).join('');
            document.getElementById('engineStatus').innerHTML = engineHtml;

            // Build trading stats
            if (status.stats) {
                const s = status.stats;
                const statsHtml = [
                    ['Orders Submitted', s.total_orders_submitted || 0],
                    ['Orders Filled', s.total_orders_filled || 0],
                    ['Orders Rejected', s.total_orders_rejected || 0],
                    ['Total Trades', s.total_trades || 0],
                    ['Total Volume', (s.total_volume || 0).toFixed(2)],
                    ['Commission', (s.total_commission || 0).toFixed(2)],
                    ['Slippage', (s.total_slippage || 0).toFixed(2)],
                    ['Net P&L', '<span class="' + (s.net_pnl >= 0 ? 'positive' : 'negative') + '">' + (s.net_pnl || 0).toFixed(2) + '</span>'],
                    ['Drawdown', '<span class="negative">' + (s.current_drawdown_pct || 0).toFixed(2) + '%</span>'],
                    ['Connection Drops', s.connection_drops || 0]
                ].map(([label, value]) =>
                    '<div class="metric-row"><span class="metric-label">' + label + '</span><span class="metric-value">' + value + '</span></div>'
                ).join('');
                document.getElementById('tradingStats').innerHTML = statsHtml;
            }
        });

        socket.on('orders', (orders) => {
            if (!orders || orders.length === 0) {
                document.getElementById('ordersList').innerHTML = '<div style="color:#5a7a9a;padding:8px;">No recent orders</div>';
                return;
            }
            const rows = orders.slice(-10).reverse().map(o =>
                '<tr>' +
                    '<td>' + (o.order_id || '').substring(0, 8) + '</td>' +
                    '<td>' + (o.symbol || '') + '</td>' +
                    '<td>' + (o.side || '') + '</td>' +
                    '<td>' + (o.type || '') + '</td>' +
                    '<td>' + (o.quantity || 0) + '</td>' +
                    '<td>' + (o.status || '') + '</td>' +
                '</tr>'
            ).join('');
            document.getElementById('ordersList').innerHTML =
                '<table class="order-table"><thead><tr>' +
                    '<th>ID</th><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Status</th>' +
                '</tr></thead><tbody>' + rows + '</tbody></table>';
        });

        socket.on('positions', (positions) => {
            if (!positions || positions.length === 0) {
                document.getElementById('positionsList').innerHTML = '<div style="color:#5a7a9a;padding:8px;">No open positions</div>';
                return;
            }
            const posHtml = positions.map(p =>
                '<div class="metric-row">' +
                    '<span class="metric-label">' + (p.symbol || '') + ' (' + (p.quantity || 0) + ')</span>' +
                    '<span class="metric-value ' + (p.unrealized_pnl >= 0 ? 'positive' : 'negative') + '">' +
                        (p.unrealized_pnl || 0).toFixed(2) +
                    '</span>' +
                '</div>'
            ).join('');
            document.getElementById('positionsList').innerHTML = posHtml;
        });

        socket.on('alerts', (alert) => {
            if (!alert) return;
            const container = document.getElementById('alertsList');
            const el = document.createElement('div');
            el.className = 'alert-item alert-' + (alert.type || 'info');
            el.innerHTML = '<span class="alert-time">' + new Date(alert.timestamp).toLocaleTimeString() + '</span>' +
                          '<span>' + (alert.message || '') + '</span>';
            container.prepend(el);
            while (container.children.length > 50) {
                container.removeChild(container.lastChild);
            }
        });

        socket.on('pnl-history', (history) => {
            if (!history || history.length === 0) return;
            const ctx = document.getElementById('pnlChart').getContext('2d');
            if (pnlChart) { pnlChart.destroy(); }
            pnlChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: history.map(h => new Date(h.timestamp).toLocaleTimeString()),
                    datasets: [{
                        label: 'PnL',
                        data: history.map(h => h.value),
                        borderColor: '#4fc3f7',
                        backgroundColor: 'rgba(79, 195, 247, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: true, grid: { color: '#152238' }, ticks: { color: '#5a7a9a', maxTicksLimit: 10 } },
                        y: { display: true, grid: { color: '#152238' }, ticks: { color: '#5a7a9a' } }
                    }
                }
            });
        });
    </script>
</body>
</html>`;
    }
}

// ------------------------------------------------------------------
// Main
// ------------------------------------------------------------------

async function main(): Promise<void> {
    const port = parseInt(process.env.DASHBOARD_PORT || '3000', 10);
    const engineApiUrl = process.env.ENGINE_API_URL || 'http://127.0.0.1:8000';
    const engineWsUrl = process.env.ENGINE_WS_URL || 'ws://127.0.0.1:8000/ws';

    const dashboard = new DashboardServer({
        port,
        engineApiUrl,
        engineWsUrl,
        refreshInterval: parseInt(process.env.DASHBOARD_REFRESH || '2000', 10)
    });

    await dashboard.start();
    console.log(`[Main] Dashboard started on port ${port}`);
    console.log(`[Main] Engine API: ${engineApiUrl}`);
    console.log(`[Main] Engine WS: ${engineWsUrl}`);
}

main().catch((err) => {
    console.error('[Main] Fatal error:', err);
    process.exit(1);
});