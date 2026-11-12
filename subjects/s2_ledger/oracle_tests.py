"""Hidden ground-truth oracle for S2. Never shown to agents or gates.

Run as a pytest file against a candidate ``ledger.py``.
"""
import pytest

from ledger import (AccountExists, AccountFrozen, AccountNotEmpty, AccountNotFound, CurrencyMismatch, IdempotencyConflict,
                    InsufficientFunds, InvalidAmount, Ledger, LimitExceeded)


def L(*accts):
    led = Ledger()
    for a, cur, bal in accts:
        led.open_account(a, cur)
        if bal:
            led.deposit(a, bal)
    return led


def test_r1_currencies():
    led = Ledger()
    for i, c in enumerate(["USD", "EUR", "GBP"]):
        led.open_account(f"a{i}", c)
        assert led.balance(f"a{i}") == 0
    with pytest.raises(ValueError):
        led.open_account("x", "JPY")


def test_r2_duplicate():
    led = L(("a", "USD", 500))
    with pytest.raises(AccountExists):
        led.open_account("a", "EUR")
    assert led.balance("a") == 500


def test_r3_ids():
    led = Ledger()
    led.open_account("a" * 20, "USD")
    for bad in ["", "a" * 21, "has space", "dash-id"]:
        with pytest.raises(ValueError):
            led.open_account(bad, "USD")


def test_r4_deposit_amounts():
    led = L(("a", "USD", 0))
    led.deposit("a", 1)
    assert led.balance("a") == 1
    for bad in [0, -5, 1.5, "10"]:
        with pytest.raises(InvalidAmount):
            led.deposit("a", bad)
    assert led.balance("a") == 1


def test_r5_unknown_account():
    led = L(("a", "USD", 100))
    for op in (lambda: led.deposit("zz", 1), lambda: led.withdraw("zz", 1), lambda: led.balance("zz"),
               lambda: led.transfer("a", "zz", 1, "k"), lambda: led.history("zz")):
        with pytest.raises(AccountNotFound):
            op()
    assert led.balance("a") == 100


def test_r5_closed_account_not_found():
    led = L(("a", "USD", 0))
    led.close_account("a")
    with pytest.raises(AccountNotFound):
        led.deposit("a", 5)
    with pytest.raises(AccountNotFound):
        led.balance("a")


def test_r6_withdraw_amounts():
    led = L(("a", "USD", 100))
    led.withdraw("a", 30)
    assert led.balance("a") == 70
    for bad in [0, -5, 2.0]:
        with pytest.raises(InvalidAmount):
            led.withdraw("a", bad)
    assert led.balance("a") == 70


def test_r7_insufficient_and_exact():
    led = L(("a", "USD", 100))
    with pytest.raises(InsufficientFunds):
        led.withdraw("a", 101)
    assert led.balance("a") == 100
    led.withdraw("a", 100)
    assert led.balance("a") == 0


def test_r8_transfer_ids():
    led = L(("a", "USD", 100), ("b", "USD", 0))
    assert led.transfer("a", "b", 10, "k1") == "T1"
    assert led.transfer("a", "b", 10, "k2") == "T2"
    assert (led.balance("a"), led.balance("b")) == (80, 20)


def test_r9_currency_mismatch():
    led = L(("a", "USD", 100), ("b", "EUR", 0))
    with pytest.raises(CurrencyMismatch):
        led.transfer("a", "b", 10, "k")
    assert (led.balance("a"), led.balance("b")) == (100, 0)


def test_r10_self_transfer():
    led = L(("a", "USD", 100))
    with pytest.raises(ValueError):
        led.transfer("a", "a", 10, "k")
    assert led.balance("a") == 100


def test_r11_atomic():
    led = L(("a", "USD", 50), ("b", "USD", 0))
    with pytest.raises(InsufficientFunds):
        led.transfer("a", "b", 51, "k")
    assert (led.balance("a"), led.balance("b")) == (50, 0)


def test_r12_idempotent_replay():
    led = L(("a", "USD", 100), ("b", "USD", 0))
    t = led.transfer("a", "b", 10, "k")
    assert led.transfer("a", "b", 10, "k") == t == "T1"
    assert (led.balance("a"), led.balance("b")) == (90, 10)
    assert led.transfer("a", "b", 10, "k2") == "T2"


def test_r13_conflict():
    led = L(("a", "USD", 100), ("b", "USD", 0))
    led.transfer("a", "b", 10, "k")
    with pytest.raises(IdempotencyConflict):
        led.transfer("a", "b", 11, "k")
    assert (led.balance("a"), led.balance("b")) == (90, 10)


def test_r14_limit_exact_and_exceed():
    led = L(("a", "USD", 3_000_000), ("b", "USD", 0))
    led.withdraw("a", 600_000)
    led.transfer("a", "b", 400_000, "k")
    with pytest.raises(LimitExceeded):
        led.withdraw("a", 1)
    assert led.balance("a") == 2_000_000


def test_r14_withdrawals_count():
    led = L(("a", "USD", 3_000_000))
    led.withdraw("a", 1_000_000)
    with pytest.raises(LimitExceeded):
        led.withdraw("a", 1)


def test_r14_daily_reset():
    led = L(("a", "USD", 3_000_000))
    led.withdraw("a", 1_000_000)
    led.set_day(1)
    led.withdraw("a", 1_000_000)
    assert led.balance("a") == 1_000_000


def test_r14_incoming_not_counted():
    led = L(("a", "USD", 1_000_000), ("b", "USD", 1_000_000))
    led.transfer("a", "b", 1_000_000, "k")
    led.withdraw("b", 1_000_000)
    assert led.balance("b") == 1_000_000


def test_r15_freeze():
    led = L(("a", "USD", 100), ("b", "USD", 100))
    led.freeze("a")
    with pytest.raises(AccountFrozen):
        led.withdraw("a", 1)
    with pytest.raises(AccountFrozen):
        led.transfer("a", "b", 1, "k")
    led.deposit("a", 5)
    led.transfer("b", "a", 5, "k2")
    assert (led.balance("a"), led.balance("b")) == (110, 95)


def test_r16_unfreeze():
    led = L(("a", "USD", 100))
    led.freeze("a")
    led.unfreeze("a")
    led.withdraw("a", 10)
    assert led.balance("a") == 90


def test_r17_close():
    led = L(("a", "USD", 10), ("b", "USD", 0))
    with pytest.raises(AccountNotEmpty):
        led.close_account("a")
    assert led.balance("a") == 10
    led.close_account("b")
    with pytest.raises(AccountExists):
        led.open_account("b", "USD")


def test_r18_balance_type():
    led = L(("a", "USD", 250))
    assert led.balance("a") == 250 and type(led.balance("a")) is int


def test_r19_history():
    led = L(("a", "USD", 0), ("b", "USD", 0))
    led.deposit("a", 100)
    led.withdraw("a", 30)
    with pytest.raises(InsufficientFunds):
        led.withdraw("a", 1000)
    led.transfer("a", "b", 20, "k")
    assert led.history("a") == [("deposit", 100, None), ("withdrawal", 30, None), ("transfer_out", 20, "b")]
    assert led.history("b") == [("transfer_in", 20, "a")]


def test_r20_total_balance():
    led = L(("a", "USD", 100), ("b", "USD", 50), ("c", "EUR", 7))
    led.freeze("b")
    assert led.total_balance("USD") == 150
    assert led.total_balance("EUR") == 7
    assert led.total_balance("GBP") == 0
