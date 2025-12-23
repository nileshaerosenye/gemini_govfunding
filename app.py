import requests
import yfinance as yf
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)
HEADERS = {'User-Agent': 'MarketCapFix/1.0 (yourname@example.com)'}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search')
def search():
    query = request.args.get('q', '').upper()
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        data = requests.get(url, headers=HEADERS).json()
        results = [{"ticker": v['ticker'], "name": v['title'], "cik": v['cik_str']}
                   for v in data.values() if query in v['ticker'] or query in v['title'].upper()][:8]
        return jsonify(results)
    except:
        return jsonify([])


@app.route('/get_financials')
def get_financials():
    ticker = request.args.get('ticker')
    cik = str(request.args.get('cik')).zfill(10)

    try:
        # 1. Fetch SEC Shares
        sec_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        res = requests.get(sec_url, headers=HEADERS).json()
        facts = res.get('facts', {})

        master_shares = {}
        # Try different taxonomies and tags
        for tax in ['dei', 'us-gaap']:
            for tag in ['EntityCommonStockSharesOutstanding', 'CommonStockSharesOutstanding']:
                if tag in facts.get(tax, {}):
                    # SEC data can be under 'shares' or 'pure'
                    units = facts[tax][tag]['units']
                    unit_key = 'shares' if 'shares' in units else 'pure'
                    for entry in units.get(unit_key, []):
                        # Filter for 10-K/Q to avoid noise
                        if entry.get('form') in ['10-K', '10-Q']:
                            master_shares[entry['end']] = entry['val']

        sorted_dates = sorted(master_shares.keys())
        if not sorted_dates:
            return jsonify({"error": "No share data found in SEC tags"}), 404

        # 2. Fetch Prices with fixed multi-index
        # We download extra padding to ensure we have prices for all SEC dates
        stock_data = yf.download(ticker, start=sorted_dates[0], progress=False)

        # FIX: Flatten yfinance multi-index if it exists
        if isinstance(stock_data.columns, pd.MultiIndex):
            stock_data.columns = stock_data.columns.get_level_values(0)

        labels, share_counts, market_caps = [], [], []

        for date_str in sorted_dates:
            target_dt = datetime.strptime(date_str, '%Y-%m-%d')

            # 3. Fuzzy Search: Find the closest price on or before this date
            # This handles weekends and holidays
            try:
                # Get all dates before or equal to target
                available_prices = stock_data[stock_data.index <= target_dt]
                if available_prices.empty: continue

                # Take the most recent price
                latest_price_row = available_prices.iloc[-1]
                price = float(latest_price_row['Close'])
                shares = float(master_shares[date_str])

                labels.append(date_str)
                share_counts.append(shares)
                market_caps.append(shares * price)
            except:
                continue

        return jsonify({
            "labels": labels,
            "shares": share_counts,
            "market_cap": market_caps
        })
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    import pandas as pd  # Ensure pandas is available for the multi-index fix

    app.run(debug=True)