"""Mock xtquant.xttype"""

class StockAccount:
    def __init__(self, account_id='', account_type=1):
        self.account_id = account_id
        self.account_type = account_type
