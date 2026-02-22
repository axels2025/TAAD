"""Test script: Can we get 7 days of past trades via IBKR API?

Compares four methods:
1. ib.fills() — current session only
2. ib.reqExecutions() — default (no lastNDays filter)
3. ib.reqExecutions() + lastNDays=7 — explicitly request 7 days
4. ib.reqCompletedOrders() — completed orders from prior sessions

DELETE THIS FILE after testing.
"""

import sys
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, ".")

from src.config.base import IBKRConfig
from src.tools.ibkr_client import IBKRClient


# ---------------------------------------------------------------------------
# Monkey-patch: ib_insync's reqExecutions does NOT send lastNDays /
# specificDates fields (added at TWS server version 200).  We patch the
# low-level client.reqExecutions to append these fields when present on the
# filter object.
# ---------------------------------------------------------------------------
def _patch_req_executions(ib_client):
    """Patch ib_insync's Client.reqExecutions to support lastNDays."""
    original = ib_client.client.reqExecutions

    def patched_reqExecutions(reqId, execFilter):
        # Check if we have the extended fields and server supports them
        last_n = getattr(execFilter, "lastNDays", None)
        specific = getattr(execFilter, "specificDates", None)
        server_ver = ib_client.client.serverVersion()

        if last_n is None and specific is None:
            # No extended fields — use original
            return original(reqId, execFilter)

        if server_ver < 200:
            print(f"  WARNING: Server version {server_ver} < 200, "
                  f"lastNDays/specificDates not supported")
            return original(reqId, execFilter)

        # Build the message manually with the extra fields
        # Protocol: msg_type=7, version=3, reqId, clientId, acctCode, time,
        #           symbol, secType, exchange, side,
        #           [lastNDays, numSpecificDates, specificDate...]
        print(f"  (Sending patched reqExecutions with lastNDays={last_n}, "
              f"specificDates={specific}, server_ver={server_ver})")

        UNSET_INT = 2**31 - 1
        last_n_val = last_n if last_n is not None else UNSET_INT

        fields = [
            7, 3, reqId,
            execFilter.clientId,
            getattr(execFilter, "acctCode", ""),
            getattr(execFilter, "time", ""),
            getattr(execFilter, "symbol", ""),
            getattr(execFilter, "secType", ""),
            getattr(execFilter, "exchange", ""),
            getattr(execFilter, "side", ""),
            last_n_val,
        ]

        if specific is not None and len(specific) > 0:
            fields.append(len(specific))
            for d in specific:
                fields.append(d)
        else:
            fields.append(0)

        ib_client.client.send(*fields)

    ib_client.client.reqExecutions = patched_reqExecutions


def _print_executions(label, executions):
    """Helper to print execution details and date summary."""
    by_date = defaultdict(list)
    for f in executions:
        ftime = f.execution.time if hasattr(f, "execution") else None
        if ftime:
            date_str = ftime.strftime("%Y-%m-%d") if hasattr(ftime, "strftime") else str(ftime)[:10]
            by_date[date_str].append(f)

    print(f"  Executions by date:")
    for date_str in sorted(by_date.keys()):
        print(f"    {date_str}: {len(by_date[date_str])} executions")
    print()

    for f in executions[:15]:
        sym = f.contract.symbol if hasattr(f, "contract") else "?"
        strike = f.contract.strike if hasattr(f.contract, "strike") else ""
        right = f.contract.right if hasattr(f.contract, "right") else ""
        side = f.execution.side if hasattr(f, "execution") else "?"
        price = f.execution.avgPrice if hasattr(f, "execution") else 0
        qty = f.execution.shares if hasattr(f, "execution") else 0
        ftime = f.execution.time if hasattr(f, "execution") else "?"
        order_id = f.execution.orderId if hasattr(f, "execution") else "?"
        perm_id = f.execution.permId if hasattr(f, "execution") else "?"
        comm = f.commissionReport.commission if hasattr(f, "commissionReport") and f.commissionReport else 0
        print(
            f"  {ftime} | {sym} {strike}{right} {side} {qty}x @ ${price:.2f} "
            f"(comm: ${comm:.2f}, orderId={order_id}, permId={perm_id})"
        )
    if len(executions) > 15:
        print(f"  ... and {len(executions) - 15} more")
    print()

    return by_date


def main():
    config = IBKRConfig()
    client = IBKRClient(config)

    print("Connecting to IBKR...")
    client.connect()
    print("Connected.")
    print(f"  Server version: {client.ib.client.serverVersion()}")
    print(f"  Server version >= 200 (lastNDays support): "
          f"{client.ib.client.serverVersion() >= 200}\n")

    # Apply the monkey-patch
    _patch_req_executions(client.ib)

    try:
        # ── Method 1: ib.fills() ──
        print("=" * 60)
        print("METHOD 1: ib.fills() (current session only)")
        print("=" * 60)
        fills = client.ib.fills()
        print(f"  Results: {len(fills)} fills\n")
        for f in fills[:10]:
            sym = f.contract.symbol if hasattr(f, "contract") else "?"
            strike = f.contract.strike if hasattr(f.contract, "strike") else ""
            side = f.execution.side if hasattr(f, "execution") else "?"
            price = f.execution.avgPrice if hasattr(f, "execution") else 0
            ftime = f.execution.time if hasattr(f, "execution") else "?"
            comm = f.commissionReport.commission if hasattr(f, "commissionReport") and f.commissionReport else 0
            print(f"  {ftime} | {sym} {strike} {side} @ ${price:.2f} (comm: ${comm:.2f})")
        if len(fills) > 10:
            print(f"  ... and {len(fills) - 10} more")
        print()

        # ── Method 2: ib.reqExecutions() — default (no lastNDays) ──
        print("=" * 60)
        print("METHOD 2: ib.reqExecutions() — DEFAULT (no lastNDays)")
        print("=" * 60)
        from ib_insync import ExecutionFilter

        exec_filter = ExecutionFilter()
        executions = client.ib.reqExecutions(exec_filter)
        print(f"  Results: {len(executions)} executions\n")
        by_date_default = _print_executions("default", executions)

        # ── Method 3: ib.reqExecutions() + lastNDays=7 ──
        print("=" * 60)
        print("METHOD 3: ib.reqExecutions() — WITH lastNDays=7")
        print("=" * 60)

        exec_filter_7d = ExecutionFilter()
        # Attach the extended field (ib_insync's ExecutionFilter doesn't have
        # it, but our monkey-patch reads it via getattr)
        exec_filter_7d.lastNDays = 7

        executions_7d = client.ib.reqExecutions(exec_filter_7d)
        print(f"  Results: {len(executions_7d)} executions\n")
        by_date_7d = _print_executions("7-day", executions_7d)

        # Compare
        print("  ── Comparison: Default vs lastNDays=7 ──")
        all_dates = sorted(set(list(by_date_default.keys()) + list(by_date_7d.keys())))
        for d in all_dates:
            cnt_def = len(by_date_default.get(d, []))
            cnt_7d = len(by_date_7d.get(d, []))
            marker = " ← NEW" if cnt_def == 0 and cnt_7d > 0 else ""
            print(f"    {d}: default={cnt_def}, 7-day={cnt_7d}{marker}")
        print()

        # ── Method 4: ib.reqCompletedOrders() ──
        print("=" * 60)
        print("METHOD 4: ib.reqCompletedOrders() (prior sessions)")
        print("=" * 60)
        completed = client.ib.reqCompletedOrders(apiOnly=False)
        print(f"  Results: {len(completed)} completed orders\n")
        for t in completed[:10]:
            sym = t.contract.symbol if hasattr(t, "contract") else "?"
            strike = t.contract.strike if hasattr(t.contract, "strike") else ""
            right = t.contract.right if hasattr(t.contract, "right") else ""
            status = t.orderStatus.status if hasattr(t, "orderStatus") else "?"
            order_id = t.order.orderId
            perm_id = t.order.permId
            lmt = t.order.lmtPrice if hasattr(t.order, "lmtPrice") else 0
            avg_fill = t.orderStatus.avgFillPrice if hasattr(t.orderStatus, "avgFillPrice") else 0
            filled_qty = t.orderStatus.filled if hasattr(t.orderStatus, "filled") else 0
            trade_fills = len(t.fills) if hasattr(t, "fills") and t.fills else 0
            print(
                f"  {sym} {strike}{right} | {status} | "
                f"lmt=${lmt:.2f}, avgFill=${avg_fill:.2f}, qty={filled_qty} | "
                f"orderId={order_id}, permId={perm_id}, fills={trade_fills}"
            )
        if len(completed) > 10:
            print(f"  ... and {len(completed) - 10} more")
        print()

        # ── Summary ──
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Method 1 - ib.fills():                {len(fills)} (current session)")
        print(f"  Method 2 - reqExecutions(default):     {len(executions)} executions")
        print(f"  Method 3 - reqExecutions(lastNDays=7): {len(executions_7d)} executions")
        print(f"  Method 4 - reqCompletedOrders():       {len(completed)} completed orders")
        print()

        if by_date_default:
            dates = sorted(by_date_default.keys())
            print(f"  reqExecutions DEFAULT date range: {dates[0]} to {dates[-1]} "
                  f"({len(dates)} day(s))")
        if by_date_7d:
            dates = sorted(by_date_7d.keys())
            print(f"  reqExecutions 7-DAY   date range: {dates[0]} to {dates[-1]} "
                  f"({len(dates)} day(s))")

        delta = len(executions_7d) - len(executions)
        if delta > 0:
            print(f"\n  ✓ lastNDays=7 returned {delta} MORE executions!")
        elif delta == 0:
            print(f"\n  → Same count. Server may not have older data, or "
                  f"lastNDays not needed for this range.")
        else:
            print(f"\n  ⚠ lastNDays=7 returned FEWER ({delta}). Unexpected.")
        print()

    finally:
        client.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    main()
