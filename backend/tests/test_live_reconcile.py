"""Adopting positions placed outside NeuradeX.

Groww's /positions/user had never been called by this codebase, so the payload
shape is unverified — and its output becomes real MIS orders. These tests pin
the two properties that matter when you are mapping an API you cannot call:
tolerant about field NAMES, strict about field VALUES.
"""
import pytest

from app.api.live_trading import _map_broker_position, _num


class TestFieldNameTolerance:
    """Several plausible spellings, because the real one is unconfirmed."""

    @pytest.mark.parametrize("sym_key", [
        "trading_symbol", "tradingSymbol", "symbol", "tradingsymbol",
    ])
    def test_symbol_spellings(self, sym_key):
        p = _map_broker_position({sym_key: "reliance", "net_quantity": 10,
                                  "average_price": 1400.0})
        assert p is not None
        assert p["symbol"] == "RELIANCE", "symbol must be upper-cased to match our book"

    @pytest.mark.parametrize("qty_key", [
        "net_quantity", "netQuantity", "quantity", "net_qty",
    ])
    def test_quantity_spellings(self, qty_key):
        p = _map_broker_position({"symbol": "SBIN", qty_key: 5, "average_price": 800.0})
        assert p is not None and p["quantity"] == 5

    @pytest.mark.parametrize("px_key", [
        "average_price", "averagePrice", "avg_price", "buy_price", "net_price", "price",
    ])
    def test_price_spellings(self, px_key):
        p = _map_broker_position({"symbol": "SBIN", "net_quantity": 5, px_key: 799.5})
        assert p is not None and p["entry_price"] == 799.5

    def test_quantity_derived_from_legs_when_no_net_field(self):
        p = _map_broker_position({"symbol": "TCS", "buy_quantity": 12,
                                  "sell_quantity": 4, "average_price": 3000.0})
        assert p is not None and p["quantity"] == 8 and p["action"] == "LONG"


class TestRefusesToGuess:
    """A fabricated number here becomes a real order, so absent means None."""

    def test_no_price_is_skipped_not_defaulted(self):
        # entry_price 0 would make every P&L calculation nonsense and would let
        # a position through with no basis for its own exit accounting.
        assert _map_broker_position({"symbol": "SBIN", "net_quantity": 10}) is None
        assert _map_broker_position(
            {"symbol": "SBIN", "net_quantity": 10, "average_price": 0}) is None

    def test_no_symbol_is_skipped(self):
        assert _map_broker_position({"net_quantity": 10, "average_price": 100.0}) is None

    def test_flat_position_is_not_an_open_one(self):
        # Groww lists squared-off positions with net qty 0. Adopting one would
        # put a phantom in the book that the square-off loop then tries to sell.
        assert _map_broker_position(
            {"symbol": "SBIN", "net_quantity": 0, "average_price": 800.0}) is None
        assert _map_broker_position(
            {"symbol": "SBIN", "buy_quantity": 7, "sell_quantity": 7,
             "average_price": 800.0}) is None

    def test_garbage_input_returns_none(self):
        for bad in (None, [], "RELIANCE", 42):
            assert _map_broker_position(bad) is None

    def test_nan_and_inf_never_become_a_quantity(self):
        # NaN compares false against every threshold, so it slips furthest.
        assert _num(float("nan")) == 0.0
        assert _num(float("inf")) == 0.0
        assert _num("not a number") == 0.0
        assert _map_broker_position(
            {"symbol": "SBIN", "net_quantity": float("nan"), "average_price": 100.0}) is None


class TestDirection:
    def test_negative_quantity_is_a_short(self):
        p = _map_broker_position({"symbol": "SBIN", "net_quantity": -10,
                                  "average_price": 800.0})
        assert p["action"] == "SHORT"
        # Quantity is stored unsigned — direction lives in `action`, and the
        # order placer reads that to decide BUY-to-cover vs SELL.
        assert p["quantity"] == 10

    def test_positive_quantity_is_a_long(self):
        p = _map_broker_position({"symbol": "SBIN", "net_quantity": 10,
                                  "average_price": 800.0})
        assert p["action"] == "LONG" and p["quantity"] == 10


class TestAdoptionMetadata:
    def test_adopted_position_is_tagged_and_unscored(self):
        p = _map_broker_position({"symbol": "SBIN", "net_quantity": 10,
                                  "average_price": 800.0})
        assert p["source"] == "groww_manual", "the UI distinguishes these"
        # Nobody scored a hand-placed trade. A number here would be a fiction
        # that then shows up on a dashboard as if the system had an opinion.
        assert p["confidence"] is None

    def test_product_is_preserved_not_assumed_mis(self):
        p = _map_broker_position({"symbol": "SBIN", "net_quantity": 10,
                                  "average_price": 800.0, "product": "cnc"})
        assert p["product"] == "CNC", "exit orders must use the entry's product"
