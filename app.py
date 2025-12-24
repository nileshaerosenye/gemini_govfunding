import requests
import yfinance as yf
import pandas as pd
from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

HEADERS = {
    "User-Agent": "EquityIntel/1.0 (contact@example.com)",
    "Accept-Encoding": "gzip, deflate"
}

USA_SPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
LIMIT = 100


@app.route("/")
def index():
    return render_template("index.html")


# ---------------- SEARCH (SEC tickers) ----------------
@app.route("/search")
def search():
    q = request.args.get("q", "").upper()
    try:
        data = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS,
            timeout=10
        ).json()

        results = []
        for v in data.values():
            if q in v["ticker"] or q in v["title"].upper():
                results.append({
                    "ticker": v["ticker"],
                    "name": v["title"],
                    "cik": v["cik_str"]
                })
            if len(results) >= 8:
                break

        return jsonify(results)
    except:
        return jsonify([])


# ---------------- GOVERNMENT CONTRACTS ----------------
def get_gov_contracts(company_name, ticker):
    payload = {
        "filters": {
            "recipient_search_text": [company_name],
            "award_type_codes": ["A", "B", "C", "D"]
        },
        "fields": [
            "Award Amount",
            "Awarding Agency",
            "Start Date"
        ],
        "limit": LIMIT,
        "page": 1
    }

    r = requests.post(
        USA_SPENDING_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=20
    )

    if r.status_code != 200:
        return []

    clean = []
    for row in r.json().get("results", []):
        amt = row.get("Award Amount")
        if amt and amt > 0:
            clean.append({
                "ticker": ticker,
                "agency": row.get("Awarding Agency", "Unknown"),
                "amount": amt,
                "date": row.get("Start Date")
            })
    return clean

def get_government_contracts_details(company_name):
    payload = {
        "filters": {
            "recipient_search_text": [company_name],
            "award_type_codes": ["A", "B", "C", "D"]  # Contracts + grants
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Award Amount",
            "Awarding Agency",
            "Start Date",
            "Award Type"
        ],
        "limit": LIMIT,
        "page": 1
    }

    headers = {"Content-Type": "application/json"}

    r = requests.post(USA_SPENDING_URL, json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()

    results = []
    for row in r.json().get("results", []):
        date_str = row.get("Start Date")

        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else None
        except ValueError:
            parsed_date = None

        amount = float(row.get("Award Amount") or 0)

        results.append({
            "date": date_str,
            "date_obj": parsed_date,  # for sorting only
            "agency": row.get("Awarding Agency", ""),
            "amount": amount,
            "type": row.get("Award Type", ""),
            "program": row.get("Award ID", ""),
            "is_large": amount >= 10_000_000
        })

    # 🔥 SORT: latest date first
    results.sort(
        key=lambda x: x["date_obj"] or datetime.min,
        reverse=True
    )

    # Remove helper field before sending to template
    for r in results:
        r.pop("date_obj", None)

    return results


# ---------------- FINANCIALS ----------------
@app.route("/get_financials")
def get_financials():
    ticker = request.args.get("ticker")
    cik = str(request.args.get("cik")).zfill(10)
    company_name = request.args.get("name", "")

    try:
        # ---- SEC Shares Outstanding ----
        sec_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        sec = requests.get(sec_url, headers=HEADERS, timeout=10).json()

        facts = sec.get("facts", {})
        shares_data = {}

        for tax in ["dei", "us-gaap"]:
            for tag in ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]:
                if tag in facts.get(tax, {}):
                    units = facts[tax][tag]["units"]
                    key = "shares" if "shares" in units else "pure"
                    for r in units.get(key, []):
                        if r.get("form") in ["10-K", "10-Q"]:
                            shares_data[r["end"]] = r["val"]

        if not shares_data:
            return jsonify({"error": "No SEC share data found"})

        dates = sorted(shares_data.keys())

        # ---- Prices ----
        prices = yf.download(ticker, start=dates[0], progress=False)
        if isinstance(prices.columns, pd.MultiIndex):
            prices.columns = prices.columns.get_level_values(0)

        labels, shares, market_caps = [], [], []

        for d in dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            valid = prices[prices.index <= dt]
            if valid.empty:
                continue
            price = float(valid.iloc[-1]["Close"])
            s = float(shares_data[d])

            labels.append(d)
            shares.append(s)
            market_caps.append(s * price)

        # ---- Government Contracts (skip ETFs) ----
        gov_contracts = []
        if not ticker.endswith(("QQQ", "SPY", "ETF")):
            gov_contracts = get_gov_contracts(company_name, ticker)

        return jsonify({
            "labels": labels,
            "shares": shares,
            "market_cap": market_caps,
            "gov_contracts": gov_contracts,
            "ticker": ticker,
            "company": company_name
        })

    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/contracts")
def contracts():
    ticker = request.args.get("ticker")
    name = request.args.get("name")

    if not name:
        return "Missing company name", 400

    try:
        contracts_data = get_government_contracts_details(name)

        return render_template(
            "contracts.html",
            ticker=ticker,
            name=name,
            contracts=contracts_data
        )

    except Exception as e:
        return f"Error fetching contracts: {e}", 500


# ---------------- M A I N ---------------- #

if __name__ == "__main__":
    app.run(debug=True)

# ----------------------------------------- #