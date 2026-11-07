"""Drive every on-screen script layout to the end without a window (a crash inside the Tk callback
otherwise shows up only as a window that ignores SPACE)."""
import datetime as dt
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import rt_cit_test as rt  # noqa: E402
from interview.case import case_plan, new_case  # noqa: E402
from interview.fusion import slow_items  # noqa: E402
from interview.identity import make_plan  # noqa: E402

ME = {"first_name": "Daniel", "last_name": "Mitchell", "hometown": "Edmonton", "mother_name": "Karen"}


class _Win:
    class root:
        @staticmethod
        def destroy():
            pass


def drive(blocks, slow=None, wrong_every=0):
    """Answer like a subject: SPACE on screens, correct key on trials (optionally some wrong)."""
    done = []
    gen = rt.make_script(blocks, random.Random(0), done, slow)(_Win())
    v, kinds, n = None, {}, 0
    while True:
        try:
            cmd = gen.send(v)
        except StopIteration:
            return done, kinds
        n += 1
        kinds[cmd[0]] = kinds.get(cmd[0], 0) + 1
        if cmd[0] == "trial":
            t = cmd[1]
            wrong = wrong_every and n % wrong_every == 0
            t.rt, t.outcome = 0.4, "wrong" if wrong else "correct"
            t.key = ("i" if t.correct_key == "e" else "e") if wrong else t.correct_key
            v = t.outcome
        elif cmd[0] == "screen":
            v = "space" if "space" in cmd[3] else next(iter(cmd[3]))
        elif cmd[0] == "flash":
            cmd[1].onset, cmd[1].key, cmd[1].rt = 1.0, "i", 0.6
            v = None
        else:
            v = None


class ScriptTests(unittest.TestCase):
    def test_identity_parts(self):
        plan = make_plan(ME, random.Random(0))
        done, kinds = drive(plan.blocks(2))
        self.assertEqual(len(done), 2 * 2 * 15)
        self.assertEqual({t.group for t in done if t.role == "candidate"}, {0, 1, 2, 3})

    def test_identity_with_sensor_round(self):
        plan = make_plan(ME, random.Random(0))
        slow = slow_items(plan, random.Random(0), 1)
        done, kinds = drive(plan.blocks(2), slow)
        self.assertEqual(kinds["flash"], len(slow))
        self.assertTrue(all(s.onset for s in slow))

    def test_full_session_layout(self):
        rng = random.Random(0)
        plan = make_plan(ME, rng)
        cp = case_plan(new_case(rng, "t", "guilty"))
        rounds = [{"items": slow_items(plan, rng, 1, block="identity_slow")},
                  {"items": slow_items(cp, rng, 1, block="case_slow"), "own": "the case"}]
        blocks = cp.blocks(2, name="case", hide="anything you may know about the case") + plan.blocks(2)
        done, kinds = drive(blocks, rounds)
        self.assertEqual({t.block for t in done}, {"case", "identity"})
        self.assertEqual(kinds["flash"], sum(len(r["items"]) for r in rounds))

    def test_progress_and_stopped_early(self):
        """A full run counts as complete; a run stopped early is INCOMPLETE and never a 'no evidence'."""
        from interview.analysis import INCOMPLETE, mark_progress
        from interview.verdict import code
        plan = make_plan(ME, random.Random(0))
        blocks = plan.blocks(2)
        slow = slow_items(plan, random.Random(0), 1)
        done, _ = drive(blocks, slow)
        pr = rt.progress(blocks, done, slow)
        self.assertEqual(pr, {"keyboard": [60, 60], "sensor": [len(slow), len(slow)]})
        res = mark_progress({"notes": [], "verdict": "NO EVIDENCE of recognition"}, pr)
        self.assertTrue(res["complete"])
        self.assertEqual(code(res["verdict"]), "no_evidence")
        res = mark_progress({"notes": [], "verdict": "NO EVIDENCE of recognition"},
                            {"keyboard": [15, 60], "sensor": [0, 0]})
        self.assertFalse(res["complete"])
        self.assertEqual(res["verdict"], INCOMPLETE)
        self.assertEqual(code(res["verdict"]), "incomplete")
        res = mark_progress({"notes": [], "verdict": "RECOGNIZES these details (strong)"}, None, False)
        self.assertEqual(code(res["verdict"]), "incomplete")      # older file marked by hand

    def test_card_test_and_failed_practice(self):
        blocks = [rt.birthday_block(dt.date(2000, 3, 14), random.Random(0), 2), rt.card_block(2)]
        done, _ = drive(blocks, wrong_every=3)          # practice fails -> repeats -> moves on
        self.assertEqual({t.block for t in done}, {"birthday_check", "cards"})


if __name__ == "__main__":
    unittest.main()
