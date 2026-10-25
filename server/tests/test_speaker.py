"""Speech in a rally: only calls that still matter get read out, and an OUT never waits behind an IN."""
import sys
import time

from pipeline import Speaker

# stands in for `say`: logs when it starts and finishes reading, takes 0.6 s
FAKE_SAY = ("import sys, time; log = open(sys.argv[1], 'a'); log.write('start ' + sys.argv[-1] + '\\n'); log.flush();"
            "time.sleep(0.6); log.write('end ' + sys.argv[-1] + '\\n')")


def make(tmp_path):
    log = tmp_path / "said.txt"
    log.write_text("")
    return Speaker(command=[sys.executable, "-c", FAKE_SAY, str(log)]), log


def said(log, wait):
    time.sleep(wait)
    return log.read_text().split("\n")[:-1]


def test_a_newer_call_replaces_the_one_waiting(tmp_path):
    sp, log = make(tmp_path)
    sp.say("A")
    time.sleep(0.25)
    sp.say("B")
    sp.say("C")
    assert said(log, 1.8) == ["start A", "end A", "start C", "end C"]


def test_an_out_cuts_off_an_in_being_read(tmp_path):
    sp, log = make(tmp_path)
    sp.say("In 1")
    time.sleep(0.3)
    sp.say("Out 2", urgent=True)
    assert said(log, 1.4) == ["start In 1", "start Out 2", "end Out 2"]


def test_an_in_never_pushes_out_a_waiting_out(tmp_path):
    sp, log = make(tmp_path)
    sp.say("Out 1", urgent=True)
    time.sleep(0.25)
    sp.say("Out 2", urgent=True)  # waits: an OUT does not cut off another OUT
    sp.say("In 3")
    assert said(log, 1.9) == ["start Out 1", "end Out 1", "start Out 2", "end Out 2"]


def test_a_stale_call_is_dropped(tmp_path):
    sp, log = make(tmp_path)
    sp.MAX_AGE_S = 0.2
    sp.say("A")
    time.sleep(0.1)
    sp.say("B")  # still waiting 0.5 s later, when A finishes: too old to be useful
    assert said(log, 1.2) == ["start A", "end A"]
