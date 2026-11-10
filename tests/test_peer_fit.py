"""Peer classification repair — the rule that keeps Goldman Sachs off IREN's card.

No network: every test hands in the business descriptions directly, which is
the point of keeping the decision in pure functions.
"""
import unittest

from product import peer_fit as pf

# Shaped like the real summaries: a shared domain vocabulary, different detail.
MINER = ("{n} owns and operates bitcoin mining data centres and high performance "
         "computing sites, developing power infrastructure and deploying graphics "
         "processing units for artificial intelligence cloud workloads at its {n} campus.")
BANK = ("{n} is an investment bank and brokerage offering wealth management, "
        "underwriting, securities trading, advisory and asset management to "
        "institutional investors and high net worth individuals.")
SODA = ("{n} makes and sells sparkling soft drinks, juices, bottled water and "
        "syrups and concentrates to bottling partners, wholesalers and retailers "
        "under its beverage brands.")


def texts(**groups):
    out = {}
    for template, syms in groups.items():
        t = {"miner": MINER, "bank": BANK, "soda": SODA}[template]
        for s in syms:
            out[s] = t.format(n=s)
    return out


class Similarity(unittest.TestCase):
    def test_same_text_is_one_and_disjoint_is_zero(self):
        v = pf.tfidf({"a": MINER.format(n="a"), "b": MINER.format(n="a"),
                      "c": BANK.format(n="c")})
        self.assertAlmostEqual(pf.cosine(v["a"], v["b"]), 1.0, places=6)
        self.assertLess(pf.cosine(v["a"], v["c"]), 0.05)

    def test_empty_description_scores_nothing(self):
        v = pf.tfidf({"a": MINER.format(n="a"), "b": ""})
        self.assertEqual(pf.cosine(v["a"], v["b"]), 0.0)


class Verdict(unittest.TestCase):
    def test_rejects_a_cohort_in_a_different_business(self):
        """IREN's case: a miner filed under Capital Markets."""
        t = texts(miner=["IREN", "HUT", "WULF", "CIFR", "APLD"],
                  bank=["MS", "GS", "SCHW", "SF", "TW"])
        v = pf.verdict("IREN", t, ["MS", "GS", "SCHW", "SF", "TW"],
                       ["HUT", "WULF", "CIFR", "APLD"])
        self.assertTrue(v["reject"])
        self.assertLess(v["industry_fit"], v["related_fit"])

    def test_keeps_a_cohort_that_matches(self):
        """KO's case: the co-viewed set is unrelated mega-caps, the cohort is right."""
        t = texts(soda=["KO", "PEP", "MNST", "KDP", "FIZZ"],
                  bank=["JPM", "GS", "MS"], miner=["IREN"])
        v = pf.verdict("KO", t, ["PEP", "MNST", "KDP", "FIZZ"], ["JPM", "GS", "MS", "IREN"])
        self.assertFalse(v["reject"])

    def test_a_good_cohort_survives_an_even_better_co_viewed_set(self):
        """The absolute floor, not just the ratio: both groups being in the same
        business must never dislodge the cohort."""
        t = texts(soda=["KO", "PEP", "MNST", "KDP", "FIZZ", "COKE", "CELH", "COCO"])
        v = pf.verdict("KO", t, ["PEP", "MNST", "KDP", "FIZZ"], ["COKE", "CELH", "COCO"])
        self.assertGreaterEqual(v["industry_fit"], pf.FIT_FLOOR)
        self.assertFalse(v["reject"])

    def test_no_verdict_without_enough_descriptions(self):
        t = texts(miner=["IREN", "HUT"], bank=["MS", "GS"])
        v = pf.verdict("IREN", t, ["MS", "GS"], ["HUT"])
        self.assertFalse(v["reject"])
        self.assertEqual(v["reason"], "too few descriptions to judge")

    def test_overlap_is_reported_as_confirmation_not_as_missing_data(self):
        """JPM's co-viewed set is Citi, BofA and Wells Fargo — already in the
        cohort, so nothing independent is left to test against. That is the
        classification being confirmed, and must not read as a data gap."""
        t = texts(bank=["JPM", "BAC", "C", "WFC", "TD", "GS"], soda=["KO"])
        v = pf.verdict("JPM", t, ["BAC", "C", "WFC", "TD"], ["C", "BAC", "WFC", "GS", "KO"])
        self.assertFalse(v["reject"])
        self.assertEqual(v["reason"], "co-viewed companies are already in the cohort")
        self.assertIsNotNone(v["industry_fit"])     # still shown on the card

    def test_no_verdict_without_a_subject_description(self):
        t = texts(bank=["MS", "GS", "SCHW", "SF"], miner=["HUT", "WULF", "CIFR"])
        t["IREN"] = ""
        v = pf.verdict("IREN", t, ["MS", "GS", "SCHW", "SF"], ["HUT", "WULF", "CIFR"])
        self.assertFalse(v["reject"])
        self.assertIn("no business description", v["reason"])

    def test_an_overlapping_candidate_counts_only_on_the_industry_side(self):
        """A name in both groups must not lift the co-viewed median it is being
        compared against, or the two sides stop being independent."""
        t = texts(miner=["IREN", "HUT", "WULF", "CIFR", "APLD"],
                  bank=["MS", "GS", "SCHW", "SF"])
        v = pf.verdict("IREN", t, ["MS", "GS", "SCHW", "SF", "HUT"],
                       ["HUT", "WULF", "CIFR", "APLD"])
        sim = v["similarity"]
        rel_only = sorted(sim[s] for s in ["WULF", "CIFR", "APLD"])
        self.assertAlmostEqual(v["related_fit"], rel_only[1], places=3)
        ind = sorted(sim[s] for s in ["MS", "GS", "SCHW", "SF", "HUT"])
        self.assertAlmostEqual(v["industry_fit"], ind[2], places=3)


class Replacements(unittest.TestCase):
    def setUp(self):
        self.t = texts(miner=["IREN", "HUT", "WULF", "CRWV", "CIFR", "APLD"],
                       bank=["OKLO", "ONDS"])
        # Nebius: a real peer whose prose shares little with IREN's.
        self.t["NBIS"] = ("NBIS operates an artificial intelligence cloud platform "
                          "providing graphics processing unit compute, storage and "
                          "developer tools to machine learning teams.")

    def test_co_citation_admits_a_peer_the_text_misses(self):
        pool = ["HUT", "WULF", "CRWV", "CIFR", "APLD", "NBIS", "ONDS", "OKLO"]
        cites = {"NBIS": 4, "CRWV": 3, "OKLO": 2, "ONDS": 1}
        ranked = pf.rank_replacements("IREN", self.t, pool, cites, strong_cites=3)
        tier0 = [s for t, s in ranked if t == 0]
        self.assertIn("NBIS", tier0)
        self.assertNotIn("OKLO", [s for _, s in ranked])   # 2 citations is not a majority
        self.assertNotIn("ONDS", tier0)

    def test_text_alone_would_have_dropped_that_peer(self):
        """The regression this guards: without citations NBIS falls under the floor."""
        pool = ["HUT", "WULF", "CRWV", "CIFR", "APLD", "NBIS"]
        ranked = pf.rank_replacements("IREN", self.t, pool, {}, strong_cites=3)
        self.assertNotIn("NBIS", [s for t, s in ranked if t == 0])

    def test_unrelated_names_are_dropped_entirely(self):
        pool = ["HUT", "WULF", "CRWV", "OKLO", "ONDS"]
        ranked = pf.rank_replacements("IREN", self.t, pool, {"OKLO": 1, "ONDS": 1}, 3)
        self.assertEqual({s for _, s in ranked}, {"HUT", "WULF", "CRWV"})

    def test_no_subject_description_yields_nothing(self):
        t = dict(self.t, IREN="")
        self.assertEqual(pf.rank_replacements("IREN", t, ["HUT", "WULF"], {}, 3), [])

    def test_subject_slot_keeps_size_order(self):
        ranked = [("A", 50.0), ("B", 20.0), ("C", 5.0)]
        self.assertEqual(pf._subject_slot(ranked, 30.0), 1)
        self.assertEqual(pf._subject_slot(ranked, 100.0), 0)
        self.assertEqual(pf._subject_slot(ranked, 1.0), 3)


class Boundary(unittest.TestCase):
    def test_calibrated_thresholds_sit_where_the_docstring_says(self):
        """Guards the numbers the calibration sample was fitted to: the observed
        gap runs 0.52 (NBIS, rejected) to 0.66 (LMT, kept)."""
        self.assertEqual((pf.FIT_FLOOR, pf.FIT_RATIO), (0.08, 0.60))
        self.assertTrue(0.52 < pf.FIT_RATIO < 0.66)


class ScaleGap(unittest.TestCase):
    """A right classification can still leave a useless cohort."""

    def test_warns_when_every_peer_is_a_rounding_error(self):
        from product.peers import _scale_note
        note = _scale_note("AAPL", 3.5e12, [1.5e9, 5e8, 3e8])   # Yahoo's real cohort
        self.assertIn("under 0.1%", note)

    def test_silent_for_a_normal_cohort(self):
        from product.peers import _scale_note
        self.assertIsNone(_scale_note("KO", 378e9, [183e9, 87e9, 43e9]))

    def test_silent_when_peers_are_larger(self):
        from product.peers import _scale_note
        self.assertIsNone(_scale_note("IREN", 17e9, [46e9, 11e9]))

    def test_no_sizes_means_no_claim(self):
        from product.peers import _scale_note
        self.assertIsNone(_scale_note("X", 0.0, [1e9]))
        self.assertIsNone(_scale_note("X", 1e9, []))
        self.assertIsNone(_scale_note("X", 1e9, [0.0, None]))


if __name__ == "__main__":
    unittest.main()
