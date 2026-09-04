"""Tests for simulated broker adapter"""

import asyncio

import pytest
from datetime import datetime
from decimal import Decimal

from src.adapters.simulated_broker import SimulatedBroker
from src.data.models import Symbol, Tick
from src.trading.models import Order, OrderSide, OrderStatus, OrderType


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


def make_order(symbol, side=OrderSide.BUY, quantity="10"):
    return Order(symbol=symbol, side=side, quantity=Decimal(quantity))


async def wait_for(predicate, timeout=2.0):
    """Wait until predicate is true or timeout elapses"""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.02)
        elapsed += 0.02
    return predicate()


class TestConnectivity:
    async def test_connect_sets_connected(self, symbol):
        broker = SimulatedBroker()
        assert broker.is_connected() is False

        assert await broker.connect() is True
        assert broker.is_connected() is True
        assert broker._fill_task is not None

        await broker.disconnect()
        assert broker.is_connected() is False

    async def test_disconnect_cancels_fill_task(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        task = broker._fill_task
        assert task is not None and not task.done()

        await broker.disconnect()
        assert task.done()

    async def test_submit_order_not_connected(self, symbol):
        broker = SimulatedBroker()
        assert await broker.submit_order(make_order(symbol)) is False


class TestSubmitAndCancel:
    async def test_submit_order_accepts(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            order = make_order(symbol)
            ok = await broker.submit_order(order)

            assert ok is True
            assert order.status == OrderStatus.ACCEPTED
            assert order.order_id in broker._orders
            assert order.submitted_at is not None
        finally:
            await broker.disconnect()

    async def test_cancel_unknown_order(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            assert await broker.cancel_order("missing") is False
        finally:
            await broker.disconnect()

    async def test_cancel_active_order(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            order = make_order(symbol)
            broker._orders[order.order_id] = order

            assert await broker.cancel_order(order.order_id) is True
            assert order.is_cancelled
        finally:
            await broker.disconnect()

    async def test_cancel_inactive_order_returns_false(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            order = make_order(symbol)
            order.cancel()  # mark cancelled first
            broker._orders[order.order_id] = order

            assert await broker.cancel_order(order.order_id) is False
        finally:
            await broker.disconnect()


class TestFillLoop:
    async def test_market_order_fills(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            broker.inject_market_data(make_tick(symbol))
            order = make_order(symbol)
            await broker.submit_order(order)

            assert await wait_for(lambda: order.is_filled)
            assert order.filled_quantity == order.quantity
            assert order.avg_fill_price == Decimal("150.10")
        finally:
            await broker.disconnect()

    async def test_sell_order_fills_at_bid(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            broker.inject_market_data(make_tick(symbol, bid="149.90", ask="150.10"))
            order = make_order(symbol, side=OrderSide.SELL)
            await broker.submit_order(order)

            assert await wait_for(lambda: order.is_filled)
            assert order.avg_fill_price == Decimal("149.90")
        finally:
            await broker.disconnect()

    async def test_no_market_data_prevents_fill(self, symbol):
        broker = SimulatedBroker()
        await broker.connect()
        try:
            order = make_order(symbol)
            await broker.submit_order(order)

            await asyncio.sleep(0.2)
            assert order.is_active
            assert not order.is_filled
        finally:
            await broker.disconnect()

    async def test_fill_probability_zero_no_fill(self, symbol):
        broker = SimulatedBroker(fill_probability=0.0)
        await broker.connect()
        try:
            broker.inject_market_data(make_tick(symbol))
            order = make_order(symbol)
            await broker.submit_order(order)

            await asyncio.sleep(0.2)
            assert order.is_active
            assert not order.is_filled
        finally:
            await broker.disconnect()

    async def test_partial_fill(self, symbol):
        broker = SimulatedBroker(partial_fill_probability=1.0)
        await broker.connect()
        try:
            broker.inject_market_data(make_tick(symbol))
            order = make_order(symbol, quantity="100")
            await broker.submit_order(order)

            assert await wait_for(lambda: order.status != OrderStatus.ACCEPTED)
            assert order.status == OrderStatus.PARTIALLY_FILLED
            assert order.remaining_quantity > 0
        finally:
            await broker.disconnect()


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
