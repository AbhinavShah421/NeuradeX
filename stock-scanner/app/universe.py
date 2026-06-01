"""The market universe the scanner continuously sweeps for intraday candidates.

A broad set of liquid NSE names — the scanner itself filters this down to the
stocks that are actually a good fit for intraday trading (liquidity + volatility).
"""

UNIVERSE: dict[str, str] = {
    # Index heavyweights / large caps
    "RELIANCE": "Reliance Industries", "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
    "HINDUNILVR": "Hindustan Unilever", "ITC": "ITC", "SBIN": "State Bank of India",
    "BHARTIARTL": "Bharti Airtel", "KOTAKBANK": "Kotak Mahindra Bank",
    "LT": "Larsen & Toubro", "AXISBANK": "Axis Bank", "BAJFINANCE": "Bajaj Finance",
    "ASIANPAINT": "Asian Paints", "MARUTI": "Maruti Suzuki", "TITAN": "Titan Company",
    "SUNPHARMA": "Sun Pharmaceutical", "WIPRO": "Wipro", "ULTRACEMCO": "UltraTech Cement",
    "ONGC": "Oil & Natural Gas Corp", "NTPC": "NTPC", "POWERGRID": "Power Grid Corp",
    "NESTLEIND": "Nestle India", "TATAMOTORS": "Tata Motors", "TATASTEEL": "Tata Steel",
    "JSWSTEEL": "JSW Steel", "HCLTECH": "HCL Technologies", "TECHM": "Tech Mahindra",
    "ADANIENT": "Adani Enterprises", "ADANIPORTS": "Adani Ports", "COALINDIA": "Coal India",
    "BAJAJFINSV": "Bajaj Finserv", "GRASIM": "Grasim Industries", "DRREDDY": "Dr Reddy's Labs",
    "CIPLA": "Cipla", "EICHERMOT": "Eicher Motors", "BPCL": "Bharat Petroleum",
    "HEROMOTOCO": "Hero MotoCorp", "BRITANNIA": "Britannia Industries", "DIVISLAB": "Divi's Labs",
    "HINDALCO": "Hindalco Industries", "INDUSINDBK": "IndusInd Bank", "M&M": "Mahindra & Mahindra",
    "APOLLOHOSP": "Apollo Hospitals", "BAJAJ-AUTO": "Bajaj Auto", "TATACONSUM": "Tata Consumer",
    "SBILIFE": "SBI Life Insurance", "HDFCLIFE": "HDFC Life", "DABUR": "Dabur India",
    # Liquid mid caps / high-beta intraday favourites
    "PNB": "Punjab National Bank", "BANKBARODA": "Bank of Baroda", "CANBK": "Canara Bank",
    "FEDERALBNK": "Federal Bank", "IDFCFIRSTB": "IDFC First Bank", "BANDHANBNK": "Bandhan Bank",
    "AUBANK": "AU Small Finance Bank", "IDBI": "IDBI Bank", "IOB": "Indian Overseas Bank",
    "SUZLON": "Suzlon Energy", "IREDA": "Indian Renewable Energy", "RVNL": "Rail Vikas Nigam",
    "IRFC": "Indian Railway Finance", "BHEL": "Bharat Heavy Electricals", "SAIL": "Steel Authority",
    "NMDC": "NMDC", "VEDL": "Vedanta", "GAIL": "GAIL India", "IOC": "Indian Oil",
    "HINDPETRO": "Hindustan Petroleum", "ZOMATO": "Zomato", "PAYTM": "One97 (Paytm)",
    "NYKAA": "FSN E-Commerce (Nykaa)", "POLICYBZR": "PB Fintech", "DMART": "Avenue Supermarts",
    "JINDALSTEL": "Jindal Steel", "TATAPOWER": "Tata Power", "ADANIPOWER": "Adani Power",
    "ADANIGREEN": "Adani Green Energy", "DLF": "DLF", "GODREJCP": "Godrej Consumer",
    "PIDILITIND": "Pidilite Industries", "HAVELLS": "Havells India", "SIEMENS": "Siemens",
    "BEL": "Bharat Electronics", "HAL": "Hindustan Aeronautics", "MOTHERSON": "Samvardhana Motherson",
    "TVSMOTOR": "TVS Motor", "ASHOKLEY": "Ashok Leyland", "BOSCHLTD": "Bosch",
    "LICI": "Life Insurance Corp", "ICICIPRULI": "ICICI Prudential", "ICICIGI": "ICICI Lombard",
    "CHOLAFIN": "Cholamandalam Investment", "SHRIRAMFIN": "Shriram Finance", "MUTHOOTFIN": "Muthoot Finance",
    "LUPIN": "Lupin", "AUROPHARMA": "Aurobindo Pharma", "BIOCON": "Biocon", "TORNTPHARM": "Torrent Pharma",
    "ZEEL": "Zee Entertainment", "JKTYRE": "JK Tyre", "TMPV": "Tata Motors DVR",
    "PERSISTENT": "Persistent Systems", "LTIM": "LTIMindtree", "COFORGE": "Coforge",
    "MPHASIS": "Mphasis", "OFSS": "Oracle Financial", "POLYCAB": "Polycab India",
}
