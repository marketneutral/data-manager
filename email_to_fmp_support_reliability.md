Subject: Premium plan — repeated connection resets / timeouts during sequential bulk job (no HTTP 429s)

Hi FMP Support,

We're on the Premium plan and hit intermittent connection-level failures during a
sequential bulk pull today (2026-08-08). I'm fairly confident this is not our rate
limit and would like your help confirming what's happening on the server side.

Setup:
- Single-threaded client, one request at a time (no parallelism), with a small
  delay between requests (0.25-0.5s) and a 45s per-request timeout.
- Endpoint mix: /stable/historical-price-eod/non-split-adjusted (as-traded EOD)
  and /api/v3/historical-price-full/{symbol}, plus standard v3 statement endpoints.
- Rate usage: sustained ~2-5 requests/second peak, well under the Premium limit
  of 750 calls/minute (12.5 req/s). We never exceeded roughly a third of it.

Observed behavior:
- ~72 failed requests out of ~5,200, spread across the whole run (not bursty):
  * 37 TimeoutError (read operation timed out, 45s)
  * 16 ConnectionResetError (Errno 54, connection reset by peer)
  * 10 IncompleteRead — e.g. "IncompleteRead(130484 bytes read, 306791 more
    expected)" i.e. the connection was dropped partway through a response
  * 9 URLError (operation timed out)
- ZERO HTTP 429 responses during the entire run. No rate-limit headers seen.
- Errors occurred intermittently across many different symbols/endpoints, at times
  when our request rate was ~1-2 req/s.
- The same endpoints respond normally when a single request is made in isolation.

Impact: the failures are not fatal (we retry), but they tripled the runtime of the
bulk job and left 42 of 2,589 tickers without data after the first pass.

Could you check:

1. Do your server / load-balancer logs show connection resets or dropped
   connections for our key or IP during this window (roughly 11:30-14:00 UTC
   2026-08-08)? If so, are there per-key / per-IP connection-concurrency limits
   that reset (rather than 429) when exceeded?
2. Is there a preferred request pattern for bulk pulls — e.g. a batch endpoint,
   a different API base, or a recommended delay — that avoids these resets?
3. Is the /stable/ non-split-adjusted endpoint on the same infrastructure as
   /api/v3/? The failures hit both.

We'd be happy to share the exact API key and a timestamped request log if that
helps you pin it down.

Thanks,
[Your name]
FMP API user — Premium plan