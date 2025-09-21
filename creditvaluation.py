import sys
from itertools import combinations
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel,
    QLineEdit, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QCheckBox
)
from PyQt6.QtGui import QDoubleValidator, QFont, QColor
from PyQt6.QtCore import Qt
import json, os
import time

# Performance tuning: limit how many cards to consider in exhaustive search
# and the maximum portfolio size to enumerate. Adjust these to trade accuracy
# for speed. Set to `None` to disable prefiltering.
MAX_CARDS_TO_CONSIDER = 16
MAX_PORTFOLIO_SIZE = 4


# -------------------------------
# Credit card dataset
# -------------------------------

def load_credit_cards(filename="credit_cards.txt"):
    base_path = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(base_path, filename)
    with open(filepath, "r") as f:
        return json.load(f)

categories = ["groceries","dining","flights_portal","hotel","gas","online_shopping","other"]

monthly_spending_defaults = {
    'groceries': 500,'dining': 400,'flights_portal': 600,
    'hotel': 150,'gas': 200,'online_shopping': 300,'other': 300,
}

# -------------------------------
# Helper functions
# -------------------------------
def get_reward_rate(card, category):
    category_map = {
        "groceries":["groceries","supermarkets","online_grocery"],
        "dining":["dining","restaurants"],
        "flights_portal":["flights_portal","flights","travel_portal","other_travel"],
        "hotel":["hotel","hotels_portal","lodging","travel_portal"],
        "gas":["gas","transport","transit"],
        "online_shopping":["online_shopping","top","other"],
        "other":["other"]
    }
    keys_to_try = category_map.get(category,["other"]) + ["all"]
    for key in keys_to_try:
        if key in card["rewards"]:
            return card["rewards"][key]
    return 1

def apply_rebates(cards, annual_spending, rebate_usage):
    """Apply category and flat rebates, avoiding double-counting across multiple cards"""
    remaining_spend = dict(annual_spending)  # track how much spending is left for category rebates
    total_rebate = 0.0
    # per_card_details maps card name -> list of structured rebate entries
    # each entry: {"idx": int, "description": str, "is_category": bool, "used": float, "amount": float, "applied": bool}
    per_card_details = {}

    # Gather all category rebates and sort by amount descending
    category_rebates = []
    for card in cards:
        for idx, rebate in enumerate(card.get("rebates", [])):
            if rebate["type"] == "category":
                category_rebates.append((rebate["category"], rebate["amount"], card, idx, rebate))

    # Sort so largest rebate is applied first
    category_rebates.sort(key=lambda x: -x[1])

    # Track which rebates have been applied
    applied_rebates = set()

    # Apply category rebates
    for cat, amount, card, idx, rebate in category_rebates:
        spent = remaining_spend.get(cat, 0.0)
        used = min(spent, amount)
        applied = used > 0
        total_rebate += used
        remaining_spend[cat] -= used  # reduce remaining spend for other cards
        per_card_details.setdefault(card["name"], []).append({
            "idx": idx,
            "description": rebate.get("description", "Category Rebate"),
            "is_category": True,
            "used": used,
            "amount": amount,
            "applied": applied,
        })
        applied_rebates.add((card["name"], idx))

    # Apply flat rebates
    for card in cards:
        details = per_card_details.setdefault(card["name"], [])
        for idx, rebate in enumerate(card.get("rebates", [])):
            if rebate["type"] == "flat":
                used_flag = rebate_usage.get(card["name"], {}).get(idx, True)
                if used_flag:
                    total_rebate += rebate["amount"]
                details.append({
                    "idx": idx,
                    "description": rebate.get("description", "Flat Rebate"),
                    "is_category": False,
                    "used": rebate["amount"] if used_flag else 0.0,
                    "amount": rebate["amount"],
                    "applied": bool(used_flag),
                })

    return total_rebate, per_card_details


def evaluate_portfolio(cards, annual_spending, rebate_usage):
    total_rewards = 0.0
    assignment = {}

    # calculate rewards per category
    for cat, spend in annual_spending.items():
        best_val, best_card = 0.0, None
        for card in cards:
            rate = get_reward_rate(card, cat)
            val = rate * spend * card.get("point_value", 0.01)
            if val > best_val:
                best_val, best_card = val, card["name"]
        assignment[cat] = (best_card, best_val)
        total_rewards += best_val

    # apply rebates with overlap logic
    total_rebates, per_card_details = apply_rebates(cards, annual_spending, rebate_usage)

    # subtract fees once per card
    fees = sum(c.get("annual_fee",0) for c in cards)
    total_net = total_rewards + total_rebates - fees

    return total_net, assignment, per_card_details, total_rewards, fees

def _score_card_approx(card, annual_spending, include_rebates=True, include_offers=True):
    # approximate monetary value of a single card for prefiltering
    point_value = card.get('point_value', 0.01)
    rewards = card.get('rewards', {})
    score = 0.0
    for cat, spend in annual_spending.items():
        # use get_reward_rate mapping logic but for this single card
        rate = 1
        # try direct keys
        if cat in rewards:
            rate = rewards[cat]
        else:
            # try some common aliases
            for alias in ([cat, 'all']):
                if alias in rewards:
                    rate = rewards[alias]
                    break
        score += rate * spend * point_value
    # add flat rebates roughly
    if include_rebates:
        for r in card.get('rebates', []):
            if r.get('type') == 'flat':
                score += r.get('amount', 0)
    # subtract fee
    score -= card.get('annual_fee', 0)
    return score


def find_best_portfolio(credit_cards, annual_spending, rebate_usage, max_cards=MAX_CARDS_TO_CONSIDER, max_portfolio_size=MAX_PORTFOLIO_SIZE, include_rebates=True, include_offers=True):
    start = time.time()

    cards_to_consider = list(credit_cards)
    # prefilter to top-N by an approximate per-card score to avoid combinatorial explosion
    if max_cards is not None and len(cards_to_consider) > max_cards:
        scored = [(c, _score_card_approx(c, annual_spending, include_rebates=include_rebates, include_offers=include_offers)) for c in cards_to_consider]
        scored.sort(key=lambda x: -x[1])
        cards_to_consider = [c for c,_ in scored[:max_cards]]

    best_val, best_portfolio, best_assignment, best_details = -1e12, None, {}, {}
    best_rewards, best_fees = 0,0
    max_r = len(cards_to_consider) if max_portfolio_size is None else min(len(cards_to_consider), max_portfolio_size)
    for r in range(1, max_r+1):
        for subset in combinations(cards_to_consider, r):
            # If offers should be ignored, we also want to ignore offer-derived rebates in our rebate usage handling
            val, assignment, details, rewards, fees = evaluate_portfolio(subset, annual_spending, rebate_usage if include_rebates else {})
            if val > best_val:
                best_val, best_portfolio, best_assignment, best_details = val, subset, assignment, details
                best_rewards, best_fees = rewards, fees

    elapsed = time.time() - start
    # return elapsed time as last value (caller may ignore)
    return best_portfolio, best_val, best_assignment, best_details, best_rewards, best_fees, elapsed

# -------------------------------
# GUI
# -------------------------------
class CreditCardOptimizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Credit Card Optimizer (Persistent Rebates)")
        self.setGeometry(100,100,950,750)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        

        # Spending inputs
        self.form_layout = QFormLayout()
        self.inputs = {}
        for cat in categories:
            inp = QLineEdit(str(monthly_spending_defaults[cat]))
            # numeric validator to prevent non-numeric input causing crashes
            validator = QDoubleValidator(0.0, 1e9, 2, self)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            inp.setValidator(validator)
            inp.setAlignment(Qt.AlignmentFlag.AlignRight)
            inp.textChanged.connect(self.calculate_and_show)  # auto recalc
            self.form_layout.addRow(cat.replace("_"," ").title(), inp)
            self.inputs[cat] = inp
        self.layout.addLayout(self.form_layout)

        # Category table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Category","Best Card","Annual Value ($)"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setStyleSheet("""
            QTableWidget { gridline-color: #ddd; font-size: 12px; }
            QHeaderView::section { font-weight: bold; }
        """)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.layout.addWidget(self.table)

        # Portfolio info
        self.portfolio_label = QLabel("")
        self.total_label = QLabel("")
        self.layout.addWidget(self.portfolio_label)
        self.layout.addWidget(self.total_label)

        # Rebate table
        self.rebate_table = QTableWidget()
        self.rebate_table.setColumnCount(4)
        self.rebate_table.setHorizontalHeaderLabels(["Card","Rebate","Value ($)","Use?"])
        self.rebate_table.setAlternatingRowColors(True)
        self.rebate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rebate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rebate_table.setStyleSheet("""
            QTableWidget { gridline-color: #ddd; font-size: 12px; }
            QHeaderView::section { font-weight: bold; }
        """)
        self.rebate_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.layout.addWidget(self.rebate_table)

        # persistent state for flat rebate usage
        credit_cards = load_credit_cards("credit_cards_converted.txt")
        self.rebate_usage = {}
        for card in credit_cards:
            self.rebate_usage[card["name"]] = {idx: True for idx,_ in enumerate(card.get("rebates",[]))}

        # Options: ignore rebates or signup offers
        from PyQt6.QtWidgets import QCheckBox, QHBoxLayout
        opts_layout = QHBoxLayout()
        self.ignore_rebates_cb = QCheckBox("Ignore Rebates")
        self.ignore_offers_cb = QCheckBox("Ignore Sign-up Offers")
        self.ignore_rebates_cb.stateChanged.connect(self.calculate_and_show)
        self.ignore_offers_cb.stateChanged.connect(self.calculate_and_show)
        opts_layout.addWidget(self.ignore_rebates_cb)
        opts_layout.addWidget(self.ignore_offers_cb)
        self.layout.addLayout(opts_layout)

        self.calculate_and_show()

    def calculate_and_show(self):
        user_spending = {cat: float(self.inputs[cat].text() or 0) for cat in categories}
        annual_spending = {cat: val*12 for cat,val in user_spending.items()}
        credit_cards = load_credit_cards("credit_cards_converted.txt")
        include_rebates = not bool(self.ignore_rebates_cb.isChecked())
        include_offers = not bool(self.ignore_offers_cb.isChecked())
        best_portfolio, best_val, best_assignment, details, rewards, fees, elapsed = find_best_portfolio(
            credit_cards, annual_spending, self.rebate_usage, include_rebates=include_rebates, include_offers=include_offers
        )

        # Category table
        self.table.setRowCount(len(categories))
        for i, cat in enumerate(categories):
            best_card, val = best_assignment.get(cat,("None",0.0))
            self.table.setItem(i,0,QTableWidgetItem(cat.replace("_"," ").title()))
            self.table.setItem(i,1,QTableWidgetItem(best_card))
            self.table.setItem(i,2,QTableWidgetItem(f"{val:,.2f}"))

        # Portfolio info
        portfolio_names = ", ".join(c["name"] for c in best_portfolio)
        self.portfolio_label.setText(f"Best Portfolio: {portfolio_names}")
        self.total_label.setText(f"Net Value: ${best_val:,.2f} (Rewards ${rewards:.2f} - Fees ${fees:.2f} + Rebates)  |  Computation: {elapsed:.2f}s")

        # Rebate table
        # Rebate table - use structured details from evaluator to avoid double-counting
        rows = []
        per_card = details  # per_card_details returned by evaluator
        for card in best_portfolio:
            card_name = card["name"]
            card_rebates = card.get("rebates", [])
            card_details = per_card.get(card_name, [])
            for idx, rebate in enumerate(card_rebates):
                desc = rebate.get("description", "")
                entry = next((e for e in card_details if e.get("idx") == idx), None)
                if entry is None:
                    # fallback (shouldn't normally happen) - show full/used based on type
                    if rebate["type"] == "category":
                        spent = annual_spending.get(rebate["category"], 0.0)
                        used = min(spent, rebate["amount"])
                        val_text = f"Used ${used:.2f} of ${rebate['amount']:.2f}"
                        is_category = True
                    else:
                        val_text = f"${rebate['amount']:.2f}"
                        is_category = False
                else:
                    if entry.get("is_category", False):
                        val_text = f"Used ${entry.get('used', 0.0):.2f} of ${entry.get('amount', 0.0):.2f}"
                        is_category = True
                    else:
                        val_text = f"${entry.get('used', 0.0):.2f}"
                        is_category = False
                rows.append((card_name, idx, desc, val_text, is_category))

        self.rebate_table.setRowCount(len(rows))
        for r, (card, idx, desc, val_text, is_category) in enumerate(rows):
            self.rebate_table.setItem(r,0,QTableWidgetItem(card))
            self.rebate_table.setItem(r,1,QTableWidgetItem(desc))
            self.rebate_table.setItem(r,2,QTableWidgetItem(val_text))

            if is_category:
                cb = QCheckBox()
                cb.setChecked(True)
                cb.setEnabled(False)
            else:
                cb = QCheckBox()
                cb.setChecked(self.rebate_usage.get(card, {}).get(idx, True))
                cb.stateChanged.connect(lambda state, c=card, i=idx: self.toggle_rebate(c,i,state))

            self.rebate_table.setCellWidget(r,3,cb)

    def toggle_rebate(self, card, idx, state):
        self.rebate_usage.setdefault(card,{})[idx] = (state == 2)
        self.calculate_and_show()

# -------------------------------
# Run
# -------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CreditCardOptimizer()
    window.show()
    sys.exit(app.exec())
