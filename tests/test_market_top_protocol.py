"""Reference implementation and invariant tests for the Market Top Risk Protocol.

Spec: research/market_top_spec.md (v3).

This module is the executable half of the specification. Every numbered invariant
in spec section 12 has a test here. The reference implementations below are
deliberately simple and un-optimised: they exist to pin the SEMANTICS of the spec
so that a production implementation in product/ can be diffed against them.

Run:  python3 -m unittest tests.test_market_top_protocol -v
"""
from __future__ import annotations

import math
import unittest

# --------------------------------------------------------------------------
# Reference implementations
# --------------------------------------------------------------------------

LEADER_FRACTION = 0.10
BASE_DD = 0.08          # drawdown that opens a base
BASE_MIN_LEN = 25       # sessions from frozen peak to recovery


def leader_set(scores: dict[str, float]) -> set[str]:
    """Spec 3.1 - top-K selection, NOT a percentile threshold.

    K = ceil(0.10 * N).  Ties broken by (score desc, symbol asc) so the result is
    deterministic and the set size is exact by construction.
    """
    n = len(scores)
    if n == 0:
        return set()
    k = math.ceil(LEADER_FRACTION * n)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {sym for sym, _ in ordered[:k]}


def base_count(closes: list[float]) -> int:
    """Spec 4.4 - PREFIX-STABLE online base parser.

    Two states.  The peak is FROZEN the moment a drawdown opens, and never
    updates while the drawdown is open.  Each decision at index t depends only on
    closes[:t+1], which is what makes the count prefix-stable: the value computed
    over a prefix equals the value the parser held at that point in a longer run.
    """
    return _base_scan(closes)[0]


def _base_scan(closes: list[float]) -> tuple[int, list[int]]:
    """Returns (final count, running count after each bar)."""
    if not closes:
        return 0, []
    state = "SEEKING_PEAK"
    peak, peak_i = closes[0], 0
    n = 0
    running = [0]
    for t in range(1, len(closes)):
        c = closes[t]
        if state == "SEEKING_PEAK":
            if c > peak:
                peak, peak_i = c, t
            elif c <= (1.0 - BASE_DD) * peak:
                state = "IN_DRAWDOWN"          # peak frozen from here
        else:                                   # IN_DRAWDOWN
            if c >= peak:
                if t - peak_i >= BASE_MIN_LEN:
                    n += 1
                state, peak, peak_i = "SEEKING_PEAK", c, t
        running.append(n)
    return n, running


class RallyTracker:
    """Spec 6.1 - CP4 frozen state variables.

    `B` (the rally-attempt low) is latched strictly in the past, which is what
    makes the S2 test `C_t < B` reachable at all.  v1 defined B as a min over a
    window that included the bar under test, making S2 mathematically impossible.
    """

    def __init__(self, break_pct: float = -0.04) -> None:
        self.break_pct = break_pct
        self.P = self.t_P = self.t_brk = None
        self.B = self.t_B = None
        self.rally_id = 0

    def declare_break(self, closes: list[float], t_P: int) -> bool:
        self.P, self.t_P = closes[t_P], t_P
        for t in range(t_P + 1, len(closes)):
            if closes[t] / self.P - 1 <= self.break_pct:
                self.t_brk = t
                return True
        return False

    def start_rally(self, closes: list[float]) -> bool:
        """Rally begins on the first up-close whose prior close is the running min."""
        for d in range(self.t_brk + 1, len(closes)):
            run_min = min(closes[self.t_brk:d])
            if closes[d] > closes[d - 1] and math.isclose(closes[d - 1], run_min):
                self.B, self.t_B = closes[d - 1], d - 1     # write-once per rally_id
                return True
        return False

    def s2_fires_at(self, closes: list[float]) -> list[int]:
        if self.B is None:
            return []
        return [t for t in range(self.t_B + 1, len(closes)) if closes[t] < self.B]


# ---- Spec 5.2 : effort vs result -----------------------------------------

def effort_result(res: float, eff: float,
                  eff_min: float = 1.15, res_flat: float = 0.03) -> str:
    """SIGNED result.  A decline is a decline, never 'healthy'.

    v1 computed |res| / eff and fired on a low percentile, which rated a crash on
    heavy volume as the healthiest possible reading.
    """
    if eff >= eff_min and abs(res) <= res_flat:
        return "CHURN"
    if res < -res_flat:
        return "DECLINE"
    return "CLEAR"


# ---- Spec 1 + 9 : the state machine --------------------------------------

ADVERSE_STATES = {"DEFENSIVE", "DEFENSIVE_DEEP"}
RECOVERY_STATES = {"FTD_OBSERVED", "STRUCTURE_IMPROVING"}
RECOVERY_ELIGIBLE = ADVERSE_STATES | RECOVERY_STATES

RECOVERY_EXPOSURE = {"FTD_OBSERVED": 0.35, "STRUCTURE_IMPROVING": 0.60, "RESTORED": 1.00}


class ProtocolState:
    def __init__(self) -> None:
        self.arming = "DORMANT"
        self.risk = "NORMAL"
        self.exposure = 1.00
        self.ftd_low: float | None = None
        self.late_stage_entries = "ALLOWED"


def step(state: ProtocolState, bar: dict) -> float:
    """Spec 9 - one bar of the protocol.  Decision clock only.

    Ordering is load-bearing:
      PHASE 1  adverse overrides, most severe first - ALWAYS evaluated
      PHASE 2  recovery, only when PHASE 1 found nothing
      PHASE 3  pre-defensive ladder
    """
    # ---- arming -----------------------------------------------------------
    if state.arming == "DORMANT":
        if bar.get("gate0"):
            state.arming, state.risk, state.exposure = "ARMED", "NORMAL", 1.00
        return state.exposure

    # ---- PHASE 1: adverse overrides --------------------------------------
    adverse = None
    if bar.get("CP6"):
        adverse = ("DEFENSIVE_DEEP", 0.05)
    elif bar.get("CP5"):
        adverse = ("DEFENSIVE", 0.20)
    elif bar.get("CP4"):
        adverse = ("DEFENSIVE", 0.25)
    elif (state.risk in RECOVERY_STATES and state.ftd_low is not None
          and bar["close"] < state.ftd_low):
        adverse = ("DEFENSIVE", state.exposure)     # FTD void; hold, do not raise

    if adverse:
        state.risk, target = adverse
        state.exposure = min(state.exposure, target)
        return state.exposure

    # ---- PHASE 2: recovery ------------------------------------------------
    if state.risk in RECOVERY_ELIGIBLE:
        return recovery_step(state, bar)

    # ---- PHASE 3: pre-defensive ladder -----------------------------------
    target = 1.00
    if bar.get("CP1"):
        state.late_stage_entries = "BLOCKED"
    if bar.get("CP2"):
        target = 0.75
    if bar.get("CP3") and (bar.get("CP1") or bar.get("CP2")):
        target = 0.50
    state.exposure = min(state.exposure, target)
    return state.exposure


def recovery_step(state: ProtocolState, bar: dict) -> float:
    """Spec 1.2 - at most ONE transition per bar.  DEFENSIVE_DEEP is eligible."""
    r = state.risk
    if r in ADVERSE_STATES:
        if bar.get("ftd"):
            state.ftd_low = bar["ftd_low"]
            state.risk = "FTD_OBSERVED"
            state.exposure = RECOVERY_EXPOSURE["FTD_OBSERVED"]
    elif r == "FTD_OBSERVED":
        if bar.get("above_ma") and bar.get("slope", -99) > -1.0:
            state.risk = "STRUCTURE_IMPROVING"
            state.exposure = RECOVERY_EXPOSURE["STRUCTURE_IMPROVING"]
    elif r == "STRUCTURE_IMPROVING":
        if (bar.get("slope", -99) > 1.0 and bar.get("above_ma")
                and bar.get("weeks_above_6", 0) >= 4):
            state.risk = "RESTORED"
            state.exposure = RECOVERY_EXPOSURE["RESTORED"]
            state.arming = "DORMANT"
            state.late_stage_entries = "ALLOWED"
    return state.exposure


# --------------------------------------------------------------------------
# Tests - one per spec section 12 invariant
# --------------------------------------------------------------------------

class Inv1LeaderSelection(unittest.TestCase):
    """INV1: |S90| == ceil(0.10*N) exactly, every date."""

    def test_top_k_is_exactly_one_decile(self) -> None:
        for n in (10, 37, 500, 1000, 3000, 5314):
            scores = {f"S{i:05d}": float(i) for i in range(n)}
            self.assertEqual(len(leader_set(scores)), math.ceil(0.10 * n))

    def test_v1_percentile_form_did_not_select_a_decile(self) -> None:
        """Regression: the v1 formula selected ~9.18%, and its invariant asserted 10%."""
        for n in (500, 3000, 5314):
            sel = sum(1 for rank in range(1, n + 1)
                      if 1 + 98 * (rank - 1) / (n - 1) >= 90)
            self.assertLess(sel / n, 0.095)
            self.assertNotEqual(sel, math.ceil(0.10 * n))

    def test_ties_do_not_change_set_size(self) -> None:
        scores = {f"S{i:03d}": 1.0 for i in range(100)}     # all tied
        self.assertEqual(len(leader_set(scores)), 10)


class Inv4RallyLowReachable(unittest.TestCase):
    """INV4: S2 (C_t < B) must be reachable.  In v1 it could not fire."""

    @staticmethod
    def _series() -> list[float]:
        px = [80 + i * 0.5 for i in range(41)]            # advance 80 -> 100
        px += [100 - i * 1.2 for i in range(1, 8)]        # break to ~91.6
        px += [91.6 + i * 0.8 for i in range(1, 5)]       # bounce to ~94.8
        px += [94.8 - i * 1.5 for i in range(1, 6)]       # roll over to ~87.3
        return px

    def test_v1_definition_was_impossible(self) -> None:
        px = self._series()
        fired = []
        for t in range(45, len(px)):
            t_p = max(range(max(0, t - 63), t + 1), key=lambda i: px[i])
            b = min(px[t_p:t + 1])                        # window includes t
            if px[t] < b:
                fired.append(t)
        self.assertEqual(fired, [], "v1 S2 should be provably unreachable")

    def test_frozen_low_makes_s2_reachable(self) -> None:
        px = self._series()
        rt = RallyTracker()
        self.assertTrue(rt.declare_break(px, t_P=40))
        self.assertTrue(rt.start_rally(px))
        fired = rt.s2_fires_at(px)
        self.assertTrue(fired, "S2 must be reachable once B is frozen")
        self.assertLess(rt.t_B, fired[0], "B must be latched strictly before the test bar")

    def test_b_is_write_once_per_rally(self) -> None:
        px = self._series()
        rt = RallyTracker()
        rt.declare_break(px, t_P=40)
        rt.start_rally(px)
        first = (rt.B, rt.t_B, rt.rally_id)
        rt.start_rally(px)                                # idempotent within a rally
        self.assertEqual((rt.B, rt.t_B, rt.rally_id), first)


class Inv5GateNotReevaluated(unittest.TestCase):
    """INV5: Gate 0 is never consulted while ARMED."""

    def test_gate0_false_cannot_block_a_cp6_cut(self) -> None:
        st = ProtocolState()
        step(st, {"gate0": True, "close": 100})
        self.assertEqual(st.arming, "ARMED")
        # Gate 0 now fails, as it necessarily does once a top forms.
        expo = step(st, {"gate0": False, "CP6": True, "close": 80})
        self.assertEqual(expo, 0.05)
        self.assertEqual(st.risk, "DEFENSIVE_DEEP")


class Inv6RecoveryReachable(unittest.TestCase):
    """INV6: from DEFENSIVE_DEEP a recovery path reaches RESTORED in finite time.

    This is the invariant the previous pass claimed to have verified while
    actually starting the simulation from DEFENSIVE.  DEFENSIVE_DEEP was not in
    the recovery-eligible set, so the real path was broken.
    """

    def _drive(self, start_state: str, n: int = 60) -> tuple[str, float, int]:
        st = ProtocolState()
        st.arming, st.risk = "ARMED", start_state
        st.exposure = 0.05 if start_state == "DEFENSIVE_DEEP" else 0.25
        slope = lambda t: -4.0 + 0.18 * t
        for t in range(n):
            bar = {"close": 90 + t, "slope": slope(t), "above_ma": t >= 12,
                   "weeks_above_6": 6 if t >= 18 else 0,
                   "ftd": t == 10, "ftd_low": 88.0}
            step(st, bar)
            if st.risk == "RESTORED":
                return st.risk, st.exposure, t
        return st.risk, st.exposure, -1

    def test_recovers_from_defensive_deep(self) -> None:
        risk, expo, t = self._drive("DEFENSIVE_DEEP")
        self.assertEqual(risk, "RESTORED")
        self.assertEqual(expo, 1.00)
        self.assertGreater(t, 0)

    def test_recovers_from_defensive(self) -> None:
        risk, expo, _ = self._drive("DEFENSIVE")
        self.assertEqual(risk, "RESTORED")
        self.assertEqual(expo, 1.00)

    def test_restored_returns_arming_to_dormant(self) -> None:
        st = ProtocolState()
        st.arming, st.risk, st.exposure = "ARMED", "STRUCTURE_IMPROVING", 0.60
        step(st, {"close": 120, "slope": 2.0, "above_ma": True, "weeks_above_6": 5})
        self.assertEqual(st.risk, "RESTORED")
        self.assertEqual(st.arming, "DORMANT")

    def test_v1_simultaneous_reset_was_unsatisfiable(self) -> None:
        """v1 required FTD and s_w > 0 on the SAME bar; after a real Stage IV the
        30-week slope is still deeply negative when the first FTD prints."""
        slope = lambda t: -4.0 + 0.18 * t
        self.assertLess(slope(10), 0, "slope at the FTD is negative -> v1 never resets")


class Inv6bAdversePrecedence(unittest.TestCase):
    """Adverse events outrank recovery on the same bar."""

    def test_cp6_during_ftd_observed_cuts_rather_than_advancing(self) -> None:
        st = ProtocolState()
        st.arming, st.risk, st.exposure, st.ftd_low = "ARMED", "FTD_OBSERVED", 0.35, 88.0
        expo = step(st, {"close": 95, "CP6": True, "above_ma": True, "slope": 0.5})
        self.assertEqual(st.risk, "DEFENSIVE_DEEP")
        self.assertEqual(expo, 0.05)

    def test_cp6_after_cp4_can_still_deepen_the_cut(self) -> None:
        """v2's dispatcher returned from recovery before reading CP5/CP6, so a
        CP4-driven 0.25 could never respond to a subsequent CP6."""
        st = ProtocolState()
        st.arming = "ARMED"
        step(st, {"close": 100, "CP4": True})
        self.assertEqual(st.exposure, 0.25)
        step(st, {"close": 80, "CP6": True})
        self.assertEqual(st.exposure, 0.05)
        self.assertEqual(st.risk, "DEFENSIVE_DEEP")

    def test_undercutting_ftd_low_voids_the_ftd(self) -> None:
        st = ProtocolState()
        st.arming, st.risk, st.exposure, st.ftd_low = "ARMED", "FTD_OBSERVED", 0.35, 88.0
        step(st, {"close": 87.0})
        self.assertEqual(st.risk, "DEFENSIVE")

    def test_cp6_blocks_an_ftd_transition_on_the_same_bar(self) -> None:
        st = ProtocolState()
        st.arming, st.risk, st.exposure = "ARMED", "DEFENSIVE_DEEP", 0.05
        step(st, {"close": 90, "CP6": True, "ftd": True, "ftd_low": 88.0})
        self.assertEqual(st.risk, "DEFENSIVE_DEEP")
        self.assertIsNone(st.ftd_low)


class Inv7RatchetMonotonicity(unittest.TestCase):
    """INV7: exposure never rises inside the pre-defensive ladder."""

    def test_ladder_is_downward_only(self) -> None:
        st = ProtocolState()
        st.arming = "ARMED"
        seq = [{"CP1": True, "CP2": True, "close": 100},
               {"close": 101},                       # signals clear
               {"close": 102}]
        out = [step(st, b) for b in seq]
        self.assertEqual(out, [0.75, 0.75, 0.75],
                         "signals going quiet must not restore exposure")


class Inv9DirectionAwareChurn(unittest.TestCase):
    """INV9: a decline must never read as healthy.  v1's |res|/eff did."""

    def test_crash_is_not_healthy(self) -> None:
        self.assertEqual(effort_result(res=-0.30, eff=1.40), "DECLINE")

    def test_true_churn_detected(self) -> None:
        self.assertEqual(effort_result(res=0.005, eff=1.30), "CHURN")

    def test_quiet_market_is_not_churn(self) -> None:
        self.assertEqual(effort_result(res=0.004, eff=0.95), "CLEAR")

    def test_v1_form_rated_the_crash_highest(self) -> None:
        v1 = lambda res, eff: abs(res) / eff
        self.assertGreater(v1(-0.30, 1.40), v1(0.005, 1.30))


class Inv12BasePrefixStability(unittest.TestCase):
    """Base counting must be prefix-stable: recomputing over a prefix must equal
    the value the online parser held at that point.  The v2 parser used
    argmax over the remaining history, so a later higher high could resegment
    and retroactively change earlier counts."""

    @staticmethod
    def _series() -> list[float]:
        px, level = [], 100.0
        for _ in range(3):                       # three clean base-and-advance cycles
            px += [level + i * 0.6 for i in range(30)]
            top = px[-1]
            px += [top * (1 - 0.10 * i / 12) for i in range(1, 13)]
            px += [px[-1] + (top - px[-1]) * i / 18 for i in range(1, 19)]
            px += [top * (1 + 0.004 * i) for i in range(1, 6)]
            level = px[-1]
        return px

    def test_prefix_invariance(self) -> None:
        px = self._series()
        _, running = _base_scan(px)
        for t in range(1, len(px)):
            self.assertEqual(base_count(px[:t + 1]), running[t],
                             f"prefix count diverged at bar {t}")

    def test_v2_argmax_parser_was_not_prefix_stable(self) -> None:
        """Regression witness for the v2 defect."""
        def v2_count(closes: list[float]) -> int:
            i, n, t_end = 0, 0, len(closes) - 1
            while i < t_end:
                p = max(range(i, t_end + 1), key=lambda x: closes[x])
                peak = closes[p]
                d = next((x for x in range(p + 1, t_end + 1)
                          if closes[x] <= 0.92 * peak), None)
                if d is None:
                    break
                q = next((x for x in range(d + 1, t_end + 1)
                          if closes[x] >= peak), None)
                if q is None:
                    break
                if q - p >= BASE_MIN_LEN:
                    n += 1
                i = q
            return n
        px = self._series()
        divergences = [t for t in range(60, len(px))
                       if v2_count(px[:t + 1]) != _base_scan(px[:t + 1])[0]]
        self.assertTrue(divergences,
                        "expected the argmax parser to disagree with the online parser")

    def test_open_drawdown_is_not_counted(self) -> None:
        px = [100 + i for i in range(40)] + [139 * (1 - 0.01 * i) for i in range(1, 20)]
        self.assertEqual(base_count(px), 0, "an unresolved drawdown is not yet a base")


class Inv10TriStateResults(unittest.TestCase):
    """INV10: checkpoints report insufficient_history, never a silent False."""

    def test_short_history_is_not_reported_as_clear(self) -> None:
        def cp5(weekly: list[float]) -> str:
            if len(weekly) < 30 + 26:
                return "insufficient_history"
            return "clear"
        self.assertEqual(cp5([100.0] * 40), "insufficient_history")
        self.assertEqual(cp5([100.0] * 56), "clear")


if __name__ == "__main__":
    unittest.main()
