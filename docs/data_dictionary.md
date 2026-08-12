# data-manager data dictionary

Column-level definitions for every table in `~/.prime/agent/data_manager.db`. Vendor definitions come from **Sharadar's own `descriptions` table** — a downloadable bulk dataset (one row per table+field: table, indicator, isfilter, isprimarykey, title, description, unittype) that this warehouse stores as the live `descriptions` table and refreshes with every `bulk_update`. Derived-table definitions are written from this repo's code. Machine-readable copy: `docs/data_dictionary.json`; the SF1 appendix of `report/data.qmd` shows the same 112-field SF1 dictionary grouped by family.

## Coverage map

| table | origin | dictionary source | key |
|---|---|---|---|
| `securities_master` | RAW (tickers zip) | `descriptions` (TICKERS) | permaticker (PK); ticker unique |
| `corporate_actions` | RAW (actions zip) | `descriptions` (ACTIONS) | (ticker, date, action) |
| `sp500_membership` | RAW (Sharadar API) | `descriptions` (SP500) | (ticker, date) |
| `metrics` | RAW (metrics zip) | `descriptions` (METRICS) | (ticker, as_of) |
| `sf1` | RAW (fundamentals zip) | `descriptions` (SF1) | (ticker, dimension, reportperiod) |
| `sf1_blob` | RAW (fundamentals zip) | `descriptions` (SF1) | same as sf1 |
| `prices` | RAW (stocks+funds zips) | `descriptions` (SEP/SFP) | (ticker, date) |
| `fundamentals` | DERIVED (sf1 ARY) | this repo | (ticker, fiscal_year) |
| `quarterly_statements` | DERIVED (sf1 ARQ) | this repo | (ticker, period) |
| `ratios` | DERIVED (sf1 MRY + price) | this repo | ticker (one row) |
| `universe_pit` | DERIVED (master+prices+sf1) | this repo | (as_of, ticker) |
| `classifications` | DERIVED (master + GICS map) | this repo | ticker |
| `universe` | DERIVED (iShares IWV) | this repo | ticker |
| `snapshots` | ledger (every pull) | this repo | id |

## Raw tables (Sharadar)

### `securities_master` — RAW (tickers zip)

| column | unit | definition |
|---|---|---|
| `permaticker` | text | Permanent Ticker Symbol — The permaticker is a unique and unchanging identifier issued by Sharadar for a security in the dataset. Distinct share classes of the same issuer receive distinct permatickers (for example primary and secondary common classes). This field is not included in other tables in order to maximise data scalability in those tables; join from those tables via ticker to TICKERS (filter on the table field as needed; e.g. SEP or SF1). |
| `ticker` | text | Ticker Symbol — The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. The ACTIONS table provides a record of historical ticker changes. |
| `name` | text | Issuer Name — The name of the security issuer. |
| `exchange` | text | Stock Exchange — The exchange on which the security's primary listing is held. Examples are: |
| `isdelisted` | Y/N | Is Delisted? — Is the security delisted? [Y]es or [N]o. |
| `category` | text | Issuer Category — The category of the issuer: Domestic; Canadian or ADR. This field is based on the filing category with the SEC. Domestic firms file form 10; ADRs file form 20 and Canadian firms file form 40. |
| `cusips` | text | CUSIPs — A security identifier. Space delimited in the event of multiple identifiers. |
| `siccode` | text | Standard Industrial Classification (SIC) Code — The Standard Industrial Classification (SIC) is a system for classifying industries by a four-digit code; as sourced from SEC filings. More on the SIC system here: https://en.wikipedia.org/wiki/Standard_Industrial_Classification |
| `sicsector` | text | SIC Sector — The SIC sector is based on the SIC code and the division tabled here: https://en.wikipedia.org/wiki/Standard_Industrial_Classification |
| `sicindustry` | text | SIC Industry — The SIC industry is based on the SIC code and the industry tabled here: https://www.sec.gov/info/edgar/siccodes.htm |
| `figi` | text | Composite FIGI — The FIGI is a security identifier provided by openfigi.com. There are different types of FIGI; and this indicator represents the Composite FIGI type for US exchanges in the event that this is available in the openfigi.com API; or alternatively the non-composite FIGI type for US exchanges if only this is available. In the event of multiple FIGIs these are space delimited. This indicator is not available for all delisted stocks due to limitations in the openfigi.com API. |
| `famaindustry` | text | Fama Industry — Industry classifications based on the SIC code and classifications by Fama and French here: http://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_48_ind_port.html |
| `sector` | text | Sector — A sector classification based on SIC codes in a format which approximates to GICS. |
| `industry` | text | Industry — An industry classification based on SIC codes in a format which approximates to GICS. |
| `scalemarketcap` | text | Company Scale - Market Cap — This field is experimental and subject to change. It categorises the company according to it's maximum observed market cap as follows: 1 - Nano <$50m; 2 - Micro < $300m; 3 - Small < $2bn; 4 - Mid <$10bn; 5 - Large < $200bn; 6 - Mega >= $200bn |
| `scalerevenue` | text | Company Scale - Revenue — This field is experimental and subject to change. It categorises the company according to it's maximum observed annual revenue as follows: 1 - Nano <$50m; 2 - Micro < $300m; 3 - Small < $2bn; 4 - Mid <$10bn; 5 - Large < $200bn; 6 - Mega >= $200bn |
| `relatedtickers` | text | Related Tickers — Where related tickers have been identified this field is populated. Related tickers can include the prior ticker before a ticker change; and it includes tickers for alternative share classes. |
| `currency` | text | Currency — The company functional reporting currency for the SF1 Fundamentals table or the currency for EOD prices in SEP and SFP. |
| `location` | text | Location — The company location as registered with the Securities and Exchange Commission. |
| `firstadded` | date (YYYY-MM-DD) | First Added Date — The date that the ticker was first added to coverage in the dataset. |
| `firstpricedate` | date (YYYY-MM-DD) | First Price Date — The date of the first price observation for a given ticker. Can be used as a proxy for IPO date. Minimum value of 1986-01-01 for IPO's that occurred prior to this date. Note: this does not necessarily represent the first price date available in our datasets since our end of day price history currently starts in December 1997. |
| `lastpricedate` | date (YYYY-MM-DD) | Last Price Date — The most recent price observation available. |
| `firstquarter` | date (YYYY-MM-DD) | First Quarter — The first financial quarter available in the dataset. |
| `lastquarter` | date (YYYY-MM-DD) | Last Quarter — The last financial quarter available in the dataset. |
| `secfilings` | text | SEC Filings URL — The URL pointing to the SEC filings which also contains the Central Index Key (CIK). |
| `companysite` | text | Company Website URL — The URL pointing to the company website. |
| `lastupdated` | date (YYYY-MM-DD) | Last Updated Date — Last Updated represents the last date that this database entry was updated; which is useful to users when updating their local records. |
| `table` | text | Table — The database table which the ticker is featured in. Examples are: SF1 or SEP. |

### `corporate_actions` — RAW (actions zip)

| column | unit | definition |
|---|---|---|
| `ticker` | text | Ticker Symbol — The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. The ACTIONS table provides a record of historical ticker changes. |
| `date` | date (YYYY-MM-DD) | Date — The date of the corporate action. |
| `action` | text | Action — The available actions in this field are outlined in the INDICATORS table where the table field equals ACTIONTYPES. |
| `name` | text | Issuer Name — The name of the issuer. |
| `value` | numeric | Value — The available values in this field are outlined in the INDICATORS table where the table field equals ACTIONTYPES. |
| `contraticker` | text | Contra Ticker Symbol — The contra ticker associated with the action; if applicable. |
| `contraname` | text | Contra Issuer Name — The name of the contra issuer associated with the contra ticker. |

### `sp500_membership` — RAW (Sharadar API)

| column | unit | definition |
|---|---|---|
| `ticker` | text | Ticker Symbol — The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. In the SP500 table; ticker and name always use the current Sharadar identifiers (history is rewired after ticker changes). Historical market symbols are recorded in the ACTIONS table (tickerchangefrom / tickerchangeto). Join to TICKERS on ticker (typically where table=SEP) to obtain permaticker and other metadata. |
| `date` | date (YYYY-MM-DD) | Date — The action date (YYYY-MM-DD). For action=added and action=removed this is the effective index-membership date (not the announcement date). For action=historical this is the quarter-end snapshot date. For action=current this is our data refresh date. We do not distinguish between market open and close at present. |
| `action` | text | Action — The type of S&P500 membership record. current: full set of present constituents; date is our data refresh date; contraticker is N/A. added: security entered the index; date is the effective membership date (not the announcement date); contraticker is the security removed in a paired swap when applicable. removed: security left the index; date is the effective membership date (not the announcement date); contraticker is the security added in a paired swap when applicable. historical: complete constituent snapshot on a calendar quarter-end (31 March; 30 June; 30 September; 31 December) from 1998-03-31 forward; one row per member on that date; contraticker is N/A. |
| `name` | text | Issuer Name — The name of the issuer associated with ticker. As with ticker; names in the SP500 table reflect current labeling rather than the name as of the historical date. |
| `contraticker` | text | Contra Ticker Symbol — The contra ticker is the opposing ticker entry when a membership swap is recorded. Where action=added it is the ticker removed in the same change; where action=removed it is the ticker added. N/A when there is no paired replacement (for example certain alternative share-class changes; see note). |
| `contraname` | text | Contra Issuer Name — The name of the contra issuer corresponding to contraticker; or N/A when not applicable. |
| `note` | text | Notes — Notes and additional information regarding additions to and removals from the S&P500. Examples include alternative share class added and alternative share class removed without replacement. |

### `metrics` — RAW (metrics zip)

> stores 19 of the vendor's 22 METRICS fields (as_of = vendor `date` renamed; dropped lastupdated, ma200w, ma50w)

| column | unit | definition |
|---|---|---|
| `ticker` | text | Ticker Symbol — The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. The ACTIONS table provides a record of historical ticker changes. |
| `as_of` | date (YYYY-MM-DD) | Price Date — The trade date of the most recent price and volume available. |
| `price` | USD/share | Price — The closing stock price on the date. |
| `beta1y` | ratio | Beta - 1 Year Daily — The beta of the stock over 1 year relative to the market calculated using daily adjusted closing price of the stock (as adjusted for stock splits; dividends and spinoffs) relative to total returns of the market as represented by the adjusted closing prices of the SPY S&P500 ETF. Recalculated and updated on a weekly basis. |
| `beta5y` | ratio | Beta - 5 Year Monthly — The beta of the stock over 5 years relative to the market calculated using monthly adjusted closing price of the stock (as adjusted for stock splits; dividends and spinoffs) relative to total returns of the market as represented by the adjusted closing prices of the SPY S&P500 ETF. Recalculated and updated on a weekly basis. |
| `ma50d` | USD/share | Price Moving Average - 50 Day — The 50 day moving average closing price as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. Not available if the stock has been trading less than 50 days. |
| `ma200d` | USD/share | Price Moving Average - 200 Day — The 200 day moving average closing price as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. Not available if the stock has been trading less than 200 days. |
| `high52w` | USD/share | High Price - 52 Week — The highest price over the last 52 weeks as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. |
| `low52w` | USD/share | Low Price - 52 Week — The lowest price over the last 52 weeks as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. |
| `return1y` | percent | Total Return - 1 Year — The total return over 1 year calculated using the closing price as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. Not available if the stock has been trading for less than 1 year. |
| `return5y` | percent | Total Return - 5 Year — The total return over 5 years calculated using the closing price as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. Not available if the stock has been trading for less than 5 years. |
| `returnytd` | percent | Total Return - Year to Date — The total return for the year to date calculated using the closing price as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. |
| `volume` | numeric | Volume — The daily traded volume across all exchanges; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. Includes opening and closing cross volumes if applicable. |
| `volumeavg1m` | numeric | Volume Average - 1 Month — The daily average traded volume over 1 month across all exchanges; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. Includes opening and closing cross volumes if applicable. |
| `volumeavg3m` | numeric | Volume Average - 3 Month — The daily average traded volume over 3 months across all exchanges; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. Includes opening and closing cross volumes if applicable. |
| `dividendyieldtrailing` | percent | Dividend Yield - Trailing — The trailing dividend yield; calculated by summing regular and special cash dividend payments in the last year; dividing by the stock price and multiplying by 100. |
| `dividendyieldforward` | percent | Dividend Yield - Forward — The forward dividend yield; calculated by annualising the last regular cash dividend payment; dividing by the stock price and multiplying by 100. |
| `high5y` | USD/share | High Price - 5 Year — The highest price over the last 5 years as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. |
| `low5y` | USD/share | Low Price - 5 Year — The lowest price over the last 5 years weeks as adjusted for stock splits and stock dividends; but not cash dividends or spinoffs. |

### `sf1` — RAW (fundamentals zip)

| column | unit | definition |
|---|---|---|
| `ticker` | text | Ticker Symbol — [Entity] The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. The ACTIONS table provides a record of historical ticker changes. |
| `dimension` | text | Dimension — [Entity] The dimension field allows you to take different dimensional views of data over time. ARQ: Quarterly; excluding restatements; MRQ: Quarterly; including restatements; ARY: annual; excluding restatements; MRY: annual; including restatements; ART: trailing-twelve-months; excluding restatements; MRT: trailing-twelve-months; including restatements. |
| `date` |  | this repo — as-of/observation date key for point-in-time lookups (filing date for AR*, report-key date for MR*) |
| `reportperiod` | date (YYYY-MM-DD) | Report Period — [Entity] The Report Period represents the end date of the fiscal period. |
| `fiscalperiod` | date (YYYY-MM-DD) | Fiscal Period — The fiscal period of the report expressed as follows: 2024-Q2; 2024-Q3; 2024-FY etc. Note that companies can have different fiscal periods for the report period due to different year end dates. |
| `calendardate` | date (YYYY-MM-DD) | Calendar Date — [Entity] The Calendar Date represents the normalized [ReportPeriod]. This provides a common date to query for which is necessary due to irregularity in report periods across companies. For example; if the report period is 2015-09-26; the calendar date will be 2015-09-30 for quarterly and trailing-twelve-month dimensions (ARQ;MRQ;ART;MRT); and 2015-12-31 for annual dimensions (ARY;MRY). We also employ offsets in order to maximise comparability of the period across companies. For example consider two companies: one with a quarter ending on 2018-07-24; and the other with a quarter ending on 2018-06-28. A naive normalization process would assign these to differing calendar quarters of 2018-09-30 and 2018-06-30 respectively. However; we assign these both to the 2018-06-30 calendar quarter because this maximises the overlap in the report periods in question and therefore the comparability of this period. |
| `lastupdated` | date (YYYY-MM-DD) | Last Updated Date — [Entity] Last Updated represents the last date that this database entry was updated; which is useful to users when updating their local records. |
| `revenue` | currency | Revenues — [Income Statement] The amount of Revenue recognised from goods sold; services rendered; insurance premiums; or other activities that constitute an earning process. Interest income for financial institutions is reported net of interest expense and provision for credit losses. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `netinc` | currency | Net Income — [Income Statement] The portion of profit or loss for the period; net of income taxes; which is attributable to the parent after the deduction of [NetIncNCI] from [ConsolInc]; and before the deduction of [PrefDivIS]. |
| `netinccmn` | currency | Net Income Common Stock — [Income Statement] The amount of net income (loss) for the period due to common shareholders. Typically differs from [NetInc] to the parent entity due to the deduction of [PrefDivIS]. |
| `assets` | currency | Total Assets — [Balance Sheet] Sum of the carrying amounts as of the balance sheet date of all assets that are recognized. Major components are [CashnEq]; [Investments];[Intangibles]; [PPNENet];[TaxAssets] and [Receivables]. |
| `liabilities` | currency | Total Liabilities — [Balance Sheet] Sum of the carrying amounts as of the balance sheet date of all liabilities that are recognized. Principal components are [Debt]; [DeferredRev]; [Payables];[Deposits]; and [TaxLiabilities]. |
| `equity` | currency | Shareholders Equity Attributable to Parent — [Balance Sheet] A principal component of the balance sheet; in addition to [Liabilities] and [Assets]; that represents the total of all stockholders' equity (deficit) items; net of receivables from officers; directors; owners; and affiliates of the entity which are attributable to the parent. |
| `cashneq` | currency | Cash and Equivalents — [Balance Sheet] A component of [Assets] representing the amount of currency on hand as well as demand deposits with banks or financial institutions. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfo` | currency | Net Cash Flow from Operations — [Cash Flow Statement] A component of [NCF] representing the amount of cash inflow (outflow) from operating activities; from continuing and discontinued operations. |
| `capex` | currency | Capital Expenditure — [Cash Flow Statement] A component of [NCFI] representing the net cash inflow (outflow) associated with the acquisition & disposal of long-lived; physical & intangible assets that are used in the normal conduct of business to produce goods and services and are not intended for resale. Includes cash inflows/outflows to pay for construction of self-constructed assets & software. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `fcf` | currency | Free Cash Flow — [Metrics] Free Cash Flow is a measure of financial performance calculated as [NCFO] minus [CapEx]. |
| `marketcap` | USD | Market Capitalization — [Metrics] Represents the product of [SharesBas]; [Price] and [ShareFactor]. |
| `ev` | USD | Enterprise Value — [Metrics] Enterprise value is a measure of the value of a business as a whole; calculated as [MarketCap] plus [DebtUSD] minus [CashnEqUSD]. |
| `pe` | ratio | Price Earnings (Damodaran Method) — [Metrics] Measures the ratio between [MarketCap] and [NetIncCmnUSD] |
| `pb` | ratio | Price to Book Value — [Metrics] Measures the ratio between [MarketCap] and [EquityUSD]. |
| `ps` | ratio | Price Sales (Damodaran Method) — [Metrics] Measures the ratio between [MarketCap] and [RevenueUSD]. |
| `price` | USD/share | Share Price (Adjusted Close) — [Entity] The price per common share adjusted for stock splits but not adjusted for dividends; used in the computation of [PE1]; [PS1]; [DivYield] and [SPS]. |
| `eps` | currency/share | Earnings per Basic Share — [Income Statement] Earnings per share as calculated and reported by the company. Approximates to the amount of [NetIncCmn] for the period per each [SharesWA] after adjusting for [ShareFactor]. |
| `dps` | USD/share | Dividends per Basic Common Share — [Income Statement] Aggregate dividends declared during the period for each split-adjusted share of common stock outstanding. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `divyield` | ratio | Dividend Yield — [Metrics] Dividend Yield measures the ratio between a company's trailing twelve month [DPS] and its close [Price]; by summing dividends by ExDate; provided in the SHARADAR/ACTIONS table; in the twelve months leading up to the date in the [DateKey] indicator.  Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `roe` | ratio | Return on Average Equity — [Metrics] Return on equity measures a corporation's profitability by calculating the amount of [NetIncCmn] returned as a percentage of [EquityAvg]. |
| `roa` | ratio | Return on Average Assets — [Metrics] Return on assets measures how profitable a company is [NetIncCmn] relative to its total assets [AssetsAvg]. |
| `roic` | ratio | Return on Invested Capital — [Metrics] Return on Invested Capital is a ratio estimated by dividing [EBIT] by [InvCapAvg]. [InvCap] is calculated as: [Debt] plus [Assets] minus [Intangibles] minus [CashnEq] minus [LiabilitiesC]. Please note this calculation method is subject to change. |
| `grossmargin` | ratio | Gross Margin — [Metrics] Gross Margin measures the ratio between a company's [GP] and [Revenue]. |
| `netmargin` | ratio | Profit Margin — [Metrics] Measures the ratio between a company's [NetIncCmn] and [Revenue]. |
| `ebitda` | currency | Earnings Before Interest Taxes & Depreciation Amortization (EBITDA) — [Metrics] EBITDA is a non-GAAP accounting metric that is widely used when assessing the performance of companies; calculated by adding [DepAmor] back to [EBIT]. |
| `shareswa` | units | Weighted Average Shares — [Income Statement] The weighted average number of shares or units issued and outstanding that are used by the company to calculate [EPS]; determined based on the timing of issuance of shares or units in the period. |
| `shareswadil` | units | Weighted Average Shares Diluted — [Income Statement] The weighted average number of shares or units issued and outstanding that are used by the company to calculate [EPSDil]; determined based on the timing of issuance of shares or units in the period. |
| `currentratio` | ratio | Current Ratio — [Metrics] The ratio between [AssetsC] and [LiabilitiesC]; for companies that operate a classified balance sheet. |
| `de` | ratio | Debt to Equity Ratio — [Metrics] Measures the ratio between [Liabilities] and [Equity]. |
| `data` |  | this repo — zlib-compressed JSON of the FULL vendor row (all 105 indicator fields beyond the 7 key/date fields) |

### `sf1_blob` — RAW (fundamentals zip) — inside sf1.data

| column | unit | definition |
|---|---|---|
| `accoci` | currency | Accumulated Other Comprehensive Income — [Balance Sheet] A component of [Equity] representing the accumulated change in equity from transactions and other events and circumstances from non-owner sources; net of tax effect; at period end. Includes foreign currency translation items; certain pension adjustments; unrealized gains and losses on certain investments in debt and equity securities. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `assetsavg` | currency | Average Assets — [Metrics] Average asset value for the period used in calculation of [ROE] and [ROA]; derived from [Assets]. |
| `assetsc` | currency | Current Assets — [Balance Sheet] The current portion of [Assets]; reported if a company operates a classified balance sheet that segments current and non-current assets. |
| `assetsnc` | currency | Assets Non-Current — [Balance Sheet] Amount of non-current assets; for companies that operate a classified balance sheet. Calculated as the different between Total Assets [Assets] and Current Assets [AssetsC]. |
| `assetturnover` | ratio | Asset Turnover — [Metrics] Asset turnover is a measure of a firms operating efficiency; calculated by dividing [Revenue] by [AssetsAVG]. Often a component of DuPont ROE analysis. |
| `bvps` | currency/share | Book Value per Share — [Metrics] Measures the ratio between [Equity] and [SharesWA] as adjusted by [ShareFactor]. |
| `cashnequsd` | USD | Cash and Equivalents (USD) — [Balance Sheet] [CashnEq] in USD; converted by [FXUSD]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `consolinc` | currency | Consolidated Income — [Income Statement] The portion of profit or loss for the period; net of income taxes; which is attributable to the consolidated entity; before the deduction of [NetIncNCI]. |
| `cor` | currency | Cost of Revenue — [Income Statement] The aggregate cost of goods produced and sold and services rendered during the reporting period. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `datekey` | date (YYYY-MM-DD) | Date Key — [Entity] The Date Key represents the SEC filing date for AR dimensions (ARQ;ART;ARY); and the [REPORTPERIOD] for MR dimensions (MRQ;MRT;MRY). In addition; this is the observation date used for [Price] based data such as [MarketCap]; [Price] and [PE]. |
| `debt` | currency | Total Debt — [Balance Sheet] A component of [Liabilities] representing the total amount of current and non-current debt owed. Includes secured and unsecured bonds issued; commercial paper; notes payable; credit facilities; lines of credit; capital lease obligations; operating lease obligations; and convertible notes. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `debtc` | currency | Debt Current — [Balance Sheet] The current portion of [Debt]; reported if the company operates a classified balance sheet that segments current and non-current liabilities. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `debtnc` | currency | Debt Non-Current — [Balance Sheet] The non-current portion of [Debt] reported if the company operates a classified balance sheet that segments current and non-current liabilities. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `debtusd` | USD | Total Debt (USD) — [Balance Sheet] [Debt] in USD; converted by [FXUSD]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `deferredrev` | currency | Deferred Revenue — [Balance Sheet] A component of [Liabilities] representing the carrying amount of consideration received or receivable on potential earnings that were not recognized as revenue; including sales; license fees; and royalties; but excluding interest income. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `depamor` | currency | Depreciation Amortization & Accretion — [Cash Flow Statement] A component of operating cash flow representing the aggregate net amount of depreciation; amortization; and accretion recognized during an accounting period. As a non-cash item; the net amount is added back to net income when calculating cash provided by or used in operations using the indirect method. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `deposits` | currency | Deposit Liabilities — [Balance Sheet] A component of [Liabilities] representing the total of all deposit liabilities held; including foreign and domestic; interest and noninterest bearing. May include demand deposits; saving deposits; Negotiable Order of Withdrawal and time deposits among others. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ebit` | currency | Earning Before Interest & Taxes (EBIT) — [Income Statement] Earnings Before Interest and Tax is calculated by adding [TaxExp] and [IntExp] back to [NetInc]. |
| `ebitdamargin` | ratio | EBITDA Margin — [Metrics] Measures the ratio between a company's [EBITDA] and [Revenue]. |
| `ebitdausd` | USD | Earnings Before Interest Taxes & Depreciation Amortization (USD) — [Metrics] [EBITDA] in USD; converted by [FXUSD]. |
| `ebitusd` | USD | Earning Before Interest & Taxes (USD) — [Income Statement] [EBIT] in USD; converted by [FXUSD]. |
| `ebt` | currency | Earnings before Tax — [Metrics] Earnings Before Tax is calculated by adding [TaxExp] back to [NetInc]. |
| `epsdil` | currency/share | Earnings per Diluted Share — [Income Statement] Earnings per diluted share as calculated and reported by the company. Approximates to the amount of [NetIncCmn] for the period per each [SharesWADil] after adjusting for [ShareFactor].. |
| `epsusd` | USD/share | Earnings per Basic Share (USD) — [Income Statement] [EPS] in USD; converted by [FXUSD]. |
| `equityavg` | currency | Average Equity — [Metrics] Average equity value for the period used in calculation of [ROE]; derived from [Equity]. |
| `equityusd` | USD | Shareholders Equity (USD) — [Balance Sheet] [Equity] in USD; converted by [FXUSD]. |
| `evebit` | ratio | Enterprise Value over EBIT — [Metrics] Measures the ratio between [EV] and [EBITUSD]. |
| `evebitda` | ratio | Enterprise Value over EBITDA — [Metrics] Measures the ratio between [EV] and [EBITDAUSD]. |
| `fcfps` | currency/share | Free Cash Flow per Share — [Metrics] Free Cash Flow per Share is a valuation metric calculated by dividing [FCF] by [SharesWA] and [ShareFactor]. |
| `fxusd` | ratio | Foreign Currency to USD Exchange Rate — [Metrics] The exchange rate used for the conversion of foreign currency to USD for non-US companies that do not report in USD. |
| `gp` | currency | Gross Profit — [Income Statement] Aggregate revenue [Revenue] less cost of revenue [CoR] directly attributable to the revenue generation activity. |
| `intangibles` | currency | Goodwill and Intangible Assets — [Balance Sheet] A component of [Assets] representing the carrying amounts of all intangible assets and goodwill as of the balance sheet date; net of accumulated amortization and impairment charges. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `intexp` | currency | Interest Expense — [Income Statement] Amount of the cost of borrowed funds accounted for as interest expense. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `invcap` | currency | Invested Capital — [Metrics] Invested capital is an input into the calculation of [ROIC]; and is calculated as: [Debt] plus [Assets] minus [Intangibles] minus [CashnEq] minus [LiabilitiesC]. Please note this calculation method is subject to change. |
| `invcapavg` | currency | Invested Capital Average — [Metrics] Average invested capital value for the period used in the calculation of [ROIC]; and derived from [InvCap]. Invested capital is an input into the calculation of [ROIC]; and is calculated as: [Debt] plus [Assets] minus [Intangibles] minus [CashnEq] minus [LiabilitiesC]. Please note this calculation method is subject to change. |
| `inventory` | currency | Inventory — [Balance Sheet] A component of [Assets] representing the amount after valuation and reserves of inventory expected to be sold; or consumed within one year or operating cycle; if longer. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `investments` | currency | Investments — [Balance Sheet] A component of [Assets] representing the total amount of marketable and non-marketable securties; loans receivable; equity-method investments; and other invested assets. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `investmentsc` | currency | Investments Current — [Balance Sheet] The current portion of [Investments]; reported if the company operates a classified balance sheet that segments current and non-current assets. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `investmentsnc` | currency | Investments Non-Current — [Balance Sheet] The non-current portion of [Investments]; reported if the company operates a classified balance sheet that segments current and non-current assets. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `liabilitiesc` | currency | Current Liabilities — [Balance Sheet] The current portion of [Liabilities]; reported if the company operates a classified balance sheet that segments current and non-current liabilities. |
| `liabilitiesnc` | currency | Liabilities Non-Current — [Balance Sheet] The non-current portion of [Liabilities]; reported if the company operates a classified balance sheet that segments current and non-current liabilities. |
| `ncf` | currency | Net Cash Flow / Change in Cash & Cash Equivalents — [Cash Flow Statement] Principal component of the cash flow statement representing the amount of increase (decrease) in cash and cash equivalents. Includes [NCFO]; investing [NCFI] and financing [NCFF] for continuing and discontinued operations; and the effect of exchange rate changes on cash [NCFX]. |
| `ncfbus` | currency | Net Cash Flow - Business Acquisitions and Disposals — [Cash Flow Statement] A component of [NCFI] representing the net cash inflow (outflow) associated with the acquisition & disposal of businesses; joint-ventures; affiliates; and other named investments. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfcommon` | currency | Issuance (Purchase) of Equity Shares — [Cash Flow Statement] A component of [NCFF] representing the net cash inflow (outflow) from common equity changes. Includes additional capital contributions from share issuances and exercise of stock options; and outflow from share repurchases.  Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfdebt` | currency | Issuance (Repayment) of Debt Securities  — [Cash Flow Statement] A component of [NCFF] representing the net cash inflow (outflow) from issuance (repayment) of debt securities. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfdiv` | currency | Payment of Dividends & Other Cash Distributions    — [Cash Flow Statement] A component of [NCFF] representing dividends and dividend equivalents paid on common stock and restricted stock units. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncff` | currency | Net Cash Flow from Financing — [Cash Flow Statement] A component of [NCF] representing the amount of cash inflow (outflow) from financing activities; from continuing and discontinued operations. Principal components of financing cash flow are: issuance (purchase) of equity shares; issuance (repayment) of debt securities; and payment of dividends & other cash distributions. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfi` | currency | Net Cash Flow from Investing — [Cash Flow Statement] A component of [NCF] representing the amount of cash inflow (outflow) from investing activities; from continuing and discontinued operations. Principal components of investing cash flow are: capital (expenditure) disposal of equipment [CapEx]; business (acquisitions) disposition [NCFBus] and investment (acquisition) disposal [NCFInv]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfinv` | currency | Net Cash Flow - Investment Acquisitions and Disposals — [Cash Flow Statement] A component of [NCFI] representing the net cash inflow (outflow) associated with the acquisition & disposal of investments; including marketable securities and loan originations. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ncfx` | currency | Effect of Exchange Rate Changes on Cash  — [Cash Flow Statement] A component of Net Cash Flow [NCF] representing the amount of increase (decrease) from the effect of exchange rate changes on cash and cash equivalent balances held in foreign currencies. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `netinccmnusd` | USD | Net Income Common Stock (USD) — [Income Statement] [NetIncCmn] in USD; converted by [FXUSD]. |
| `netincdis` | currency | Net Loss Income from Discontinued Operations — [Income Statement] Amount of loss (income) from a disposal group; net of income tax; reported as a separate component of income. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `netincnci` | currency | Net Income to Non-Controlling Interests — [Income Statement] The portion of income which is attributable to non-controlling interest shareholders; subtracted from [ConsolInc] in order to obtain [NetInc]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `opex` | currency | Operating Expenses — [Income Statement] Operating expenses represent the total expenditure on [SGnA]; [RnD] and other operating expense items; it excludes [CoR]. |
| `opinc` | currency | Operating Income — [Income Statement] Operating income is a measure of financial performance before the deduction of [IntExp]; [TaxExp] and other Non-Operating items. It is calculated as [GP] minus [OpEx]. |
| `payables` | currency | Trade and Non-Trade Payables — [Balance Sheet] A component of [Liabilities] representing trade and non-trade payables. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `payoutratio` | ratio | Payout Ratio — [Metrics] The percentage of earnings paid as dividends to common stockholders. Calculated by dividing [DPS] by [EPSUSD]. |
| `pe1` | ratio | Price to Earnings Ratio — [Metrics] An alternative to [PE] representing the ratio between [Price] and [EPSUSD]. |
| `ppnenet` | currency | Property Plant & Equipment Net — [Balance Sheet] A component of [Assets] representing the amount after accumulated depreciation; depletion and amortization of physical assets used in the normal conduct of business to produce goods and services and not intended for resale. Includes Operating Right of Use Assets. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `prefdivis` | currency | Preferred Dividends Income Statement Impact — [Income Statement] Income statement item reflecting dividend payments to preferred stockholders. Subtracted from Net Income to Parent [NetInc] to obtain Net Income to Common Stockholders [NetIncCmn]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ps1` | ratio | Price to Sales Ratio — [Metrics] An alternative calculation method to [PS]; that measures the ratio between a company's [Price] and it's [SPS]. |
| `receivables` | currency | Trade and Non-Trade Receivables — [Balance Sheet] A component of [Assets] representing trade and non-trade receivables. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `retearn` | currency | Accumulated Retained Earnings (Deficit) — [Balance Sheet] A component of [Equity] representing the cumulative amount of the entities undistributed earnings or deficit. May only be reported annually by certain companies; rather than quarterly. |
| `revenueusd` | USD | Revenues (USD) — [Income Statement] [Revenue] in USD; converted by [FXUSD]. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `rnd` | currency | Research and Development Expense — [Income Statement] A component of [OpEx] representing the aggregate costs incurred in a planned search or critical investigation aimed at discovery of new knowledge with the hope that such knowledge will be useful in developing a new product or service. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `ros` | ratio | Return on Sales — [Metrics] Return on Sales is a ratio to evaluate a company's operational efficiency; calculated by dividing [EBIT] by [Revenue]. ROS is often a component of DuPont ROE analysis. |
| `sbcomp` | currency | Share Based Compensation — [Cash Flow Statement] A component of [NCFO] representing the total amount of noncash; equity-based employee remuneration. This may include the value of stock or unit options; amortization of restricted stock or units; and adjustment for officers' compensation. As noncash; this element is an add back when calculating net cash generated by operating activities using the indirect method. |
| `sgna` | currency | Selling General and Administrative Expense — [Income Statement] A component of [OpEx] representing the aggregate total costs related to selling a firm's product and services; as well as all other general and administrative expenses. Direct selling expenses (for example; credit; warranty; and advertising) are expenses that can be directly linked to the sale of specific products. Indirect selling expenses are expenses that cannot be directly linked to the sale of specific products; for example telephone expenses; Internet; and postal charges. General and administrative expenses include salaries of non-sales personnel; rent; utilities; communication; etc. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `sharefactor` | ratio | Share Factor — [Entity] Share factor is a multiplicand in the calculation of [MarketCap] and is used to adjust for: American Depository Receipts (ADRs) that represent more or less than 1 underlying share; and; companies which have different earnings share for different share classes (eg Berkshire Hathaway - BRK.B). |
| `sharesbas` | units | Shares (Basic) — [Entity] The number of shares or other units outstanding of the entity's capital or common stock or other ownership interests; as stated on the cover of related periodic report (10-K/10-Q); after adjustment for stock splits. |
| `sps` | USD/share | Sales per Share — [Metrics] Sales per Share measures the ratio between [RevenueUSD] and [SharesWA] as adjusted by [ShareFactor]. |
| `tangibles` | currency | Tangible Asset Value — [Metrics] The value of tangibles assets calculated as the difference between [Assets] and [Intangibles]. |
| `taxassets` | currency | Tax Assets — [Balance Sheet] A component of [Assets] representing tax assets and receivables. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `taxexp` | currency | Income Tax Expense — [Income Statement] Amount of current income tax expense (benefit) and deferred income tax expense (benefit) pertaining to continuing operations. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `taxliabilities` | currency | Tax Liabilities — [Balance Sheet] A component of [Liabilities] representing outstanding tax liabilities. Where this item is not contained on the company consolidated financial statements and cannot otherwise be imputed the value of 0 is used. |
| `tbvps` | currency/share | Tangible Assets Book Value per Share — [Metrics] Measures the ratio between [Tangibles] and [SharesWA] as adjusted by [ShareFactor]. |
| `workingcapital` | currency | Working Capital — [Metrics] Working capital measures the difference between [AssetsC] and [LiabilitiesC]. |

### `prices` — RAW (stocks+funds zips)

> from the SEP/SFP bulk price columns; closeadj/closeunadj collapsed into `adjustment`; stored close = closeunadj (as-traded), OHLC as-traded (vendor OHLC are split-adjusted)

| column | unit | definition |
|---|---|---|
| `ticker` | text | Ticker Symbol — The ticker is a unique identifier for a security in the database. Where a company is delisted and the ticker subsequently recycled for use by a different company; we utilise that ticker for the currently active company and append a number to the ticker of the delisted company. The ACTIONS table provides a record of historical ticker changes. |
| `date` | date (YYYY-MM-DD) | Price Date — The trade date of the price observations. |
| `open` | USD/share | Open Price - Split Adjusted — The official exchange opening price; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. |
| `high` | USD/share | High Price - Split Adjusted — The high share price; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. |
| `low` | USD/share | Low Price - Split Adjusted — The low share price; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. |
| `close` | USD/share | Close Price - Split Adjusted — The official exchange close price; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. |
| `volume` | numeric | Volume - Split Adjusted — The daily traded volume across all exchanges; adjusted for stock splits and stock dividends. Not adjusted for cash dividends or spinoffs. Includes opening and closing cross volumes if applicable. |
| `adjustment` |  | this repo — total-return factor = closeadj/closeunadj (split+dividend chain, normalized to 1.0 at latest quote) |

## Derived tables (this repo)

### `fundamentals` — DERIVED (sf1 ARY)

| column | type | definition |
|---|---|---|
| `ticker` | `TEXT` | instrument symbol |
| `fiscal_year` | `INTEGER` | fiscal year end |
| `roa` | `REAL` | return on assets (net income / total assets) |
| `cfo` | `REAL` | operating cash flow / total assets |
| `d_roa` | `REAL` | change in ROA vs prior year |
| `accruals` | `REAL` | CFO − net income (accrual quality) |
| `d_leverage` | `REAL` | change in long-term-debt-to-assets |
| `d_liquidity` | `REAL` | change in current ratio |
| `equity_issuance` | `REAL` | new share issuance (Piotroski criterion) |
| `d_gross_margin` | `REAL` | change in gross margin |
| `d_asset_turnover` | `REAL` | change in asset turnover |
| `f_score` | `INTEGER` | Piotroski F-Score 0–9 (sum of the 9 binary criteria) |

### `quarterly_statements` — DERIVED (sf1 ARQ)

| column | type | definition |
|---|---|---|
| `ticker` | `TEXT` | instrument symbol |
| `period` | `TEXT` | report period end (YYYY-MM-DD) |
| `net_income` | `REAL` | net income, ARQ |
| `revenue` | `REAL` | revenue, ARQ |
| `gross_profit` | `REAL` | gross profit, ARQ |
| `operating_cash_flow` | `REAL` | CFO, ARQ |
| `total_assets` | `REAL` | total assets, ARQ |
| `total_liabilities` | `REAL` | total liabilities, ARQ |
| `current_assets` | `REAL` | current assets, ARQ |
| `current_liabilities` | `REAL` | current liabilities, ARQ |
| `shares_out` | `REAL` | shares outstanding, ARQ |
| `roa` | `REAL` | net income / total assets |
| `cfo` | `REAL` | operating cash flow / total assets |

### `ratios` — DERIVED (sf1 MRY latest + price)

| column | type | definition |
|---|---|---|
| `ticker` | `TEXT` | instrument symbol |
| `as_of` | `TEXT` | build date |
| `trailing_pe` | `REAL` | trailing P/E (MRY) |
| `forward_pe` | `REAL` | ALWAYS NULL — SF1 has no forward estimates |
| `price_to_book` | `REAL` | P/B (MRY) |
| `price_to_sales` | `REAL` | P/S (MRY) |
| `roe` | `REAL` | return on equity (MRY) |
| `roa` | `REAL` | return on assets (MRY) |
| `net_margin` | `REAL` | net margin (MRY) |
| `gross_margin` | `REAL` | gross margin (MRY) |
| `operating_margin` | `REAL` | operating margin (MRY) |
| `debt_to_equity` | `REAL` | D/E (MRY) |
| `current_ratio` | `REAL` | current ratio (MRY) |
| `dividend_yield` | `REAL` | dividend yield (MRY) |
| `market_cap` | `REAL` | market cap (MRY) |
| `enterprise_value` | `REAL` | EV (MRY) |
| `ev_to_ebitda` | `REAL` | EV/EBITDA (MRY) |
| `beta` | `REAL` | ALWAYS NULL — SF1 has no beta |
| `shares_outstanding` | `REAL` | shares outstanding (MRY) |

### `universe_pit` — DERIVED (master+prices+sf1)

| column | type | definition |
|---|---|---|
| `as_of` | `TEXT` | trading day (YYYY-MM-DD) |
| `ticker` | `TEXT` | instrument symbol |
| `category` | `TEXT` | master category (Domestic Common Stock Primary/Second, …) |
| `exchange` | `TEXT` | master exchange |
| `isdelisted` | `TEXT` | Y/N (master) |
| `sector` | `TEXT` | Sharadar taxonomy sector |
| `industry` | `TEXT` | Sharadar taxonomy industry |
| `price` | `REAL` | most recent valid as-traded close ≤ as_of (per-day pointer, not run-start) |
| `mcap` | `REAL` | PIT market cap = price × shareswa (ARQ/ARY ≤ as_of) |
| `dvol_avg` | `REAL` | trailing 20-session average dollar volume (≥10 sessions) |
| `dvol_days` | `INTEGER` | sessions with volume in the trailing window |
| `firstpricedate` | `TEXT` | master price-history start |
| `lastpricedate` | `TEXT` | master price-history end (delisting date) |

### `classifications` — DERIVED (master + GICS map)

| column | type | definition |
|---|---|---|
| `ticker` | `TEXT` | instrument symbol |
| `sector` | `TEXT` | GICS sector label (11) |
| `industry` | `TEXT` | GICS industry label |
| `as_of` | `TEXT` | map build date |

### `universe` — DERIVED (iShares IWV)

| column | type | definition |
|---|---|---|
| `ticker` | `TEXT` | instrument symbol |
| `name` | `TEXT` | company name |
| `source` | `TEXT` | IWV |
| `added_at` | `TEXT` | row added timestamp |
| `figi` | `TEXT` | FIGI identifier |
| `cik` | `TEXT` | SEC CIK |
| `sic` | `TEXT` | SIC code |
| `sic_description` | `TEXT` | SIC description |
| `lei` | `TEXT` | legal entity identifier |

### `snapshots` — ledger (every pull)

| column | type | definition |
|---|---|---|
| `id` | `INTEGER` | auto-increment |
| `source` | `TEXT` | provider name |
| `pulled_at` | `TEXT` | UTC pull timestamp |
| `as_of` | `TEXT` | vendor as-of date |
| `row_count` | `INTEGER` | rows loaded |

## Notes

- The vendor dictionary documents 17 datasets; this warehouse stores the ones in the coverage map. Vendor tables we do **not** store: `SF2` (insiders), `SF3/SF3A/SF3B` (institutional investors), `EVENTS`/`EVENTCODES` (material events), `DAILY` (daily fundamental metrics: mcap/EV/PE slices), and the meta-tables `INDICATORS`, `ACTIONTYPES`, `TABLE-DESCRIPTIONS` (all present inside `descriptions` if needed).

- `prices` comes from the SEP (stocks) / SFP (funds) bulk price columns; `closeadj` and `closeunadj` are collapsed into the single `adjustment` factor; vendor OHLC are split-adjusted, we store them as-traded (see the adjustment section of the report).

- `metrics` stores 19 of the vendor's 22 METRICS fields: `as_of` is the vendor `date` renamed; `lastupdated`, `ma200w`, `ma50w` are dropped.

- `sf1` stores 29 typed columns + 7 key/date fields; the other 76 vendor fields live compressed in the `data` blob (`sf1_blob` above).

- `securities_master` is the union of the vendor's SEP-stocks and SFP-funds master metadata, distinguished by the `table` column.
