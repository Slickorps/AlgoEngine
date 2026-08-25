"""Tests for simulated broker adapter"""

import pytest
from datetime import datetime
from decimal import Decimal

from src.adapters.simulated_broker import SimulatedBroker
from src.data.models import Symbol, Tick
from src.trading.models import Order, OrderSide, OrderType


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL", security_type="EQUITY", exchange="NASDAQ")


def make_tick(symbol, bid="149.90", ask="150.10", last="150.00"):
    return Tick(
        symbol=symbol,
        timestamp=datetime.now(),
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        last_price=Decimal(last),
    )


class TestInjectMarketData:
    def test_inject_stores_latest_tick(self, symbol):
        broker = SimulatedBroker()
        tick = make_tick(symbol)

        broker.inject_market_data(tick)

        assert broker._latest_ticks[symbol] is tick

    def test_inject_overwrites_previous_tick(self, symbol):
        broker = SimulatedBroker()
        broker.inject_market_data(make_tick(symbol, ask="150.10"))
        broker.inject_market_data(make_tick(symbol, ask="151.00"))

        assert broker._latest_ticks[symbol].ask_price == Decimal("151.00")


class TestGetFillPrice:
    async def test_buy_uses_ask_price(self, symbol):
        broker = SimulatedBroker()
        broker.inject_market_data(make_tick(symbol, bid="149.90", ask="150.10"))
        order = Order(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("10"))

        price = await broker._get_fill_price(order)

        assert price == Decimal("150.10")

    async def test_sell_uses_bid_price(self, symbol):
        broker = SimulatedBroker()
        broker.inject_market_data(make_tick(symbol, bid="149.90", ask="150.10"))
        order = Order(symbol=symbol, side=OrderSide.SELL, quantity=Decimal("10"))

        price = await broker._get_fill_price(order)

        assert price == Decimal("149.90")

    async def test_limit_order_uses_limit_price(self, symbol):
        broker = SimulatedBroker()
        broker.inject_market_data(make_tick(symbol))
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            limit_price=Decimal("148.00"),
        )

        price = await broker._get_fill_price(order)

        assert price == Decimal("148.00")

    async def test_no_market_data_returns_none(self, symbol):
        broker = SimulatedBroker()
        order = Order(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("10"))

        price = await broker._get_fill_price(order)

        assert price is None

    async def test_falls_back_to_last_price_without_quote(self, symbol):
        broker = SimulatedBroker()
        tick = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("0"),
            ask_price=Decimal("0"),
            last_price=Decimal("150.00"),
        )
        broker.inject_market_data(tick)
        order = Order(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("10"))

        price = await broker._get_fill_price(order)

        assert price == Decimal("150.00")
