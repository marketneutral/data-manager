Subject: Premium plan — historical price depth capped at ~5 years on all price endpoints

Hi FMP Support,

I'm on the Premium plan (dashboard confirms; 750 API calls/minute). I'm writing
because every price-history endpoint on my key returns only ~5 years of data,
even though the Premium plan description states "Up to 30 Years of Historical
Data."

What I tested today (2026-08-08) with my API key:

1. https://financialmodelingprep.com/api/v3/historical-price-full/AAPL?timeseries=10000
   -> 1,254 rows, 2021-08-10 to 2026-08-07 (only ~5 years), despite timeseries=10000

2. https://financialmodelingprep.com/stable/historical-price-eod/non-split-adjusted?symbol=TSLA
   -> 1,254 rows, 2021-08-10 to 2026-08-07 (only ~5 years)

3. Same ~5-year depth for every symbol on every price endpoint I tried
   (historical-price-full and the new /stable/ EOD endpoints).

The data I do receive looks correct (as-traded prices with split jumps visible,
e.g. TSLA 891.30 -> 296.07 at the 2022-08-25 3:1 split), so this appears to be a
per-plan data-depth limit rather than a data-quality issue.

My questions:

1. Is my key actually provisioned for the 30-year historical depth advertised on
   the Premium plan? Could you verify the plan/depth limits on my account?
   (API key: [YOUR API KEY — insert here])
2. Is there an endpoint-specific depth cap? E.g., does the non-split-adjusted
   EOD endpoint have different (lower) depth limits than historical-price-full?
3. What do I need to do to retrieve 10 years of as-traded daily history
   (back to 2016-08-01) on this plan — a setting, a different param, a plan
   change, or is this a known limitation I should know about?

Happy to share account details or request logs if that helps you look into it.

Thanks,
[Your name]
FMP API user