"""Score the live server against a clip's truth file while fake phones stream it.

python tools/e2e_check.py --cam "Left sideline" --truth clips/demo30.truth.json [--seconds 50]
Calibrates the named camera from the truth file, waits, then matches events to the known bounces.
"""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(url):
    with urllib.request.urlopen(url, context=ctx, timeout=10) as r:
        return json.loads(r.read())


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://localhost:8443")
    ap.add_argument("--cam", required=True, action="append", help="camera name (repeatable)")
    ap.add_argument("--truth", required=True, action="append", help="truth json per --cam, same order")
    ap.add_argument("--seconds", type=float, default=50)
    ap.add_argument("--margin", type=float, default=2.0)
    args = ap.parse_args()

    # wait for the cameras to show up with frames
    deadline = time.time() + 60
    cams = {}
    while time.time() < deadline and len(cams) < len(args.cam):
        st = get(f"{args.base}/api/state")
        for c in st["cams"]:
            if c["name"] in args.cam and c["status"] == "LIVE" and c["capture"]:
                cams[c["name"]] = c
        if len(cams) < len(args.cam):
            time.sleep(1)
    missing = [n for n in args.cam if n not in cams]
    if missing:
        print(f"FAIL: cameras never went live: {missing}")
        print(json.dumps(get(f"{args.base}/api/state"), indent=1)[:2000])
        return 1
    for n, c in cams.items():
        print(f"{n}: capture {c['capture']} in {c['fps_in']} fps judged {c['fps_proc']} fps phone={c.get('phone')}")

    truths = {}
    for n, tpath in zip(args.cam, args.truth):
        truth = json.load(open(tpath))
        truths[n] = truth
        p1, p2, in_pt = truth["calibration"]
        cam = cams[n]
        # the clip is 1280x720; the server's work size equals capture if <= 1280 wide
        sx = cam["work"][0] / 1280.0
        sy = cam["work"][1] / 720.0
        body = {"p1": [p1[0] * sx, p1[1] * sy], "p2": [p2[0] * sx, p2[1] * sy], "in": [in_pt[0] * sx, in_pt[1] * sy],
                "ball": None, "coords": "work"}
        res = post(f"{args.base}/api/cam/{cam['id']}/calibrate", body)
        print(f"{n}: calibrated, scale={res.get('scale')} snapped={res.get('snapped')}")
    post(f"{args.base}/api/settings", {"margin_cm": args.margin})

    before = get(f"{args.base}/api/events")
    last_id = max((e.get("id", 0) for e in before), default=0)
    print(f"watching for {args.seconds:.0f}s …")
    time.sleep(args.seconds)
    new = get(f"{args.base}/api/events?after={last_id}")
    st = get(f"{args.base}/api/state")
    for c in st["cams"]:
        if c["name"] in cams:
            print(f"{c['name']}: in {c['fps_in']} fps judged {c['fps_proc']} fps status {c['status']}")

    ok = True
    for n in args.cam:
        evs = [e for e in new if e["cam_name"] == n]
        exp = truths[n]["bounces"]
        print(f"\n{n}: {len(evs)} events for {len(exp)} bounces per loop ({truths[n]['fps']} fps clip)")
        matched = 0
        wrong = 0
        for e in evs:
            # nearest truth bounce by gap
            best = min(exp, key=lambda b: abs(b["gap_cm"] - (e["gap_cm"] if e["gap_cm"] is not None else 99)))
            d = None if e["gap_cm"] is None else e["gap_cm"] - best["gap_cm"]
            # the call must match; the number may drift 1 cm + 10% (far-out balls sit at a different depth)
            tol = 1.0 + 0.1 * abs(best["gap_cm"])
            good = e["verdict"] == best["expected"] and d is not None and abs(d) <= tol
            matched += good
            wrong += not good
            flag = "" if good else "   <-- check"
            print(f"  {e['wall_time']} {e['verdict']:<9} meas {e['gap_cm']!s:>6} cm  nearest truth {best['gap_cm']:+.1f} "
                  f"({best['expected']}) Z={best['Zb']} q={e['quality']} {e['n_before']}+{e['n_after']} {e['reason']}{flag}")
        print(f"  matched {matched}, off {wrong}")
        if len(evs) == 0 or wrong > max(1, len(evs) // 5):
            ok = False
    print("\nE2E", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
