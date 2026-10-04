"""Synthetic but realistic retail-banking world with planted financial-crime typologies.

Typologies planted (ground truth kept in GOV.GROUND_TRUTH, never read by detection):
  * MULE_LAYERING  - victims of cyber scams pay collector accounts -> layer accounts
                     -> cash-out accounts (ATM / crypto exchange) within hours.
  * STRUCTURING    - repeated cash deposits just below the 50k PAN threshold,
                     consolidated and wired to a shell entity.
  * ROUND_TRIPPING - large circular transfers between related business accounts.
Decoys: high fan-in kirana merchants, families sharing a phone, students funded by parents.

All names, banks, publications and identifiers are fictional.
"""
from __future__ import annotations

import uuid
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------------
CITIES = {
    "Delhi": ("Delhi", "North"), "Lucknow": ("Uttar Pradesh", "North"), "Jaipur": ("Rajasthan", "North"),
    "Chandigarh": ("Punjab", "North"), "Bengaluru": ("Karnataka", "South"), "Chennai": ("Tamil Nadu", "South"),
    "Hyderabad": ("Telangana", "South"), "Kochi": ("Kerala", "South"), "Kolkata": ("West Bengal", "East"),
    "Bhubaneswar": ("Odisha", "East"), "Patna": ("Bihar", "East"), "Guwahati": ("Assam", "East"),
    "Mumbai": ("Maharashtra", "West"), "Pune": ("Maharashtra", "West"), "Ahmedabad": ("Gujarat", "West"),
    "Surat": ("Gujarat", "West"), "Bhopal": ("Madhya Pradesh", "Central"), "Nagpur": ("Maharashtra", "Central"),
    "Indore": ("Madhya Pradesh", "Central"), "Raipur": ("Chhattisgarh", "Central"),
}
CITY_LIST = list(CITIES)
CITY_W = np.array([10, 5, 4, 3, 10, 7, 8, 3, 6, 2, 3, 2, 11, 6, 5, 3, 2, 3, 3, 2], dtype=float)
CITY_W /= CITY_W.sum()

FIRST_M = ["Aarav", "Vihaan", "Arjun", "Rohan", "Karthik", "Rahul", "Siddharth", "Aditya", "Imran", "Rakesh",
           "Suresh", "Manoj", "Vikram", "Anil", "Pranav", "Harish", "Gaurav", "Nikhil", "Sanjay", "Faisal",
           "Deepak", "Ajay", "Varun", "Ramesh", "Tarun", "Kunal", "Abhishek", "Naveen", "Sameer", "Yusuf"]
FIRST_F = ["Ananya", "Diya", "Priya", "Sneha", "Kavya", "Pooja", "Meera", "Aisha", "Lakshmi", "Neha",
           "Divya", "Swati", "Ritu", "Anjali", "Shreya", "Fatima", "Nandini", "Bhavna", "Isha", "Komal",
           "Sunita", "Rekha", "Geeta", "Pallavi", "Tanvi", "Zoya", "Harini", "Madhuri", "Payal", "Revathi"]
LAST = ["Sharma", "Verma", "Iyer", "Reddy", "Nair", "Patel", "Shah", "Gupta", "Singh", "Khan", "Das",
        "Mukherjee", "Rao", "Menon", "Joshi", "Kulkarni", "Chatterjee", "Agarwal", "Pillai", "Yadav",
        "Mishra", "Bose", "Naidu", "Desai", "Kapoor", "Malhotra", "Ansari", "Hegde", "Saxena", "Pandey"]

OCCUPATIONS = {  # name: (prob, income_lo, income_hi, txn/day)
    "Salaried": (0.38, 300_000, 2_500_000, 1.2),
    "Self-employed": (0.12, 300_000, 2_000_000, 1.5),
    "Business owner": (0.10, 500_000, 6_000_000, 2.0),
    "Student": (0.10, 0, 150_000, 0.6),
    "Homemaker": (0.08, 0, 100_000, 0.3),
    "Retired": (0.07, 200_000, 800_000, 0.4),
    "Gig worker": (0.08, 150_000, 400_000, 1.5),
    "Farmer": (0.07, 100_000, 500_000, 0.4),
}
OCC_NAMES = list(OCCUPATIONS)
OCC_P = np.array([OCCUPATIONS[o][0] for o in OCC_NAMES])
OCC_P /= OCC_P.sum()

BANKS = ["Kaveri Bank", "Northstar Bank", "Indus Valley Co-op Bank", "Saffron Small Finance Bank",
         "Coastal Gramin Bank", "Himalaya Bank", "Deccan Mercantile Bank"]
SOURCES = ["Deccan Ledger", "Metro Courier", "Bharat Business Daily", "City Pulse", "The Evening Mirror"]
EMPLOYERS = ["Infotech Solutions Pvt Ltd", "Shree Ganesh Traders", "Apex Logistics", "Sunrise Hospitals",
             "Bluewave Retail", "Orbit Fintech", "Greenleaf Agro", "Metro Transit Corp", "Vertex Pharma"]
COLLEGES = ["Government Arts College", "City Engineering Institute", "St. Mary's College", "National Polytechnic"]
BIZ = ["a kirana store", "a mobile phone shop", "a pharmacy", "a hardware store", "a tea stall",
       "a garment shop", "a tiffin service", "a stationery shop"]
SCAM_TYPES = ["Investment / trading app scam", "Digital arrest scam", "Part-time job / task scam",
              "Loan app extortion", "Phishing / KYC update scam"]

HOUR_P = np.array([1, .5, .3, .3, .3, .5, 1.5, 3, 5, 6, 6.5, 6.5, 6.5, 6, 6, 6, 6, 6.5, 7, 7.5, 7, 5.5, 4, 2.5])
HOUR_P /= HOUR_P.sum()

# For the rule NEAR_THRESHOLD_CASH (PAN requirement for cash deposits of Rs 50,000+)
PAN_CASH_LIMIT = 50_000


@dataclass
class World:
    seed: int = 42
    end_ts: pd.Timestamp = None
    days: int = 90
    id_prefix: str = ""          # used by the live stream simulator for unique ids
    rng: np.random.Generator = field(init=False)

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)
        if self.end_ts is None:
            self.end_ts = pd.Timestamp.utcnow().tz_localize(None).floor("min")
        self.start_ts = self.end_ts - pd.Timedelta(days=self.days)
        self.customers, self.accounts, self.txns = [], [], []
        self.device_events, self.payment_msgs, self.kyc_notes = [], [], []
        self.media, self.watchlist, self.complaints, self.truth = [], [], [], []
        self._c = self._a = self._t = self._n = 0

    # ------------------------------------------------------------------ ids
    def _cid(self):
        self._c += 1
        return f"C{self.id_prefix}{self._c:07d}"

    def _aid(self):
        self._a += 1
        return f"AC{self.id_prefix}{self._a:08d}"

    def _tid(self):
        self._t += 1
        return f"T{self.id_prefix}{self._t:011d}"

    # ------------------------------------------------------------ helpers
    def _name(self, gender=None):
        r = self.rng
        gender = gender or r.choice(["M", "F"])
        first = r.choice(FIRST_M if gender == "M" else FIRST_F)
        return f"{first} {r.choice(LAST)}", gender

    def _pan(self):
        r = self.rng
        letters = "".join(r.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), 5))
        return f"{letters[:3]}P{letters[3]}{r.integers(1000, 9999)}{r.choice(list('ABCDEFGHJKLMNPQRSTUVWXYZ'))}"

    def _ts_on(self, day: pd.Timestamp, hour_lo=0, hour_hi=24, night=False):
        r = self.rng
        if night:
            hour = int(r.integers(0, 5))
        elif hour_lo == 0 and hour_hi == 24:
            hour = int(r.choice(24, p=HOUR_P))
        else:
            hour = int(r.integers(hour_lo, hour_hi))
        ts = day.normalize() + pd.Timedelta(hours=hour, minutes=int(r.integers(0, 60)), seconds=int(r.integers(0, 60)))
        return min(ts, self.end_ts - pd.Timedelta(seconds=int(r.integers(1, 600))))

    # ------------------------------------------------------------ entities
    def new_customer(self, occupation=None, onboard_days_ago=None, city=None, channel=None, income=None,
                     segment="RETAIL"):
        r = self.rng
        occupation = occupation or r.choice(OCC_NAMES, p=OCC_P)
        _, lo, hi, _ = OCCUPATIONS[occupation]
        name, gender = self._name()
        city = city or r.choice(CITY_LIST, p=CITY_W)
        state, region = CITIES[city]
        age = int(r.integers(18, 25)) if occupation == "Student" else int(r.integers(22, 78))
        if occupation == "Retired":
            age = int(r.integers(60, 82))
        onboard = self.end_ts.normalize() - pd.Timedelta(days=int(onboard_days_ago if onboard_days_ago is not None
                                                                  else r.integers(120, 3000)))
        cid = self._cid()
        branch = f"BR-{city[:3].upper()}-{int(r.integers(1, 4))}"
        c = dict(CUSTOMER_ID=cid, FULL_NAME=name,
                 DOB=(self.end_ts - pd.Timedelta(days=age * 365 + int(r.integers(0, 364)))).date(),
                 GENDER=gender, PHONE=f"9{r.integers(100000000, 999999999)}",
                 EMAIL=f"{name.lower().replace(' ', '.')}{r.integers(1, 999)}@mailbox.example",
                 PAN=self._pan(), AADHAAR_LAST4=f"{r.integers(0, 9999):04d}", CITY=city, STATE=state,
                 REGION=region, OCCUPATION_DECLARED=occupation,
                 ANNUAL_INCOME_DECLARED=float(income if income is not None else round(r.uniform(lo, hi), -3)),
                 KYC_LEVEL="FULL" if r.random() > 0.1 else "MIN",
                 ONBOARD_DATE=onboard.date(),
                 ONBOARD_CHANNEL=channel or r.choice(["BRANCH", "VIDEO_KYC", "DIGITAL"], p=[.5, .25, .25]),
                 BRANCH_ID=branch, CUSTOMER_SEGMENT=segment)
        self.customers.append(c)
        return c

    def new_account(self, cust, account_type="SAVINGS", open_days_ago=None):
        r = self.rng
        onboard = pd.Timestamp(cust["ONBOARD_DATE"])
        if open_days_ago is None:
            max_back = max(1, (self.end_ts.normalize() - onboard).days)
            open_date = self.end_ts.normalize() - pd.Timedelta(days=int(r.integers(0, max_back + 1)))
        else:
            open_date = self.end_ts.normalize() - pd.Timedelta(days=int(open_days_ago))
        aid = self._aid()
        first, last = cust["FULL_NAME"].lower().split(" ", 1)
        a = dict(ACCOUNT_ID=aid, CUSTOMER_ID=cust["CUSTOMER_ID"], ACCOUNT_TYPE=account_type,
                 OPEN_DATE=open_date.date(), STATUS="ACTIVE", BRANCH_ID=cust["BRANCH_ID"],
                 REGION=cust["REGION"], CITY=cust["CITY"],
                 VPA=f"{first}.{last}{aid[-4:]}@arcadia", IFSC=f"ARCD0{zlib.crc32(cust['BRANCH_ID'].encode()) % 999999:06d}")
        self.accounts.append(a)
        return a

    def txn(self, ts, channel, amount, src, dst, remarks="", device=None, city=None, vpa=None):
        if ts > self.end_ts:
            ts = self.end_ts - pd.Timedelta(seconds=int(self.rng.integers(1, 900)))
        t = dict(TXN_ID=self._tid(), TXN_TS=ts, CHANNEL=channel, AMOUNT=round(float(amount), 2),
                 SRC_ACCOUNT_ID=src, DST_ACCOUNT_ID=dst, COUNTERPARTY_VPA=vpa, REMARKS=remarks,
                 STATUS="SUCCESS", GEO_CITY=city, DEVICE_ID=device)
        self.txns.append(t)
        if channel in ("NEFT", "IMPS", "RTGS") and amount >= 50_000 and str(src).startswith("AC"):
            self.payment_msgs.append({
                "msg_id": f"MSG-{t['TXN_ID']}", "txn_id": t["TXN_ID"], "msg_type": channel,
                "value_ts": str(ts), "amount": {"value": round(float(amount), 2), "ccy": "INR"},
                "remitter": {"account": src, "bank": "Arcadia Bank"},
                "beneficiary": {"account": dst, "bank": "Arcadia Bank" if str(dst).startswith("AC")
                                else self.rng.choice(BANKS)},
                "purpose_code": "P0103" if "INV" in remarks.upper() else "P1006",
                "remittance_info": remarks})
        return t

    def device_event(self, account_id, device_id, ip, city, ts, rooted=False, event_type="LOGIN"):
        self.device_events.append({
            "event_id": str(uuid.UUID(int=int(self.rng.integers(0, 2**63)))),
            "account_id": account_id, "event_type": event_type, "ts": str(ts),
            "device": {"id": device_id, "os": self.rng.choice(["android", "ios"], p=[.85, .15]),
                       "model": self.rng.choice(["RX-11", "Nova 5", "Pixelate 7", "Galaxy-ish A3", "iPhone-ish 13"]),
                       "rooted": bool(rooted)},
            "network": {"ip": ip, "isp": self.rng.choice(["Jiofy", "Airwave", "BSNet", "ActFiber"]),
                        "geo": {"city": city}}})

    def _ip(self):
        r = self.rng
        return f"{r.integers(10, 223)}.{r.integers(0, 255)}.{r.integers(0, 255)}.{r.integers(1, 254)}"

    # ------------------------------------------------------------ population
    def build_population(self, n_customers: int, activity_scale: float = 0.35):
        r = self.rng
        for _ in range(n_customers):
            occ = r.choice(OCC_NAMES, p=OCC_P)
            seg = "SME" if occ == "Business owner" else ("PRIORITY" if r.random() < 0.05 else "RETAIL")
            c = self.new_customer(occupation=occ, segment=seg)
            self.new_account(c, "CURRENT" if occ == "Business owner" else "SAVINGS")
            if r.random() < 0.15:
                self.new_account(c, "WALLET")
        self._normal_activity(activity_scale)
        self._devices_normal()
        self._kyc_normal()
        self._decoy_bc_agents(max(8, n_customers // 400))
        self._decoy_windfalls(max(10, n_customers // 300))

    def _normal_activity(self, scale: float):
        r = self.rng
        acc = pd.DataFrame(self.accounts)
        cus = pd.DataFrame(self.customers).set_index("CUSTOMER_ID")
        acc["OCC"] = acc["CUSTOMER_ID"].map(cus["OCCUPATION_DECLARED"])
        acc["INCOME"] = acc["CUSTOMER_ID"].map(cus["ANNUAL_INCOME_DECLARED"])
        acc["OPEN_TS"] = pd.to_datetime(acc["OPEN_DATE"])
        lam = acc["OCC"].map(lambda o: OCCUPATIONS[o][3]).astype(float) * scale
        lam = np.where(acc["ACCOUNT_TYPE"] == "WALLET", 0.5 * scale, lam)
        active_from = np.maximum(acc["OPEN_TS"].values, np.datetime64(self.start_ts))
        active_days = np.maximum((np.datetime64(self.end_ts) - active_from) / np.timedelta64(1, "D"), 0)
        counts = r.poisson(lam * active_days)
        idx = np.repeat(np.arange(len(acc)), counts)
        n = len(idx)
        if n == 0:
            return
        offs = r.random(n) * np.repeat(active_days, counts)
        day = np.repeat(active_from, counts) + (offs * 86400).astype("timedelta64[s]")
        day = pd.to_datetime(day).normalize()
        hours = r.choice(24, size=n, p=HOUR_P)
        ts = day + pd.to_timedelta(hours, unit="h") + pd.to_timedelta(r.integers(0, 3600, n), unit="s")
        ts = ts.where(ts < self.end_ts, self.end_ts - pd.Timedelta(minutes=5))
        kinds = np.array(["MERCHANT", "P2P_OUT", "P2P_IN", "BILL", "ATM", "NEFT_OUT", "CASH_DEP"])
        kind = r.choice(kinds, size=n, p=[.45, .12, .12, .10, .08, .10, .03])
        aid = acc["ACCOUNT_ID"].values[idx]
        all_acc = acc["ACCOUNT_ID"].values
        amt = np.zeros(n)
        src = np.empty(n, dtype=object)
        dst = np.empty(n, dtype=object)
        chan = np.empty(n, dtype=object)
        rem = np.empty(n, dtype=object)
        m = kind == "MERCHANT"
        amt[m] = np.clip(r.lognormal(np.log(600), 1.0, m.sum()), 10, 50_000)
        src[m], dst[m], chan[m] = aid[m], [f"EXT-MER-{x}" for x in r.integers(1, 4000, m.sum())], "UPI"
        rem[m] = "UPI purchase"
        m = kind == "P2P_OUT"
        amt[m] = np.clip(r.lognormal(np.log(2000), 1.0, m.sum()), 50, 100_000)
        src[m], dst[m] = aid[m], r.choice(all_acc, m.sum())
        chan[m] = r.choice(["UPI", "IMPS"], m.sum(), p=[.85, .15])
        rem[m] = r.choice(["dinner split", "rent share", "gift", "family", "trip", "groceries"], m.sum())
        m = kind == "P2P_IN"
        amt[m] = np.clip(r.lognormal(np.log(2500), 1.0, m.sum()), 50, 150_000)
        src[m], dst[m], chan[m] = [f"EXT-ACC-{x}" for x in r.integers(1, 60000, m.sum())], aid[m], "UPI"
        rem[m] = r.choice(["family", "repayment", "gift", "shared expense", ""], m.sum())
        m = kind == "BILL"
        amt[m] = np.clip(r.lognormal(np.log(1500), 0.6, m.sum()), 100, 30_000)
        src[m], dst[m] = aid[m], [f"EXT-BILLER-{x}" for x in r.integers(1, 300, m.sum())]
        chan[m], rem[m] = "UPI", r.choice(["electricity", "mobile recharge", "broadband", "insurance"], m.sum())
        m = kind == "ATM"
        amt[m] = r.choice(np.arange(500, 10_500, 500), m.sum())
        src[m], dst[m], chan[m], rem[m] = aid[m], "CASH", "ATM", "ATM withdrawal"
        m = kind == "NEFT_OUT"
        amt[m] = np.clip(r.lognormal(np.log(15000), 1.0, m.sum()), 1000, 500_000)
        src[m], dst[m] = aid[m], [f"EXT-ACC-{x}" for x in r.integers(1, 60000, m.sum())]
        chan[m] = r.choice(["NEFT", "IMPS"], m.sum())
        rem[m] = r.choice(["RENT", "EMI", "SCHOOL FEES", "INV-", "family", "insurance premium"], m.sum())
        m = kind == "CASH_DEP"
        amt[m] = np.clip(r.lognormal(np.log(8000), 1.0, m.sum()), 500, 200_000)
        src[m], dst[m], chan[m], rem[m] = "CASH", aid[m], "CASH_DEPOSIT", "cash deposit"
        rem = np.array([x + str(r.integers(1000, 9999)) if x == "INV-" else x for x in rem], dtype=object)
        df = pd.DataFrame(dict(TXN_TS=ts, CHANNEL=chan, AMOUNT=np.round(amt, 2), SRC_ACCOUNT_ID=src,
                               DST_ACCOUNT_ID=dst, REMARKS=rem))
        df["GEO_CITY"] = acc["CITY"].values[idx]
        df["DEVICE_ID"] = [f"DEV-{a}-1" for a in aid]
        df["COUNTERPARTY_VPA"] = None
        df["STATUS"] = "SUCCESS"
        base = self._t
        df["TXN_ID"] = [f"T{self.id_prefix}{base + i + 1:011d}" for i in range(n)]
        self._t += n
        # payment messages for large outward NEFT/IMPS
        big = df[(df.CHANNEL.isin(["NEFT", "IMPS"])) & (df.AMOUNT >= 50_000) & df.SRC_ACCOUNT_ID.str.startswith("AC")]
        for row in big.itertuples(index=False):
            self.payment_msgs.append({
                "msg_id": f"MSG-{row.TXN_ID}", "txn_id": row.TXN_ID, "msg_type": row.CHANNEL,
                "value_ts": str(row.TXN_TS), "amount": {"value": row.AMOUNT, "ccy": "INR"},
                "remitter": {"account": row.SRC_ACCOUNT_ID, "bank": "Arcadia Bank"},
                "beneficiary": {"account": row.DST_ACCOUNT_ID, "bank": str(r.choice(BANKS))},
                "purpose_code": "P0103" if "INV" in str(row.REMARKS) else "P1006",
                "remittance_info": row.REMARKS})
        self._bulk = [df]
        self._salary_and_merchants(acc)

    def _salary_and_merchants(self, acc):
        r = self.rng
        months = pd.date_range(self.start_ts.normalize(), self.end_ts, freq="MS")
        n_kirana = 0
        for a in acc.itertuples(index=False):
            if a.OCC in ("Salaried", "Retired") and a.ACCOUNT_TYPE == "SAVINGS":
                emp = f"EXT-EMP-{r.integers(1, 800)}"
                for mth in months:
                    if mth >= a.OPEN_TS:
                        self.txn(mth + pd.Timedelta(hours=int(r.integers(9, 12))), "NEFT",
                                 a.INCOME / 12 * r.uniform(.85, 1.0), emp, a.ACCOUNT_ID,
                                 "SALARY" if a.OCC == "Salaried" else "PENSION")
            elif a.OCC == "Gig worker" and a.ACCOUNT_TYPE == "SAVINGS":
                for wk in pd.date_range(self.start_ts, self.end_ts, freq="7D"):
                    if wk >= a.OPEN_TS and wk < self.end_ts:
                        self.txn(wk + pd.Timedelta(hours=int(r.integers(10, 20))), "IMPS",
                                 a.INCOME / 52 * r.uniform(.6, 1.3), f"EXT-PLATFORM-{r.integers(1, 6)}",
                                 a.ACCOUNT_ID, "weekly payout")
            elif a.OCC == "Business owner" and a.ACCOUNT_TYPE == "CURRENT" and n_kirana < max(50, len(acc) // 70):
                n_kirana += 1
                # DECOY: many small UPI collections from walk-in customers, weekly supplier payments
                n_days = int(min(self.days, (self.end_ts - max(a.OPEN_TS, self.start_ts)).days))
                for d in range(n_days):
                    day = self.end_ts.normalize() - pd.Timedelta(days=d)
                    for _ in range(int(r.poisson(4))):
                        self.txn(self._ts_on(day, 8, 22), "UPI", np.clip(r.lognormal(np.log(350), .9), 10, 8000),
                                 f"EXT-ACC-{r.integers(1, 60000)}", a.ACCOUNT_ID, "UPI collection")
                    if d % 7 == 3:
                        self.txn(self._ts_on(day, 11, 17), "NEFT", r.uniform(8_000, 60_000), a.ACCOUNT_ID,
                                 f"EXT-SUPPLIER-{r.integers(1, 500)}", f"INV-{r.integers(1000, 9999)}")

    def _decoy_bc_agents(self, n):
        """HARD NEGATIVES: business-correspondent / money-transfer outlets. Legitimately receive many
        payments from walk-in customers and withdraw cash the same day - the textbook mule pattern."""
        r = self.rng
        for _ in range(n):
            c = self.new_customer(occupation="Self-employed", onboard_days_ago=int(r.integers(300, 2500)),
                                  channel="BRANCH", income=round(r.uniform(600_000, 2_000_000), -3), segment="SME")
            a = self.new_account(c, "CURRENT", open_days_ago=int(r.integers(250, 2000)))
            self._note(c, f"Customer {c['FULL_NAME']} operates a licensed business-correspondent and money-transfer "
                          f"outlet in {c['CITY']}. High volume of small customer remittances and same-day cash "
                          f"settlement is expected. Agreement with the corporate BC sighted.")
            self.device_event(a["ACCOUNT_ID"], f"DEV-{a['ACCOUNT_ID']}-1", self._ip(), c["CITY"],
                              self.end_ts - pd.Timedelta(days=int(r.integers(1, 20))))
            for d in range(min(self.days, 45)):
                day = self.end_ts.normalize() - pd.Timedelta(days=d)
                tot = 0.0
                for _ in range(int(r.integers(4, 14))):
                    amt = float(r.uniform(2_000, 25_000))
                    self.txn(self._ts_on(day, 9, 19), "UPI", amt, f"EXT-ACC-{r.integers(1, 10**6)}",
                             a["ACCOUNT_ID"], "remittance")
                    tot += amt
                if r.random() < .8:
                    self.txn(self._ts_on(day, 18, 20), "CASH_WITHDRAWAL", tot * r.uniform(.5, .9), a["ACCOUNT_ID"],
                             "CASH", "branch cash settlement")
                else:
                    self.txn(self._ts_on(day, 18, 20), "NEFT", tot * r.uniform(.8, .95), a["ACCOUNT_ID"],
                             f"EXT-CORP-{r.integers(1, 20)}", "BC settlement")

    def _decoy_windfalls(self, n):
        """HARD NEGATIVES: new accounts receiving large legitimate one-off sums (property sale, bonus)
        and moving them on quickly (FD, home loan prepayment)."""
        r = self.rng
        for _ in range(n):
            c = self.new_customer(occupation=r.choice(["Salaried", "Retired", "Business owner"]),
                                  onboard_days_ago=int(r.integers(20, 80)), channel="BRANCH")
            a = self.new_account(c, "SAVINGS", open_days_ago=int(r.integers(15, 75)))
            self.device_event(a["ACCOUNT_ID"], f"DEV-{a['ACCOUNT_ID']}-1", self._ip(), c["CITY"],
                              self.end_ts - pd.Timedelta(days=int(r.integers(1, 10))))
            day = self.end_ts.normalize() - pd.Timedelta(days=int(r.integers(1, 25)))
            amt = float(r.uniform(800_000, 4_000_000))
            self.txn(self._ts_on(day, 10, 15), "RTGS", amt, f"EXT-ACC-{r.integers(1, 60000)}", a["ACCOUNT_ID"],
                     str(r.choice(["SALE PROCEEDS FLAT", "ANNUAL BONUS", "MATURITY PROCEEDS"])))
            self.txn(self._ts_on(day, 15, 18), "NEFT", amt * r.uniform(.6, .95), a["ACCOUNT_ID"],
                     f"EXT-ACC-{r.integers(1, 60000)}", str(r.choice(["HOME LOAN PREPAYMENT", "FD BOOKING", "family"])))

    def _devices_normal(self):
        r = self.rng
        accs = self.accounts
        for a in accs:
            for k in range(int(r.integers(2, 6))):
                dev = f"DEV-{a['ACCOUNT_ID']}-{1 if r.random() < .8 else 2}"
                ts = self.start_ts + pd.Timedelta(seconds=int(r.integers(0, self.days * 86400)))
                self.device_event(a["ACCOUNT_ID"], dev, self._ip(), a["CITY"], ts, rooted=r.random() < .03)
        # DECOY: families sharing one phone (2-5 accounts per device)
        for _ in range(int(len(accs) * 0.03)):
            k = int(r.choice([2, 2, 2, 3, 4, 5]))
            dev = f"DEV-FAM-{r.integers(1, 10**9)}"
            for i in r.choice(len(accs), k, replace=False):
                a = accs[i]
                self.device_event(a["ACCOUNT_ID"], dev, self._ip(), a["CITY"],
                                  self.start_ts + pd.Timedelta(seconds=int(r.integers(0, self.days * 86400))))

    def _kyc_normal(self):
        r = self.rng
        for c in self.customers:
            occ = c["OCCUPATION_DECLARED"]
            if occ != "Business owner" and r.random() > 0.15:
                continue
            monthly = int(c["ANNUAL_INCOME_DECLARED"] / 12)
            if occ == "Business owner":
                text = (f"Customer {c['FULL_NAME']} runs {r.choice(BIZ)} in {c['CITY']} for {r.integers(2, 20)} years. "
                        f"Declared annual turnover about Rs {int(c['ANNUAL_INCOME_DECLARED'] * r.uniform(2, 4)):,}. "
                        f"Receives UPI collections from walk-in customers and pays suppliers by NEFT. "
                        f"GST registration sighted. Profile consistent with activity.")
            elif occ == "Student":
                text = (f"Student at {r.choice(COLLEGES)}, {c['CITY']}. Account funded by parents; expected credits "
                        f"below Rs {r.choice([5000, 10000, 15000]):,} per month. No concerns noted.")
            elif occ in ("Salaried", "Retired"):
                text = (f"Periodic KYC review for {c['FULL_NAME']}. Customer confirms "
                        f"{'employment with ' + str(r.choice(EMPLOYERS)) if occ == 'Salaried' else 'pension income'}, "
                        f"monthly take-home approx Rs {monthly:,}. Source of funds: "
                        f"{'salary' if occ == 'Salaried' else 'pension'} credits. Transactions in line with profile.")
            else:
                text = (f"Customer {c['FULL_NAME']}, {occ.lower()} based in {c['CITY']}. States monthly income of "
                        f"around Rs {monthly:,}. Source of funds explained satisfactorily. No adverse observations.")
            self._note(c, text)

    def _note(self, c, text, days_after_onboard=None):
        r = self.rng
        self._n += 1
        d = pd.Timestamp(c["ONBOARD_DATE"]) + pd.Timedelta(days=int(days_after_onboard if days_after_onboard
                                                                   is not None else r.integers(0, 30)))
        self.kyc_notes.append(dict(NOTE_ID=f"KN{self.id_prefix}{self._n:07d}", CUSTOMER_ID=c["CUSTOMER_ID"],
                                   NOTE_DATE=min(d, self.end_ts).date(),
                                   AUTHOR=f"KYC-OFFICER-{r.integers(1, 40):02d}", NOTE_TEXT=text))

    # ------------------------------------------------------------ crime
    def _truth(self, acc_id, ring_key, typ, role):
        self.truth.append(dict(ACCOUNT_ID=acc_id, IS_MULE=True, RING_KEY=ring_key, TYPOLOGY=typ, ROLE=role))

    def _mule_person(self, city, open_days, aged=False):
        r = self.rng
        if aged:  # bought / rented aged account - looks like an ordinary customer
            occ = r.choice(["Salaried", "Self-employed", "Gig worker"], p=[.4, .3, .3])
            open_days = int(r.integers(150, 900))
            channel = r.choice(["BRANCH", "VIDEO_KYC", "DIGITAL"])
        else:
            occ = r.choice(["Student", "Homemaker", "Gig worker", "Farmer"], p=[.4, .2, .3, .1])
            channel = r.choice(["VIDEO_KYC", "DIGITAL"])
        lo, hi = OCCUPATIONS[occ][1], OCCUPATIONS[occ][2]
        c = self.new_customer(occupation=occ, onboard_days_ago=open_days + int(r.integers(0, 3)), city=city,
                              channel=channel, income=round(r.uniform(lo, hi * (.5 if aged else 1)), -3))
        a = self.new_account(c, "SAVINGS", open_days_ago=open_days)
        return c, a

    def _mule_note(self, c):
        r = self.rng
        inc = int(c["ANNUAL_INCOME_DECLARED"] / 12)
        occ = c["OCCUPATION_DECLARED"].lower()
        templates = [
            f"Account opened via video KYC. Customer states occupation as {occ} and monthly income about Rs {inc:,}. "
            f"During the call another person was heard prompting answers. Customer could not explain the purpose "
            f"of the account beyond 'receiving payments from friends'.",
            f"Customer, a {occ}, says the account is for 'part-time online work'. Could not name an employer. "
            f"Expected credits stated as below Rs {max(inc, 5000):,} per month. Mobile number was recently ported.",
            f"New-to-bank customer ({occ}). Declares income of Rs {max(inc, 3000):,} per month from family support. "
            f"Address proof is a rented PG accommodation; customer appeared unsure of own address.",
            f"Customer requested immediate activation of UPI and an increase in IMPS limits on the day of opening. "
            f"States occupation {occ} with no regular income. Mentioned being referred by an 'agent' whom the "
            f"customer did not name.",
            # bland notes - not every mule is obvious from KYC
            f"Customer {c['FULL_NAME']}, {occ}, {c['CITY']}. Monthly income approx Rs {max(inc, 3000):,}. "
            f"Documents verified. No adverse observations at onboarding.",
        ]
        self._note(c, str(r.choice(templates, p=[.2, .2, .15, .15, .3])), days_after_onboard=0)

    def ring_layering(self, ring_key, window_days=28, now_mode=False):
        """Victims -> collectors -> layers -> cash-out (ATM / crypto)."""
        r = self.rng
        city = r.choice(CITY_LIST, p=CITY_W)
        k1, k2, k3 = int(r.integers(3, 8)), int(r.integers(2, 4)), int(r.integers(1, 3))
        members = {"COLLECTOR": [], "LAYER": [], "CASH_OUT": []}
        # ~35% of rings are "low and slow": aged accounts, own phones, smaller amounts, multi-day holds
        stealth = (not now_mode) and r.random() < .35
        devices = [f"DEV-RING-{uuid.UUID(int=int(r.integers(0, 2**63))).hex[-10:]}" for _ in range(int(r.integers(1, 3)))]
        ips = [self._ip() for _ in range(2)]
        for role, k in (("COLLECTOR", k1), ("LAYER", k2), ("CASH_OUT", k3)):
            for _ in range(k):
                aged = r.random() < (.6 if stealth else .15)
                c, a = self._mule_person(city if r.random() < .7 else r.choice(CITY_LIST),
                                         open_days=int(r.integers(12, 85)) if not now_mode else int(r.integers(5, 40)),
                                         aged=aged)
                members[role].append(a)
                self._truth(a["ACCOUNT_ID"], ring_key, "MULE_LAYERING", role)
                self._mule_note(c)
                for _ in range(int(r.integers(2, 6))):
                    ts = self.end_ts - pd.Timedelta(minutes=int(r.integers(10, 60 * 24 * 25)))
                    dev = r.choice(devices) if r.random() < (.3 if stealth else .8) else f"DEV-{a['ACCOUNT_ID']}-1"
                    self.device_event(a["ACCOUNT_ID"], dev, r.choice(ips), city, ts, rooted=r.random() < .3)
                # light camouflage activity
                for _ in range(int(r.integers(0, 6))):
                    day = self.end_ts - pd.Timedelta(days=int(r.integers(0, 20)))
                    self.txn(self._ts_on(day), "UPI", r.uniform(50, 900), a["ACCOUNT_ID"],
                             f"EXT-MER-{r.integers(1, 4000)}", "UPI purchase", device=f"DEV-{a['ACCOUNT_ID']}-1")
        if now_mode:
            attack_days = [self.end_ts - pd.Timedelta(hours=3)]
        else:
            attack_days = [self.end_ts.normalize() - pd.Timedelta(days=int(d))
                           for d in r.choice(np.arange(0, window_days), int(r.integers(4, 10)), replace=False)]
        use_vda = r.random() < .5
        for day in attack_days:
            credits = {a["ACCOUNT_ID"]: [] for a in members["COLLECTOR"]}
            for _ in range(int(r.integers(2, 6)) if stealth else int(r.integers(3, 12))):
                col = self._pick(members["COLLECTOR"])
                ts = (day + pd.Timedelta(minutes=int(r.integers(0, 120)))) if now_mode else self._ts_on(day, 9, 22)
                amt = float(np.clip(r.lognormal(np.log(15_000 if stealth else 40_000), .7), 2_000, 300_000))
                vic = f"EXT-ACC-{r.integers(100000, 9999999)}"  # victims look like any other remitter
                t = self.txn(ts, r.choice(["UPI", "IMPS"], p=[.7, .3]), amt, vic, col["ACCOUNT_ID"], "",
                             vpa=col["VPA"])
                credits[col["ACCOUNT_ID"]].append((ts, amt))
                if r.random() < (.1 if stealth else .25):
                    self._complaint(t, col)
            layer_in = {a["ACCOUNT_ID"]: [] for a in members["LAYER"]}
            for col_id, lst in credits.items():
                if not lst:
                    continue
                last_ts = max(x[0] for x in lst)
                total = sum(x[1] for x in lst) * r.uniform(.92, .98)
                for part in self._split(total, int(r.integers(1, 3))):
                    lay = self._pick(members["LAYER"])
                    hold = int(r.integers(720, 3600)) if stealth else int(r.integers(10, 240))
                    ts = last_ts + pd.Timedelta(minutes=hold)
                    self.txn(ts, "IMPS", part, col_id, lay["ACCOUNT_ID"],
                             str(r.choice(["personal", "help", "loan return", "family support"])),
                             device=r.choice(devices))
                    layer_in[lay["ACCOUNT_ID"]].append((ts, part))
            for lay_id, lst in layer_in.items():
                if not lst:
                    continue
                last_ts = max(x[0] for x in lst)
                total = sum(x[1] for x in lst) * r.uniform(.95, .99)
                if use_vda and r.random() < .4:
                    self.txn(last_ts + pd.Timedelta(minutes=int(r.integers(20, 200))), "IMPS", total, lay_id,
                             f"EXT-VDA-EXCH-{r.integers(1, 4)}", "wallet topup", device=r.choice(devices))
                    continue
                co = self._pick(members["CASH_OUT"])
                ts = last_ts + pd.Timedelta(minutes=int(r.integers(720, 2880)) if stealth else int(r.integers(30, 360)))
                self.txn(ts, "IMPS", total, lay_id, co["ACCOUNT_ID"], "personal", device=r.choice(devices))
                cash = total * (r.uniform(.25, .55) if stealth else r.uniform(.6, .9))
                n_atm = int(cash // 10_000)
                for i in range(min(n_atm, 25)):
                    self.txn(ts + pd.Timedelta(minutes=30 + 7 * i), "ATM", 10_000, co["ACCOUNT_ID"], "CASH",
                             "ATM withdrawal", city=r.choice(CITY_LIST))
                rest = total - min(n_atm, 25) * 10_000
                if rest > 1000:
                    dst = f"EXT-VDA-EXCH-{r.integers(1, 4)}" if use_vda else f"EXT-ACC-{r.integers(1, 60000)}"
                    self.txn(ts + pd.Timedelta(hours=4), "IMPS", rest, co["ACCOUNT_ID"], dst, "personal")
        return members

    def _pick(self, seq):
        return seq[int(self.rng.integers(0, len(seq)))]

    def _split(self, total, k):
        w = self.rng.dirichlet(np.ones(k))
        return [total * x for x in w]

    def _complaint(self, t, col):
        r = self.rng
        scam = r.choice(SCAM_TYPES)
        amt = f"Rs {t['AMOUNT']:,.0f}"
        vpa = col["VPA"]
        texts = {
            SCAM_TYPES[0]: f"I was added to a WhatsApp group promising stock market tips. Invested {amt} in a "
                           f"trading app; when I tried to withdraw they asked for more 'tax'. Money sent via UPI to {vpa}.",
            SCAM_TYPES[1]: f"Received a video call from people posing as police officers saying a parcel in my name "
                           f"had drugs. They kept me on call for hours and made me transfer {amt} to {vpa} for 'verification'.",
            SCAM_TYPES[2]: f"Was offered a part-time job rating hotels online. After two small payouts I was asked to "
                           f"pay {amt} to unlock premium tasks. Paid to {vpa}; now they do not respond.",
            SCAM_TYPES[3]: f"An instant loan app threatened to send morphed photos to my contacts unless I paid. "
                           f"I paid {amt} to {vpa}.",
            SCAM_TYPES[4]: f"Got an SMS that my bank KYC had expired, clicked the link and shared the OTP. {amt} was "
                           f"debited and transferred to {vpa}.",
        }
        self.complaints.append(dict(
            COMPLAINT_ID=f"NCRP{self.id_prefix}{len(self.complaints) + 1:08d}",
            REPORTED_TS=t["TXN_TS"] + pd.Timedelta(hours=float(r.uniform(.5, 48))),
            CHANNEL="NCRP-1930", VICTIM_BANK=str(r.choice(BANKS)),
            VICTIM_STATE=CITIES[r.choice(CITY_LIST)][0], BENEFICIARY_ACCOUNT_ID=t["DST_ACCOUNT_ID"],
            TXN_ID=t["TXN_ID"], AMOUNT=t["AMOUNT"], COMPLAINT_TEXT=texts[scam]))
        if self.complaints[-1]["REPORTED_TS"] > self.end_ts:
            self.complaints[-1]["REPORTED_TS"] = self.end_ts

    def ring_structuring(self, ring_key):
        r = self.rng
        city = r.choice(CITY_LIST, p=CITY_W)
        k = int(r.integers(3, 7))
        deps = []
        for _ in range(k):
            occ = r.choice(["Self-employed", "Salaried", "Homemaker"])
            c = self.new_customer(occupation=occ, onboard_days_ago=int(r.integers(200, 2000)), city=city)
            a = self.new_account(c, "SAVINGS", open_days_ago=int(r.integers(150, 1500)))
            deps.append(a)
            self._truth(a["ACCOUNT_ID"], ring_key, "STRUCTURING", "DEPOSITOR")
            if r.random() < .6:
                self._note(c, f"Customer {c['FULL_NAME']}, {occ.lower()}. Explains frequent cash deposits as "
                              f"collections from relatives. Unable to provide supporting documents. Advised on PAN "
                              f"requirement for cash deposits of Rs 50,000 and above.")
        cc = self.new_customer(occupation="Business owner", onboard_days_ago=int(r.integers(300, 2000)), city=city)
        cons = self.new_account(cc, "CURRENT", open_days_ago=int(r.integers(250, 1500)))
        self._truth(cons["ACCOUNT_ID"], ring_key, "STRUCTURING", "CONSOLIDATOR")
        shared = f"DEV-RING-{uuid.UUID(int=int(r.integers(0, 2**63))).hex[-10:]}"
        for a in deps + [cons]:
            self.device_event(a["ACCOUNT_ID"], shared if r.random() < .5 else f"DEV-{a['ACCOUNT_ID']}-1",
                              self._ip(), city, self.end_ts - pd.Timedelta(days=int(r.integers(1, 25))))
        total_cons = 0.0
        for a in deps:
            dep_total = 0.0
            for _ in range(int(r.integers(3, 9))):
                day = self.end_ts.normalize() - pd.Timedelta(days=int(r.integers(1, 29)))
                amt = float(r.uniform(45_000, PAN_CASH_LIMIT - 100))
                self.txn(self._ts_on(day, 10, 16), "CASH_DEPOSIT", amt, "CASH", a["ACCOUNT_ID"], "cash deposit",
                         city=r.choice(CITY_LIST))
                dep_total += amt
            ts = self.end_ts - pd.Timedelta(days=float(r.uniform(.2, 3)))
            self.txn(ts, "NEFT", dep_total * r.uniform(.9, 1.0), a["ACCOUNT_ID"], cons["ACCOUNT_ID"],
                     str(r.choice(["family support", "personal", "business advance"])))
            total_cons += dep_total
        ts = self.end_ts - pd.Timedelta(hours=float(r.uniform(1, 30)))
        shell = f"EXT-CORP-{r.integers(1, 5000)}"
        for part in self._split(total_cons * .95, int(r.integers(2, 4))):
            self.txn(ts, "NEFT", part, cons["ACCOUNT_ID"], shell, "advance for goods")
            ts += pd.Timedelta(minutes=int(r.integers(5, 90)))

    def ring_round_trip(self, ring_key):
        r = self.rng
        city = r.choice(CITY_LIST, p=CITY_W)
        k = int(r.integers(3, 5))
        accs = []
        for i in range(k):
            c = self.new_customer(occupation="Business owner", onboard_days_ago=int(r.integers(400, 2500)),
                                  city=city, segment="SME")
            a = self.new_account(c, "CURRENT", open_days_ago=int(r.integers(300, 2000)))
            accs.append(a)
            self._truth(a["ACCOUNT_ID"], ring_key, "ROUND_TRIPPING", "CIRCULAR_PARTY")
            if i == 0:
                self._note(c, f"Director of {c['FULL_NAME'].split()[1]} Enterprises. Major counterparties are group "
                              f"concerns with common directors. Invoices provided were generic and lacked "
                              f"delivery evidence.")
            self.device_event(a["ACCOUNT_ID"], f"DEV-{a['ACCOUNT_ID']}-1", self._ip(), city,
                              self.end_ts - pd.Timedelta(days=int(r.integers(1, 25))))
        for _ in range(int(r.integers(3, 7))):
            amt = float(r.uniform(500_000, 2_500_000))
            ts = self.end_ts - pd.Timedelta(days=float(r.uniform(1, 28)))
            for i in range(k):
                src, dst = accs[i], accs[(i + 1) % k]
                ts = ts + pd.Timedelta(hours=float(r.uniform(2, 30)))
                ts = min(ts, self.end_ts - pd.Timedelta(minutes=1))
                self.txn(ts, r.choice(["NEFT", "RTGS"]), amt, src["ACCOUNT_ID"], dst["ACCOUNT_ID"],
                         f"INV-{r.integers(10000, 99999)}")
                amt *= r.uniform(.97, .995)

    # ------------------------------------------------------------ media & watchlist
    def build_media_and_watchlist(self, n_noise=260):
        r = self.rng
        truth = pd.DataFrame(self.truth)
        cust = pd.DataFrame(self.customers).set_index("CUSTOMER_ID")
        acc = pd.DataFrame(self.accounts).set_index("ACCOUNT_ID")
        art = 0

        def add(headline, body, name, city, days_ago):
            nonlocal art
            art += 1
            self.media.append(dict(ARTICLE_ID=f"ART{self.id_prefix}{art:06d}",
                                   PUBLISHED_DATE=(self.end_ts - pd.Timedelta(days=int(days_ago))).date(),
                                   SOURCE=str(r.choice(SOURCES)), HEADLINE=headline, BODY=body,
                                   MENTIONED_NAME=name, CITY=city))

        if len(truth):
            for rk, g in truth.groupby("RING_KEY"):
                typ = g.TYPOLOGY.iloc[0]
                if r.random() > (.4 if typ == "MULE_LAYERING" else .25):
                    continue
                acc_id = g.ACCOUNT_ID.iloc[int(r.integers(0, len(g)))]
                c = cust.loc[acc.loc[acc_id, "CUSTOMER_ID"]]
                if typ == "MULE_LAYERING":
                    add(f"Cyber police bust mule-account racket in {c.CITY}",
                        f"{c.CITY}: The cyber crime police have arrested {r.integers(2, 6)} persons, including "
                        f"{c.FULL_NAME}, for allegedly renting out bank accounts to fraudsters running fake "
                        f"trading-app and 'digital arrest' scams. Police said the accused received money from victims "
                        f"across several states and withdrew it through ATMs within hours. Further investigation is on.",
                        c.FULL_NAME, c.CITY, r.integers(1, 40))
                else:
                    add(f"Officials probe unexplained cash deposits linked to {c.CITY} trader",
                        f"Authorities are examining a series of cash deposits and onward transfers involving "
                        f"{c.FULL_NAME} of {c.CITY}. Officials suspect deposits were split to avoid reporting. "
                        f"No charges have been filed so far.", c.FULL_NAME, c.CITY, r.integers(1, 60))
        normal_c = cust.sample(min(n_noise, len(cust)), random_state=int(r.integers(0, 1e6)))
        for i, (cid, c) in enumerate(normal_c.iterrows()):
            kind = i % 4
            if kind == 0:
                add(f"{c.CITY} entrepreneur wins state MSME award",
                    f"{c.FULL_NAME}, who started a small business in {c.CITY}, was felicitated at the state MSME "
                    f"awards for creating local jobs.", c.FULL_NAME, c.CITY, r.integers(1, 300))
            elif kind == 1:
                other = r.choice([x for x in CITY_LIST if x != c.CITY])
                # Same name, DIFFERENT city: a false-match trap the AI filter should reject
                add(f"Man held for chain snatching in {other}",
                    f"Police in {other} arrested {c.FULL_NAME}, a resident of {other}, for a series of chain "
                    f"snatching incidents. The accused has prior cases in {other}.", c.FULL_NAME, other,
                    r.integers(1, 300))
            elif kind == 2:
                add(f"Marathon draws record crowd in {c.CITY}",
                    f"Over 20,000 runners took part. {c.FULL_NAME} finished first in the veterans category.",
                    c.FULL_NAME, c.CITY, r.integers(1, 300))
            else:
                add("Banks warn customers about fake KYC update messages",
                    "Lenders have urged customers not to click on links in SMS messages claiming KYC has expired. "
                    "Customers should report fraud on the national helpline 1930.", None, None, r.integers(1, 120))
        # Watchlist: fictional names + a few ring members with spelling variations
        for i in range(400):
            nm, _ = self._name()
            self.watchlist.append(dict(ENTRY_ID=f"WL{i + 1:05d}", NAME=nm,
                                       LIST_TYPE=str(r.choice(["INTERNAL_BLOCKLIST", "LEA_REQUEST", "PEP"], p=[.5, .3, .2])),
                                       COUNTRY="IN", ADDED_DATE=(self.end_ts - pd.Timedelta(days=int(r.integers(1, 900)))).date(),
                                       REMARKS=""))
        if len(truth):
            pick = truth.sample(min(6, len(truth)), random_state=7)
            for j, row in enumerate(pick.itertuples(index=False)):
                nm = cust.loc[acc.loc[row.ACCOUNT_ID, "CUSTOMER_ID"], "FULL_NAME"]
                first, last = nm.split(" ", 1)
                variant = f"{first} {last[:-1] + last[-1].upper()}" if j % 2 else f"{first[:-1]}{first[-1]}h {last}"
                self.watchlist.append(dict(ENTRY_ID=f"WL9{j:04d}", NAME=variant, LIST_TYPE="LEA_REQUEST",
                                           COUNTRY="IN", ADDED_DATE=(self.end_ts - pd.Timedelta(days=5)).date(),
                                           REMARKS="Account flagged by law enforcement - mule suspicion"))

    # ------------------------------------------------------------ outputs
    def frames(self) -> dict:
        txn = pd.concat([*getattr(self, "_bulk", []), pd.DataFrame(self.txns)], ignore_index=True)
        cols = ["TXN_ID", "TXN_TS", "CHANNEL", "AMOUNT", "SRC_ACCOUNT_ID", "DST_ACCOUNT_ID", "COUNTERPARTY_VPA",
                "REMARKS", "STATUS", "GEO_CITY", "DEVICE_ID"]
        txn = txn[cols] if len(txn) else pd.DataFrame(columns=cols)
        if len(txn):
            txn["TXN_TS"] = pd.to_datetime(txn["TXN_TS"]).astype("datetime64[ns]")
        acc_ids = [a["ACCOUNT_ID"] for a in self.accounts]
        truth = pd.DataFrame(self.truth, columns=["ACCOUNT_ID", "IS_MULE", "RING_KEY", "TYPOLOGY", "ROLE"])
        normal = pd.DataFrame({"ACCOUNT_ID": sorted(set(acc_ids) - set(truth.ACCOUNT_ID))})
        normal["IS_MULE"], normal["RING_KEY"], normal["TYPOLOGY"], normal["ROLE"] = False, None, None, None
        gt = pd.concat([truth, normal], ignore_index=True)
        comp = pd.DataFrame(self.complaints, columns=["COMPLAINT_ID", "REPORTED_TS", "CHANNEL", "VICTIM_BANK",
                                                       "VICTIM_STATE", "BENEFICIARY_ACCOUNT_ID", "TXN_ID", "AMOUNT",
                                                       "COMPLAINT_TEXT"])
        if len(comp):
            comp["REPORTED_TS"] = pd.to_datetime(comp["REPORTED_TS"]).astype("datetime64[ns]")
        return dict(customers=pd.DataFrame(self.customers), accounts=pd.DataFrame(self.accounts), transactions=txn,
                    device_events=self.device_events, payment_messages=self.payment_msgs,
                    kyc_notes=pd.DataFrame(self.kyc_notes), adverse_media=pd.DataFrame(self.media),
                    watchlist=pd.DataFrame(self.watchlist), complaints=comp, ground_truth=gt)


def generate_frames(n_customers=20000, n_rings=30, seed=42, days=90, end_ts=None, activity_scale=0.35):
    w = World(seed=seed, end_ts=end_ts, days=days)
    w.build_population(n_customers, activity_scale)
    r = w.rng
    for i in range(n_rings):
        key = f"GT-RING-{i + 1:03d}"
        u = r.random()
        if u < .6:
            w.ring_layering(key)
        elif u < .8:
            w.ring_structuring(key)
        else:
            w.ring_round_trip(key)
    w.build_media_and_watchlist()
    out = w.frames()
    out["historical_labels"] = historical_labels(out["ground_truth"], out["customers"], out["accounts"], seed)
    return out


def historical_labels(gt: pd.DataFrame, customers: pd.DataFrame, accounts: pd.DataFrame, seed=42) -> pd.DataFrame:
    """Simulate outcomes of PAST investigations: half the rings were confirmed, plus reviewed negatives.
    The other half of the rings is held out and only used for evaluation."""
    rng = np.random.default_rng(seed + 1)
    rings = sorted(gt.RING_KEY.dropna().unique())
    train_rings = set(rings[::2])
    pos = gt[gt.RING_KEY.isin(train_rings)][["ACCOUNT_ID"]].assign(LABEL=1, SOURCE="CLOSED_CASE_STR_FILED")
    # real-world label noise: some past mule cases were closed for insufficient evidence
    flip = rng.random(len(pos)) < .10
    pos.loc[flip, "LABEL"] = 0
    pos.loc[flip, "SOURCE"] = "CLOSED_INSUFFICIENT_EVIDENCE"
    neg_pool = gt[~gt.IS_MULE.astype(bool)]
    acc = accounts.merge(customers[["CUSTOMER_ID", "OCCUPATION_DECLARED"]], on="CUSTOMER_ID")
    decoys = acc[(acc.OCCUPATION_DECLARED == "Business owner") & (acc.ACCOUNT_TYPE == "CURRENT")].ACCOUNT_ID
    decoys = decoys[decoys.isin(neg_pool.ACCOUNT_ID)]
    dec = decoys.sample(frac=.5, random_state=int(rng.integers(0, 1e6)))
    rand = neg_pool.ACCOUNT_ID.sample(min(2500, len(neg_pool)), random_state=int(rng.integers(0, 1e6)))
    neg = pd.DataFrame({"ACCOUNT_ID": pd.concat([dec, rand]).drop_duplicates()}).assign(
        LABEL=0, SOURCE="CLOSED_CASE_NO_ACTION")
    out = pd.concat([pos, neg], ignore_index=True)
    out["LABELED_AT"] = pd.Timestamp.utcnow().tz_localize(None).floor("s")
    return out


# =============================================================================
# Stored procedure entry points
# =============================================================================
def _load_all(session, f: dict, overwrite: bool):
    from common import DB, write_df, write_variant
    n = {}
    n["customers"] = write_df(session, f["customers"], f"{DB}.RAW.CUSTOMERS", overwrite)
    n["accounts"] = write_df(session, f["accounts"], f"{DB}.RAW.ACCOUNTS", overwrite)
    n["transactions"] = write_df(session, f["transactions"], f"{DB}.RAW.TRANSACTIONS", overwrite)
    n["device_events"] = write_variant(session, f["device_events"], f"{DB}.RAW.DEVICE_EVENTS", "EVENT",
                                       "INGESTED_AT", overwrite)
    n["payment_messages"] = write_variant(session, f["payment_messages"], f"{DB}.RAW.PAYMENT_MESSAGES", "MSG",
                                          "RECEIVED_AT", overwrite)
    n["kyc_notes"] = write_df(session, f["kyc_notes"], f"{DB}.RAW.KYC_NOTES", overwrite)
    if "adverse_media" in f:
        n["adverse_media"] = write_df(session, f["adverse_media"], f"{DB}.RAW.ADVERSE_MEDIA", overwrite)
    if "watchlist" in f:
        n["watchlist"] = write_df(session, f["watchlist"], f"{DB}.RAW.WATCHLIST", overwrite)
    n["complaints"] = write_df(session, f["complaints"], f"{DB}.RAW.FRAUD_COMPLAINTS", overwrite)
    n["ground_truth"] = write_df(session, f["ground_truth"], f"{DB}.GOV.GROUND_TRUTH", overwrite)
    if "historical_labels" in f:
        n["historical_labels"] = write_df(session, f["historical_labels"], f"{DB}.ANALYTICS.HISTORICAL_LABELS",
                                          overwrite)
    return n


def generate_sp(session, n_customers: int = 20000, n_rings: int = 30, seed: int = 42) -> dict:
    """CALL MULEWATCH.APP.GENERATE_SYNTHETIC_DATA(20000, 30, 42)"""
    import time
    from common import audit
    t0 = time.time()
    f = generate_frames(int(n_customers), int(n_rings), int(seed))
    n = _load_all(session, f, overwrite=True)
    audit(session, "DATA_GENERATOR", "GENERATE", details=n, started=t0)
    return {"status": "ok", "rows": n, "seconds": round(time.time() - t0, 1)}


def simulate_stream_sp(session, n_txn: int = 2000, ring_probability: float = 0.25) -> dict:
    """Append a few minutes of 'live' traffic; sometimes a brand-new mule ring strikes.
    CALL MULEWATCH.APP.SIMULATE_STREAM(2000, 1.0)  -- force a new ring for the demo"""
    import time
    from common import DB, audit
    t0 = time.time()
    now = pd.Timestamp.utcnow().tz_localize(None).floor("s")
    prefix = "S" + now.strftime("%m%d%H%M%S")
    w = World(seed=int(time.time()) % 10**6, end_ts=now, days=1, id_prefix=prefix)
    acc = session.sql(f"""SELECT A.ACCOUNT_ID, A.CITY FROM {DB}.RAW.ACCOUNTS A
                          SAMPLE (3000 ROWS)""").to_pandas()
    r = w.rng
    if len(acc):
        for _ in range(int(n_txn)):
            a = acc.iloc[int(r.integers(0, len(acc)))]
            ts = now - pd.Timedelta(seconds=int(r.integers(0, 300)))
            k = r.random()
            if k < .6:
                w.txn(ts, "UPI", np.clip(r.lognormal(np.log(600), 1), 10, 50_000), a.ACCOUNT_ID,
                      f"EXT-MER-{r.integers(1, 4000)}", "UPI purchase", city=a.CITY)
            elif k < .85:
                w.txn(ts, "UPI", np.clip(r.lognormal(np.log(2500), 1), 50, 100_000), f"EXT-ACC-{r.integers(1, 60000)}",
                      a.ACCOUNT_ID, "family", city=a.CITY)
            else:
                w.txn(ts, "ATM", float(r.choice(np.arange(500, 10_500, 500))), a.ACCOUNT_ID, "CASH", "ATM withdrawal",
                      city=a.CITY)
    injected = None
    if r.random() < float(ring_probability):
        injected = f"GT-LIVE-{prefix}"
        w.ring_layering(injected, now_mode=True)
    f = w.frames()
    n = _load_all(session, f, overwrite=False)
    audit(session, "STREAM_SIMULATOR", "APPEND", details={"rows": n, "injected_ring": injected}, started=t0)
    return {"status": "ok", "rows": n, "injected_ring": injected}
