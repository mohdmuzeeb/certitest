"""Reference implementation of the S2 multi-account ledger service.

Generic illustrative domain written for this study; not derived from any
production system.
"""
from __future__ import annotations

CURRENCIES = ("USD", "EUR", "GBP")
DAILY_LIMIT = 1_000_000  # cents


class LedgerError(Exception):
    pass


class AccountExists(LedgerError):
    pass


class AccountNotFound(LedgerError):
    pass


class InvalidAmount(LedgerError):
    pass


class InsufficientFunds(LedgerError):
    pass


class CurrencyMismatch(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    pass


class LimitExceeded(LedgerError):
    pass


class AccountFrozen(LedgerError):
    pass


class AccountNotEmpty(LedgerError):
    pass


class Ledger:
    def __init__(self):
        self._accounts = {}
        self._used_ids = set()
        self._history = {}
        self._frozen = set()
        self._day = 0
        self._outgoing = {}
        self._transfers = {}
        self._next_transfer = 1

    # ------------------------------------------------------------ helpers
    def _get(self, account_id):
        if account_id not in self._accounts:
            raise AccountNotFound(account_id)
        return self._accounts[account_id]

    @staticmethod
    def _check_amount(amount):
        if type(amount) is not int or amount <= 0:
            raise InvalidAmount(amount)

    def _check_limit(self, account_id, amount):
        used = self._outgoing.get((account_id, self._day), 0)
        if used + amount > DAILY_LIMIT:
            raise LimitExceeded(account_id)

    def _record_outgoing(self, account_id, amount):
        key = (account_id, self._day)
        self._outgoing[key] = self._outgoing.get(key, 0) + amount

    # ------------------------------------------------------------ accounts
    def open_account(self, account_id, currency):
        if not isinstance(account_id, str) or not account_id or not account_id.isalnum() or len(account_id) > 20:
            raise ValueError("invalid account id")
        if currency not in CURRENCIES:
            raise ValueError("unsupported currency")
        if account_id in self._used_ids:
            raise AccountExists(account_id)
        self._accounts[account_id] = {"currency": currency, "balance": 0}
        self._used_ids.add(account_id)
        self._history[account_id] = []

    def close_account(self, account_id):
        acct = self._get(account_id)
        if acct["balance"] != 0:
            raise AccountNotEmpty(account_id)
        del self._accounts[account_id]
        self._frozen.discard(account_id)

    def freeze(self, account_id):
        self._get(account_id)
        self._frozen.add(account_id)

    def unfreeze(self, account_id):
        self._get(account_id)
        self._frozen.discard(account_id)

    def set_day(self, day):
        self._day = day

    # ------------------------------------------------------------ money movement
    def deposit(self, account_id, amount):
        acct = self._get(account_id)
        self._check_amount(amount)
        acct["balance"] += amount
        self._history[account_id].append(("deposit", amount, None))

    def withdraw(self, account_id, amount):
        acct = self._get(account_id)
        self._check_amount(amount)
        if account_id in self._frozen:
            raise AccountFrozen(account_id)
        if amount > acct["balance"]:
            raise InsufficientFunds(account_id)
        self._check_limit(account_id, amount)
        acct["balance"] -= amount
        self._record_outgoing(account_id, amount)
        self._history[account_id].append(("withdrawal", amount, None))

    def transfer(self, src, dst, amount, idempotency_key):
        params = (src, dst, amount)
        if idempotency_key in self._transfers:
            tid, prev = self._transfers[idempotency_key]
            if prev != params:
                raise IdempotencyConflict(idempotency_key)
            return tid
        if src == dst:
            raise ValueError("source and destination must differ")
        s = self._get(src)
        d = self._get(dst)
        self._check_amount(amount)
        if s["currency"] != d["currency"]:
            raise CurrencyMismatch(f"{s['currency']} != {d['currency']}")
        if src in self._frozen:
            raise AccountFrozen(src)
        if amount > s["balance"]:
            raise InsufficientFunds(src)
        self._check_limit(src, amount)
        s["balance"] -= amount
        d["balance"] += amount
        self._record_outgoing(src, amount)
        tid = f"T{self._next_transfer}"
        self._next_transfer += 1
        self._transfers[idempotency_key] = (tid, params)
        self._history[src].append(("transfer_out", amount, dst))
        self._history[dst].append(("transfer_in", amount, src))
        return tid

    # ------------------------------------------------------------ queries
    def balance(self, account_id):
        return self._get(account_id)["balance"]

    def history(self, account_id):
        self._get(account_id)
        return list(self._history[account_id])

    def total_balance(self, currency):
        return sum(a["balance"] for a in self._accounts.values() if a["currency"] == currency)
