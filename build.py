#!/usr/bin/env python3
"""US CPI breadth dashboard: % of CPI components rising >0.5% QoQ vs CPI YoY.

Builds site/index.html from scratch. Data: BLS CPI-U flat files (primary) or the
BLS Public Data API v2 (fallback, needs BLS_API_KEY). Exits non-zero if the data
fail sanity checks, so the scheduled workflow never deploys a broken page.

    python build.py            # -> site/index.html
Env: BLS_CONTACT_EMAIL (sent in User-Agent, BLS asks for one), BLS_API_KEY (optional)
"""
import os, sys, re, json, time, base64, datetime as dt
import numpy as np, pandas as pd, requests

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, '.cache'); OUT = os.path.join(ROOT, 'site')
os.makedirs(WORK, exist_ok=True); os.makedirs(OUT, exist_ok=True)
EMAIL = os.environ.get('BLS_CONTACT_EMAIL', 'research@example.com')
API_KEY = os.environ.get('BLS_API_KEY', '').strip()
UA = f'Mozilla/5.0 (compatible; cpi-breadth-dashboard; {EMAIL})'
BLS = 'https://download.bls.gov/pub/time.series/cu/'
DATA_FILES = ['cu.data.1.AllItems', 'cu.data.2.Summaries', 'cu.data.11.USFoodBeverage', 'cu.data.12.USHousing',
              'cu.data.13.USApparel', 'cu.data.14.USTransportation', 'cu.data.15.USMedical', 'cu.data.16.USRecreation',
              'cu.data.17.USEducationAndCommunication', 'cu.data.18.USOtherGoodsAndServices', 'cu.data.20.USCommoditiesServicesSpecial']
FONTS = {'sg600': 'space-grotesk/files/space-grotesk-latin-600-normal.woff2',
         'px400': 'ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2',
         'px500': 'ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff2',
         'pm400': 'ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2',
         'pm500': 'ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2'}
THR = 0.5          # QoQ % threshold (not annualised)
START = '1988-01'  # matches VP x-axis
GAP_MONTH = pd.Timestamp('2025-10-01')  # no Oct-2025 CPI (federal shutdown)
NBER = [('1990-07', '1991-03'), ('2001-03', '2001-11'), ('2007-12', '2009-06'), ('2020-02', '2020-04')]
S = requests.Session(); S.headers.update({'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9', 'Accept': '*/*'})

def get(url, path=None, tries=4, min_bytes=1000):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) >= min_bytes:
                if path: open(path, 'wb').write(r.content)
                return r.content
            last = f'HTTP {r.status_code}, {len(r.content)} bytes'
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f'{url}: {last}')

def parse_flat(path):
    t = pd.read_csv(path, sep='\t', dtype=str); t.columns = [c.strip() for c in t.columns]
    return t[['series_id', 'year', 'period', 'value']].apply(lambda s: s.str.strip())

# ---------- item hierarchy ----------
try:
    get(BLS + 'cu.item', os.path.join(WORK, 'cu.item')); item_path = os.path.join(WORK, 'cu.item')
except RuntimeError as e:
    print('WARN cu.item download failed, using committed ref/cu.item:', e); item_path = os.path.join(ROOT, 'ref', 'cu.item')
it = pd.read_csv(item_path, sep='\t', dtype=str); it.columns = [c.strip() for c in it.columns]
it = it.apply(lambda s: s.str.strip()); it['seq'] = it.sort_sequence.astype(int); it = it.sort_values('seq')

# ---------- observations: flat files, then API ----------
source = None
try:
    frames = []
    for f in DATA_FILES:
        p = os.path.join(WORK, f); get(BLS + f, p, min_bytes=100_000); frames.append(parse_flat(p))
    d = pd.concat(frames); source = 'BLS flat files'
except RuntimeError as e:
    print('WARN flat files failed:', e)
    if not API_KEY:
        sys.exit('ERROR: BLS flat files unavailable and no BLS_API_KEY secret set for the API fallback.')
    ids = ['CUSR0000' + c for c in it[it.seq < 356].item_code if c != 'SA0'] + ['CUUR0000SA0', 'CUSR0000SA0']
    now = dt.date.today().year; rows = []
    windows = [(y, min(y + 19, now)) for y in range(1985, now + 1, 20)]
    for i in range(0, len(ids), 50):
        for y0, y1 in windows:
            body = {'seriesid': ids[i:i + 50], 'startyear': str(y0), 'endyear': str(y1), 'registrationkey': API_KEY}
            for k in range(4):
                r = S.post('https://api.bls.gov/publicAPI/v2/timeseries/data/', json=body, timeout=120)
                j = r.json() if r.ok else {}
                if j.get('status') == 'REQUEST_SUCCEEDED': break
                time.sleep(10 * (k + 1))
            else:
                sys.exit(f'ERROR: BLS API failed: {j.get("message") or r.status_code}')
            for s in j['Results']['series']:
                rows += [(s['seriesID'], o['year'], o['period'], o['value']) for o in s['data']]
    d = pd.DataFrame(rows, columns=['series_id', 'year', 'period', 'value']); source = 'BLS API v2'
print('source:', source, f'{len(d):,} rows')

d = d[d.period.str.match(r'M(0[1-9]|1[0-2])$')].copy()
d['date'] = pd.to_datetime(d.year + '-' + d.period.str[1:] + '-01')
d['value'] = pd.to_numeric(d.value, errors='coerce'); d = d.dropna(subset=['value']).drop_duplicates(['series_id', 'date'])

fonts = {}
for k, p in FONTS.items():
    fp = os.path.join(WORK, k + '.woff2')
    if not os.path.exists(fp): get('https://cdn.jsdelivr.net/npm/@fontsource/' + p, fp, min_bytes=5000)
    fonts[k] = base64.b64encode(open(fp, 'rb').read()).decode()

sa = d[d.series_id.str.startswith('CUSR0000')].pivot(index='date', columns='series_id', values='value')
sa.columns = [c[8:] for c in sa.columns]
# universe: every SA U.S. city-average item in the main expenditure hierarchy (excl. All items and the
# special aggregates such as 'All items less food', 'Services', 'Energy' that sit after sort_sequence 356)
comps = [c for c in it[(it.seq < 356)].item_code if c in sa.columns and c != 'SA0']
x = sa[comps].sort_index()
if GAP_MONTH not in x.index: x.loc[GAP_MONTH] = np.nan; x = x.sort_index()
lx = np.log(x); prv, nxt = GAP_MONTH - pd.DateOffset(months=1), GAP_MONTH + pd.DateOffset(months=1)
lx.loc[GAP_MONTH] = lx.loc[GAP_MONTH].fillna((lx.loc[prv] + lx.loc[nxt]) / 2)  # log-linear bridge
x = np.exp(lx)
q = (x / x.shift(3) - 1) * 100
valid = q.notna(); n = valid.sum(axis=1)
share = (q > THR).sum(axis=1) / n * 100; share[n < 20] = np.nan
breadth = share.rolling(3).mean()

nsa = d[d.series_id == 'CUUR0000SA0'].set_index('date').value.sort_index()
if GAP_MONTH not in nsa.index: nsa.loc[GAP_MONTH] = np.nan; nsa = nsa.sort_index()
ln = np.log(nsa); ln.loc[GAP_MONTH] = (ln.loc[prv] + ln.loc[nxt]) / 2
yoy = np.exp(ln).pct_change(12) * 100

df = pd.DataFrame({'b': breadth, 's': share, 'y': yoy, 'n': n}).loc[START:].dropna(subset=['b', 'y'])

# ---------- stress tests ----------
lags = list(range(-12, 25))
lvl = [round(df.b.corr(df.y.shift(-k)), 3) for k in lags]
chg = [round(df.b.diff(6).corr(df.y.shift(-k).diff(6)), 3) for k in lags]

def r2(sub, h, use_b):
    f = sub.y.shift(-h) - sub.y
    t = pd.DataFrame({'f': f, 'y': sub.y, 'b': sub.b}).dropna()
    X = np.c_[np.ones(len(t)), t.y] if not use_b else np.c_[np.ones(len(t)), t.y, t.b]
    beta = np.linalg.lstsq(X, t.f, rcond=None)[0]; res = t.f - X @ beta
    return 1 - res.var() / t.f.var(), beta
tests = []
for label, sub in [('Full sample', df), ('Pre-2020', df.loc[:'2019'])]:
    for h in (6, 12):
        a, _ = r2(sub, h, False); b, beta = r2(sub, h, True)
        tests.append({'sample': label, 'h': h, 'r2y': round(a, 3), 'r2yb': round(b, 3), 'bcoef': round(beta[2] * 10, 3)})
_, beta12 = r2(df, 12, True); _, beta12y = r2(df, 12, False)
last = df.iloc[-1]
fc_b = float(last.y + beta12[0] + beta12[1] * last.y + beta12[2] * last.b)
fc_y = float(last.y + beta12y[0] + beta12y[1] * last.y)
z = lambda s: (s - s.mean()) / s.std()
gap = (z(df.b) - z(df.y))

payload = {
    'dates': [t.strftime('%Y-%m') for t in df.index],
    'breadth': [round(v, 2) for v in df.b], 'yoy': [round(v, 2) for v in df.y],
    'raw': [round(v, 2) for v in df.s], 'n': [int(v) for v in df.n],
    'bridged': sorted({GAP_MONTH.strftime('%Y-%m'), (GAP_MONTH + pd.DateOffset(months=3)).strftime('%Y-%m')}),
    'thr': THR,
    'nber': NBER, 'lags': lags, 'lvl': lvl, 'chg': chg, 'tests': tests,
    'latest': {'date': df.index[-1].strftime('%b %Y'), 'b': round(float(last.b), 1), 'y': round(float(last.y), 2),
               'n': int(last.n), 'gap': round(float(gap.iloc[-1]), 2), 'bmean': round(float(df.b.mean()), 1),
               'fc_b': round(fc_b, 2), 'fc_y': round(fc_y, 2),
               'peak_lag': lags[int(np.argmax(lvl))], 'peak_lvl': max(lvl), 'peak_chg_lag': lags[int(np.argmax(chg))],
               'peak_chg': max(chg), 'ncomp': len(comps)},
    'built': dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y %H:%M UTC'),
}

TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>% of US CPI components rising >0.5% QoQ</title>
<style>
@font-face{font-family:"Space Grotesk";font-weight:600;src:url(data:font/woff2;base64,__FONT_sg600__) format("woff2")}
@font-face{font-family:"IBM Plex Sans";font-weight:400;src:url(data:font/woff2;base64,__FONT_px400__) format("woff2")}
@font-face{font-family:"IBM Plex Sans";font-weight:500;src:url(data:font/woff2;base64,__FONT_px500__) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:400;src:url(data:font/woff2;base64,__FONT_pm400__) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:500;src:url(data:font/woff2;base64,__FONT_pm500__) format("woff2")}
:root{--paper:#F7F1E6;--card:#FDFAF3;--ink:#221C14;--muted:#6B6153;--rule:#E4DACA;--orange:#D2622A;--teal:#0E756C;--ochre:#A67A22;--slate:#4E6577;--band:#E6DED0;
  box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#1A1612;--card:#221D17;--ink:#EDE5D6;--muted:#A89C8A;--rule:#3A3229;--orange:#E8834F;--teal:#3FA89D;--ochre:#C99A45;--slate:#8BA3B5;--band:#2E2821}}
:root[data-theme="dark"]{--paper:#1A1612;--card:#221D17;--ink:#EDE5D6;--muted:#A89C8A;--rule:#3A3229;--orange:#E8834F;--teal:#3FA89D;--ochre:#C99A45;--slate:#8BA3B5;--band:#2E2821}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--paper);color:var(--ink);font:400 15px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:36px 22px 56px}
h1{font:600 clamp(26px,4.2vw,40px)/1.08 "Space Grotesk","Helvetica Neue",Arial,sans-serif;letter-spacing:-.02em;margin:0 0 10px;max-width:30ch}
h2{font:600 19px/1.25 "Space Grotesk","Helvetica Neue",Arial,sans-serif;letter-spacing:-.01em;margin:0 0 6px}
.dek{color:var(--muted);max-width:68ch;margin:0}
.num{font-family:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;font-feature-settings:"tnum"}
.readings{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:2px solid var(--ink);border-bottom:1px solid var(--rule);margin:26px 0 22px}
.readings div{padding:12px 14px 12px 0}
.readings div+div{padding-left:14px;border-left:1px solid var(--rule)}
.readings b{display:block;font:500 24px/1.1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:-.01em}
.readings span{font-size:13px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:20px 20px 16px}
.hero{padding:22px 22px 14px;border-radius:8px}
.chart-head{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:flex-end;margin-bottom:10px}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:5px;overflow:hidden}
.seg button{font:500 13px "IBM Plex Sans",system-ui,sans-serif;background:transparent;color:var(--muted);border:0;padding:6px 11px;cursor:pointer}
.seg button+button{border-left:1px solid var(--rule)}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--card)}
.seg button:focus-visible{outline:2px solid var(--orange);outline-offset:-2px}
.cv{position:relative;height:440px}
.cv.sm{height:300px}
.legend{display:flex;flex-wrap:wrap;gap:6px 22px;margin:10px 0 0;font-size:14px}
.legend i{display:inline-block;width:22px;height:0;border-top:2.5px solid;vertical-align:middle;margin-right:8px}
.legend .band{width:14px;height:12px;border:0;background:var(--band);vertical-align:-1px}
.foot{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;color:var(--muted);font-size:12.5px;margin-top:10px;border-top:1px solid var(--rule);padding-top:8px}
.findings{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:26px;margin:34px 0}
.findings p{margin:6px 0 0;font-size:14.5px}
.findings h2{font-size:16.5px;border-top:2px solid var(--ink);padding-top:10px}
.findings .neg h2{border-color:var(--orange)}
.two{display:grid;grid-template-columns:1.35fr 1fr;gap:18px}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:right;padding:7px 6px;border-bottom:1px solid var(--rule)}
th:first-child,td:first-child{text-align:left}
th{font-weight:500;color:var(--muted);font-size:12.5px}
td.num{white-space:nowrap}
.fc{margin-top:16px;padding-top:12px;border-top:2px solid var(--ink);font-size:14px}
.fc .num{font-size:15px}
.notes{margin-top:34px;color:var(--muted);font-size:13.5px;max-width:78ch}
.notes h2{color:var(--ink);font-size:16px}
.notes li{margin:5px 0}
.tw{overflow-x:auto}
.dl{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}
.dl[hidden]{display:none}
.dl button{font:500 13px "IBM Plex Sans",system-ui,sans-serif;background:transparent;color:var(--ink);border:1px solid var(--rule);border-radius:5px;padding:5px 10px;cursor:pointer}
.dl button:hover{border-color:var(--ink)}
.dl button:focus-visible{outline:2px solid var(--orange);outline-offset:2px}
.dl output{font-size:12.5px;color:var(--muted)}
@media (max-width:820px){.findings{grid-template-columns:1fr;gap:18px}.two{grid-template-columns:1fr}.readings{grid-template-columns:repeat(2,minmax(0,1fr))}.readings div:nth-child(3){padding-left:0;border-left:0}.readings div:nth-child(n+3){border-top:1px solid var(--rule)}.cv{height:340px}.wrap{padding:24px 14px 44px}.hero,.card{padding:16px 14px 12px}}
</style>
</head>
<body>
<main class="wrap">
  <h1>% of US CPI components rising &gt;0.5% QoQ</h1>
  <p class="dek">A rebuild of Variant Perception's CPI breadth chart from BLS item-level data, with a check on whether breadth actually leads headline inflation.</p>

  <section class="readings" aria-label="Latest readings">
    <div><b class="num" id="rB">–</b><span>Components rising &gt;0.5% QoQ, 3mma</span></div>
    <div><b class="num" id="rY">–</b><span>US CPI YoY</span></div>
    <div><b class="num" id="rG">–</b><span>Breadth minus CPI, z-score gap</span></div>
    <div><b class="num" id="rN">–</b><span>Components in the latest month</span></div>
  </section>

  <section class="card hero">
    <div class="chart-head">
      <div><h2>Breadth vs headline CPI</h2><p class="sub" style="margin:0">Left axis CPI YoY, right axis component share, both %. Grey bands are NBER recessions.</p></div>
      <div class="seg" role="group" aria-label="Date range">
        <button data-from="1988-01" aria-pressed="true">Since 1988</button><button data-from="2000-01" aria-pressed="false">Since 2000</button><button data-from="2015-01" aria-pressed="false">Since 2015</button>
      </div>
    </div>
    <div class="cv"><canvas id="main" role="img" aria-label="Line chart of US CPI YoY and the share of CPI components rising more than 0.5% quarter on quarter since 1988"></canvas></div>
    <div class="legend"><span><i style="border-color:var(--ink)"></i>US CPI YoY</span><span><i style="border-color:var(--orange)"></i>% of CPI components rising &gt;0.5% QoQ (3mma)</span><span><i class="band"></i>NBER recession</span></div>
    <div class="foot"><span id="footL">Source: BLS CPI-U item indices; Acheron Insights calculations</span><span id="footR"></span></div>
    <div class="dl" id="dl" hidden aria-label="Download data">
      <button type="button" data-kind="series">Download monthly data (CSV)</button>
      <button type="button" data-kind="stats">Download lead/lag and R² (CSV)</button>
      <output id="dlMsg" aria-live="polite"></output>
    </div>
  </section>

  <section class="findings" id="findings"></section>

  <section class="two">
    <div class="card">
      <h2>Where the correlation peaks</h2>
      <p class="sub">Correlation of breadth today with CPI YoY <em>k</em> months later. Positive <em>k</em> means breadth leads.</p>
      <div class="cv sm"><canvas id="lag" role="img" aria-label="Cross-correlation of CPI breadth with CPI YoY across leads and lags"></canvas></div>
      <div class="legend"><span><i style="border-color:var(--slate)"></i>Levels</span><span><i style="border-color:var(--teal)"></i>6-month changes</span></div>
    </div>
    <div class="card">
      <h2>Does breadth add anything?</h2>
      <p class="sub">R² for the change in CPI YoY over the next <em>h</em> months. Baseline uses today's YoY only (mean reversion); the second model adds breadth.</p>
      <div class="tw"><table id="tbl"><thead><tr><th>Sample</th><th><em>h</em></th><th>YoY only</th><th>+ breadth</th><th>Δ R²</th></tr></thead><tbody></tbody></table></div>
      <div class="fc" id="fc"></div>
    </div>
  </section>

  <section class="notes">
    <h2>Method and data notes</h2>
    <ul id="notes"></ul>
  </section>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const D = /*__DATA__*/null;
(function(){
const $=id=>document.getElementById(id);
const L=D.latest;
const f1=v=>v.toFixed(1), f2=v=>v.toFixed(2), sg=v=>(v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(2);
$('rB').textContent=f1(L.b)+'%'; $('rY').textContent=f1(L.y)+'%'; $('rG').textContent=sg(L.gap)+'σ'; $('rN').textContent=L.n;
$('footR').textContent='Data through '+L.date+' · built '+D.built;

// ---- findings (numbers live from payload) ----
const iPk=D.dates.reduce((a,d,i)=>d>=D.dates[D.dates.length-9]&&D.breadth[i]>D.breadth[a]?i:a,D.dates.length-9);
const pkD=new Date(D.dates[iPk]+'-01').toLocaleString('en-US',{month:'short',year:'numeric'});
const t12=D.tests.find(t=>t.sample==='Full sample'&&t.h===12), p12=D.tests.find(t=>t.sample==='Pre-2020'&&t.h===12);
const lagAt=k=>D.lvl[D.lags.indexOf(k)], chgAt=k=>D.chg[D.lags.indexOf(k)];
$('findings').innerHTML=
 `<div><h2>Breadth has rolled over while headline has not</h2><p>The share of components rising faster than 0.5% a quarter fell to <span class="num">${f1(L.b)}%</span> in ${L.date}, from <span class="num">${f1(D.breadth[iPk])}%</span> in ${pkD} and below its ${D.dates[0].slice(0,4)}–present mean of <span class="num">${f1(L.bmean)}%</span>. CPI YoY is still <span class="num">${f1(L.y)}%</span>. On standardised terms breadth sits <span class="num">${Math.abs(L.gap).toFixed(2)}σ</span> ${L.gap<0?'below':'above'} headline.</p></div>`+
 `<div class="neg"><h2>It is a coincident gauge, not a lead</h2><p>The level correlation peaks at <em>k</em> = <span class="num">${L.peak_lag}</span> (<span class="num">${f2(L.peak_lvl)}</span>)${L.peak_lag<0?', breadth trailing headline by '+(-L.peak_lag)+'m':''}, and decays in both directions: <span class="num">${f2(lagAt(6))}</span> six months ahead, <span class="num">${f2(lagAt(12))}</span> at twelve. On 6-month changes it peaks at <em>k</em> = <span class="num">${L.peak_chg_lag}</span> (<span class="num">${f2(L.peak_chg)}</span>) and falls to <span class="num">${f2(chgAt(12))}</span> a year out. Breadth confirms turns; it does not front-run them.</p></div>`+
 `<div><h2>The residual signal is small but stable</h2><p>Where it earns its keep is relative to headline. Adding breadth to a pure mean-reversion model lifts 12-month-ahead R² from <span class="num">${f2(t12.r2y)}</span> to <span class="num">${f2(t12.r2yb)}</span>, and from <span class="num">${f2(p12.r2y)}</span> to <span class="num">${f2(p12.r2yb)}</span> pre-2020, so the gain is not a 2021–22 artefact. Today that nudges the 12-month-ahead YoY estimate from <span class="num">${f1(L.fc_y)}%</span> to <span class="num">${f1(L.fc_b)}%</span>.</p></div>`;

// ---- table ----
$('tbl').querySelector('tbody').innerHTML=D.tests.map(t=>`<tr><td>${t.sample}</td><td class="num">${t.h}m</td><td class="num">${f2(t.r2y)}</td><td class="num">${f2(t.r2yb)}</td><td class="num">+${f2(t.r2yb-t.r2y)}</td></tr>`).join('');
$('fc').innerHTML=`Implied CPI YoY 12 months after ${L.date}: <span class="num">${f1(L.fc_y)}%</span> from YoY alone, <span class="num">${f1(L.fc_b)}%</span> with breadth. In-sample OLS fits on overlapping monthly windows, so treat these as descriptive rather than a forecast with honest error bands.`;

// ---- notes ----
$('notes').innerHTML=[
 `Universe: every seasonally adjusted U.S. city-average CPI-U index in the main expenditure hierarchy, all levels from major group down to special item (${L.ncomp} series over the full history; ${L.n} report in the latest month). "All items" and cross-cutting special aggregates (e.g. core, services, energy) are excluded. VP's exact basket is proprietary; this universe reproduces its published contours closely (≈33% trough in 2015, ≈80% peak in 2022, ≈64% early-2026 high, high-40s latest).`,
 `Signal: a component counts if its SA index is more than 0.5% above its level three months earlier (not annualised, ≈2% a year). The share of reporting components is then smoothed with a 3-month average. Months with fewer than 20 reporting components are dropped.`,
 `Coverage grows from ≈105 components in 1988 to ≈280 today as BLS adds and seasonally adjusts more items; the share is computed on whatever reports each month. BLS also drops items from seasonal adjustment when they fail its tests, so the basket is not constant.`,
 `October 2025: BLS published no October CPI owing to the federal shutdown. Each index (and headline NSA CPI) is bridged with the log-linear midpoint of September and November. This affects the October 2025 and January 2026 quarterly changes and the 3mma through March 2026.`,
 `CPI YoY is headline CPI-U, not seasonally adjusted (CUUR0000SA0). Recession bands are NBER peak-to-trough months.`,
 `Lead/lag and R² panels use ${D.dates[0]} onward. Overlapping horizons inflate the apparent precision of the regressions; no out-of-sample test is shown.`
].map(s=>`<li>${s}</li>`).join('');

// ---- charts ----
const dark=()=>{const t=document.documentElement.dataset.theme;return t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches};
const P=()=>dark()?{ink:'#EDE5D6',muted:'#A89C8A',rule:'#3A3229',orange:'#E8834F',teal:'#3FA89D',slate:'#8BA3B5',band:'#2E2821',card:'#221D17'}
                 :{ink:'#221C14',muted:'#6B6153',rule:'#E4DACA',orange:'#D2622A',teal:'#0E756C',slate:'#4E6577',band:'#E6DED0',card:'#FDFAF3'};
const MONO='"IBM Plex Mono", ui-monospace, Menlo, monospace', SANS='"IBM Plex Sans", system-ui, sans-serif';
Chart.defaults.font.family=SANS; Chart.defaults.font.size=12; Chart.defaults.animation=false;

let from='1988-01';
const bands={id:'bands',beforeDatasetsDraw(c){const {ctx,chartArea:a,scales:{x}}=c;if(!x||!c.$labels)return;ctx.save();ctx.fillStyle=P().band;
  D.nber.forEach(([s,e])=>{const i0=c.$labels.indexOf(s),i1=c.$labels.indexOf(e);if(i1<0&&i0<0)return;const l=i0<0?a.left:x.getPixelForValue(i0),r=i1<0?a.right:x.getPixelForValue(i1);if(r<a.left||l>a.right)return;ctx.fillRect(Math.max(l,a.left),a.top,Math.min(r,a.right)-Math.max(l,a.left),a.bottom-a.top)});ctx.restore()}};
const endTags={id:'endTags',afterDatasetsDraw(c){const {ctx}=c;const p=P();ctx.save();ctx.font='500 11px '+MONO;
  c.data.datasets.forEach((ds,di)=>{const m=c.getDatasetMeta(di),pt=m.data[m.data.length-1];if(!pt)return;const v=ds.data[ds.data.length-1],txt=(di===0?v.toFixed(1):v.toFixed(1))+'%';const w=ctx.measureText(txt).width+8;
    ctx.fillStyle=ds.borderColor;const y=pt.y-8;ctx.fillRect(pt.x+4,y,w,16);ctx.fillStyle=p.card;ctx.textBaseline='middle';ctx.fillText(txt,pt.x+8,y+8)});ctx.restore()}};

let main,lag;
function build(){
  const p=P();
  const i0=Math.max(0,D.dates.findIndex(d=>d>=from));
  const labels=D.dates.slice(i0), full=from==='1988-01';
  if(main)main.destroy();
  main=new Chart($('main'),{type:'line',plugins:[bands,endTags],
    data:{labels,datasets:[
      {label:'US CPI YoY',data:D.yoy.slice(i0),borderColor:p.ink,borderWidth:1.8,pointRadius:0,yAxisID:'y',tension:0},
      {label:'% of components rising >0.5% QoQ (3mma)',data:D.breadth.slice(i0),borderColor:p.orange,borderWidth:1.8,pointRadius:0,yAxisID:'y1',tension:0}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},layout:{padding:{right:0}},
      plugins:{legend:{display:false},tooltip:{backgroundColor:p.card,titleColor:p.ink,bodyColor:p.ink,borderColor:p.rule,borderWidth:1,padding:10,
        titleFont:{family:MONO,weight:'500'},bodyFont:{family:MONO},boxWidth:10,boxHeight:2,
        callbacks:{title:i=>new Date(i[0].label+'-01').toLocaleString('en-US',{month:'short',year:'numeric'}),label:i=>' '+(i.datasetIndex?'Breadth ':'CPI YoY ')+i.parsed.y.toFixed(i.datasetIndex?1:2)+'%'}}},
      scales:{
        x:{grid:{display:false},border:{color:p.ink},ticks:{color:p.muted,autoSkip:false,maxRotation:0,font:{family:MONO},
          callback:(v,i)=>{const d=labels[i];if(d.slice(5)!=='01')return null;const yr=+d.slice(0,4),nar=innerWidth<640;const st=full?(nar?10:5):(from==='2000-01'?(nar?5:2):(nar?2:1));return yr%st===0?yr:null}}},
        y:{position:'left',min:full?-2.5:undefined,max:full?9.5:undefined,grid:{color:p.rule,drawTicks:false},border:{display:false},
          ticks:{color:p.ink,font:{family:MONO},padding:6}},
        y1:{position:'right',min:full?28:undefined,max:full?86:undefined,grid:{display:false},border:{display:false},
          ticks:{color:p.orange,font:{family:MONO},padding:48}}}}});
  main.$labels=labels; main.update();

  if(lag)lag.destroy();
  const zero={id:'zero',beforeDatasetsDraw(c){const {ctx,chartArea:a,scales:{x}}=c;const px=x.getPixelForValue(D.lags.indexOf(0));ctx.save();ctx.strokeStyle=p.muted;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(px,a.top);ctx.lineTo(px,a.bottom);ctx.stroke();ctx.restore()}};
  lag=new Chart($('lag'),{type:'line',plugins:[zero],data:{labels:D.lags,datasets:[
      {label:'Levels',data:D.lvl,borderColor:p.slate,backgroundColor:p.slate,borderWidth:2,pointRadius:0,pointHoverRadius:3},
      {label:'6-month changes',data:D.chg,borderColor:p.teal,backgroundColor:p.teal,borderWidth:2,pointRadius:0,pointHoverRadius:3}]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{display:false},tooltip:{backgroundColor:p.card,titleColor:p.ink,bodyColor:p.ink,borderColor:p.rule,borderWidth:1,titleFont:{family:MONO},bodyFont:{family:MONO},
        callbacks:{title:i=>{const k=+i[0].label;return k===0?'Contemporaneous':k>0?`Breadth leads by ${k}m`:`Breadth lags by ${-k}m`},label:i=>' '+i.dataset.label+'  '+i.parsed.y.toFixed(2)}}},
      scales:{x:{grid:{display:false},border:{color:p.ink},title:{display:true,text:'k, months (breadth leads →)',color:p.muted},ticks:{color:p.muted,font:{family:MONO},autoSkip:false,maxRotation:0,callback:(v,i)=>D.lags[i]%6===0?D.lags[i]:null}},
        y:{min:-0.4,max:1,grid:{color:p.rule},border:{display:false},ticks:{color:p.ink,font:{family:MONO},stepSize:0.2,callback:v=>(Math.abs(v)<1e-9?0:v).toFixed(1)}}}}});
}
document.querySelectorAll('.seg button').forEach(b=>b.addEventListener('click',()=>{from=b.dataset.from;document.querySelectorAll('.seg button').forEach(x=>x.setAttribute('aria-pressed',x===b));build()}));
// ---- downloads ----
const stamp=D.dates[D.dates.length-1];
function csvSeries(){const h='date,cpi_yoy_pct,breadth_3mma_pct,breadth_monthly_pct,components_reporting,oct2025_bridge_affected';
  const aff=d=>(d>='2025-10'&&d<='2026-03')?1:0;
  return h+'\n'+D.dates.map((d,i)=>[d,D.yoy[i],D.breadth[i],D.raw[i],D.n[i],aff(d)].join(',')).join('\n')+'\n'}
function csvStats(){let s='# cross-correlation: corr(breadth_3mma[t], cpi_yoy[t+k]); k>0 = breadth leads\nk_months,corr_levels,corr_6m_changes\n';
  s+=D.lags.map((k,i)=>[k,D.lvl[i],D.chg[i]].join(',')).join('\n');
  s+='\n\n# R2 for change in CPI YoY over next h months\nsample,h_months,r2_yoy_only,r2_yoy_plus_breadth,breadth_coef_per_10pp\n';
  s+=D.tests.map(t=>[t.sample,t.h,t.r2y,t.r2yb,t.bcoef].join(',')).join('\n')+'\n';return s}
const files={series:()=>({filename:`us_cpi_breadth_${stamp}.csv`,data:csvSeries()}),stats:()=>({filename:`us_cpi_breadth_leadlag_${stamp}.csv`,data:csvStats()})};
const dlBox=$('dl'),dlMsg=$('dlMsg');let saver=null;
function wire(){dlBox.hidden=false;dlBox.querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{
  const f=files[b.dataset.kind]();dlMsg.textContent='';
  try{const r=await saver(f);dlMsg.textContent=r==='saved'?'Saved '+f.filename:''}
  catch(e){const c=e&&e.code;
    if(c==='declined')dlMsg.textContent='Download cancelled';
    else if(c==='rate_limited')dlMsg.textContent='A download prompt is already open';
    else{dlMsg.textContent='Downloads are unavailable in this view';dlBox.querySelectorAll('button').forEach(x=>x.disabled=true)}}}))}
if(window.claude&&typeof window.claude.use==='function'){
  window.claude.use('downloads').then(dl=>{if(!dl)return;saver=async f=>(await dl.save(f)).status;wire()}).catch(()=>{});
}else{ // standalone file opened locally: plain browser download
  saver=async f=>{const u=URL.createObjectURL(new Blob([f.data],{type:'text/csv'}));const a=document.createElement('a');a.href=u;a.download=f.filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);return 'saved'};wire();
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',build);
let rt;addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(build,200)});
new MutationObserver(build).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
build();
})();
</script>
</body>
</html>
'''

# ---------- sanity checks (fail -> workflow keeps last good deploy) ----------
lastdate = df.index[-1]
age = (pd.Timestamp.today().normalize() - lastdate).days
problems = []
if age > 100: problems.append(f'latest observation {lastdate:%Y-%m} is {age} days old')
if int(df.n.iloc[-1]) < 200: problems.append(f'only {int(df.n.iloc[-1])} components in latest month')
if not (0 <= df.b.iloc[-1] <= 100) or not (-5 < df.y.iloc[-1] < 20): problems.append('latest values out of range')
if len(df) < 400: problems.append(f'only {len(df)} months of history')
if problems: sys.exit('ERROR sanity check: ' + '; '.join(problems))
payload['source'] = source

html = TEMPLATE
html, k = re.subn(r'/\*__DATA__\*/null', lambda m: json.dumps(payload, separators=(',', ':')), html); assert k == 1
for key, b64 in fonts.items():
    html, k = re.subn('__FONT_' + key + '__', lambda m, b=b64: b, html); assert k == 1
open(os.path.join(OUT, 'index.html'), 'w').write(html)
print('wrote', os.path.join(OUT, 'index.html'), f'{len(html)/1e6:.2f} MB', payload['latest'])
